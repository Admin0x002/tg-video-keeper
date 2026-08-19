# X 视频链接下载 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收藏夹收到含 x.com/twitter.com 视频链接的文本消息时,自动下载视频并走现有"抽帧封面 → 压缩 → 上传备份频道"管线;现有 t.me 流程、媒体克隆、相册流程均不动。

**Architecture:** 新增独立模块 `x_downloader.py`(链接解析 + yt-dlp 下载,仿 `compress.py` 模式),keeper.py 仅在 `on_new_message` 中追加一个 X 链接分支,编排复用现有 `_extract_thumbnail` / `_maybe_compress` / `_send_local_file` / `_delete_originals_if_enabled`。

**Tech Stack:** Python 3.11+, Telethon 1.44.0(现有), yt-dlp(Python 库,新增), pytest(现有)。

## Global Constraints

- 现有监听范围(`chats=["me"]` 收藏夹)与发送目标(`TARGET_CHAT_ID`)不变。
- 现有 t.me 链接流程、媒体克隆、相册流程的代码不改动(只在其后追加 X 分支)。
- **git 提交:用户全局规则"git 提交仅在用户明确要求时进行"。各任务末尾的 commit 步骤默认跳过,如需提交先问用户。**
- 测试运行:`uv run python -m pytest tests/ -v`;单测需先注入最小环境变量(API_ID/API_HASH/TARGET_CHAT_ID),模式见现有 `tests/test_link_parsing.py:16-19`。
- 代码风格:中文注释、与现有文件一致;新模块名 `x_downloader.py`,keeper.py 中 `import x_downloader as xdl`(对齐 `import compress as cmp`)。
- 双超时默认值复用现有语义:`LINK_STALL_TIMEOUT=60` / `LINK_DOWNLOAD_TIMEOUT=3600`,X 独立配置同默认值。

---

### Task 1: 依赖 + `parse_x_link` 链接解析 + 单测

**Files:**
- Create: `x_downloader.py`(本任务只含链接解析部分)
- Create: `tests/test_x_link_parsing.py`
- Modify: `pyproject.toml`(uv add 自动修改)

**Interfaces:**
- Produces: `x_downloader.parse_x_link(text: str) -> Optional[str]` — 提取第一条 X 推文链接并补全 scheme;无 `/status/` 链接返回 None

- [ ] **Step 1: 安装依赖**

```bash
uv add yt-dlp
```

Expected: pyproject.toml 出现 `yt-dlp` 依赖行,uv.lock 更新,无报错。

- [ ] **Step 2: 写失败测试**

`tests/test_x_link_parsing.py`(完整内容):

