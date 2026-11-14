# 外服 LoL 一键汉化

把外服（美服 / 日服 / 欧服……）英雄联盟客户端改成中文。**双击运行即可**，不用再讲究先开 Riot Client 还是先跑脚本。

## 用法

1. 在 Actions 的构建产物里下载 `lol-hanhua.exe`（或直接 `python lol.py`，无第三方依赖）。
2. 双击运行。脚本会自动：
   - 把语言改成 `zh_CN`，并把 `zh_CN` 加进客户端认可的语言列表
   - 没开 Riot Client 就帮你打开；没登录就等你登录
   - 启动英雄联盟（首次会自动下载中文语言包），期间盯着配置，被客户端改回去就再改一次
3. 登录后右上角是中文「在线」就成功了。

4. 最后会问要不要开启「保持汉化」，直接回车即可。开启后它会开机自启、在后台守着配置，
   Riot Client 每次启动把语言改回去时它会立刻改回来 —— 以后正常打开 Riot Client 玩就行，不用再运行本工具。

如果运行时游戏已经开着：关掉游戏再运行一次即可。

> 仓库里旧的 `lol汉化.exe`（v0.1）已经失效，原因见下面的「原理」。

## 参数（可选）

| 参数 | 作用 |
| --- | --- |
| `-l zh_TW` | 换成别的语言（`zh_CN` 默认，`zh_TW` / `zh_MY` / `ja_JP` / `ko_KR` / `en_US` …） |
| `-r` / `--restore` | 还原成汉化前的语言 |
| `--patchline pbe` | 改 PBE 测试服 |
| `--install` / `--uninstall` | 开启 / 关闭「保持汉化」（后台常驻） |
| `--no-launch` | 只改配置，不启动客户端和游戏 |
| `--wait 30` | 最多等游戏启动多少分钟（含登录、更新） |
| `--no-pause` | 结束时不等回车（适合做成快捷方式） |

## 原理

配置文件：`C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml`

- `settings.locale` 决定游戏启动时的 `--locale=`。
- `locale_data.available_locales` 是客户端校验语言用的列表。外服的列表里没有 `zh_CN`，
  语言不在列表里时客户端不报错，直接悄悄改回默认语言 —— 所以只改 `settings.locale` 的老办法失效了。
  游戏本体其实带着简体中文资源，把 `zh_CN` 加进列表即可。
- 客户端启动、更新时会用自己的状态重写这个文件（设只读也没用，它会自己清掉只读属性），
  所以脚本通过 Riot Client 的本地接口同步语言、启动游戏，并在这期间持续把配置改回来。

`available_locales` 和本地接口的做法参考了 [Tariolle/LeagueAccounts](https://github.com/Tariolle/LeagueAccounts)（MIT）。

已知问题：`zh_CN` 下客户端首页的活动页可能是空白的（Riot 没给外服发布简体中文的活动资源），不影响对局。

## 自己打包

```bash
pip install pyinstaller
```

```bash
pyinstaller --onefile --name lol-hanhua lol.py
```
