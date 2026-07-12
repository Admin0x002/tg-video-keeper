# AGENTS.md — tg-video-keeper 项目通用规范

> 本文件为所有在此项目中工作的 AI Agent（含人类协作者）提供通用上下文。
> 修改本文件需经人类负责人（Emiya）确认。

## 1. 项目概述

**项目名称：** tg-video-keeper
**一句话目标：** 私密 Telegram UserBot，监控本人收藏夹 / 私密频道，将手动转发进来的
视频/图片/媒体消息**克隆**（非转发）到私密备份频道，防止原频道被封导致媒体失效。
仅处理本人账号 + 指定频道，**不对外提供任何公开交互服务**。

## 2. 核心技术栈

| 类别 | 选型 | 版本 | 理由 |
|------|------|------|------|
| 语言 | Python | 3.11+ | asyncio 原生，Telethon 异步友好 |
| Telegram 库 | **Telethon** | 1.44.0（v1 稳定分支） | 主流 MTProto UserBot 库；`events.Album` 原生处理相册；`send_message(file=msg)` 干净克隆媒体无转发头 |
| 配置 | python-dotenv | 1.0.1 | `.env` 管理 API 凭证 |
| 日志 | logging + RotatingFileHandler | stdlib | 滚动日志，无需第三方依赖 |
| 进程守护 | systemd | — | 海外 VPS 24h 挂机 |

### Telethon 选型说明（重要）

Telethon v1 的 GitHub 仓库已于 2026-02 归档，v2 迁移至 Codeberg（telethon.dev）。
当前**仍选用 v1.44.0 PyPI 稳定版**，原因：
1. 生态最成熟，文档完善，社区案例最多；
2. `events.Album` 原生聚合多图/多视频相册；
3. `send_message(entity, file=message)` 克隆语义清晰——把消息对象当 file 传入，
   Telethon 自动提取媒体并以 `InputMedia` 重发，生成不带 "转发自" 头部的独立消息；
4. PyPI 安装稳定，`pip install telethon==1.44.0` 即可。

**后续若需最新 Telegram 特性（Stories / Business 等），评估迁移至 Kurigram**
（Pyrogram 的活跃维护分支，drop-in 替换）。当前需求不涉及这些特性，不提前迁移。

## 3. 常用命令

### 开发
```bash
# 进入项目目录
cd /home/emiya/data/workspace/tg-video-keeper

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制配置模板并填写
cp .env.example .env
# 编辑 .env 填入 API_ID / API_HASH / TARGET_CHAT_ID 等

# 首次登录（交互式，输入手机号 + 验证码，生成 session 文件）
python keeper.py --login

# 前台运行（调试模式，日志同时输出到控制台）
python keeper.py

# 健康检查（打印当前账号信息 + 监听/目标配置，不发任何消息）
python keeper.py --check
```

### 部署（海外 Linux VPS 24h 挂机）
```bash
# 安装 systemd 服务
sudo cp deploy/tg-video-keeper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-video-keeper

# 查看运行状态
sudo systemctl status tg-video-keeper
# 实时日志
journalctl -u tg-video-keeper -f
# 应用滚动日志
tail -f logs/keeper.log
```

### 测试
```bash
python -m pytest tests/ -v
```

## 4. 防错指南（硬性规则）

1. **禁止乱猜 API 凭证与 Chat ID。** `API_ID` / `API_HASH` / `TARGET_CHAT_ID` 必须从
   `.env` 读取；缺失则启动失败并明确报错，**绝不使用占位值默认运行**。
2. **禁止使用 `forward_messages` / `message.forward_to`。** 必须用 `send_message(file=...)`
   克隆，确保不带 "转发自" 头部。此为**核心需求**，违反即视为严重缺陷。
3. **禁止过度工程化。** 不预先实现未要求的功能（多账号、Web 面板、HTTP API 等）。
   遵循 YAGNI；新增功能需经人类负责人确认后再加。
4. **修改后必须验证。** 任何代码改动后，至少运行 `python keeper.py --check` 确认
   配置加载与客户端初始化正常；涉及克隆/删除逻辑的改动需用小文件实测一次。
5. **Session 文件是登录凭证，禁止提交 git。** `sessions/*.session` 不可入版本库；
   `.env` 不可入版本库。泄露即视为账号被盗风险。
6. **仅处理配置的来源。** 事件处理器必须校验 `chat_id in Config.SOURCE_CHAT_IDS`
   且发送者在 `ALLOWED_USER_IDS`，超出范围一律忽略。
7. **相册去重。** 检测到 `message.grouped_id` 时交给 `events.Album` 处理，
   `NewMessage` 中跳过相册成员，避免重复克隆。
8. **同一操作连续失败 3 次即停止重试**，汇报情况询问处理方式（继承自用户通用偏好）。

## 5. 项目结构

```
tg-video-keeper/
├── AGENTS.md            # 本文件：通用规范
├── MEMORY.md            # 项目状态：阶段 / 已完成 / 下一步任务
├── SOLO.md              # 单兵 Agent 角色定义与确认协议
├── README.md            # 配置 + 部署完整指南（面向零基础）
├── .env.example         # 配置模板（复制为 .env 后填写）
├── .gitignore
├── requirements.txt
├── config.py            # 配置加载层（.env → Config 对象）
├── keeper.py            # 主脚本：监听 + 克隆 + 相册聚合 + 自动删除 + 重连
├── deploy/
│   └── tg-video-keeper.service   # systemd unit 模板
├── sessions/            # Telethon session 文件（gitignore，敏感）
├── logs/                # 滚动日志（gitignore）
├── tests/
│   └── test_safety.py   # 安全检查单测
└── docs/plans/          # 实施计划存档
```

## 6. 核心工作逻辑（务必遵守）

```
用户手机浏览其他频道
  └─ 遇到喜欢的视频 → 手动转发到 [收藏夹] 或 [私密来源频道]
       └─ keeper.py 监听到新消息
            ├─ 安全校验：来源 chat_id ∈ SOURCE？发送者 ∈ ALLOWED？
            ├─ 有媒体？无媒体则忽略（纯文本不克隆）
            ├─ 单条媒体 → send_message(file=msg) 克隆到 TARGET
            └─ 相册（grouped_id）→ 等 Album 事件聚合 → 作为相册整体克隆
       └─ 克隆成功后
            ├─ 来源=收藏夹('me') 且 DELETE_ORIGINAL_FROM_SAVED=true → 删除原消息
            └─ 来源=私密频道 → 保留原消息
```

## 7. 安全模型

- 仅监听 `Config.SOURCE_CHAT_IDS` 中的聊天（白名单）。
- 仅响应 `Config.ALLOWED_USER_IDS` 中的用户（默认仅本人）。
- 不注册任何 `/command`、不响应陌生消息、不开放 HTTP 端口。
- API 凭证与 session 文件视为最高敏感资产，仅本地存储。