```python
# -*- coding: utf-8 -*-
"""
test_x_link_parsing.py — parse_x_link 纯函数单测

覆盖 x.com/twitter.com 推文链接识别:带 query、无 scheme、大小写、
x.com/i/status 结构、嵌入正文、多链接取首条;profile/搜索/t.co/无效输入不命中。

运行:uv run python -m pytest tests/test_x_link_parsing.py -v
"""
import os
import sys

# 让测试能 import 项目根的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 注入最小环境变量，使 config 模块能加载
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import x_downloader  # noqa: E402


class TestHit:
    def test_x_dot_com(self):
        assert x_downloader.parse_x_link(
            "https://x.com/elonmusk/status/1234567890123456789"
        ) == "https://x.com/elonmusk/status/1234567890123456789"

    def test_twitter_dot_com(self):
        assert x_downloader.parse_x_link(
            "https://twitter.com/foo_bar/status/42"
        ) == "https://twitter.com/foo_bar/status/42"

    def test_with_query(self):
        assert x_downloader.parse_x_link(
            "https://x.com/user/status/7?s=20"
        ) == "https://x.com/user/status/7?s=20"

    def test_no_scheme(self):
        assert x_downloader.parse_x_link(
            "x.com/foo/status/1"
        ) == "https://x.com/foo/status/1"

    def test_case_insensitive(self):
        assert x_downloader.parse_x_link(
            "https://X.COM/Foo/Status/5"
        ) == "https://X.COM/Foo/Status/5"

    def test_i_status_structure(self):
        # 新版 UI 结构:x.com/i/status/<id>
        assert x_downloader.parse_x_link(
            "https://x.com/i/status/88"
        ) == "https://x.com/i/status/88"

    def test_embedded_in_text(self):
        text = "看这个视频 https://x.com/a/status/1 好帅"
        assert x_downloader.parse_x_link(text) == "https://x.com/a/status/1"

    def test_multiple_takes_first(self):
        text = "https://x.com/first/status/1 再看 https://x.com/second/status/2"
        assert x_downloader.parse_x_link(text) == "https://x.com/first/status/1"

    def test_punctuation_after_url(self):
        # 链接后紧跟中文标点不应被吞进 URL
        assert x_downloader.parse_x_link(
            "https://x.com/a/status/1，好看"
        ) == "https://x.com/a/status/1"


class TestMiss:
    def test_empty(self):
        assert x_downloader.parse_x_link("") is None
        assert x_downloader.parse_x_link(None) is None

    def test_no_link(self):
        assert x_downloader.parse_x_link("普通文本，没有链接") is None

    def test_profile_link(self):
        # profile 不是推文链接
        assert x_downloader.parse_x_link("https://x.com/elonmusk") is None

    def test_search_link(self):
        assert x_downloader.parse_x_link("https://x.com/search?q=video") is None

    def test_tco_short_link(self):
        # t.co 短链不处理(需额外展开请求,超出范围)
        assert x_downloader.parse_x_link("https://t.co/abc123") is None

    def test_non_numeric_status_id(self):
        assert x_downloader.parse_x_link("https://x.com/user/status/abc") is None

    def test_tg_link_not_affected(self):
        # t.me 链接不触发 X 逻辑
        assert x_downloader.parse_x_link("https://t.me/chan/1") is None
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_x_link_parsing.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'x_downloader'`

- [ ] **Step 4: 实现 `x_downloader.py`(链接解析部分)**

`x_downloader.py`(完整内容,本任务只到 parse_x_link):

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x_downloader.py — X(Twitter) 视频链接解析与下载。

只负责两件事:
  1. parse_x_link:从文本中识别 x.com/twitter.com 的 /status/ 推文链接
  2. download_x_video:用 yt-dlp 把推文对应的视频下载到本地目录(Task 3 实现)

