# MEMORY.md — 项目状态与记忆

> 本文件记录项目当前状态、已完成事项与下一步任务。
> Agent 每次接手项目前应先读本文件恢复上下文。
> 完成的任务勾选后保留，便于回溯；新任务追加到末尾。

## 当前阶段

**初始化（INIT）** — 项目骨架与首版脚本已就位，待人工填写凭证后首次登录联调。

最后更新：2026-07-12

## 已完成

- [x] 调研主流 Telegram UserBot 框架（Telethon / Pyrogram / Kurigram / TG-UserBot）
- [x] 技术选型：**Telethon v1.44.0**（理由见 AGENTS.md §2）
- [x] 项目骨架：`AGENTS.md` / `MEMORY.md` / `SOLO.md` / `README.md`
- [x] 核心脚本 `keeper.py`（监听 + 克隆 + 相册聚合 + 自动删除 + 断线重连）
- [x] 配置层 `config.py`（`.env` 驱动，缺失即失败）
- [x] 部署文档 `README.md`（API 申请 / Chat ID 获取 / systemd 挂机）
- [x] systemd unit 模板 `deploy/tg-video-keeper.service`

## 下一步急需进行的任务（按优先级）

### P0 — 联调阻塞项（需人工）
1. **[人工] 申请 API ID/Hash** — 访问 https://my.telegram.org → API development tools
   → 创建应用，获取 `api_id` 与 `api_hash`，填入 `.env`。
2. **[人工] 创建私密备份频道** — Telegram 新建一个仅自己可见的频道，
   获取其 Chat ID（方法见 README §配置说明 - 获取 Chat ID）。
3. **[人工] 填写 `.env`** — `cp .env.example .env`，填入所有值。
4. **首次登录联调** — `python keeper.py --login`，完成手机号 + 验证码登录，
   生成 `sessions/keeper.session`。
5. **实发测试** — 从任意频道转发一个视频到收藏夹，观察：
   - 是否克隆到备份频道（无 "转发自" 头部）；
   - 收藏夹原消息是否被自动删除。
6. **相册测试** — 转发一个多图相册到收藏夹，验证整体克隆。

### P1 — 可用性增强
7. 补充 `tests/test_safety.py` 单测（来源白名单 / 用户白名单 / 媒体类型判定）。
8. 实现 `keeper.py --check` 健康检查子命令（打印账号 + 配置自检）。
9. VPS 部署联调（systemd 启停、日志查看、断网恢复）。

### P2 — 远期可选
10. 媒体去重（避免同一视频重复克隆到备份频道，按 `file_unique_id` 判定）。
11. 备份频道容量 / 消息数监控与告警。
12. 评估迁移 Telethon v2（Codeberg）或 Kurigram，视 Telegram API 演进而定。

## 关键决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-07-12 | 选用 Telethon v1.44.0 | 生态成熟、文档全、`events.Album` + `send_message(file=)` 克隆语义清晰；PyPI 稳定 |
| 2026-07-12 | 克隆而非转发 | 核心需求：原频道被封后备份不失效；`send_message(file=)` 不带转发头 |
| 2026-07-12 | 仅收藏夹来源自动删除原消息 | 保持收藏夹干净；私密频道来源保留原消息（用户未要求删除） |
| 2026-07-12 | 相册用 `events.Album` 聚合 | Telethon 原生支持，`NewMessage` 中按 `grouped_id` 跳过避免重复 |

## 已知坑点 / 注意事项

- Telethon v1 GitHub 仓库已归档（2026-02），v2 在 Codeberg；当前用 v1.44.0 PyPI 包，
  升级主版本前必须评估 breaking changes。
- `send_message(file=message)` 默认 `allow_cache=True`，会复用原 file_id 引用
  （Telegram 全局去重，原频道被封后媒体仍可访问）。如需"完全独立的新 file_id"
  需下载后重新上传，但实践中 Telegram 仍会去重到同一文件，收益有限、成本高，
  当前不采用。
- 收藏夹的 chat_id 在 Telethon 中用 `'me'` 表示，不要硬编码数字 ID。
- 海外 VPS 部署时，session 文件需随项目一起迁移（或在新机器重新登录）。
