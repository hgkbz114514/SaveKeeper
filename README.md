# SaveKeeper · 游戏存档一键管理工具

> 一键打开存档目录，快速备份 / 还原 / 校验游戏存档。
> **不改游戏本体，只操作存档目录内的文件。零第三方依赖，解压即用。**

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey)
![Deps](https://img.shields.io/badge/runtime%20deps-0-success)
![Tests](https://img.shields.io/badge/tests-27%20passed-brightgreen)
![License](https://img.shields.io/badge/License-MIT-green)

![SaveKeeper 主界面](assets/screenshot.png)

---

## 为什么做这个

游戏存档散落在 `%APPDATA%`、`Documents`、Steam `userdata` 各个角落，手动翻文件夹找半天、还容易找错。
换机器、重装系统、想回退到某个 BOSS 战前，只能靠"我记得复制过一个文件夹"。

SaveKeeper 把这件事压缩成两步：**选游戏 → 点按钮**。

| 功能 | 说明 |
|---|---|
| 📂 **一键打开存档目录** | 自动检测，直接定位到存档所在文件夹 |
| ⬇ **导出存档** | 完整快照存档目录，带 SHA-256 清单 |
| ⬆ **导入存档** | 选中快照一键还原；**导入前自动备份当前存档**，随时可回退 |
| 🔍 **完整性校验** | 导入前逐文件校验 SHA-256，校验失败**阻止覆盖**并报错 |
| 🗂 **自定义游戏** | 内置库没有的游戏，手动指定路径即可 |
| 💻 **CLI + GUI** | 图形界面为主，命令行同样全功能（脚本 / 无桌面环境可用） |
| 🎮 **内置 73 款游戏路径** | 自动检测；库里没有的用「自定义游戏」手动指定 |

---

## 快速开始

### 方式 A：下载打包版（推荐普通用户）

到 [Releases](../../releases) 下载 `SaveKeeper-Windows.zip`，解压后双击里面的 `SaveKeeper.exe` 即可，
无需安装 Python。

> 解压后请保持文件夹结构完整（`SaveKeeper.exe` 需要同目录下的 `_internal`），
> 想放桌面的话建个快捷方式，不要只把 exe 单独拖出来。

### 方式 B：从源码运行

```bash
git clone https://github.com/hgkbz114514/SaveKeeper.git
cd SaveKeeper

# GUI（主用法）
python -m savekeeper.gui

# 或命令行
python -m savekeeper list
```

> 运行时**零第三方依赖**，只用 Python 标准库（Python 3.9+）。
> 需要 tkinter —— Windows 官方安装包默认自带；Linux 装 `python3-tk`。

### 方式 C：自己打包 exe

```bash
pip install pyinstaller
python -m PyInstaller SaveKeeper.spec --noconfirm --clean
```

产物在 `dist/SaveKeeper/SaveKeeper.exe`（整个 `dist/SaveKeeper/` 目录一起分发）。

> 用的是 onedir 而不是 onefile：onefile 每次启动都要把自己解压到 `%TEMP%`，
> 在受限 TEMP 环境里会直接报 `Could not create temporary directory`，
> 而且更容易被杀软盯上。onedir 启动更快、更稳。

---

## 使用说明

### GUI 主界面（操作 ≤ 2 步）

```
┌──────────────────────────────────────────────────────────────┐
│ SaveKeeper · 游戏存档一键管理                                 │
├───────────────────────┬──────────────────────────────────────┤
│ ① 选择游戏            │ 当前游戏                             │
│  ✓ 艾尔登法环 (Steam)  │  存档目录：C:\...\AppData\Roaming\... │
│  ✓ 只狼 (Steam)        │  3 个文件 · 12.4 KB                  │
│    博德之门3 (Steam)  │                                      │
│    星露谷物语 (Steam)  │  [📂 打开存档目录] [⬇ 导出] [⬆ 导入]  │
│    ...                │                                      │
│                       │ ② 备份快照                           │
│                       │  通关前_20261025_200946  2文件  校验通过 │
│                       │  打BOSS前_20261025_190102  2文件  校验通过 │
└───────────────────────┴──────────────────────────────────────┘
```

**导出**：选中游戏 → 点「导出存档」（可填备注，如"通关前"）。
**还原**：选中游戏 → 在快照列表选中一条 → 点「导入存档」。导入前会自动把当前存档再备份一份。

### 命令行

```bash
# 列出全部游戏 + 自动检测结果
python -m savekeeper list

# 一键打开存档目录
python -m savekeeper open eldenring

# 导出存档（--label 加备注）
python -m savekeeper export eldenring --label "通关前"

# 查看某游戏的所有备份
python -m savekeeper snapshots eldenring

# 还原（覆盖前自动备份当前存档）
python -m savekeeper import eldenring --snapshot 20261025_200946

# 从外部文件夹导入
python -m savekeeper import eldenring --from "D:/别人分享的存档"

# 手动指定存档路径（自动检测失败时）
python -m savekeeper setpath eldenring "C:\Users\你\AppData\Roaming\EldenRing"

# 添加自定义游戏
python -m savekeeper add "潜水员戴夫" "C:\...\AppData\LocalLow\Mintrocket\dave the diver"

# 校验某个快照是否完整
python -m savekeeper verify "C:\Users\你\Documents\SaveKeeper-Backups\eldenring\xxx"
```

---

## 存档路径是怎么找到的

**内置游戏库已收录 73 款游戏**（艾尔登法环、博德之门3、黑神话：悟空、原神、星露谷物语、
GTA5、赛博朋克2077、幻兽帕鲁、潜水员戴夫、死亡细胞……），完整列表在 `savekeeper/games.py`。

按优先级依次探测，命中即用（全部**只读**探测，不写任何文件）：

1. **Steam 云存档目录** —— `Steam/userdata/<用户ID>/<appid>/remote`，比硬编码路径可靠得多
2. **硬编码候选路径** —— 内置游戏库里每个游戏登记的常见路径（`%APPDATA%` / `%LOCALAPPDATA%` / `Documents`）
3. **Steam userdata 兜底** —— 扫描 `userdata/*/<appid>`
4. **用户手动配置** —— `setpath` 或 GUI 里的「设置存档路径」

Steam 安装目录从注册表 `HKCU\Software\Valve\Steam` 读取，只读不写。

---

## 备份目录结构

```
%USERPROFILE%/Documents/SaveKeeper-Backups/
└── eldenring/                                  # 游戏 id
    ├── 打女武神前_20261025_203009/              # 快照（备注_时间戳）
    │   ├── manifest.json                       # 清单：每个文件的 SHA-256 / 大小 / mtime
    │   └── data/                               # 存档目录的完整镜像
    │       ├── ER0000.sl2
    │       └── steam_autocloud.vdf
    └── 20261025_200946/                        # 导入前的自动备份（可回退）
```

备份根目录不是写死在「文档」下，会按下面顺序挑**第一个真正可写**的位置：

1. 你在「设置」里指定的目录
2. `%USERPROFILE%/Documents/SaveKeeper-Backups`
3. `%LOCALAPPDATA%/SaveKeeper/backups`
4. `%USERPROFILE%/SaveKeeper-Backups`

> 之所以要兜底：「文档」目录经常被 OneDrive 重定向、被企业策略或
> Windows **受控文件夹访问**锁住——未签名的新 exe 往里写会直接 `WinError 5 拒绝访问`。
> 现在遇到这种情况会自动换到下一个可写位置，程序照常能用。

快照文件夹名保留中文备注（NTFS 本身就是 UTF-16，中文路径完全合法），
只替换 `<>:"/\|?*` 这些真正非法的字符、去掉首尾空格与点、超过 40 字符截断。
备注原文另存在 `manifest.json` 的 `label` 字段里，改名不影响校验。

---

## 安全设计

| 约束 | 实现 |
|---|---|
| 不修改游戏本体 | 只对存档目录做**读取**和**写入存档目录内**，绝不碰游戏 exe / 注册表 / 配置 |
| 导入前必须校验 | `verify_snapshot()` 逐文件比对 SHA-256，**任何一项不符即抛 `IntegrityError` 中止**，一个字节都不会被覆盖 |
| 导入前自动备份 | 每次还原/导入前，先把当前存档打一份带时间戳的快照，任何误操作可回退 |
| 外部导入也校验 | 从文件夹导入时校验非空与可读性；空目录直接拒绝 |
| 无网络行为 | 不联网、不上传、无云同步、无遥测（见下方"明确不做的事"） |

**明确不做的事**（按需求约束）：云同步、社区分享、自动修改游戏配置。
工具只做"定位 + 备份 + 还原 + 校验"这四件事。

---

## 测试

```bash
python -m pytest tests -v
```

27 个测试覆盖关键路径，包括：

- `test_verify_detects_tampering` —— 删除 / 改写 / 追加 / 清空四种破坏都能被检出
- `test_restore_blocked_when_snapshot_corrupt` —— **校验失败时当前存档一字节不被覆盖**
- `test_restore_roundtrip_no_data_loss` —— 还原后文件集合与原存档完全一致，无丢失
- `test_restore_creates_safety_backup` —— 导入前的旧状态被完整保存
- `test_builtin_games_not_mutated_by_overrides` —— 改配置不污染内置游戏库
- `test_chinese_label_kept_in_folder_name` —— 中文备注能正确进快照名，不退化
- `test_safe_label_strips_illegal_chars` —— 路径非法字符被替换，不会造出打不开的目录

---

## 项目结构

```
savekeeper/
├── savekeeper/
│   ├── __init__.py
│   ├── core.py      # 快照引擎：导出 / SHA-256 校验 / 还原 / 外部导入
│   ├── paths.py     # 路径展开、Steam 注册表与 userdata 探测、资源管理器打开
│   ├── games.py     # 内置游戏库 + 自动检测优先级
│   ├── config.py    # 配置持久化 + 备份目录多层兜底
│   ├── gui.py       # tkinter 界面
│   └── cli.py       # 命令行入口
├── tests/
│   ├── test_core.py
│   └── test_games_config.py
├── assets/
│   ├── icon.ico
│   ├── icon.png
│   └── screenshot.png
├── run_gui.py           # 打包入口（GUI）
├── SaveKeeper.spec      # PyInstaller 打包配置（onedir）
├── build_icon.py        # 程序化生成图标，无需外部素材
├── capture_screenshot.py# 只截目标窗口，用于更新 README 截图
├── demo_setup.py        # 生成演示数据（截图 / 试用）
├── pyproject.toml
└── README.md
```

---

## 常见问题

**双击 exe 没反应 / 弹出「启动失败」？**
先确认解压后 `_internal` 文件夹还在（onedir 版不能只拷 exe）。杀软误报的话把整个目录加白名单。

**备份目录跑到奇怪的地方去了？**
见「备份目录结构」——程序会挑第一个真正可写的位置。界面上「打开备份目录」显示的就是当前实际路径。

**提示「存档完整性校验失败，已阻止覆盖」？**
说明选中的快照被改过或损坏了，程序一个字节都不会写。换一条快照，或重新导出。

---

## 已知限制

- 部分冷门/新游不在内置库里，**用「自定义游戏」手动指定即可**（或提 Issue 补充）
- 存档是单个二进制大文件时（如 `ER0000.sl2`），快照就是整文件拷贝，没有做增量
- 目前主要面向 Windows 10/11；Linux/macOS 下 CLI 可用（路径探测部分降级），GUI 未验证
- 存档路径硬编码来自社区经验，不同机器/系统版本可能有差异，检测不到请手动指定

---

## 更新记录

- **0.1.0** —— 首个版本：73 款内置游戏路径、快照导出/还原/校验、GUI + CLI、打包脚本

欢迎提 Issue / PR，特别是：

1. **补充内置游戏库**（`savekeeper/games.py` 的 `BUILTIN_GAMES`）——附上你验证过的存档路径
2. 更好的存档路径自动检测思路
3. Linux/macOS 适配

提 PR 前请先跑 `python -m pytest tests -q`。

---

## License

[MIT](LICENSE)