下载后的封面抽取/压缩/上传/清理由 keeper.py 复用现有 t.me 流程处理。
"""
from __future__ import annotations

import re
from typing import Optional

# 匹配 x.com / twitter.com 的推文链接:<user 或 i>/status/<id>[?query]
# 只认 /status/ 路径;profile/搜索/hashtag 不触发;t.co 短链不处理;
# query(以 ? 开头)随 URL 保留,中文标点等不会吞进 URL。
_X_STATUS_URL_RE = re.compile(
    r"(?:https?://)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]{1,15}|i)/status/\d+(?:\?[^\s]*)?",
    re.IGNORECASE,
)


def parse_x_link(text: str) -> Optional[str]:
    """从文本中提取第一条 X 推文链接,返回补全 scheme 的完整 URL。

    无链接 / 非 /status/ 链接返回 None。
    """
    if not text:
        return None
    m = _X_STATUS_URL_RE.search(text)
    if not m:
        return None
    url = m.group(0)
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_x_link_parsing.py -v`
Expected: 16 个测试全部 PASS

- [ ] **Step 6: Commit(默认跳过,见 Global Constraints;如需提交先问用户)**

```bash
git add x_downloader.py tests/test_x_link_parsing.py pyproject.toml uv.lock
git commit -m "feat: X 推文链接解析 parse_x_link"
```

---

### Task 2: 配置项 X_COOKIES_FILE / X_DOWNLOAD_TIMEOUT / X_STALL_TIMEOUT

**Files:**
- Modify: `config.py:74`(行为开关段后追加 X 段)
- Modify: `config.py:94-110`(summary 追加三行)
- Modify: `.env.example`(追加 X 段)

**Interfaces:**
- Produces: `cfg.X_COOKIES_FILE: str`、`cfg.X_DOWNLOAD_TIMEOUT: int`、`cfg.X_STALL_TIMEOUT: int`(Task 3 消费)

- [ ] **Step 1: config.py 追加 X 配置段**

在 `config.py` 的 `KEEP_LOCAL_FILE_AFTER_UPLOAD`(第 72 行)之后、`视频压缩` 段之前插入:

```python
# --------------------- X 视频下载 ---------------------
# X(Twitter) 视频链接下载的 cookies 文件路径(Netscape 格式,浏览器扩展可导出)。
# 公开推文无需 cookies;私有推文/403 时才需要。留空=yt-dlp 不带 cookies。
X_COOKIES_FILE: str = os.getenv("X_COOKIES_FILE", "").strip()

# X 下载整体超时(秒)。默认 3600,与 LINK_DOWNLOAD_TIMEOUT 语义一致。
X_DOWNLOAD_TIMEOUT: int = int(os.getenv("X_DOWNLOAD_TIMEOUT", "3600"))

# X 下载停滞超时(秒):进度回调超过此秒无进展即判定挂起,取消跳过。默认 60。
X_STALL_TIMEOUT: int = int(os.getenv("X_STALL_TIMEOUT", "60"))
```

- [ ] **Step 2: config.py 的 summary() 追加三行**

在 `summary()` 的 `KEEP_LOCAL_FILE_AFTER_UPLOAD=...` 行后追加:

```python
        f"X_COOKIES_FILE={X_COOKIES_FILE or '(未设置)'}\n"
        f"X_DOWNLOAD_TIMEOUT={X_DOWNLOAD_TIMEOUT}\n"
        f"X_STALL_TIMEOUT={X_STALL_TIMEOUT}\n"
```

- [ ] **Step 3: .env.example 追加 X 段**

在 `KEEP_LOCAL_FILE_AFTER_UPLOAD=false` 之后、`===== 视频压缩 =====` 之前插入:

```ini
# ===== X(Twitter) 视频下载 =====
# 收到 x.com/twitter.com 视频链接消息时自动下载并上传到备份频道。
# cookies 文件路径(Netscape 格式):私有推文/403 时才需要,公开推文无需。留空=不带 cookies。
X_COOKIES_FILE=
# X 下载整体超时(秒)。默认 3600。
X_DOWNLOAD_TIMEOUT=3600
# X 下载停滞超时(秒):进度无进展超过此秒即取消。默认 60。
X_STALL_TIMEOUT=60
```

- [ ] **Step 4: 验证配置加载**

Run: `uv run python keeper.py --check`
Expected: 输出中出现 `X_COOKIES_FILE=(未设置)`、`X_DOWNLOAD_TIMEOUT=3600`、`X_STALL_TIMEOUT=60`,无报错。

- [ ] **Step 5: Commit(默认跳过,见 Global Constraints)**

```bash
git add config.py .env.example
git commit -m "feat: X 下载配置项"
```

---

### Task 3: `download_x_video`(yt-dlp 下载器)+ 单测

**Files:**
- Modify: `x_downloader.py`(追加下载部分)
- Create: `tests/test_x_download.py`

**Interfaces:**
- Consumes: `cfg.X_COOKIES_FILE` / `cfg.X_DOWNLOAD_TIMEOUT` / `cfg.X_STALL_TIMEOUT`(Task 2)
- Produces: `x_downloader.download_x_video(url: str, out_dir: str, progress_sink=None) -> Optional[str]` — 返回视频文件路径;失败/无视频/超时返回 None 并在 progress_sink 上设置失败文案。progress_sink 接口:同步 `report(label, pct, received_mb, total_mb)`(如 `SavedMessagesProgress.report`)+ 异步 `set_text(text)`(如 `SavedMessagesProgress.set_text`)。
- Produces: `x_downloader._info_has_video(info: dict) -> bool`(内部判定,供测试直接验证)

**设计要点(实现前必读):**
- 两阶段:先 `extract_info(download=False)` 判断是否有视频(无视频→明确报错),再 `download=True` 下载。
- yt-dlp 是同步库,两阶段均置于 `asyncio.to_thread`,不阻塞事件循环。
- 双超时兜底(对齐 `keeper._download_to_dir` 语义):停滞超时 → 放弃等待 + rmtree 清理目录(线程因目录被删/写孤儿句柄而自然失败退出);整体超时同样处理。`asyncio.wait_for(asyncio.shield(task), tick)` 轮询,tick = `min(5.0, cfg.X_STALL_TIMEOUT)`。
- progress hook 只记录 `state["last_progress"]` 并调用 `progress_sink.report`(同步安全,内部 create_task);`set_text`(async)只在主协程调用。
- 所有失败路径在 `download_x_video` 内通过 `await progress_sink.set_text(...)` 设置文案,返回 None。
- 私有推文/403 / "Unable to extract"(X 改版):日志提示 `uv pip install -U yt-dlp` 或配置 `X_COOKIES_FILE`。
- 下载完成定位文件:优先 `info["requested_downloads"][0]["filepath"]`,兜底扫描 out_dir 取最大文件。

- [ ] **Step 1: 写失败测试**

`tests/test_x_download.py`(完整内容):

```python
# -*- coding: utf-8 -*-
"""
test_x_download.py — download_x_video 单测(monkeypatch yt_dlp)

