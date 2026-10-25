"""pytest 全局隔离：任何测试都不许碰真实配置目录。

踩过的坑：开发时把 ``SAVEKEEPER_HOME`` 指向 ``demo-home``（为了截演示图），
然后同一个 shell 里跑 pytest —— 测试里的 ``cfg_mod.save(cfg)`` 会走
``app_dir()`` → ``SAVEKEEPER_HOME``，把演示配置覆盖成测试数据
（``backup_root`` 变成 pytest 的临时目录，``last_game_id`` 变成某测试用的值），
下次打开 GUI 就选错了游戏、备份路径也是错的。

所以这里在收集阶段就把它钉到一个临时目录，无论外部 shell 设了什么。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# 模块级执行：必须早于任何 savekeeper 导入（app_dir 里没有缓存，但保险）
_ISOLATED_HOME = Path(tempfile.mkdtemp(prefix="savekeeper-test-home-"))
os.environ["SAVEKEEPER_HOME"] = str(_ISOLATED_HOME)


@pytest.fixture(scope="session")
def isolated_home() -> Path:
    """测试专用配置根目录，每个 session 一个。"""
    return _ISOLATED_HOME
