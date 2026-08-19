# X(Twitter) 视频链接处理 — 设计文档

日期:2026-08-19
状态:已批准(brainstorming 阶段,用户确认 1A 2A 3A 4A + 独立模块 + 不处理混合链接)

## 目标

往收藏夹发送含 x.com / twitter.com 视频链接的文本消息时,自动获取视频信息并下载视频,
然后走现有"下载 → 压缩 → 上传备份频道"管线。**监听位置(收藏夹)与发送目标(TARGET_CHAT_ID)不变,
现有 t.me 链接流程、媒体克隆流程、相册流程全部不动。**

## 与现有流程的关系

```
收藏夹新消息(文本)
  ├─ parse_tg_link 命中 → 现有 t.me 链接流程(完全不动)
  ├─ parse_x_link 命中 → 新增 X 链接流程
  └─ 均未命中 → 无媒体则忽略(不动)
```

用户已明确:一条消息只含一种链接,不需要处理"同时含 t.me 与 x.com"的场景
(但实现上仍采用顺序判断,t.me 优先,天然无歧义)。

## 架构

新增独立模块 `x_downloader.py`(仿 `compress.py` 模式),keeper.py 仅新增一个事件分支。
压缩、缩略图、上传、清理、删除原消息等全部复用 keeper.py 现有函数。

### 复用清单(不改动)

| 复用点 | 函数 |
|--------|------|
| 进度消息(下载/压缩/上传阶段节流更新) | `SavedMessagesProgress` |
| ffmpeg 抽帧封面 | `_extract_thumbnail` / `_ffmpeg_extract_frame` |
| 压缩判断 + 压缩 + faststart 重封 | `_maybe_compress` |
| 本地文件上传(带 thumb、静默、流式) | `_send_local_file` |
| 上传后清理本地文件 | `_cleanup_local_files` / `_safe_remove` / `_cleanup_dir` |
| 成功后删除收藏夹原消息 | `_delete_originals_if_enabled` |
| 超时与停滞检测常量 | `cfg.LINK_DOWNLOAD_TIMEOUT` / `cfg.LINK_STALL_TIMEOUT` |

## 设计细节

### 1. 链接识别 `parse_x_link(text) -> Optional[str]`(x_downloader.py)

- 正则匹配 `(?:https?://)?(?:x|twitter)\.com/<user>/status/<id>`,大小写不敏感,
  忽略 query 参数(如 `?s=20`),返回完整 status URL 交给 yt-dlp。
- **只认 `/status/` 路径**。profile、搜索、hashtag、`i/status` 之外的路径不触发。
- `t.co` 短链不处理(需额外展开请求,超出本次范围)。
- 一条文本只取第一个命中链接。

### 2. 下载编排 `process_x_link(client, url) -> bool`(x_downloader.py)

流程与 `process_link` 对齐:

1. 发进度消息:`⏳ 准备下载: <title>`(title 取 yt-dlp info 的 title,截断到 6 字符,对齐 `_short_title` 的截断语义;X 流程无 Message 对象,不直接复用该函数)
2. `yt_dlp.YoutubeDL` 以 Python 库方式调用(非 CLI 子进程):
   - `extract_info(url, download=False)` 取信息 → 若无视频格式 → 失败"该推文没有可下载的视频"
   - 再 `download()` 到 `downloads/x_<status_id>/`,progress_hooks → `SavedMessagesProgress.report("⏳ 下载中", ...)`
3. 双超时兜底(对齐 `_download_to_dir`):
   - 停滞超时:`X_STALL_TIMEOUT`(默认 60s,复用 `LINK_STALL_TIMEOUT` 默认值)——
     progress hook 记录最后进展时间,主协程轮询检测,超时则中断并清理目录
   - 整体超时:`X_DOWNLOAD_TIMEOUT`(默认 3600s,复用 `LINK_DOWNLOAD_TIMEOUT` 默认值)
   - yt-dlp 下载是同步阻塞,置于 `asyncio.to_thread`;外部用 `asyncio.wait_for` 整体超时兜底,
     停滞检测通过 progress hook 记录 + 主协程检查实现(实现时验证 hook 中断行为,
     若 hook 抛异常不能可靠中止下载,则依赖整体超时 + 目录清理)
4. 下载完成返回本地路径;任一步失败返回 None(调用方决定是否保留原消息)

### 3. keeper.py 事件分支(唯一改动点)

`on_new_message` 中 `parse_tg_link` 之后追加:

```python
x_url = parse_x_link(text)
if x_url:
    ok = await process_x_link(client, x_url)
    if ok:
        await _delete_originals_if_enabled(client, [msg.id])
    return
```

编排明确放在 keeper.py:`x_downloader` 只负责解析 + 下载并返回本地路径(或 None),
keeper.py 拿到路径后执行 `_extract_thumbnail` → `_maybe_compress` → `_send_local_file`
(thumb 传入、caption 用原消息文本)→ 成功删进度消息;失败进度消息显示原因,原链接保留。

### 4. 配置新增(config.py,.env 全部可选)

| 变量 | 默认 | 说明 |
|------|------|------|
| `X_COOKIES_FILE` | 空 | Netscape cookies.txt 路径;私有推文/403 时兜底;为空则 yt-dlp 不带 cookies |
| `X_DOWNLOAD_TIMEOUT` | 3600 | X 下载整体超时(秒) |
| `X_STALL_TIMEOUT` | 60 | X 下载停滞超时(秒) |

`config.summary()` 中追加三行。`.env.example` 同步追加(注释说明)。

### 5. 依赖

`uv add yt-dlp`(Python 库,含 CLI,但本项目只用库 API)。

### 6. 错误处理

| 场景 | 表现 |
|------|------|
| 链接无效 / 推文不存在 | 进度消息"✗ 下载失败,原链接已保留",保留原消息 |
| 非视频推文(图片/纯文本) | 明确提示"该推文没有可下载的视频" |
| 403 / 需登录(私有推文) | 报错并提示配置 X_COOKIES_FILE |
| "Unable to extract"(X 改版) | 报错并提示升级 yt-dlp(`pip install -U yt-dlp`) |
| 超时/停滞 | 取消并清理 downloads 子目录,保留原消息 |

### 7. 测试

- 单测 `tests/test_x_link_parsing.py`:
  - 命中:x.com/twitter.com、带 query、无 scheme、大小写混合、
    `x.com/<user>/status/<id>` 与 `x.com/i/status/<id>`(新版 UI 结构)均覆盖
  - 不命中:profile 链接、搜索链接、hashtag、普通文本、t.co 短链
- 实测(AGENTS.md 规则):真实公开 X 视频链接跑一次全流程,用小视频验证
  下载 → 压缩判断 → 上传 → 删除原消息
- 收尾 `uv run python keeper.py --check` 确认配置加载正常

## 明确不做(YAGNI)

- 不处理 t.co 短链展开
- 不下载推文图片/多图
- 不展示标题/作者信息(用户确认:仅进度消息)
- 不新增监听位置、不改变发送目标
- 不处理混合链接(用户确认:一条一条复制)

## 风险与对策

| 风险 | 对策 |
|------|------|
| X 改版导致 yt-dlp 提取失败 | 报错提示升级 yt-dlp;yt-dlp 社区维护活跃 |
| 私有推文无法下载 | X_COOKIES_FILE 可选配置兜底 |
| 同步库阻塞事件循环 | 下载置于 asyncio.to_thread |
| hook 中断不可靠 | 整体超时兜底 + 目录清理,实测验证 |