覆盖:无视频推文、下载成功、extract_info 异常、停滞超时、整体超时。
不依赖真实网络:monkeypatch 替换 x_downloader.yt_dlp.YoutubeDL。

运行:uv run python -m pytest tests/test_x_download.py -v
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import config as cfg  # noqa: E402
import x_downloader as xdl  # noqa: E402


class FakeProgressSink:
    """记录 set_text / report 调用的假进度接收器。"""

    def __init__(self):
        self.texts = []
        self.reports = []

    async def set_text(self, text):
        self.texts.append(text)

    def report(self, label, pct=None, received_mb=None, total_mb=None):
        self.reports.append((label, pct))


class FakeYDL:
    """脚本化的假 YoutubeDL。类属性控制行为:

    - info: extract_info(download=False) 返回的 dict
    - dl_info: extract_info(download=True) 返回的 dict(含 requested_downloads)
    - info_raise: 非 None 时第一阶段直接抛它
    - dl_delay: 第二阶段 sleep 秒数(模拟慢下载)
    - dl_file: 第二阶段应"下载"出的文件路径(测试需预先创建)
    """

    info = {"title": "test video", "vcodec": "h264"}
    dl_info = None
    info_raise = None
    dl_delay = 0.0
    dl_file = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if not download:
            if FakeYDL.info_raise is not None:
                raise FakeYDL.info_raise
            return FakeYDL.info
        if FakeYDL.dl_delay:
            time.sleep(FakeYDL.dl_delay)
        if FakeYDL.dl_file is not None:
            return {
                "title": "test video",
                "requested_downloads": [{"filepath": FakeYDL.dl_file}],
            }
        return {"title": "test video"}


def _run(coro):
    return asyncio.run(coro)


def _reset():
    FakeYDL.info = {"title": "test video", "vcodec": "h264"}
    FakeYDL.dl_info = None
    FakeYDL.info_raise = None
    FakeYDL.dl_delay = 0.0
    FakeYDL.dl_file = None


class TestNoVideo:
    def test_no_video_returns_none(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.info = {"title": "仅图片", "vcodec": "none", "formats": []}
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path), sink))
        assert path is None
        assert any("没有可下载的视频" in t for t in sink.texts)


class TestSuccess:
    def test_download_returns_path(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake-video-data")
        FakeYDL.dl_file = str(video)
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path), sink))
        assert path == str(video)
        assert any("准备下载" in t for t in sink.texts)


class TestInfoError:
    def test_extract_exception_returns_none(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.info_raise = RuntimeError("Unable to extract")
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path), sink))
        assert path is None
        assert any("获取视频信息失败" in t for t in sink.texts)


class TestStall:
    def test_stall_timeout_aborts(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(cfg, "X_STALL_TIMEOUT", 0.1)
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.dl_delay = 0.4  # 第二阶段挂起,无进度回调
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path), sink))
        assert path is None
        assert any("停滞" in t for t in sink.texts)
        assert not list(tmp_path.iterdir())  # 目录已清理


class TestOverallTimeout:
    def test_overall_timeout_aborts(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(cfg, "X_DOWNLOAD_TIMEOUT", 0.3)
        monkeypatch.setattr(cfg, "X_STALL_TIMEOUT", 60)  # 停滞不触发,靠整体超时
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.dl_delay = 0.6
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path), sink))
        assert path is None
        assert any("超时" in t for t in sink.texts)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_x_download.py -v`
Expected: FAIL,报 `AttributeError: module 'x_downloader' has no attribute 'download_x_video'`

- [ ] **Step 3: 实现下载器(追加到 x_downloader.py)**

在 `parse_x_link` 之后追加(完整内容):

```python
import asyncio
import logging
import os
import shutil
import time

