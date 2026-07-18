# 受限频道内容下载 — 尝试记录与方案分析

> 本文档记录 tg-video-keeper 项目在尝试"自动下载/克隆受限频道（启用了
> 「禁止保存/转发」的频道）媒体"过程中的所有尝试、失败原因与可行方案。
> 供后续对话/实现参考。

## 背景

需求：把受限频道（Restrict Saving Content = true）中的媒体自动备份到自己的私密备份频道。

约束：用户希望"只备份自己筛选的特定内容"，而非整频道抓取。已采用
「复制消息链接 → 粘贴到收藏夹 → Bot 解析链接下载」的流程。

## 关键事实

Telegram 对受限频道的保护在服务端两层封堵：

1. **客户端层**：官方 App 禁止转发、保存、截图（UI 限制）
2. **MTProto API 层**：`upload.getFile` 请求对受限频道的文件**被服务端直接无视**
   ——不返回数据、不报错、永久挂起

但官方客户端能**播放**受限频道的视频，说明文件数据能以流式方式传输到客户端。
这是因为播放走的是 Telegram CDN 的 HTTP stream 通道，而非 MTProto 的文件下载接口。

## 尝试过的方案与结果

| # | 方案 | 接口/方法 | 结果 |
|---|------|-----------|------|
| 1 | 转发/引用克隆 | `send_file(file=msg)` | 失败：`You can't forward messages from a protected chat (SendMediaRequest)` |
| 2 | 下载再上传 | `client.download_media(msg)` | 失败：永久卡死，无输出 |
| 3 | 流式下载 | `client.iter_download(msg)` | 失败：永久卡死，无输出 |
| 4 | CDN 标志下载 | `upload.GetFileRequest(cdn_supported=True)` 手动构造 `InputDocumentFileLocation` | 失败：永久卡死，无输出 |
| 5 | MTProto 拿 stream URL | 无对应接口 | 不存在：MTProto API 不暴露 Web 的 HTTP stream URL |

### 详细失败分析

**方案 1**：`send_file(file=msg)` 把消息对象当 file 传入，纯服务端引用原 file_id。
对普通频道有效，但对受限频道直接被服务端拒绝（`protected chat`）。

**方案 2-4**：本质都是调 `upload.getFile`（MTProto 文件下载）。
受限频道的文件，服务端收到 `GetFileRequest` 后**不返回任何数据也不报错**，
调用方永久 await。已验证：
- `download_media` / `iter_download` / 手动 `GetFileRequest(cdn_supported=True)`
  三种方式全部卡死
- 进度回调从未被触发（说明连第一个 chunk 都没收到）
- 加 5~10 分钟超时后确认是"永久挂起"而非"极慢"

**方案 5**：Chrome 扩展（见下节）能下载，是因为它拦截的是 Telegram Web
页面中 `<audio>/<video>` 元素的 `src` 属性（形如
`https://web.telegram.org/a/...stream/{...}` 的 HTTP CDN URL），然后发 HTTP Range
请求逐块下载。这个 URL 只存在于 Web 客户端的 DOM/JS 运行时中，
MTProto API 层面不暴露，Python Bot 无法直接获取。

## 参考实现：Chrome 扩展的原理

分析过的扩展目录：
`~/.config/google-chrome/Profile 1/Extensions/ddkogamcapjjcjpeapeagfklmaodgagk/1.3.2_0/`

核心代码（`downloader/index.iife.js`）逻辑：

1. content script 注入到 `https://*.telegram.org/*` 页面
2. 扫描页面 `<audio>` / `<video>` 元素，取 `src`（含 `stream/` 的 CDN URL）
3. 解析 stream URL 中的 JSON 段（含 fileName、mimeType、location 等）
4. 用 **HTTP GET + Range 请求**逐块下载，伪装 Firefox User-Agent
5. 拼接 Blob，触发浏览器下载

关键请求头：
```
Range: bytes={offset}-
User-Agent: Mozilla/5.0 (... Firefox/117.0) Gecko/...
```

请求成功响应：HTTP 206 Partial Content + `Content-Range` 头。

