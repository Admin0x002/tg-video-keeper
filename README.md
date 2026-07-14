# tg-video-keeper

> 私密 Telegram 媒体克隆守护进程：监控你的收藏夹，把你手动转发进来的
> 视频、图片、媒体**克隆**（非转发）到私密备份频道，永久保存，原频道被封也不受影响。

## 这是什么

你在手机上浏览 Telegram 频道时，遇到喜欢的视频/图片，手动转发到自己的**收藏夹（Saved Messages）**。
本脚本自动监听收藏夹，一旦发现媒体消息，立即用 Telegram API 的
**克隆发送机制**（`send_file(file=msg)`）把它作为一条全新的独立消息发到你的
**私密备份频道**——不带 "转发自" 头部、不保留原 file_id 的转发依赖。

> **为什么不用普通转发？** 普通转发（Forward）会保留 `fwd_from` 头部。如果原频道被封、
> 消息被删，转发消息会显示 "频道不可用"。克隆则生成一条干净的新消息，与原频道彻底解耦。

克隆成功后，脚本自动删除收藏夹原消息，保持收藏夹干净（可通过配置关闭）。

---

## 配置说明

### 1. 申请 API ID 和 API Hash

1. 打开 https://my.telegram.org ，用你的 Telegram 账号登录
2. 进入 **API development tools**
3. 填写 App 名称（随便填，如 `video-keeper`）、平台选 Other
4. 获取 **App api_id**（数字）和 **App api_hash**（字母数字串）

> ⚠️ 这是账号级凭证，**等同于你的账号密钥**，切勿泄露。

### 2. 创建备份频道 + 获取 Chat ID

1. 在 Telegram 里新建一个**私有频道**
2. 获取 Chat ID，用以下命令列出你加入的频道：

```bash
uv run python -c "
import asyncio
from telethon import TelegramClient
import config as cfg

async def main():
    client = TelegramClient('sessions/keeper', cfg.API_ID, cfg.API_HASH)
    await client.start()
    print(f'{\"ID\":>18} | {\"类型\":<6} | 名称')
    print('-' * 50)
    async for d in client.iter_dialogs():
        if d.is_channel or d.is_group:
            t = '频道' if d.is_channel else '群组'
            print(f'{d.id:>18} | {t:<6} | {d.name}')
    await client.disconnect()

asyncio.run(main())
"
```

`-100` 开头的就是频道 ID。填入 `.env` 的 `TARGET_CHAT_ID`。

### 3. 填写 .env

```bash
cp .env.example .env
```

```ini
API_ID=你的 api_id
API_HASH=你的 api_hash
TARGET_CHAT_ID=-100你的备份频道ID
DELETE_ORIGINAL_FROM_SAVED=true
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `API_ID` | ✅ | my.telegram.org 申请 |
| `API_HASH` | ✅ | my.telegram.org 申请 |
| `TARGET_CHAT_ID` | ✅ | 备份频道，格式 `-100xxx` |
| `DELETE_ORIGINAL_FROM_SAVED` | 默认 true | 克隆后是否删除收藏夹原消息 |
| `SILENT_SEND` | 默认 true | 静默发送，不触发通知 |
| `SESSION_NAME` | 默认 keeper | 多账号时改 session 文件名 |
| `LOG_LEVEL` | 默认 INFO | DEBUG/INFO/WARNING/ERROR |

---

## 本地运行

### 1. 环境准备

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
cd tg-video-keeper
uv sync
```

### 2. 首次登录

```bash
uv run python keeper.py --login
```

按提示输入手机号 + 验证码。成功后生成 `sessions/keeper.session`。

### 3. 验证

```bash
uv run python keeper.py --check
```

看到 `✓ 健康检查通过` 即正常。

### 4. 前台运行

```bash
uv run python keeper.py
```

从任意频道转发一个视频到收藏夹，观察是否克隆到备份频道。

---

## VPS 部署（Debian 12）

### 1. 上传项目