import yt_dlp

import config as cfg

log = logging.getLogger("keeper")


def _info_has_video(info: dict) -> bool:
    """yt-dlp info 中是否存在可下载的视频格式。"""
    if info.get("vcodec") and info.get("vcodec") != "none":
        return True
    return any(
        f.get("vcodec") and f.get("vcodec") != "none"
        for f in (info.get("formats") or [])
    )


def _short_title(info: dict) -> str:
    """取 yt-dlp info 的标题,折叠空白并截断到 6 字符(对齐 keeper._short_title)。"""
    title = str(info.get("title") or "")
    return " ".join(title.split())[:6]


def _ydl_opts(out_dir: str, state: dict, progress_sink=None) -> dict:
    """构造 YoutubeDL 选项;progress hook 记录停滞时间并推送下载百分比。"""
    def _hook(d: dict) -> None:
        if d.get("status") in ("downloading", "finished"):
            state["last_progress"] = time.monotonic()
        if d.get("status") != "downloading" or progress_sink is None:
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        received = d.get("downloaded_bytes", 0) or 0
        if total > 0:
            pct = int(received * 100 / total)
            if pct >= state.get("pct", -1) + 5 or pct == 100:
                state["pct"] = pct
                log.info("… X 下载中 %s%% (%.1f/%.1f MB) %s",
                         pct, received / 1048576, total / 1048576, url)
            progress_sink.report("⏳ 下载中", pct=pct,
                                 received_mb=received / 1048576,
                                 total_mb=total / 1048576)
        elif received > 0:
            progress_sink.report("⏳ 下载中", pct=None,
                                 received_mb=received / 1048576, total_mb=0)

    return {
        "outtmpl": os.path.join(out_dir, "video.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
        "progress_hooks": [_hook],
        "cookiefile": cfg.X_COOKIES_FILE or None,
    }


def _fetch_info(url: str, out_dir: str, state: dict) -> Optional[dict]:
    """第一阶段:只取信息不下载。异常返回 None(日志含排查提示)。"""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(out_dir, state)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.error("✗ 获取 X 视频信息失败 %s: %s", url, e)
        log.info("  提示:X 改版时常见,可尝试 `uv pip install -U yt-dlp`;"
                 "私有推文可配置 X_COOKIES_FILE")
        return None


def _download(url: str, out_dir: str, state: dict, progress_sink=None) -> Optional[str]:
    """第二阶段:下载视频文件,返回本地路径。失败返回 None。"""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(out_dir, state, progress_sink)) as ydl:
            info = ydl.extract_info(url, download=True)
        reqs = (info or {}).get("requested_downloads") or []
        for r in reqs:
            p = r.get("filepath")
            if p and os.path.exists(p):
                return p
        # 兜底:扫描目录取最大文件(合并格式与 outtmpl 可能产生命名差异)
        files = [
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if os.path.isfile(os.path.join(out_dir, f))
        ]
        return max(files, key=os.path.getsize) if files else None
    except Exception as e:
        log.error("✗ X 下载异常 %s: %s", url, e)
        return None


