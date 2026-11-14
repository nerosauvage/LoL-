"""外服 LoL 一键汉化 —— 改语言 + 自动开 Riot Client + 自动启动游戏。

原理（2026 年仍有效的做法）：
  1. product_settings.yaml 里 settings.locale 改成目标语言（默认 zh_CN）；
  2. 同一个文件的 locale_data.available_locales 里也要有这个语言。外服的列表里
     没有 zh_CN，客户端发现语言不在列表里就会悄悄改回默认语言 —— 这就是 v0.1
     只改 settings.locale "失效" 的原因；
  3. 通过 Riot Client 本地接口把语言告诉客户端，并由接口启动游戏；
  4. 客户端启动/更新时会重写这个文件，所以启动期间一直盯着，被改回就再改。

本地接口与 available_locales 的做法参考了 Tariolle/LeagueAccounts（MIT）。
"""
import argparse
import base64
import json
import os
import re
import ssl
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

VERSION = "0.3"
PROGRAM_DATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
RIOT_DATA = os.path.join(PROGRAM_DATA, "Riot Games")
STATE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "lol-hanhua")
CLIENT_EXE = "RiotClientServices.exe"
GAME_EXES = ("LeagueClient.exe", "League of Legends.exe")
LOCALE_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}$")

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def info(msg):
    print(f"{GREEN}[info]{RESET} {msg}", flush=True)


def warn(msg):
    print(f"{YELLOW}[warn]{RESET} {msg}", flush=True)


def fail(msg):
    print(f"{RED}[error]{RESET} {msg}", flush=True)


# ---------- 配置文件 ----------

def settings_path(patchline):
    name = f"league_of_legends.{patchline}"
    return os.path.join(RIOT_DATA, "Metadata", name, f"{name}.product_settings.yaml")


def _find_locale_line(lines):
    """返回 settings: 块里 locale 那一行的下标，找不到返回 None。"""
    in_settings = False
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_settings = line.rstrip().rstrip(":").strip() == "settings" and line.rstrip().endswith(":")
            continue
        if in_settings and re.match(r"\s+locale\s*:", line):
            return i
    return None


def read_locale(text):
    lines = text.splitlines()
    i = _find_locale_line(lines)
    if i is None:
        return None
    return lines[i].split(":", 1)[1].strip().strip("\"'")


def _entry(line):
    """`    - "en_US"` 这样的列表项 -> en_US；不是列表项返回 None。"""
    t = line.strip()
    if not t.startswith("- "):
        return None
    return t[2:].strip().strip("\"'")


def _available_range(lines):
    """available_locales 列表项的 [起, 止) 下标；没有这个键返回 None。"""
    for i, line in enumerate(lines):
        if line.strip() == "available_locales:":
            j = i + 1
            while j < len(lines) and _entry(lines[j]) is not None:
                j += 1
            return i + 1, j
    return None


def is_available(text, locale):
    lines = text.splitlines()
    r = _available_range(lines)
    return r is not None and any(_entry(l) == locale for l in lines[r[0]:r[1]])


def patch_text(text, locale):
    """改 settings.locale，并保证 available_locales 里有它。其余内容原样保留。"""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    i = _find_locale_line(lines)
    if i is None:
        raise ValueError("配置文件里没找到 settings.locale")
    line = lines[i]
    indent = line[: len(line) - len(line.lstrip())]
    ending = line[len(line.rstrip("\r\n")):]
    lines[i] = f'{indent}locale: "{locale}"{ending}'

    r = _available_range(lines)
    if r is not None and not any(_entry(l) == locale for l in lines[r[0]:r[1]]):
        start, end = r
        if end > start:  # 照抄现有条目的缩进和引号风格
            sample = lines[start]
            indent = sample[: len(sample) - len(sample.lstrip())]
            quote = '"' if '"' in sample else ("'" if "'" in sample else "")
        else:
            head = lines[start - 1]
            indent, quote = head[: len(head) - len(head.lstrip())], '"'
        pos = next((k for k in range(start, end) if _entry(lines[k]) > locale), end)
        if pos > 0 and not lines[pos - 1].endswith(("\n", "\r")):
            lines[pos - 1] += newline
        lines.insert(pos, f"{indent}- {quote}{locale}{quote}{newline}")
    return "".join(lines)


