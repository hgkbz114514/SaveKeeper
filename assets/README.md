# assets

项目用到的静态资源。

| 文件 | 用途 | 来源 |
|---|---|---|
| `icon.ico` | 应用图标，256/128/64/48/32/24/16 共 7 个尺寸，供 PyInstaller `--icon` 使用 | `python build_icon.py` 生成 |
| `icon.png` | 512×512 PNG 版图标，用于仓库头像 / 文档 | 同上 |
| `screenshot.png` | README 顶部的 GUI 主界面截图 | `python capture_screenshot.py` 生成 |

## 资源是怎么来的

图标和截图都由脚本生成，没有引入外部素材，不需要担心版权问题：

```bash
# 重新生成图标（Pillow 程序化绘制，超采样 8× 后缩小）
python build_icon.py

# 重新截主界面图（需要先准备演示数据，见下）
python demo_setup.py
set SAVEKEEPER_HOME=%CD%\demo-home
start python run_gui.py
python capture_screenshot.py
```

`capture_screenshot.py` 只截 `SaveKeeper` 这一个窗口的区域，不是全屏截图；
截完记得把底部状态栏裁掉（脚本里的 `crop_status_bar`）再存成 `screenshot.png`。