async def download_x_video(
    url: str,
    out_dir: str,
    progress_sink=None,
) -> Optional[str]:
    """用 yt-dlp 下载 X 推文视频到 out_dir,返回视频文件路径;失败返回 None。

    两阶段:先取信息判断是否有视频(无视频明确报错),再下载。
    双超时兜底(对齐 keeper._download_to_dir 语义):
      - 停滞:X_STALL_TIMEOUT 内进度无进展 → 放弃等待 + 清理目录
      - 整体:X_DOWNLOAD_TIMEOUT 上限 → 放弃等待 + 清理目录
    yt-dlp 同步阻塞,置于 asyncio.to_thread;放弃等待后线程因目录被清理
    (rmtree)而自然失败退出,不阻塞事件循环。所有失败路径在此设置
    progress_sink 文案并返回 None。
    """
    os.makedirs(out_dir, exist_ok=True)
    state = {"last_progress": time.monotonic(), "pct": -1}

    # ---- 第一阶段:取信息 ----
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_fetch_info, url, out_dir, state),
            timeout=cfg.X_DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("✗ 获取 X 视频信息超时(>%ss) %s", cfg.X_DOWNLOAD_TIMEOUT, url)
        if progress_sink is not None:
            await progress_sink.set_text("✗ 获取视频信息超时,原链接已保留")
        return None
    if info is None:
        if progress_sink is not None:
            await progress_sink.set_text("✗ 获取视频信息失败,原链接已保留")
        return None
    if not _info_has_video(info):
        log.info("跳过:X 推文没有可下载的视频 %s", url)
        if progress_sink is not None:
            await progress_sink.set_text("✗ 该推文没有可下载的视频,原链接已保留")
        return None
    if progress_sink is not None:
        await progress_sink.set_text(f"⏳ 准备下载: {_short_title(info)}")

    # ---- 第二阶段:下载(停滞 + 整体双超时) ----
    # to_thread 返回 coroutine,create_task 包装成 Task 才能 .done()/.cancel();
    # wait_for 用 shield 包裹,超时不取消底层 task,由下面的停滞/整体判断决定取消
    task = asyncio.create_task(
        asyncio.to_thread(_download, url, out_dir, state, progress_sink)
    )
    start = time.monotonic()
    # 轮询周期取三者最小值:保证任一超时都能在下个周期被检测到
    # (整体超时 < 5s 时(如测试),tick 必须跟着变小,否则 wait_for 直接等任务完成)
    tick = min(5.0, cfg.X_STALL_TIMEOUT, cfg.X_DOWNLOAD_TIMEOUT)
    try:
        while True:
            try:
                path = await asyncio.wait_for(asyncio.shield(task), timeout=tick)
                break
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if now - state["last_progress"] >= cfg.X_STALL_TIMEOUT:
                task.cancel()  # to_thread 无法真正取消,配合 rmtree 兜底
                shutil.rmtree(out_dir, ignore_errors=True)
                log.warning("✗ X 下载停滞(>%ss 无进展),已取消 %s",
                            cfg.X_STALL_TIMEOUT, url)
                if progress_sink is not None:
                    await progress_sink.set_text("✗ 下载停滞,已取消,原链接已保留")
                return None
            if now - start >= cfg.X_DOWNLOAD_TIMEOUT:
                task.cancel()
                shutil.rmtree(out_dir, ignore_errors=True)
                log.warning("✗ X 下载超时(>%ss),已取消 %s",
                            cfg.X_DOWNLOAD_TIMEOUT, url)
                if progress_sink is not None:
                    await progress_sink.set_text("✗ 下载超时,已取消,原链接已保留")
                return None

        if not path:
            if progress_sink is not None:
                await progress_sink.set_text("✗ 下载失败,原链接已保留")
            return None
        return path
    finally:
        if not task.done():
            task.cancel()
```

注意:模块顶部的 `import asyncio / logging / os / shutil / time / yt_dlp / config as cfg` 需合并到文件顶部(替换 Task 1 写的头部),`Optional` 已从 typing 导入。最终文件头部 import 块为:

```python
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Optional

import yt_dlp

import config as cfg

