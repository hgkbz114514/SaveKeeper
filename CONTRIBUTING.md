# 提交规范（Conventional Commits）

```
feat: 新功能
fix: 修 bug
docs: 文档
test: 测试
refactor: 重构
chore: 构建 / 依赖 / 杂项
```

示例：
- `feat(games): 补充潜水员戴夫存档路径`
- `fix(core): 同一秒内多次导出导致目录冲突`
- `docs: 补充 Linux 下的 CLI 用法`

# 提 PR 前检查清单

- [ ] `python -m pytest tests -q` 全绿
- [ ] 新功能带了对应测试
- [ ] 改动存档路径相关逻辑时，注明验证过的游戏与系统版本
- [ ] 未引入第三方运行时依赖（保持零依赖）

# 如何补充内置游戏库

编辑 `savekeeper/games.py` 的 `BUILTIN_GAMES`：

```python
Game(
    id="game_id",              # 唯一英文 id，用于备份目录名
    name="游戏中文名",
    platform="Steam",          # Steam / Epic / GOG / 自定义 ...
    steam_appid=123456,        # Steam appid（有就填，云存档检测更准）
    candidates=["%APPDATA%/X"],  # 候选路径，按优先级排列，%VAR% 会展开
    notes="备注，如路径特殊性",
),
```

**务必先在真机上验证路径存在**再提交，并在 PR 描述里说明验证环境。
