# 外服 LoL 一键汉化

把外服（美服 / 日服 / 欧服……）英雄联盟客户端改成中文。**双击运行即可**，不用再讲究先开 Riot Client 还是先跑脚本。

## 用法

1. 到 [Releases](../../releases) 或 Actions 构建产物里下载 `lol汉化.exe`（或直接 `python lol.py`，无第三方依赖）。
2. 双击运行。脚本会自动：
   - 把语言改成 `zh_CN`
   - 没开 Riot Client 就帮你打开，并盯着配置文件，被客户端改回去就再改一次
   - 启动英雄联盟（首次会自动下载中文语言包，等它更新完）
3. 登录后右上角是中文"在线"就成功了。

如果运行时游戏已经开着：关掉游戏再启动一次即可。

## 参数（可选）

| 参数 | 作用 |
| --- | --- |
| `-l zh_TW` | 换成别的语言（`zh_CN` 默认，`zh_TW` / `ja_JP` / `ko_KR` / `en_US` …） |
| `-r` / `--restore` | 还原成汉化前的语言 |
| `--patchline pbe` | 改 PBE 测试服 |
| `--no-launch` | 只改配置，不启动客户端和游戏 |
| `--readonly` | 改完把配置文件设为只读，防止被改回（实验性） |
| `--no-pause` | 结束时不等回车（适合做成快捷方式） |

## 原理

修改 `C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml`
里的 `settings.locale`。Riot Client 启动时会把它同步回客户端自己的语言，所以旧版必须"先开客户端再运行脚本"；
新版自己启动客户端并在启动期间守着这个值，所以顺序无所谓。

## 自己打包

```bash
pip install pyinstaller
```

```bash
pyinstaller --onefile --name lol汉化 lol.py
```