log = logging.getLogger("keeper")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_x_download.py tests/test_x_link_parsing.py -v`
Expected: 全部 PASS(约 4 个测试类;停滞/超时用例各 ~0.5s)

- [ ] **Step 5: Commit(默认跳过,见 Global Constraints)**

```bash
git add x_downloader.py tests/test_x_download.py
git commit -m "feat: yt-dlp X 视频下载器(双超时兜底)"
```

---

### Task 4: keeper.py 事件分支 + `process_x_link` 编排

**Files:**
- Modify: `keeper.py:19-45`(顶部 import,追加 `import x_downloader as xdl`)
- Modify: `keeper.py:948-958`(on_new_message:parse_tg_link 块后、_has_media 前追加 X 分支)
- Modify: `keeper.py:899` 之后(新增 `process_x_link` 编排函数,放 `process_link` 之后)

**Interfaces:**
- Consumes: `xdl.parse_x_link`(Task 1)、`xdl.download_x_video`(Task 3)、现有 `_extract_thumbnail` / `_maybe_compress` / `_send_local_file` / `_cleanup_local_files` / `SavedMessagesProgress` / `_delete_originals_if_enabled`(全部复用,不改签名)
- Produces: `keeper.process_x_link(client, url, src_msg) -> bool` — X 链接完整流程编排

**设计要点:**
- X 分支必须放在 `_has_media` 判断之前(Telegram 会给 x.com 链接生成 WebPage 预览,与 t.me 链接同理)。
- 编排复用现有函数**零签名改动**:`_maybe_compress(path, src_msg, ...)` 与 `_send_local_file(client, final_path, src_msg, ...)` 的 src_msg 直接传触发消息(收藏夹那条含链接的消息)。
- 下载失败文案由 `download_x_video` 设置;编排只处理 None → return False(原链接保留)。

- [ ] **Step 1: keeper.py 顶部 import**

在 `import compress as cmp` 后追加:

```python
import x_downloader as xdl
```

- [ ] **Step 2: 新增 `process_x_link` 编排函数**

在 `process_link`(第 850-898 行)函数结束后追加(完整内容):

```python
async def process_x_link(
    client: TelegramClient,
    url: str,
    src_msg: Message,
) -> bool:
    """X 链接流程:yt-dlp 下载 → 抽帧封面 → 压缩 → 上传目标频道。

    成功返回 True;任一步失败返回 False(调用方据此决定是否删原链接消息)。
    下载与失败文案由 x_downloader.download_x_video 负责;此处仅编排复用
    现有 t.me 流程的封面/压缩/上传/清理。
    """
    sub_dir = os.path.join(cfg.DOWNLOADS_DIR, f"x_{src_msg.id}")
    progress = SavedMessagesProgress(client)
    await progress.send("⏳ 正在获取 X 视频信息...")

    path: Optional[str] = None
    final_path: Optional[str] = None
    thumb: Optional[str] = None
    try:
        path = await xdl.download_x_video(url, sub_dir, progress_sink=progress)
        if not path:
            return False

        # 下载完成后用 ffmpeg 从视频抽帧作封面(与 t.me 流程一致)
        await progress.set_text("⏳ 提取封面...")
        thumb = await _extract_thumbnail(path, os.path.dirname(path))

        final_path = await _maybe_compress(path, src_msg, progress_sink=progress)
        await progress.set_text("⏳ 正在上传...")
        ok = await _send_local_file(
            client, final_path, src_msg,
            progress_sink=progress, thumb=thumb,
        )
        if ok:
            await progress.delete()
        else:
            await progress.set_text("✗ 上传失败，原链接已保留")
        return ok
    finally:
        # 上传完成(无论成功失败)后清理本地临时文件(原文件 + 可能的压缩件)
        _cleanup_local_files([path, final_path], sub_dir)
```

- [ ] **Step 3: on_new_message 追加 X 分支**

现有代码(keeper.py 951-958 附近):

```python
        parsed = parse_tg_link(text)
        if parsed:
            log.info("▶ 收到链接消息 msg_id=%s → peer=%s msg_id=%s",
                     msg.id, parsed[0], parsed[1])
            ok = await process_link(client, parsed)
            if ok:
                await _delete_originals_if_enabled(client, [msg.id])
            return

        # 媒体克隆流程：真媒体(非链接预览)才克隆
        if not _has_media(msg):
```

替换为(在 `return` 后插入 X 分支):

```python
        parsed = parse_tg_link(text)
        if parsed:
            log.info("▶ 收到链接消息 msg_id=%s → peer=%s msg_id=%s",
                     msg.id, parsed[0], parsed[1])
            ok = await process_link(client, parsed)
            if ok:
                await _delete_originals_if_enabled(client, [msg.id])
            return

        # X 链接流程：文本含 x.com/twitter.com 推文链接即走 X 下载上传。
        # 同样必须在 _has_media 之前：Telegram 会给 x.com 链接生成 WebPage 预览。
        x_url = xdl.parse_x_link(text)
        if x_url:
            log.info("▶ 收到 X 链接消息 msg_id=%s url=%s", msg.id, x_url)
            ok = await process_x_link(client, x_url, msg)
            if ok:
                await _delete_originals_if_enabled(client, [msg.id])
            return

        # 媒体克隆流程：真媒体(非链接预览)才克隆
        if not _has_media(msg):
