#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compress.py — 视频压缩(ffmpeg 子进程封装) + 压缩决策。

决策核心:按"码率 = 大小 / 时长"判断是否需要压缩,而非只看文件大小。
  - 200MB / 1min  ≈ 26.7 Mbps → 浪费,压
  - 200MB / 30min ≈ 0.9 Mbps  → 已高效,跳过
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

import config as cfg


@dataclass
class MediaProbe:
    """ffprobe 解析结果。is_video=False 表示无视频流(图片/音频/纯文档)。"""
    path: str
    size_bytes: int
    duration: float       # 秒;0 表示未知
    width: int
    height: int
    video_codec: str
    is_video: bool


def source_bitrate_mbps(probe: MediaProbe) -> float:
    """Mbps = size_bytes * 8 / duration / 1e6。duration<=0 返回 0。"""
    if probe.duration <= 0:
        return 0.0
    return probe.size_bytes * 8 / probe.duration / 1_000_000


def probe_media(path: str) -> Optional[MediaProbe]:
    """ffprobe 取时长/分辨率/视频编码。无视频流或失败返回 None。"""
    if not path or not os.path.exists(path):
        return None
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return None

    streams = data.get("streams") or []
    if not streams:
        return None  # 无视频流
    s = streams[0]
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return MediaProbe(
        path=path,
        size_bytes=size,
        duration=duration,
        width=int(s.get("width") or 0),
        height=int(s.get("height") or 0),
        video_codec=str(s.get("codec_name") or ""),
        is_video=True,
    )


def should_compress(probe: MediaProbe) -> bool:
    """是否需要压缩。"""
    if not probe.is_video:
        return False
    if probe.size_bytes < cfg.COMPRESS_MIN_SIZE_MB * 1024 * 1024:
        return False
    if probe.duration <= 0:
        return False
    return source_bitrate_mbps(probe) > cfg.COMPRESS_BITRATE_THRESHOLD


def skip_reason(probe: MediaProbe) -> str:
    """should_compress 为 False 时的可读原因。"""
    if not probe.is_video:
        return "非视频"
    if probe.size_bytes < cfg.COMPRESS_MIN_SIZE_MB * 1024 * 1024:
        return f"文件过小(<{cfg.COMPRESS_MIN_SIZE_MB}MB)"
    if probe.duration <= 0:
        return "时长未知"
    return (f"码率已高效({source_bitrate_mbps(probe):.1f}Mbps "
            f"≤ {cfg.COMPRESS_BITRATE_THRESHOLD})")


def compress_video(src: str, dst: str, log_fn=None) -> bool:
    """转码 src → dst(H.264/AAC/mp4)，仅按码率重编码、不缩放分辨率。

    在线程池中调用(由 keeper 用 asyncio.to_thread 包裹),不阻塞事件循环。
    """
    probe = probe_media(src)
    if probe is None:
        if log_fn:
            log_fn("ffprobe 失败,跳过压缩")
        return False

    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-c:v", "libx264", "-preset", cfg.COMPRESS_PRESET,
        "-crf", str(cfg.COMPRESS_CRF),
        "-maxrate", cfg.COMPRESS_MAXRATE,
        "-bufsize", cfg.COMPRESS_BUFSIZE,
        "-c:a", "aac", "-b:a", cfg.COMPRESS_AUDIO_BITRATE,
        "-movflags", "+faststart",
        dst,
    ]

    # 给 4 倍时长预算(至少 30min),防异常卡死
    timeout = max(1800, int(probe.duration) * 4) if probe.duration > 0 else 7200
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if log_fn:
            log_fn(f"ffmpeg 超时(>{timeout}s)")
        return False
    except FileNotFoundError:
        if log_fn:
            log_fn("ffmpeg 未安装")
        return False

    if r.returncode != 0:
        if log_fn:
            log_fn("ffmpeg 失败: " + (r.stderr or "")[-500:])
        return False
    return os.path.exists(dst)