**结论**：可行路径只有"拿到 Web 端 stream URL 后 HTTP 下载"。
而拿 stream URL 必须运行 Web 客户端（DOM 环境）。

## 可行方案：Playwright + Web 客户端

### 思路

用 Playwright 启动一个真实 Chromium，加载 Telegram Web，
复用本地 Chrome Profile 的登录态（避免重新鉴权），
导航到消息链接，提取 `<audio>/<video>` 的 stream URL，再 HTTP 下载。

### 登录态复用（关键，避免每次重新登录）

- `launch_persistent_context(user_data_dir)` 持久化 Chromium profile 到
  `pw_data/` 目录，首次手动登录后，登录态（cookie/session）存入该目录
- 后续运行（含 headless）复用同一 `pw_data/`，不再需要登录
- **隐患**：Telegram Web session 会过期（数周到数月），过期后 headless VPS
  无法重新登录，需人工介入（本地 headful 重新登录，再把 `pw_data/` 传上去）

### 已写的 POC（download_poc.py，本仓库根目录）

流程：
```
launch_persistent_context(pw_data/, headless=False)  # 首次 headful 登录
  → goto web.telegram.org
  → 检测登录态（查 #column-left / .chat-list）
  → 未登录则等待人工登录后按 Enter
  → 解析消息链接 t.me/username/123
  → goto web.telegram.org/a/#username/123
  → 等待媒体元素加载
  → evaluate 提取 audio/video 的 src (stream URL)
  → requests.get(stream_url, Range, Firefox UA) 逐块下载
  → 写入本地文件
```

已知问题（POC 尚未跑通）：
- 登录态检测选择器需要实测（Telegram Web A 版的 DOM 结构）
- 媒体元素加载需要等待，可能要增大 sleep 或滚动到消息位置
- stream URL 可能是动态生成的，导航方式（hash 路由）需调整

### 隐患清单（Playwright 方案）

| 隐患 | 严重度 | 说明 |
|------|--------|------|
| 登录态过期 | 高 | Telegram Web session 数周后过期，headless VPS 无法重新登录 |
| 首次登录 | 高 | headless 无法显示 QR/表单，只能本地 headful 登录后传 `pw_data/` |
| 资源消耗 | 高 | Chromium headless 300-500MB 内存，低配 VPS 压力大 |
| 多会话冲突 | 中 | Playwright 占用额外 Web 会话，可能挤掉其他设备 |
| Headless 检测 | 中 | `navigator.webdriver` 可能被 Telegram 检测 |
| UI 变更 | 中 | Telegram Web DOM 变动会导致 stream URL 提取失败 |
| 安装体积 | 中 | Chromium + Playwright ~300MB |

## 推荐架构（后续对话实现时参考）

**不在 VPS 上跑 Playwright**，改为"本地辅助 + VPS 主力"分工：

```
本地（有 GUI、Chrome 已登录 Telegram Web）
  ├─ 能转发的频道 → 转发到收藏夹 → VPS Bot 自动克隆
  ├─ 受限频道 → 复制消息链接到收藏夹
  └─ 滞留的受限链接 → 本地 Playwright 下载 → 上传到备份频道

VPS（轻量 Telethon Bot）
  └─ 监听收藏夹
      ├─ 媒体消息 → send_file(file=msg) 克隆
      └─ 受限链接 → 跳过（提示"需本地处理"），不卡死
```

本地工具（download_poc.py 演进版）应实现：
1. 输入消息链接
2. Playwright 拿 stream URL（复用 pw_data/ 登录态）
3. HTTP Range 下载
4. 用现有 Telethon session 上传到备份频道（`send_file(file=本地路径)`）

## 下一步（接续对话时的切入点）

1. 先在本地跑通 `download_poc.py`：修正登录态检测选择器 + 媒体元素等待逻辑
2. 验证 stream URL 能否稳定提取（不同消息类型：视频/图片/音频/文档）
3. 验证 stream URL 的时效性（是否每次导航都重新生成、能否缓存复用）
4. 把下载与"上传到备份频道"串起来（复用 keeper.py 的 session）
5. 评估是否值得做成本地常驻服务（监听收藏夹链接自动触发），还是手动单条触发即可