```

- [ ] **Step 4: run() 启动日志追加一行**

在 `run()`(keeper.py 1037-1040 附近)的注册信息中追加:

```python
    log.info("  - X 链接:  文本含 x.com/twitter.com 链接 → 下载上传")
```

- [ ] **Step 5: 验证编译与现有测试**

Run: `uv run python -m pytest tests/ -v && uv run python -c "import ast; ast.parse(open('keeper.py').read()); ast.parse(open('x_downloader.py').read()); print('语法 OK')"`
Expected: 现有测试全 PASS,输出 `语法 OK`。

Run: `uv run python keeper.py --check`
Expected: 健康检查通过,配置摘要含 X_* 三项。

- [ ] **Step 6: Commit(默认跳过,见 Global Constraints)**

```bash
git add keeper.py
git commit -m "feat: 收藏夹 X 链接自动下载上传"
```

---

### Task 5: 全量验证 + 真实 X 链接实测

**Files:**
- 无代码改动;验证 + 手动实测

- [ ] **Step 1: 全量测试**

Run: `uv run python -m pytest tests/ -v`
Expected: 全部 PASS(现有 + 新增共约 30 个用例)

- [ ] **Step 2: 健康检查**

Run: `uv run python keeper.py --check`
Expected: `✓ 健康检查通过`,配置摘要含 `X_COOKIES_FILE` / `X_DOWNLOAD_TIMEOUT` / `X_STALL_TIMEOUT`

- [ ] **Step 3: 真实 X 链接实测(需要用户配合)**

1. 前台运行:`uv run python keeper.py`
2. 用户在 Telegram 收藏夹发送一条**公开 X 视频链接**(建议先用短/小视频)
3. 观察日志(前台)预期顺序:
   - `▶ 收到 X 链接消息 msg_id=... url=...`
   - `… X 下载中 xx% ...`(若文件较大)
   - `✓ X 下载完成` 附近日志(压缩/上传由现有代码打印:`✓ 压缩成功` / `✓ 链接内容已上传`)
   - 收藏夹中进度消息显示 `⏳ 准备下载: <标题>` → 上传完成后自动删除
   - 备份频道出现该视频(带缩略图、可播放、流式)
4. 收藏夹原链接消息按 `DELETE_ORIGINAL_FROM_SAVED` 设置被删除/保留
5. 若失败(如 403 / Unable to extract):按日志提示处理(升级 yt-dlp / 配置 X_COOKIES_FILE),原链接消息保留,流程不卡死

- [ ] **Step 4: 异常场景抽查(可选)**

- 发送非视频推文链接(纯图片)→ 进度消息 `✗ 该推文没有可下载的视频,原链接已保留`
- 发送含 x.com 链接但带媒体附件的消息 → 走 X 分支(附件忽略,与 t.me 行为一致)

- [ ] **Step 5: 汇报实测结果,由用户决定是否提交**

---

## Self-Review

**Spec 覆盖检查:**
- ✅ 链接识别(仅 /status/、大小写、query、无 scheme、i/status 结构)→ Task 1
- ✅ 下载器(两阶段、双超时、无视频报错、cookies/升级提示)→ Task 3
- ✅ 事件分支顺序(t.me 优先 → X → _has_media)→ Task 4
- ✅ 编排复用(封面/压缩/上传/清理/删原消息,零签名改动)→ Task 4
- ✅ 配置三项 + summary + .env.example → Task 2
- ✅ 依赖 yt-dlp → Task 1
- ✅ 单测(解析 + 下载 mock)→ Task 1/3;实测 → Task 5
- ✅ 明确不做:t.co 短链(测试断言不命中)、图片下载(无视频报错)、标题信息展示(仅进度消息含短标题)

**占位符检查:** 无 TBD/TODO;所有代码步骤含完整代码。

**类型一致性检查:**
- `parse_x_link(text) -> Optional[str]` — Task 1 定义,Task 4 消费,一致
- `download_x_video(url, out_dir, progress_sink=None) -> Optional[str]` — Task 3 定义,Task 4 消费,一致
- `process_x_link(client, url, src_msg) -> bool` — Task 4 定义并使用,一致
- progress_sink 接口(report 同步 + set_text 异步)在 Task 3 测试 FakeProgressSink 与真实 `SavedMessagesProgress` 一致
- `_info_has_video(info: dict) -> bool` — Task 3 定义并测试,一致
