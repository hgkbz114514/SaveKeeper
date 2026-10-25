"""生成演示数据，用于截图 / 录屏 / 手动试用。

建一个假的存档目录，再导出 3 个快照（含一条故意损毁的），
然后把配置指向它。运行后界面上就有真实数据可看。

用法：
    python demo_setup.py
    set SAVEKEEPER_HOME=demo-home & python run_gui.py
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DEMO_HOME = ROOT / "demo-home"
DEMO_SAVE = ROOT / "demo-save"
GAME_ID = "eldenring"
GAME_NAME = "艾尔登法环"


def fake_save_files() -> dict[str, bytes]:
    """模拟一份艾尔登法环存档：主槽位 + 备份槽位 + 配置文件。"""
    return {
        "ER0000.sl2": bytes(range(256)) * 12,          # 3072 B，伪随机内容
        "ER0000.sl2.bak": bytes(range(255, -1, -1)) * 12,
        "steam_autocloud.vdf": b'"SteamCloud"\n{\n\t"appid"\t\t"1245620"\n}\n',
        "GraphicsConfig.xml": b"<config><quality>high</quality></config>",
    }


def build() -> None:
    if DEMO_HOME.exists():
        shutil.rmtree(DEMO_HOME)
    if DEMO_SAVE.exists():
        shutil.rmtree(DEMO_SAVE)

    DEMO_SAVE.mkdir(parents=True)
    for name, data in fake_save_files().items():
        (DEMO_SAVE / name).write_bytes(data)

    # 配置：把备份根指到 demo-home，游戏路径指向假存档
    home = DEMO_HOME
    home.mkdir(parents=True)
    cfg = {
        "version": 1,
        "backup_root": str(home / "backups"),
        "custom_games": [],
        "path_overrides": {GAME_ID: str(DEMO_SAVE)},
        "auto_verify_on_export": True,
        "last_game_id": GAME_ID,
    }
    (home / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 用真实引擎导出快照，保证截图里的数据是真的
    os.environ["SAVEKEEPER_HOME"] = str(home)
    import importlib

    from savekeeper import config as cfg_mod
    from savekeeper import core

    importlib.reload(cfg_mod)
    conf = cfg_mod.load()
    assert conf.backup_dir() == home / "backups", conf.backup_dir()

    labels = ["首次通关", "打女武神前", "换流派-力量"]
    for label in labels:
        core.create_snapshot(DEMO_SAVE, conf.backup_dir(), GAME_ID, GAME_NAME, label)

    # 再故意弄坏最后一条的文件，截图里就能展示"损坏"状态被识别出来
    snaps = core.list_snapshots(conf.backup_dir(), GAME_ID)
    if snaps:
        victim = Path(snaps[-1]["path"]) / core.DATA_DIR / "ER0000.sl2"
        if victim.is_file():
            victim.write_bytes(b"tampered")

    print(f"演示配置: {home}")
    print(f"假存档:   {DEMO_SAVE}")
    print(f"快照数:   {len(core.list_snapshots(conf.backup_dir(), GAME_ID))}")
    for s in core.list_snapshots(conf.backup_dir(), GAME_ID):
        print(f"  - {s['name']}  ok={s['ok']}  files={s['file_count']}")
    print()
    print("启动截图版界面：")
    print(f'  set SAVEKEEPER_HOME={home}')
    print("  python run_gui.py")


if __name__ == "__main__":
    build()
