"""外服 LoL 一键汉化 —— 改语言 + 自动开 Riot Client + 自动启动游戏。

原理：把 product_settings.yaml 里 settings.locale 改成目标语言（默认 zh_CN）。
Riot Client 启动时会把这个值改回去，所以 v0.1 必须"先开客户端再跑脚本"。
这一版自己负责启动客户端，并在客户端启动期间盯着文件，被改回去就再改一次，
所以什么时候运行都行，双击一下即可。
"""
import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time

VERSION = "0.2"
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


def replace_locale(text, locale):
    """只改 locale 这一行，其余内容原样保留。"""
    lines = text.splitlines(keepends=True)
    i = _find_locale_line(lines)
    if i is None:
        raise ValueError("配置文件里没找到 settings.locale")
    line = lines[i]
    indent = line[: len(line) - len(line.lstrip())]
    ending = line[len(line.rstrip("\r\n")):]
    lines[i] = f'{indent}locale: "{locale}"{ending}'
    return "".join(lines)


def load(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def apply_locale(path, locale):
    """把语言改成 locale。返回 (之前的语言, 是否真的写了文件)。"""
    text = load(path)
    current = read_locale(text)
    if current == locale:
        return current, False
    new_text = replace_locale(text, locale)
    if not os.access(path, os.W_OK):
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
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


def guard(path, locale, quiet_for, timeout):
    """盯着配置文件：被客户端改回去就再改一次，连续 quiet_for 秒没动静就算稳了。"""
    end = time.monotonic() + timeout
    stable_since = time.monotonic()
    reverts = 0
    while time.monotonic() < end:
        try:
            _, changed = apply_locale(path, locale)
        except (OSError, ValueError):
            changed = True  # 客户端正在写文件，等下一轮
        if changed:
            reverts += 1
            stable_since = time.monotonic()
            info(f"语言设置被客户端改回，已重新写入 {locale}")
        elif time.monotonic() - stable_since >= quiet_for:
            break
        time.sleep(0.5)
    return reverts


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
        set_readonly(path, False)
        before, _ = apply_locale(path, locale)
    except PermissionError:
        fail("没有权限修改配置文件，请右键「以管理员身份运行」。")
        return 1
    except ValueError as e:
        fail(str(e))
        return 1
    if not args.restore:
        remember_original(patchline, before if before != locale else None)
    info(f"当前语言: {before}  ->  新语言: {locale}")

    if not args.no_launch:
        if client and not client_was_running:
            info("正在启动 Riot Client ...")
            spawn([client])
            if not wait_for(lambda: is_running(CLIENT_EXE), 60):
                warn("等了 60 秒 Riot Client 还没起来，继续尝试。")
        if client or client_was_running:
            # 客户端启动时会把语言同步回去；刚启动的多盯一会儿。
            quiet, timeout = (3, 10) if client_was_running else (10, args.guard)
            info("正在确认语言设置没有被客户端改回 ...")
            guard(path, locale, quiet, timeout)

    if args.readonly:
        set_readonly(path, True)
        info("已把配置文件设为只读（如果之后游戏更新出问题，用 --restore 解除）。")

    if client and not args.no_launch:
        info("正在启动英雄联盟（首次会自动下载语言包，等更新完即可）...")
        spawn([client, "--launch-product=league_of_legends", f"--launch-patchline={patchline}"])
        # 启动/更新过程中再兜底盯一小会儿
        guard(path, locale, 5, 20)

    final = read_locale(load(path))
    if final != locale:
        fail(f"语言又变回了 {final}。可以试试加 --readonly 参数锁住配置文件。")
        return 1
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
    p.add_argument("--readonly", action="store_true", help="改完后把配置文件设为只读，防止被改回（实验性）")
    p.add_argument("--guard", type=int, default=90, help="客户端启动后最多盯多少秒，默认 90")
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
