#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x_downloader.py — X(Twitter) 视频链接解析与下载。

只负责两件事:
  1. parse_x_link:从文本中识别 x.com/twitter.com 的 /status/ 推文链接
  2. download_x_video:用 yt-dlp 把推文对应的视频下载到本地目录

下载后的封面抽取/压缩/上传/清理由 keeper.py 复用现有 t.me 流程处理。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from typing import Optional, Tuple

import yt_dlp

import config as cfg

log = logging.getLogger("keeper")

# 匹配 x.com / twitter.com 的推文链接:<user 或 i>/status/<id>[/video/<n>][?query]
# 只认 /status/ 路径;profile/搜索/hashtag 不触发;t.co 短链不处理;
# /video/<n> 是多视频推文的单视频 deep-link,X 对其无 /video 后缀提取不到视频,
# 必须完整保留交给 yt-dlp;query(以 ? 开头,在末尾)随 URL 保留,
# 中文标点等不会吞进 URL。
_X_STATUS_URL_RE = re.compile(
    r"(?:https?://)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]{1,15}|i)"
    r"/status/\d+(?:/video/\d+)?(?:\?[^\s]*)?",
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


def _ydl_opts(out_dir: str, state: dict, progress_sink=None, url: str = "") -> dict:
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
        with yt_dlp.YoutubeDL(_ydl_opts(out_dir, state, url=url)) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        log.error("✗ 获取 X 视频信息失败 %s: %s", url, e)
        log.info("  提示:X 改版时常见,可尝试 `uv pip install -U yt-dlp`;"
                 "私有推文可配置 X_COOKIES_FILE")
        return None


def _download(url: str, out_dir: str, state: dict, progress_sink=None) -> Optional[str]:
    """第二阶段:下载视频文件,返回本地路径。失败返回 None。"""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(out_dir, state, progress_sink, url)) as ydl:
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
) -> Optional[Tuple[str, str]]:
    """用 yt-dlp 下载 X 推文视频到 out_dir,返回 (视频文件路径, 推文标题)。

    失败(含无视频/超时)返回 None。标题用于上传时的 caption;
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
    title = str(info.get("title") or "")

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
        return (path, title)
    finally:
        if not task.done():
            task.cancel()