```bash
# 本地打包（排除 .venv、logs、sessions、.git）
tar -czf /tmp/tg-keeper.tar.gz \
    --exclude='.venv' --exclude='__pycache__' \
    --exclude='logs' --exclude='sessions' \
    --exclude='.git' --exclude='.idea' \
    -C /path/to/tg-video-keeper .

scp /tmp/tg-keeper.tar.gz root@<VPS_IP>:/opt/
```

### 2. VPS 上安装

```bash
ssh root@<VPS_IP>

# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 部署项目
mkdir -p /opt/tg-video-keeper
tar -xzf /opt/tg-keeper.tar.gz -C /opt/tg-video-keeper
cd /opt/tg-video-keeper
mkdir -p sessions logs

# 安装依赖
uv sync

# 配置 .env
nano .env
```

### 3. 登录 + 验证

```bash
cd /opt/tg-video-keeper
uv run python keeper.py --login   # 输入手机号 + 验证码
uv run python keeper.py --check   # 验证
```

> 也可直接把本地 `sessions/keeper.session` scp 到服务器，跳过 `--login`。

### 4. 注册 systemd 服务

```bash
cat > /etc/systemd/system/tg-video-keeper.service << 'EOF'
[Unit]
Description=tg-video-keeper 媒体克隆守护进程
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/tg-video-keeper
ExecStart=REPLACE_ME run python keeper.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/opt/tg-video-keeper/logs/systemd.log
StandardError=append:/opt/tg-video-keeper/logs/systemd.log

NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/opt/tg-video-keeper/sessions /opt/tg-video-keeper/logs

[Install]
WantedBy=multi-user.target
EOF

# 替换 uv 路径
sed -i "s|REPLACE_ME|$(which uv)|" /etc/systemd/system/tg-video-keeper.service

systemctl daemon-reload
systemctl enable --now tg-video-keeper
systemctl status tg-video-keeper
```

### 5. 运维命令

```bash
systemctl status tg-video-keeper     # 状态
journalctl -u tg-video-keeper -f     # 实时日志
tail -f /opt/tg-video-keeper/logs/keeper.log  # 应用日志
systemctl restart tg-video-keeper    # 重启
```

---

## 使用方法

1. 手机上浏览 Telegram 频道
2. 遇到想保存的视频/图片 → 长按 → 转发 → **收藏夹（Saved Messages）**
3. 脚本自动克隆到备份频道 + 删除收藏夹原消息

---

## 常见问题

### Q: 克隆的视频和原视频画质一样吗？
一样。`send_file(file=msg)` 直接引用原媒体文件，Telegram 全局去重，画质、时长完全一致，只是生成了一条不带转发头的新消息。

### Q: 原频道被封后，备份频道里的还能看吗？
能。克隆消息引用的媒体文件在服务器端独立于原频道存在。原频道被封、消息被删，不影响备份频道。

### Q: 为什么收藏夹原消息会被删除？
因为 `DELETE_ORIGINAL_FROM_SAVED=true`。收藏夹作为"临时中转站"：你转发进去 → 脚本克隆到备份频道 → 删除原消息，保持收藏夹干净。不想删的话设为 `false`。

### Q: 相册（多图）能处理吗？
能。脚本用 `events.Album` 聚合同一相册的多条消息，整体克隆。

### Q: 纯文本消息会被克隆吗？
不会。只处理携带媒体（视频/图片/文档/音频）的消息。

### Q: 账号会被封吗？
这是 UserBot，只处理你自己转发到收藏夹的消息，频率很低，不会触发风控。不要用于高频批量抓取他人频道。

### Q: session 文件丢了怎么办？
重新 `uv run python keeper.py --login` 登录即可。session 文件等同于登录态，妥善保管。

### Q: 如何更换备份频道？
修改 `.env` 的 `TARGET_CHAT_ID`，重启服务即可。

---

## 项目文档

- [AGENTS.md](AGENTS.md) — 技术规范与防错指南
- [MEMORY.md](MEMORY.md) — 项目状态与关键决策
- [SOLO.md](SOLO.md) — Agent 角色定义