def load(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def apply_locale(path, locale):
    """把语言改成 locale。返回 (之前的语言, 是否真的写了文件)。"""
    text = load(path)
    current = read_locale(text)
    new_text = patch_text(text, locale)
    if new_text == text:
        return current, False
    set_readonly(path, False)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    return current, True


def set_readonly(path, readonly):
    mode = os.stat(path).st_mode
    os.chmod(path, mode & ~stat.S_IWUSR if readonly else mode | stat.S_IWUSR)


# ---------- 记住原来的语言，方便还原 ----------

def _state_file(patchline):
    return os.path.join(STATE_DIR, f"original_locale.{patchline}.txt")


def remember_original(patchline, locale):
    path = _state_file(patchline)
    if locale and not os.path.exists(path):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(locale)


def original_locale(patchline):
    try:
        with open(_state_file(patchline), "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


# ---------- Riot Client ----------

def is_running(image):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, text=True, errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except OSError:
        return False
    return image.lower() in out.lower()


def find_client():
    """从 RiotClientInstalls.json 找 RiotClientServices.exe。"""
    candidates = []
    try:
        with open(os.path.join(RIOT_DATA, "RiotClientInstalls.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        candidates += [data.get(k) for k in ("rc_default", "rc_live", "rc_beta")]
    except (OSError, ValueError):
        pass
    candidates.append(os.path.join(r"C:\Riot Games\Riot Client", CLIENT_EXE))
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.normpath(c)
    return None


def spawn(args):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(predicate, timeout, interval=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class LocalApi:
    """Riot Client 的本地 HTTPS 接口（地址和密码在 lockfile 里）。"""

    def __init__(self, port, password):
        self.base = f"https://127.0.0.1:{port}"
        self.auth = "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # 只连本机回环地址，证书是客户端自签的
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))

    @classmethod
    def read(cls):
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        try:
            with open(os.path.join(local, "Riot Games", "Riot Client", "Config", "lockfile"),
                      "r", encoding="utf-8") as f:
                parts = f.read().strip().split(":")  # name:pid:port:password:protocol
            return cls(int(parts[2]), parts[3]) if len(parts) == 5 else None
        except (OSError, ValueError):
            return None

    def request(self, method, path, body=None):
        """返回 (状态码, 解析后的 JSON)；连不上返回 (None, None)。"""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=3) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                raw = e.read()
            except OSError:
                raw = b""
        except (OSError, ValueError):
            return None, None
        try:
            return status, json.loads(raw.decode("utf-8", "ignore") or "null")
        except ValueError:
            return status, None

    def _locale_path(self, patchline):
        return f"/riotclient/product-locales/products/league_of_legends/patchlines/{patchline}"

    def product_locale(self, patchline):
        status, body = self.request("GET", self._locale_path(patchline))
        return body if status == 200 and isinstance(body, str) else None

    def set_product_locale(self, patchline, locale):
        """客户端不认的语言会保持原值，所以以读回来的结果为准。"""
        self.request("PUT", self._locale_path(patchline), locale)
        return self.product_locale(patchline) == locale

    def launch(self, patchline):
        """'ok' 已启动 / 'updating' 正在更新 / 'wait' 还不能启动（多半没登录）/ None 连不上。"""
        status, _ = self.request(
            "POST", f"/product-launcher/v1/products/league_of_legends/patchlines/{patchline}", {})
        if status is None:
            return None
        if 200 <= status < 300 or status == 423:
            return "ok"
        return "updating" if status == 424 else "wait"


_api_rejects = [0]


def sync(path, patchline, locale):
    """让客户端和配置文件都是 locale。返回文件是否被（重新）写入。"""
    # 外服的语言注册表里没有 zh_CN，接口可能不认；试几次就只靠配置文件。
    api = LocalApi.read() if _api_rejects[0] < 3 else None
    if api and api.product_locale(patchline) not in (None, locale):
        if not api.set_product_locale(patchline, locale):
            _api_rejects[0] += 1
    try:
        return apply_locale(path, locale)[1]
    except (OSError, ValueError):
        return True  # 客户端正在写文件，下一轮再来


def launch_game(path, patchline, locale, client, timeout):
    """通过本地接口启动游戏，期间守住语言。接口不可用时退回命令行启动。"""
    end = time.monotonic() + timeout
    state, tick = None, 0
    while time.monotonic() < end:
        if sync(path, patchline, locale) and tick:
            info(f"语言设置被客户端改回，已重新写入 {locale}")
        if any(is_running(exe) for exe in GAME_EXES):
            return True
        if tick % 5 == 0:
            api = LocalApi.read()
            new = api.launch(patchline) if api else None
            if new is None and tick >= 30 and client:
                spawn([client, "--launch-product=league_of_legends", f"--launch-patchline={patchline}"])
                new = "ok"
            if new != state and new:
                info({"ok": "已请求启动游戏，等待游戏窗口 ...",
                      "updating": "客户端正在更新游戏 / 下载语言包，完成后会自动启动 ...",
                      "wait": "请在 Riot Client 里登录账号，登录后会自动启动游戏 ..."}[new])
            state = new or state
        tick += 1
        time.sleep(1)
    return False


def guard(path, patchline, locale, quiet_for, timeout):
    """盯着配置：被客户端改回去就再改一次，连续 quiet_for 秒没动静就算稳了。"""
    end = time.monotonic() + timeout
    stable_since = time.monotonic()
    first = True
    while time.monotonic() < end:
        if sync(path, patchline, locale):
            stable_since = time.monotonic()
            if not first:
                info(f"语言设置被客户端改回，已重新写入 {locale}")
        elif time.monotonic() - stable_since >= quiet_for:
            break
        first = False
        time.sleep(0.5)


# ---------- 主流程 ----------

def run(args):
    patchline = args.patchline
    path = settings_path(patchline)

    if args.restore:
        locale = original_locale(patchline) or "en_US"
        info(f"还原语言为 {locale}")
    else:
        locale = args.locale
    if not LOCALE_RE.match(locale):
        fail(f"语言代码格式不对: {locale}（应类似 zh_CN / en_US / ja_JP）")
        return 2

    if not os.path.isfile(path):
        fail(f"没找到配置文件: {path}")
        fail("请确认已通过 Riot Client 安装英雄联盟，并至少启动过一次。")
        return 1
    info(f"配置文件: {path}")

    for exe in GAME_EXES:
        if is_running(exe):
            warn(f"{exe} 正在运行 —— 新语言要等关掉游戏、重新启动（会自动更新语言包）后才生效。")
            break

    client = None if args.no_launch else find_client()
    client_was_running = is_running(CLIENT_EXE)
    if not args.no_launch and not client and not client_was_running:
        warn("没找到 Riot Client 的安装位置，只修改配置，请手动打开客户端。")

    try:
        before = read_locale(load(path))
        apply_locale(path, locale)
    except PermissionError:
        fail("没有权限修改配置文件，请右键「以管理员身份运行」。")
        return 1
    except ValueError as e:
        fail(str(e))
        return 1
    if not args.restore:
        remember_original(patchline, before if before != locale else None)
    info(f"当前语言: {before}  ->  新语言: {locale}")
    if not is_available(load(path), locale):
        warn("配置文件里没有 available_locales 列表，客户端可能不接受这个语言。")

    launched = False
    if not args.no_launch and (client or client_was_running):
        if not client_was_running:
            info("正在启动 Riot Client ...")
            spawn([client])
            if not wait_for(lambda: is_running(CLIENT_EXE), 60):
                warn("等了 60 秒 Riot Client 还没起来，继续尝试。")
        info("正在把语言同步给客户端 ...")
        # 客户端刚启动时会用自己的状态重写配置文件；刚启动的多盯一会儿。
        guard(path, patchline, locale, *((3, 10) if client_was_running else (10, 90)))
        info("正在启动英雄联盟（Ctrl+C 可随时退出）...")
        try:
            launched = launch_game(path, patchline, locale, client, args.wait * 60)
        except KeyboardInterrupt:
            pass
        if launched:
            guard(path, patchline, locale, 5, 20)

    final_text = load(path)
    final = read_locale(final_text)
    if final != locale or not is_available(final_text, locale):
        fail(f"语言又被客户端改回了 {final}，这次没有成功。")
        return 1
    if args.no_launch or not (client or client_was_running):
        info("配置已修改。请打开 Riot Client 后再运行一次本工具（不带 --no-launch）来启动游戏。")
    elif not launched:
        warn("没等到游戏启动。登录 / 更新完成后再运行一次本工具即可。")
    print(f"{GREEN}完成。登录后右上角显示中文「在线」即表示成功。{RESET}" if locale == "zh_CN"
          else f"{GREEN}完成，当前语言 {locale}。{RESET}")
    return 0


def main():
    os.system("")  # 让 Windows 控制台支持颜色
    p = argparse.ArgumentParser(description=f"外服 LoL 一键汉化 v{VERSION}")
    p.add_argument("-l", "--locale", default="zh_CN", help="目标语言，默认 zh_CN（也可 zh_TW / ja_JP / ko_KR 等）")
    p.add_argument("-r", "--restore", action="store_true", help="还原成汉化前的语言")
    p.add_argument("--patchline", default="live", help="live（默认）或 pbe")
    p.add_argument("--no-launch", action="store_true", help="只改配置，不启动客户端和游戏")
    p.add_argument("--wait", type=int, default=30, help="最多等游戏启动多少分钟（含登录和更新），默认 30")
    p.add_argument("--no-pause", action="store_true", help="结束时不等待回车")
    args = p.parse_args()

    info(f"外服 LoL 一键汉化 v{VERSION}   By: 白丝萝莉 #HEISI   免费工具 欢迎传播")
    try:
        code = run(args)
    except Exception as e:  # 双击运行时别让窗口一闪而过
        fail(f"出错了: {e!r}")
        code = 1
    if not args.no_pause and sys.stdin and sys.stdin.isatty():
        input("按回车退出...")
    return code


if __name__ == "__main__":
    sys.exit(main())
