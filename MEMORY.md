# MEMORY.md — 项目状态与记忆

> 本文件记录项目当前状态、已完成事项与下一步任务。
> Agent 每次接手项目前应先读本文件恢复上下文。

## 当前阶段

**维护中（MAINTENANCE）** — 核心功能已完成并通过实发验证，VPS 部署成功。

最后更新：2026-07-13

## 已完成

- [x] 技术选型：**Telethon v1.44.0**
- [x] 项目骨架：`AGENTS.md` / `MEMORY.md` / `SOLO.md` / `README.md`
- [x] 核心脚本 `keeper.py`（监听收藏夹 + 克隆 + 相册聚合 + 自动删除 + 断线重连）
- [x] 配置层 `config.py`（`.env` 驱动，缺失即失败）
- [x] 依赖管理迁移至 **uv**（`pyproject.toml` + `uv.lock`）
- [x] 部署文档 + systemd 服务
- [x] 首次登录联调 + 实发测试 + VPS 部署验证
- [x] 修复 `get_input_entity` 新 session 缓存为空问题（改用 `get_entity` + fallback `get_dialogs`）
- [x] 修复 `.env` 引号导致 Chat ID 解析失败（新增 `_strip_quotes` + 转 int）
- [x] 修复频道消息 `from_id=None` 被误拒问题
- [x] **简化为仅监听收藏夹** — 移除 `SOURCE_CHATS` / `ALLOWED_USER_IDS` 配置，硬编码 `chats=["me"]`

## 下一步任务（按优先级）

### P1 — 可用性增强
1. 补充 `tests/test_safety.py` 单测（媒体类型判定、删除逻辑、相册聚合）。
2. 媒体去重（避免同一视频重复克隆，按 `file_unique_id` 判定）。

### P2 — 远期可选
3. 备份频道容量 / 消息数监控与告警。
4. 评估迁移 Telethon v2（Codeberg）或 Kurigram，视 Telegram API 演进而定。
5. 代理支持（国内 VPS 访问 Telegram）。

## 关键决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-07-12 | 选用 Telethon v1.44.0 | 生态成熟、文档全、`events.Album` + `send_file(file=)` 语义清晰 |
| 2026-07-12 | 克隆而非转发 | 核心需求：原频道被封后备份不失效；`send_file(file=)` 不带转发头 |
| 2026-07-12 | 克隆后自动删除收藏夹原消息 | 保持收藏夹干净（可通过 `DELETE_ORIGINAL_FROM_SAVED` 关闭） |
| 2026-07-12 | 相册用 `events.Album` 聚合 | Telethon 原生支持，`NewMessage` 中按 `grouped_id` 跳过避免重复 |
| 2026-07-12 | 依赖管理用 uv | `pyproject.toml`+`uv.lock` 锁定可复现 |
| 2026-07-13 | 简化为仅监听收藏夹 | 移除多来源支持，安全模型从白名单模式变为天然隔离 |
| 2026-07-13 | `TARGET_CHAT_ID` 改为 int | Telethon 对字符串 ID 不解析为整数，导致缓存查找失败 |
| 2026-07-13 | `get_entity` + `get_dialogs` fallback | 新 session 本地缓存为空时，先同步对话列表再解析 |

## 已知坑点 / 注意事项

- Telethon v1 GitHub 仓库已归档（2026-02），v2 在 Codeberg；当前用 v1.44.0 PyPI 包。
- `send_file(file=message)` 默认 `allow_cache=True`，会复用原 file_id 引用（Telegram 全局去重）。
- 收藏夹的 chat_id 在 Telethon 中用 `'me'` 表示，不要硬编码数字 ID。
- `.env` 中值不要加引号（`TARGET_CHAT_ID=-100xxx` 而非 `TARGET_CHAT_ID='-100xxx'`）。
- VPS 部署时 `ProtectHome=true` 会阻止 systemd 访问 `/root/.local/bin/uv`，已移除。
