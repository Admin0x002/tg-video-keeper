# tg-video-keeper

> 私密 Telegram 媒体克隆守护进程：监控你的收藏夹 / 私密频道，把你手动转发进来的
> 视频、图片、媒体**克隆**（非转发）到私密备份频道，永久保存，原频道被封也不受影响。

## 这是什么

你在手机上浏览 Telegram 频道时，遇到喜欢的视频，手动转发到自己的**收藏夹**或
**私密来源频道**。本脚本自动监听这些地方，一旦发现媒体消息，立即用 Telegram API 的
**克隆发送机制**（`send_message(file=msg)`）把它作为一条全新的独立消息发到你的
**私密备份频道**——不带 "转发自" 头部、不保留原 file_id 的转发依赖。

> **为什么不用普通转发？** 普通转发（Forward）会保留 `fwd_from` 头部和原消息引用。
> 如果原频道因违规被封、消息被删，你的转发消息虽然媒体通常仍可访问（Telegram 全局
> 去重），但会显示 "频道不可用" 并丢失来源信息。克隆则生成一条干净的新消息，体验上
> 与原消息无异，且更彻底地与原频道解耦。

克隆成功后，若来源是**收藏夹**，脚本会自动删除原转发消息，保持收藏夹干净。
若来源是**私密频道**，原消息保留不动。

---

## 目录

