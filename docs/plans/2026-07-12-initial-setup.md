# tg-video-keeper 实施计划

> **For Hermes:** 按 MEMORY.md 的 P0/P1/P2 顺序推进，每完成一项勾选并提交。

## 目标
搭建私密 Telegram UserBot，监控收藏夹/私密频道，克隆媒体到备份频道，永久保存。

## 架构
Telethon v1.44 UserBot → events.NewMessage + events.Album 监听来源白名单 →
send_file(file=message) 克隆（无转发头）→ 收藏夹来源自动删原消息 →
auto_reconnect + systemd 保活。

## 技术栈
Python 3.11+ / Telethon 1.44.0 / python-dotenv / systemd

## 任务清单（对应 MEMORY.md）

### P0 联调阻塞项
- [ ] [人工] 申请 API ID/Hash → my.telegram.org
- [ ] [人工] 创建私密备份频道 → 获取 Chat ID
- [ ] [人工] 填写 .env
- [ ] 首次登录 `python keeper.py --login`
- [ ] 单条视频实发测试
- [ ] 相册实发测试

### P1 可用性增强
- [ ] tests/test_safety.py 单测全绿
- [ ] `--check` 子命令联调
- [ ] VPS systemd 部署联调

### P2 远期
- [ ] 媒体去重（file_unique_id）
- [ ] 备份频道容量监控
- [ ] 评估迁移 Telethon v2 / Kurigram

## 验证命令
```bash
python -m pytest tests/ -v          # 单测
python keeper.py --check            # 健康检查
python keeper.py                    # 前台运行
```