- [配置说明](#配置说明)
  - [1. 申请 API ID 和 API Hash](#1-申请-api-id-和-api-hash)
  - [2. 获取目标备份频道的 Chat ID](#2-获取目标备份频道的-chat-id)
  - [3. 获取你自己的 User ID（可选）](#3-获取你自己的-user-id可选)
  - [4. 填写 .env](#4-填写-env)
- [部署指南](#部署指南)
  - [本地电脑运行](#本地电脑运行)
  - [海外 Linux VPS 24h 挂机](#海外-linux-vps-24h-挂机)
- [使用方法](#使用方法)
- [常见问题](#常见问题)

---

## 配置说明

### 1. 申请 API ID 和 API Hash

1. 用浏览器打开 https://my.telegram.org ，用你的 Telegram 账号登录（输入手机号 +
   收到的验证码）。
2. 进入 **API development tools**。
3. 填写 App 名称（随便填，如 `video-keeper`）、平台选 Other、URL 留空。
4. 创建后你会看到 **App api_id**（一串数字）和 **App api_hash**（一串字母数字）。
5. 这两个值就是 `.env` 里的 `API_ID` 和 `API_HASH`。

> ⚠️ 这是账号级凭证，**等同于你的账号密钥**，切勿泄露或提交到公开仓库。

### 2. 获取目标备份频道的 Chat ID

1. 在 Telegram 里新建一个频道，设为**仅自己可见**（私有频道）。
2. 获取它的 Chat ID，有两种方法：

**方法 A：用 @userinfobot（最简单）**

- 把你新建的频道转发一条消息给 `@userinfobot`（或 `@getidsbot`），它会回复该频道的 ID。
- 注意：私有频道需要先把它设为公开（有 username）才能转发给 bot，获取后再改回私有。

**方法 B：用本脚本内置命令**

```bash
# 先填好 .env 里的 API_ID / API_HASH，TARGET_CHAT_ID 先留空
python -c "
import asyncio
from telethon import TelegramClient
import config as cfg
async def main():
    c = TelegramClient('sessions/tmp', cfg.API_ID, cfg.API_HASH)
    await c.start()
    # 把下面 username 换成你频道的公开 username
    e = await c.get_entity('your_channel_username')
    print('频道 ID:', e.id)
    print('完整 Chat ID: -100' + str(e.id))
    await c.disconnect()
asyncio.run(main())
"
```

填入 `.env` 的格式是 `-100` + 频道 ID，例如 `-1001234567890`。

### 3. 获取你自己的 User ID（可选）

把任意消息转发给 `@userinfobot`，它会回复你的 User ID。填入 `ALLOWED_USER_IDS`
可进一步收紧安全（仅响应你本人）。留空则仅依赖来源白名单保护。

### 4. 填写 .env

```bash
cp .env.example .env
# 用编辑器打开 .env，填入以下值：
```

```ini
API_ID=你的 api_id
API_HASH=你的 api_hash
TARGET_CHAT_ID=-100你的备份频道ID
SOURCE_CHATS=me
ALLOWED_USER_IDS=你的user_id
DELETE_ORIGINAL_FROM_SAVED=true
```

| 变量 | 必填 | 说明 |
|------|------|------|
| `API_ID` | ✅ | my.telegram.org 申请 |
| `API_HASH` | ✅ | my.telegram.org 申请 |
| `TARGET_CHAT_ID` | ✅ | 备份频道，格式 `-100xxx` |
| `SOURCE_CHATS` | ✅ | 监听来源，逗号分隔；`me`=收藏夹 |
| `ALLOWED_USER_IDS` | 可选 | 你的 user id，收紧安全 |
| `DELETE_ORIGINAL_FROM_SAVED` | 默认 true | 收藏夹来源克隆后是否删原消息 |
| `SILENT_SEND` | 默认 true | 静默发送，不触发通知 |
| `SESSION_NAME` | 默认 keeper | session 文件名 |
| `LOG_LEVEL` | 默认 INFO | DEBUG/INFO/WARNING/ERROR |

---

## 部署指南

### 本地电脑运行

#### 1. 安装 Python 环境

需要 Python 3.11+。检查：

```bash
python3 --version
```

#### 2. 克隆/进入项目目录

```bash
cd /home/emiya/data/workspace/tg-video-keeper
```

#### 3. 创建虚拟环境 + 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 4. 配置 .env

```bash
cp .env.example .env
# 编辑 .env，填入 API_ID / API_HASH / TARGET_CHAT_ID 等
```

#### 5. 首次登录（生成 session 文件）

```bash
python keeper.py --login
```

按提示输入：
- 手机号（带国家区号，如 `+8613800138000`）
- 收到的验证码
- （若开启了两步验证）你的密码

成功后会生成 `sessions/keeper.session` 文件，之后无需再次登录。

#### 6. 健康检查

```bash
python keeper.py --check
```

应输出：配置摘要 + 账号信息 + 目标频道解析成功。

#### 7. 前台运行（调试）

```bash
python keeper.py
```

看到 `守护进程运行中` 即表示已在监听。现在从任意频道转发一个视频到收藏夹，
观察是否克隆到备份频道 + 收藏夹原消息是否被删除。

#### 8. 后台运行

```bash
# 方式一：tmux（推荐本地调试）
tmux new -s keeper
python keeper.py
# 按 Ctrl+B 然后 D 脱离

# 方式二：nohup
nohup python keeper.py > /dev/null 2>&1 &
```

---

### 海外 Linux VPS 24h 挂机

> ⚠️ 建议用**海外 VPS**（如新加坡、日本节点）。国内 VPS 连 Telegram 可能不稳定。
> ⚠️ 不要把本地登录好的 session 文件传到 VPS——session 是登录凭证，传输有风险。
> 在 VPS 上**重新执行 `--login`** 最安全。

#### 1. 上传项目代码到 VPS

```bash
# 在本地执行（把代码传到 VPS，注意不要传 .env 和 sessions）
rsync -av --exclude='.env' --exclude='sessions' --exclude='logs' --exclude='.venv' \
  ./ user@your-vps:/home/user/tg-video-keeper/
```

#### 2. VPS 上安装环境

```bash
ssh user@your-vps
cd /home/user/tg-video-keeper

# 安装 Python（Ubuntu/Debian）
sudo apt update && sudo apt install -y python3 python3-venv python3-pip

# 虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. 配置 + 登录

```bash
cp .env.example .env
# 编辑 .env 填入凭证
python keeper.py --login    # 输入手机号 + 验证码
python keeper.py --check     # 验证
```

#### 4. 配置 systemd 服务

```bash
# 复制 unit 模板
sudo cp deploy/tg-video-keeper.service /etc/systemd/system/

# 编辑模板，确认路径与用户名
sudo nano /etc/systemd/system/tg-video-keeper.service
# 重点修改：
#   User=你的用户名
#   WorkingDirectory=/home/你的用户名/tg-video-keeper
#   ExecStart= 路径

# 重新加载 + 启动
sudo systemctl daemon-reload
sudo systemctl enable --now tg-video-keeper

# 查看状态
sudo systemctl status tg-video-keeper

# 实时日志
journalctl -u tg-video-keeper -f
```

#### 5. 开机自启 + 自动重连

systemd 会自动重启（见 unit 文件 `Restart=always`）。掉线后 Telethon 内置
`auto_reconnect` 自动重连；进程崩溃后 systemd 自动拉起。

---

## 使用方法

1. **正常使用流程：**
   - 在手机上浏览任意 Telegram 频道
   - 遇到喜欢的视频 → 长按 → 转发 → 选择**收藏夹**（或你配置的私密来源频道）
   - 脚本自动克隆到备份频道 + 清理收藏夹原消息

2. **多来源：** 在 `.env` 的 `SOURCE_CHATS` 里加多个频道 ID（逗号分隔）：
   ```ini
   SOURCE_CHATS=me,-1001111111111,-1002222222222
   ```

3. **停止服务：**
   ```bash
   # systemd
   sudo systemctl stop tg-video-keeper
   # 本地前台
   # Ctrl+C
   ```

4. **更新代码后重启：**
   ```bash
   sudo systemctl restart tg-video-keeper
   ```

---

## 常见问题

### Q: 克隆的视频和原视频画质一样吗？
A: 一样。`send_message(file=msg)` 直接引用原媒体文件，Telegram 全局去重，
   画质、时长、文件大小完全一致，只是生成了一条不带转发头的新消息。

### Q: 原频道被封后，我备份频道里的视频还能看吗？
A: 能。Telegram 的媒体文件是全局存储去重的，克隆消息引用的 file 在服务器端
   独立于原频道存在。原频道被封、消息被删，不影响你备份频道里的克隆消息。

### Q: 为什么收藏夹的原消息会被删？
A: 因为配置了 `DELETE_ORIGINAL_FROM_SAVED=true`。这样收藏夹只作为"临时中转站"：
   你转发进去 → 脚本克隆到备份频道 → 删除收藏夹原消息，保持收藏夹干净。
   如果你不想删，设为 `false`。

### Q: 相册（多图）能处理吗？
A: 能。脚本用 `events.Album` 聚合同一相册的多条消息，作为相册整体克隆到备份频道。

### Q: 纯文本消息会被克隆吗？
A: 不会。脚本只处理携带媒体（视频/图片/文档/音频）的消息，纯文本忽略。

### Q: 账号会被封吗？
A: 这是使用你自己账号的 UserBot，只处理你本人转发到收藏夹的消息，频率很低，
   不会触发 Telegram 的批量操作风控。但请勿用于高频批量抓取他人频道。

### Q: session 文件丢了怎么办？
A: 重新运行 `python keeper.py --login` 重新登录即可。session 文件等同于登录态，
   务必妥善保管，不要外传。

### Q: 如何更换备份频道？
A: 修改 `.env` 里的 `TARGET_CHAT_ID`，重启服务即可，无需重新登录。

---

## 项目文档

- [AGENTS.md](AGENTS.md) — 通用规范、技术栈、命令、防错指南
- [MEMORY.md](MEMORY.md) — 项目状态与下一步任务
- [SOLO.md](SOLO.md) — Agent 角色定义与确认协议
