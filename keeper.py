#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg-video-keeper — 私密 Telegram 媒体克隆守护进程

=====================================================================
工作逻辑
=====================================================================
1. 监听收藏夹（Saved Messages）的新消息。
2. 检测到媒体（视频/图片/文档/音频）→ 用 send_file(file=msg) 克隆到目标备份频道，
   生成**不带 "转发自" 头部**的独立消息；Telegram 全局去重，原频道被封后备份仍可访问。
3. DELETE_ORIGINAL_FROM_SAVED=true → 克隆后删除原消息，保持收藏夹干净。
4. 相册（同一 grouped_id 的多条媒体）由 events.Album 聚合后整体克隆，
   NewMessage 中按 grouped_id 跳过相册成员，避免重复克隆。

安全：仅监听收藏夹（只有账号本人能发消息），不对外提供任何服务。
=====================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyError,
    FloodWaitError,
    RPCError,
    UserDeactivatedError,
)
from telethon.tl.custom import Message
from telethon.tl.types import MessageMediaEmpty, MessageMediaWebPage

import compress as cmp
import config as cfg


# =====================================================================
# 一、日志配置
# =====================================================================
def setup_logger() -> logging.Logger:
    """配置同时输出到控制台和滚动文件的日志器。"""
    logger = logging.getLogger("keeper")
    logger.setLevel(getattr(logging, cfg.LOG_LEVEL, logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 滚动文件：单文件 5MB，保留 5 份
    log_path = os.path.join(cfg.LOGS_DIR, "keeper.log")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


log = setup_logger()


# =====================================================================
# 二、客户端工厂
# =====================================================================
def make_client() -> TelegramClient:
    """构造 Telethon 客户端。session 文件存放在 sessions/ 目录。"""
    session_path = os.path.join(cfg.SESSIONS_DIR, cfg.SESSION_NAME)
    client = TelegramClient(
        session_path,
        cfg.API_ID,
        cfg.API_HASH,
        connection_retries=None,   # 无限重连尝试（配合 auto_reconnect）
        retry_delay=2,             # 重连间隔 2 秒
        auto_reconnect=True,       # 掉线自动重连
        request_retries=5,         # 单次请求重试次数
    )
    return client


# =====================================================================
# 三、运行期状态（启动后填充）
# =====================================================================
TARGET_PEER = None                      # 目标频道实体缓存
MY_USER_ID: Optional[int] = None        # 本人 user id（用于判定收藏夹）


# =====================================================================
# 四、工具函数
# =====================================================================
def _has_media(message: Message) -> bool:
    """消息是否携带可克隆媒体（视频/图片/文档/音频等）。

    排除空媒体与链接预览(WebPage)：WebPage 预览不是真实文件，
    send_file(file=msg) 无法克隆，应按文本处理或走链接流程。
    """
    if message.media is None or isinstance(message.media, (MessageMediaEmpty, MessageMediaWebPage)):
        return False
    return True


# =====================================================================
# 四之二、收藏夹链接解析
# =====================================================================
# 匹配 t.me / telegram.me 的消息链接（不含 scheme 也行），取路径部分解析。
_TME_URL_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/[^\s]+", re.IGNORECASE)
# 匹配 tg:// 自定义 scheme（resolve / privatepost）。
_TG_SCHEME_RE = re.compile(r"tg://(?:resolve|privatepost)\?[^\s]+", re.IGNORECASE)

# 私有频道/超级群 marked peer id 的前缀偏移：marked = -(10**12) - channel_id。
# t.me/c/<channel_id>/<msg> 中的 channel_id 是原始 id，需用此公式转成 Telethon 的负 id。
_CHANNEL_MARK_OFFSET = 10 ** 12


def parse_tg_link(text: str) -> Optional[Tuple[Union[str, int], int]]:
    """从文本中提取第一条 Telegram 消息分享链接，解析为 (peer, msg_id)。

    支持：
      - 公开： https://t.me/<username>/<msg_id>
      - 私有： https://t.me/c/<channel_id>/<msg_id>（带话题时末段为 msg_id）
      - tg://resolve?domain=<username>&post=<msg_id>
      - tg://privatepost?channel=<channel_id>&post=<msg_id>

    解析失败/无链接返回 None。peer 为 username(str) 或负数 channel id(int)。
    """
    if not text:
        return None

    m = _TME_URL_RE.search(text)
    if m:
        return _parse_tme_url(m.group(0))

    m = _TG_SCHEME_RE.search(text)
    if m:
        return _parse_tg_scheme(m.group(0))

    return None


def _parse_tme_url(url: str) -> Optional[Tuple[Union[str, int], int]]:
    """解析 t.me/telegram.me 链接。"""
    # 去掉 query（?single / ?comment=... 等）
    path = url.split("?", 1)[0]
    m = re.search(r"(?:t\.me|telegram\.me)/(.+)$", path, re.IGNORECASE)
    if not m:
        return None
    parts = [p for p in m.group(1).split("/") if p]
    if not parts:
        return None

    if parts[0] == "c":
        # 私有：/c/<channel_id>/<msg_id> 或 /c/<channel_id>/<topic_id>/<msg_id>
        nums = [p for p in parts[1:] if p.isdigit()]
        if len(nums) < 2:
            return None
        channel_id = int(nums[0])
        msg_id = int(nums[-1])
        return (-_CHANNEL_MARK_OFFSET - channel_id, msg_id)

    # 公开：/<username>/<msg_id>
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    username = parts[0]
    if username.lower() in ("joinchat", "addstickers", "share", "join"):
        # 这些是邀请/分享链接，不是消息链接
        return None
    return (username, int(parts[1]))


def _parse_tg_scheme(url: str) -> Optional[Tuple[Union[str, int], int]]:
    """解析 tg://resolve / tg://privatepost 链接。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    post = (qs.get("post") or [None])[0]
    if not post or not post.isdigit():
        return None

    if parsed.netloc.lower() == "resolve":
        domain = (qs.get("domain") or [None])[0]
        if not domain:
            return None
        return (domain, int(post))
    if parsed.netloc.lower() == "privatepost":
        channel = (qs.get("channel") or [None])[0]
        if not channel or not channel.isdigit():
            return None
        return (-_CHANNEL_MARK_OFFSET - int(channel), int(post))
    return None


def contains_tg_link(text: str) -> bool:
    """文本是否包含可解析的 Telegram 消息分享链接。"""
    return parse_tg_link(text) is not None


def _short_title(msg: Message) -> str:
    """进度消息用的短标题：取文件名/caption 前 6 字符，折叠换行与多余空白。

    原始名称过长或含换行会撑破进度消息排版，故统一截断到 6 字符。
    """
    name = ""
    try:
        if getattr(msg, "file", None) is not None and msg.file.name:
            name = msg.file.name
    except Exception:
        name = ""
    if not name:
        name = msg.message or ""
    if not name:
        name = type(msg.media).__name__
    name = " ".join(str(name).split())  # 折叠所有空白(含换行)为单空格
    return name[:6]


# =====================================================================
# 五、媒体克隆核心
# =====================================================================
async def clone_single(client: TelegramClient, message: Message) -> bool:
    """克隆单条媒体消息到目标备份频道。

    使用 send_file(file=message)：把消息对象当 file 传入，Telethon 自动提取
    message.media 并以 InputMedia 重发，生成不带 "转发自" 头部的独立消息。
    原频道被封后，克隆消息仍可访问（Telegram 全局去重存储媒体）。
    """
    try:
        sent = await client.send_file(
            TARGET_PEER,
            file=message,
            caption=message.message,
            formatting_entities=message.entities,
            silent=cfg.SILENT_SEND,
        )
        sent_id = getattr(sent, "id", "?")
        log.info(
            "✓ 单条克隆成功  src_msg_id=%s → target_msg_id=%s  media=%s",
            message.id, sent_id, type(message.media).__name__,
        )
        return True
    except FloodWaitError as e:
        log.warning("⚠ 限流，等待 %ss 后重试 (msg_id=%s)", e.seconds, message.id)
        await asyncio.sleep(e.seconds + 1)
        return await clone_single(client, message)
    except RPCError as e:
        log.error("✗ 克隆失败(RPC) msg_id=%s: %s", message.id, e)
        return False
    except Exception as e:
        log.exception("✗ 克隆失败(未知) msg_id=%s: %s", message.id, e)
        return False


async def clone_album(client: TelegramClient, messages: List[Message]) -> bool:
    """克隆相册（多图/多视频组）到目标频道，作为相册整体发送。"""
    if not messages:
        return False
    try:
        captions = [m.message for m in messages]
        sent = await client.send_file(
            TARGET_PEER,
            file=messages,
            caption=captions,
            silent=cfg.SILENT_SEND,
        )
        first_id = getattr(sent[0], "id", "?") if isinstance(sent, list) else getattr(sent, "id", "?")
        log.info(
            "✓ 相册克隆成功  共 %s 条  首条 src_msg_id=%s → target_msg_id=%s  grouped_id=%s",
            len(messages), messages[0].id, first_id, messages[0].grouped_id,
        )
        return True
    except FloodWaitError as e:
        log.warning("⚠ 限流，等待 %ss 后重试相册", e.seconds)
        await asyncio.sleep(e.seconds + 1)
        return await clone_album(client, messages)
    except RPCError as e:
        log.error("✗ 相册克隆失败(RPC): %s", e)
        return False
    except Exception as e:
        log.exception("✗ 相册克隆失败(未知): %s", e)
        return False


# =====================================================================
# 五之二、收藏夹链接 → 下载 → 上传
# =====================================================================
async def _resolve_source_message(
    client: TelegramClient,
    parsed: Tuple[Union[str, int], int],
) -> Optional[Message]:
    """根据 parse_tg_link 的结果，拉取指向的源消息对象。

    返回 None 表示：非成员/链接失效/无媒体/属相册（v1 不处理）。
    """
    peer, msg_id = parsed
    try:
        msg = await client.get_messages(peer, ids=msg_id)
    except ValueError:
        log.warning("✗ 无法解析频道实体（可能未加入该频道或为私有频道），跳过: peer=%s", peer)
        return None
    except RPCError as e:
        log.error("✗ get_messages 失败 peer=%s msg_id=%s: %s", peer, msg_id, e)
        return None

    if not msg:
        log.warning("✗ 源消息不存在或不可访问 peer=%s msg_id=%s", peer, msg_id)
        return None
    if not _has_media(msg):
        log.info("跳过：源消息无媒体 peer=%s msg_id=%s", peer, msg_id)
        return None
    return msg


class SavedMessagesProgress:
    """收藏夹进度消息：发一条、节流编辑、完成后删除。

    bot 自己发出的消息是 outgoing，不会被 incoming=True 的处理器重复触发，
    无回环风险。编辑节流(MIN_EDIT_INTERVAL)避免触发 Telegram 编辑限流。
    report() 从同步进度回调调用，内部用 asyncio.create_task 调度异步编辑。
    """

    MIN_EDIT_INTERVAL = 2.5  # 秒，同一消息两次编辑的最小间隔

    def __init__(self, client: TelegramClient, chat: str = "me"):
        self._client = client
        self._chat = chat
        self.msg_id: Optional[int] = None
        self._last_edit = 0.0
        self._last_pct = -1

    async def send(self, text: str) -> None:
        try:
            m = await self._client.send_message(self._chat, text)
            self.msg_id = getattr(m, "id", None)
        except Exception as e:
            log.debug("进度消息发送失败(忽略): %s", e)

    def report(self, label: str, pct: Optional[int] = None,
               received_mb: Optional[float] = None,
               total_mb: Optional[float] = None) -> None:
        """同步进度回调入口：节流后调度一次异步编辑。"""
        now = time.monotonic()
        if pct is not None and pct < 100 and pct < self._last_pct + 5 \
                and now - self._last_edit < self.MIN_EDIT_INTERVAL:
            return
        if pct is None and now - self._last_edit < self.MIN_EDIT_INTERVAL:
            return
        text = self._format(label, pct, received_mb, total_mb)
        self._last_edit = now
        if pct is not None:
            self._last_pct = pct
        try:
            asyncio.create_task(self._edit(text))
        except RuntimeError:
            pass  # 无运行中的事件循环(忽略)

    async def set_text(self, text: str) -> None:
        """立即(不节流)更新文本，用于阶段切换(下载→压缩→上传)。"""
        self._last_edit = time.monotonic()
        await self._edit(text)

    def reset(self) -> None:
        """重置节流状态(每个新文件下载前调用,避免上一个文件 100% 卡住下一个)。"""
        self._last_edit = 0.0
        self._last_pct = -1

    async def _edit(self, text: str) -> None:
        if not self.msg_id:
            return
        try:
            await self._client.edit_message(self._chat, self.msg_id, text)
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 1)
            try:
                await self._client.edit_message(self._chat, self.msg_id, text)
            except Exception:
                pass
        except Exception as e:
            log.debug("进度消息编辑失败(忽略): %s", e)

    async def delete(self) -> None:
        if not self.msg_id:
            return
        try:
            await self._client.delete_messages(self._chat, [self.msg_id])
        except Exception as e:
            log.debug("进度消息删除失败(忽略): %s", e)
        self.msg_id = None

    @staticmethod
    def _format(label: str, pct: Optional[int],
                received_mb: Optional[float], total_mb: Optional[float]) -> str:
        if pct is not None and received_mb is not None and total_mb is not None:
            return f"{label} {pct}% ({received_mb:.1f}/{total_mb:.1f}MB)"
        if pct is not None:
            return f"{label} {pct}%"
        if received_mb is not None and total_mb is not None:
            return f"{label} ({received_mb:.1f}/{total_mb:.1f}MB)"
        return label


async def _download_to_dir(
    client: TelegramClient,
    msg: Message,
    sub_dir: str,
    progress_sink: Optional[SavedMessagesProgress] = None,
    progress_label: str = "⏳ 下载中",
) -> Optional[str]:
    """下载单条消息媒体到 downloads 子目录，返回本地文件路径。

    双超时保护（避免受限频道挂起时白等、又让大文件慢链路能下完）：
      - LINK_STALL_TIMEOUT：进度回调超过此秒无任何进展 → 判定挂起/断流，取消。
        受限频道(noforwards)的 download_media 进度回调永不触发，靠此快速跳出。
      - LINK_DOWNLOAD_TIMEOUT：整体最大时长兜底。
    进度回调每收 5% 打一行，便于区分"在下载只是慢"与"挂起"。
    成功返回路径，失败/超时返回 None。
    """
    log.info("⚠ [diag] 进入 _download_to_dir msg_id=%s", msg.id)
    os.makedirs(sub_dir, exist_ok=True)
    state = {"last_progress": time.monotonic(), "pct": -1, "got_bytes": False}

    def _progress(received: int, total: int) -> None:
        state["last_progress"] = time.monotonic()
        state["got_bytes"] = True
        if total > 0:
            pct = int(received * 100 / total)
            if pct >= state["pct"] + 5 or pct == 100:
                state["pct"] = pct
                log.info("… 下载中 %s%% (%.1f/%.1f MB) msg_id=%s",
                         pct, received / 1048576, total / 1048576, msg.id)
            if progress_sink is not None:
                progress_sink.report(progress_label, pct=pct,
                                     received_mb=received / 1048576,
                                     total_mb=total / 1048576)
        elif received > 0:
            if received % (10 * 1048576) < 1024 * 1024:
                log.info("… 下载中(总大小未知)已收 %.1f MB msg_id=%s",
                         received / 1048576, msg.id)
            if progress_sink is not None:
                progress_sink.report(progress_label, pct=None,
                                     received_mb=received / 1048576, total_mb=0)

    task = asyncio.create_task(
        client.download_media(msg, file=sub_dir, progress_callback=_progress)
    )
    log.info("⚠ [diag] task 已创建,进入循环 msg_id=%s", msg.id)
    start = time.monotonic()
    max_timeout = cfg.LINK_DOWNLOAD_TIMEOUT
    stall_timeout = cfg.LINK_STALL_TIMEOUT
    try:
        while True:
            try:
                # 每 5s 醒来检查停滞；shield 保证超时不取消底层下载任务
                path = await asyncio.wait_for(asyncio.shield(task), timeout=5)
                break
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            log.info("⚠ [diag] tick stall=%.0fs pct=%s got=%s msg_id=%s",
                     now - state["last_progress"], state["pct"],
                     state["got_bytes"], msg.id)
            if now - state["last_progress"] > stall_timeout:
                task.cancel()
                if state["got_bytes"]:
                    log.warning(
                        "⚠ 下载停滞(有进度后 %ss 无进展),取消 msg_id=%s "
                        "进度=%s%%(网络中断?可重试)",
                        stall_timeout, msg.id, state["pct"],
                    )
                else:
                    log.warning(
                        "⚠ 进度回调从未触发,疑似受限频道(noforwards)MTProto "
                        "upload.getFile 被服务端挂起,取消 msg_id=%s", msg.id,
                    )
                _cleanup_dir(sub_dir)
                return None
            if now - start > max_timeout:
                task.cancel()
                log.warning(
                    "⚠ 下载超过最大时长 %ss,取消 msg_id=%s 进度=%s%%",
                    max_timeout, msg.id, state["pct"],
                )
                _cleanup_dir(sub_dir)
                return None

        if not path:
            log.warning("✗ 下载未产生文件 msg_id=%s", msg.id)
            _cleanup_dir(sub_dir)
            return None
        return path
    except FloodWaitError as e:
        log.warning("⚠ 下载限流 %ss,等待后重试 msg_id=%s", e.seconds, msg.id)
        await asyncio.sleep(e.seconds + 1)
        return await _download_to_dir(client, msg, sub_dir)
    except RPCError as e:
        log.error("✗ 下载失败(RPC) msg_id=%s: %s", msg.id, e)
        _cleanup_dir(sub_dir)
        return None
    except Exception as e:
        log.exception("✗ 下载失败(未知) msg_id=%s: %s", msg.id, e)
        _cleanup_dir(sub_dir)
        return None
    finally:
        if not task.done():
            task.cancel()


async def _send_local_file(
    client: TelegramClient,
    file_path: str,
    src_msg: Message,
    progress_sink: Optional[SavedMessagesProgress] = None,
) -> bool:
    """把本地下载好的文件上传到目标备份频道，不带转发头。"""
    def _upload_progress(sent: int, total: int) -> None:
        if progress_sink is not None and total > 0:
            pct = int(sent * 100 / total)
            progress_sink.report("⏳ 上传中", pct=pct,
                                 received_mb=sent / 1048576,
                                 total_mb=total / 1048576)

    try:
        sent = await client.send_file(
            TARGET_PEER,
            file=file_path,
            caption=src_msg.message,
            formatting_entities=src_msg.entities,
            silent=cfg.SILENT_SEND,
            supports_streaming=True,  # 视频置流式属性，目标端可边下边播而非必须下完
            progress_callback=_upload_progress,
        )
        sent_id = getattr(sent, "id", "?")
        log.info(
            "✓ 链接内容已上传  src_msg_id=%s → target_msg_id=%s  file=%s  media=%s",
            src_msg.id, sent_id, os.path.basename(file_path),
            type(src_msg.media).__name__,
        )
        return True
    except FloodWaitError as e:
        log.warning("⚠ 上传限流 %ss，等待后重试 msg_id=%s", e.seconds, src_msg.id)
        await asyncio.sleep(e.seconds + 1)
        return await _send_local_file(client, file_path, src_msg)
    except RPCError as e:
        log.error("✗ 上传失败(RPC) msg_id=%s: %s", src_msg.id, e)
        return False
    except Exception as e:
        log.exception("✗ 上传失败(未知) msg_id=%s: %s", src_msg.id, e)
        return False


def _cleanup_dir(path: str) -> None:
    """删除下载子目录及其内容（已上传或失败后回收）。"""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        log.debug("清理目录失败（忽略）: %s", path)


def _safe_remove(path: str) -> None:
    """安全删除单个文件（忽略错误）。"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        log.debug("删除文件失败（忽略）: %s", path)


def _cleanup_local_files(paths: List[str], sub_dir: str) -> None:
    """上传后清理本地临时文件与子目录。

    KEEP_LOCAL_FILE_AFTER_UPLOAD=true 时保留，仅打印路径（便于用
    ffmpeg/ffprobe 核对视频元数据/可播放性）；否则删除文件并清空子目录。
    """
    kept: List[str] = []
    for p in paths:
        if p and p not in kept:
            kept.append(p)
    if cfg.KEEP_LOCAL_FILE_AFTER_UPLOAD:
        for p in kept:
            log.info("保留本地文件(KEEP_LOCAL_FILE_AFTER_UPLOAD=true): %s", p)
        log.info("保留下载子目录: %s", sub_dir)
        return
    for p in kept:
        _safe_remove(p)
    _cleanup_dir(sub_dir)


async def _maybe_compress(
    path: str,
    msg: Message,
    progress_sink: Optional[SavedMessagesProgress] = None,
) -> str:
    """按"码率=大小/时长"判断是否压缩；需要则 ffmpeg 转码。

    返回最终要上传的路径（压缩后的或原文件）。压缩失败/未变小/无需压缩 → 原文件。
    ffmpeg 在线程池运行（asyncio.to_thread），不阻塞事件循环。
    """
    if not cfg.COMPRESS_VIDEO:
        return path

    probe = cmp.probe_media(path)
    if probe is None:
        log.warning("跳过压缩：ffprobe 失败，上传原文件 msg_id=%s", msg.id)
        return path

    if not cmp.should_compress(probe):
        log.info(
            "跳过压缩(%s) msg_id=%s 大小=%.1fMB 时长=%.1fs 码率=%.1fMbps",
            cmp.skip_reason(probe), msg.id,
            probe.size_bytes / 1048576, probe.duration,
            cmp.source_bitrate_mbps(probe),
        )
        # 跳过重编码，但仍做 faststart 重封(移 moov 到头部)，
        # 否则上传后视频无法在 Telegram 边下边播(需下完才能播)。
        fs_path = await asyncio.to_thread(
            cmp.ensure_faststart, path, probe,
            lambda m: log.info("  ffmpeg(fs): %s", m),
        )
        if fs_path != path:
            log.info("✓ faststart 重封完成 msg_id=%s → %s", msg.id, os.path.basename(fs_path))
        return fs_path

    dst = f"{os.path.splitext(path)[0]}_compressed.mp4"
    if progress_sink is not None:
        await progress_sink.set_text("⏳ 正在压缩...")
    log.info(
        "▶ 开始压缩 msg_id=%s 源=%.1fMB/%.1fs(%.1fMbps) %dx%d → "
        "CRF%s maxrate=%s preset=%s",
        msg.id, probe.size_bytes / 1048576, probe.duration,
        cmp.source_bitrate_mbps(probe), probe.width, probe.height,
        cfg.COMPRESS_CRF, cfg.COMPRESS_MAXRATE, cfg.COMPRESS_PRESET,
    )
    ok = await asyncio.to_thread(
        cmp.compress_video, path, dst,
        log_fn=lambda m: log.info("  ffmpeg: %s", m),
    )
    if not ok or not os.path.exists(dst):
        log.warning("✗ 压缩失败，上传原文件 msg_id=%s", msg.id)
        _safe_remove(dst)
        return path

    orig_size = os.path.getsize(path)
    new_size = os.path.getsize(dst)
    if new_size >= orig_size:
        log.info(
            "压缩后未变小(%.1fMB → %.1fMB)，保留原文件 msg_id=%s",
            orig_size / 1048576, new_size / 1048576, msg.id,
        )
        _safe_remove(dst)
        return path
    log.info(
        "✓ 压缩成功 %.1fMB → %.1fMB(-%d%%) msg_id=%s",
        orig_size / 1048576, new_size / 1048576,
        int((1 - new_size / orig_size) * 100), msg.id,
    )
    return dst


async def _fetch_album_members(
    client: TelegramClient,
    peer: Union[str, int],
    base_msg: Message,
) -> List[Message]:
    """取与 base_msg 同 grouped_id 的相册全部成员(按 id 升序)。

    相册成员 id 通常连续,在 base 附近 ±50 窗口内扫描即可覆盖(相册最多 10 条)。
    """
    gid = base_msg.grouped_id
    base_id = base_msg.id
    members: List[Message] = []
    try:
        async for m in client.iter_messages(peer, min_id=base_id - 50, max_id=base_id + 50):
            if m.grouped_id == gid and _has_media(m):
                members.append(m)
    except (ValueError, RPCError) as e:
        log.warning("⚠ 拉取相册成员失败 peer=%s: %s", peer, e)
        return [base_msg] if _has_media(base_msg) else []
    members.sort(key=lambda x: x.id)
    if not members:  # 找不到邻居至少用 base
        members = [base_msg]
    return members


async def _send_album(
    client: TelegramClient,
    paths: List[str],
    src_msgs: List[Message],
    progress_sink: Optional[SavedMessagesProgress] = None,
) -> bool:
    """把多个本地文件作为相册整体上传到目标频道。"""
    captions = [m.message or "" for m in src_msgs]

    def _upload_progress(sent: int, total: int) -> None:
        if progress_sink is not None and total > 0:
            progress_sink.report("⏳ 上传相册", pct=int(sent * 100 / total),
                                 received_mb=sent / 1048576, total_mb=total / 1048576)

    try:
        sent = await client.send_file(
            TARGET_PEER,
            file=paths,
            caption=captions,
            silent=cfg.SILENT_SEND,
            supports_streaming=True,  # 视频置流式属性，目标端可边下边播而非必须下完
            progress_callback=_upload_progress,
        )
        first_id = getattr(sent[0], "id", "?") if isinstance(sent, list) else getattr(sent, "id", "?")
        log.info("✓ 相册链接内容已上传  共 %s 条  首条 → target_msg_id=%s",
                 len(paths), first_id)
        return True
    except FloodWaitError as e:
        log.warning("⚠ 相册上传限流 %ss，等待后重试", e.seconds)
        await asyncio.sleep(e.seconds + 1)
        return await _send_album(client, paths, src_msgs, progress_sink)
    except RPCError as e:
        log.error("✗ 相册上传失败(RPC): %s", e)
        return False
    except Exception as e:
        log.exception("✗ 相册上传失败(未知): %s", e)
        return False


async def process_album_link(
    client: TelegramClient,
    parsed: Tuple[Union[str, int], int],
    base_msg: Message,
) -> bool:
    """相册链接流程：拉取相册全部成员 → 逐条下载(可选压缩) → 作为相册上传。"""
    peer, _ = parsed
    members = await _fetch_album_members(client, peer, base_msg)
    n = len(members)
    log.info("▶ 相册链接  grouped_id=%s 共 %s 条 peer=%s", base_msg.grouped_id, n, peer)

    progress = SavedMessagesProgress(client)
    await progress.send(f"⏳ 准备下载相册({n}条)")

    sub_dir = os.path.join(cfg.DOWNLOADS_DIR, f"album_{peer}_{base_msg.id}")
    final_paths: List[str] = []
    final_msgs: List[Message] = []
    try:
        for i, m in enumerate(members, 1):
            await progress.set_text(f"⏳ 下载相册 {i}/{n}")
            progress.reset()
            one_dir = os.path.join(sub_dir, str(m.id))
            label = f"⏳ 下载相册 {i}/{n}"
            path = await _download_to_dir(
                client, m, one_dir, progress_sink=progress, progress_label=label,
            )
            if not path:
                log.warning("✗ 相册第 %s/%s 条下载失败,跳过 msg_id=%s", i, n, m.id)
                continue
            final_path = await _maybe_compress(path, m, progress_sink=progress)
            final_paths.append(final_path)
            final_msgs.append(m)

        if not final_paths:
            await progress.set_text("✗ 相册全部下载失败,原链接已保留")
            return False

        await progress.set_text("⏳ 正在上传相册...")
        ok = await _send_album(client, final_paths, final_msgs, progress_sink=progress)
        if ok:
            await progress.delete()
        else:
            await progress.set_text("✗ 相册上传失败,原链接已保留")
        return ok
    finally:
        _cleanup_local_files(final_paths, sub_dir)


async def process_link(
    client: TelegramClient,
    parsed: Tuple[Union[str, int], int],
) -> bool:
    """收藏夹链接 → 解析源消息 → 下载到本地 → 上传到目标频道。

    成功返回 True。任一步失败均返回 False（调用方据此决定是否删原链接消息）。
    源消息若属于相册(grouped_id)则走 process_album_link。
    """
    msg = await _resolve_source_message(client, parsed)
    if msg is None:
        return False

    # 源消息属于相册 → 走相册流程
    if msg.grouped_id is not None:
        return await process_album_link(client, parsed, msg)

    peer, _ = parsed
    sub_dir = os.path.join(cfg.DOWNLOADS_DIR, f"link_{peer}_{msg.id}")

    # 收藏夹进度消息：下载/压缩/上传各阶段节流更新，成功后删除
    progress = SavedMessagesProgress(client)
    title = _short_title(msg)
    await progress.send(f"⏳ 准备下载: {title}")

    path: Optional[str] = None
    final_path: Optional[str] = None
    try:
        path = await _download_to_dir(client, msg, sub_dir, progress_sink=progress)
        if not path:
            await progress.set_text("✗ 下载失败/超时，原链接已保留")
            return False

        final_path = await _maybe_compress(path, msg, progress_sink=progress)
        await progress.set_text("⏳ 正在上传...")
        ok = await _send_local_file(client, final_path, msg, progress_sink=progress)
        if ok:
            await progress.delete()
        else:
            await progress.set_text("✗ 上传失败，原链接已保留")
        return ok
    finally:
        # 上传完成（无论成功失败）后清理本地临时文件（原文件 + 可能的压缩件）
        _cleanup_local_files([path, final_path], sub_dir)


# =====================================================================
# 六、收尾删除
# =====================================================================
async def _delete_originals_if_enabled(
    client: TelegramClient,
    message_ids: List[int],
) -> None:
    """DELETE_ORIGINAL_FROM_SAVED=true 时删除收藏夹原消息。"""
    if not cfg.DELETE_ORIGINAL_FROM_SAVED:
        return
    try:
        await client.delete_messages("me", message_ids)
        log.info("✓ 已删除收藏夹原消息  ids=%s", message_ids)
    except RPCError as e:
        log.error("✗ 删除原消息失败(RPC) ids=%s: %s", message_ids, e)
    except Exception as e:
        log.exception("✗ 删除原消息失败(未知) ids=%s: %s", message_ids, e)


# =====================================================================
# 七、事件处理器注册
# =====================================================================
def register_handlers(client: TelegramClient) -> None:
    """注册 NewMessage + Album 事件处理器（仅监听收藏夹）。"""

    @client.on(events.NewMessage(chats=["me"]))
    async def on_new_message(event):
        """处理收藏夹新消息。

        不能用 incoming=True：收藏夹消息都是"你发给自己的"，MTProto 的 out 标记
        为 True，incoming=True 会把它们全过滤掉，事件永不触发。
        本进程自己发的进度消息(⏳/✗ 开头)会被下面的守卫跳过，避免自触发回环。
        """
        msg: Message = event.message

        # 守卫：跳过本进程自己发出的进度/错误消息（避免自触发）
        text = msg.message or ""
        if text.startswith(("⏳", "✓", "✗")):
            log.debug("跳过本进程进度消息 msg_id=%s", msg.id)
            return

        # 相册成员跳过（避免与 Album 重复克隆）
        if msg.grouped_id is not None:
            log.debug("跳过相册成员 msg_id=%s grouped_id=%s（交由 Album 处理）",
                      msg.id, msg.grouped_id)
            return

        # 收藏夹链接流程优先：文本含 t.me/tg:// 分享链接即走链接下载上传。
        # 必须在 _has_media 之前：Telegram 会给 t.me 链接生成 WebPage 预览，
        # 此时 _has_media 为 True，会被误当媒体克隆而 send_file 失败。
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
            log.debug("跳过无媒体消息 msg_id=%s", msg.id)
            return

        log.info("▶ 收到单条媒体 msg_id=%s media=%s",
                 msg.id, type(msg.media).__name__)

        ok = await clone_single(client, msg)
        if ok:
            await _delete_originals_if_enabled(client, [msg.id])

    @client.on(events.Album(chats=["me"]))
    async def on_album(event):
        """处理相册：聚合多条 grouped_id 相同的媒体，整体克隆。"""
        msgs: List[Message] = event.messages
        if not msgs:
            return

        log.info("▶ 收到相册 共 %s 条 grouped_id=%s",
                 len(msgs), msgs[0].grouped_id)

        ok = await clone_album(client, msgs)
        if ok:
            ids = [m.id for m in msgs]
            await _delete_originals_if_enabled(client, ids)


# =====================================================================
# 八、启动流程
# =====================================================================
async def init_state(client: TelegramClient) -> None:
    """启动后填充运行期状态：解析目标实体、获取本人 user id。"""
    global TARGET_PEER, MY_USER_ID

    target_id = cfg.TARGET_CHAT_ID
    try:
        TARGET_PEER = await client.get_entity(target_id)
    except ValueError:
        log.info("本地缓存中未找到目标频道，正在同步对话列表...")
        await client.get_dialogs(limit=100)
        try:
            TARGET_PEER = await client.get_entity(target_id)
        except ValueError as e:
            log.critical(
                "✗ 无法解析目标频道 '%s'。\n"
                "  可能原因：\n"
                "  1. 当前账号尚未加入该频道 — 请在 Telegram 客户端中搜索并加入\n"
                "  2. Chat ID 不正确 — 尝试用 @userinfobot 获取正确的 ID\n"
                "  3. .env 中 TARGET_CHAT_ID 误加了引号 — 去掉引号，直接写值\n"
                "  4. Session 过期 — 删除 sessions/ 目录下的 .session 文件后重新登录\n"
                "  原始错误: %s",
                target_id, e,
            )
            raise
    log.info("目标备份频道已解析: %s", TARGET_PEER)

    me = await client.get_me()
    MY_USER_ID = me.id
    log.info("当前账号: id=%s username=%s name=%s",
             me.id, me.username, f"{me.first_name or ''} {me.last_name or ''}".strip())


async def run() -> None:
    """主运行流程：登录 → 初始化 → 注册处理器 → 挂机监听。"""
    log.info("=" * 60)
    log.info("tg-video-keeper 启动")
    log.info("配置摘要:\n%s", cfg.summary())
    log.info("=" * 60)

    client = make_client()
    await client.start()
    log.info("✓ Telegram 登录成功")

    await init_state(client)
    register_handlers(client)

    log.info("✓ 已注册事件处理器，开始监听收藏夹")
    log.info("  - 单条媒体: NewMessage")
    log.info("  - 相册:     Album")
    log.info("  - 克隆后删除原消息: %s", cfg.DELETE_ORIGINAL_FROM_SAVED)
    log.info("守护进程运行中，按 Ctrl+C 退出...")

    await client.run_until_disconnected()


# =====================================================================
# 九、断线重连外层循环
# =====================================================================
async def run_with_reconnect() -> None:
    """带外层重连循环的主入口。

    Telethon 内置 auto_reconnect 处理临时网络断线；
    本循环处理 run_until_disconnected() 抛出的可恢复异常（如 session 短暂失效），
    指数退避重试。认证类错误立即终止不重试。
    """
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            await run()
            log.info("客户端已正常断开，退出。")
            return
        except (AuthKeyError, UserDeactivatedError) as e:
            log.critical("✗ 不可恢复的认证错误，停止重试: %s", e)
            raise
        except KeyboardInterrupt:
            log.info("收到中断信号，退出。")
            return
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log.warning(
                "✗ 第 %s/%s 次运行异常: %s，%ss 后重试",
                attempt, max_attempts, e, wait,
            )
            if attempt >= max_attempts:
                log.critical("✗ 连续 %s 次运行失败，停止。请检查日志后手动处理。", max_attempts)
                raise
            await asyncio.sleep(wait)


# =====================================================================
# 十、子命令
# =====================================================================
async def cmd_check() -> None:
    """健康检查：打印账号信息 + 配置，不监听不发消息。"""
    log.info("=" * 60)
    log.info("tg-video-keeper 健康检查")
    log.info("配置摘要:\n%s", cfg.summary())
    log.info("=" * 60)

    client = make_client()
    await client.start()
    await init_state(client)
    log.info("✓ 健康检查通过：登录正常、目标频道可解析、账号信息已读取。")
    await client.disconnect()


async def cmd_login() -> None:
    """显式登录：生成 session 文件。"""
    log.info("开始登录流程（首次需输入手机号 + 验证码）...")
    client = make_client()
    await client.start()
    me = await client.get_me()
    log.info("✓ 登录成功！账号: id=%s username=%s", me.id, me.username)
    log.info("session 文件已生成: %s/%s.session", cfg.SESSIONS_DIR, cfg.SESSION_NAME)
    await client.disconnect()


async def cmd_clean_forwards(limit: Optional[int] = None,
                            reverse: bool = False) -> None:
    """清洗目标频道中的转发消息：将转发来的媒体/文本重新上传为自主消息。

    原理：遍历频道消息，检测 fwd_from（转发标记），
    对媒体消息用 send_file(file=msg) 引用原文件重新发送，
    对文本消息用 send_message 重新发送，然后删除原转发。
    """
    client = make_client()
    await client.start()
    await init_state(client)

    entity = TARGET_PEER

    log.info("=" * 60)
    log.info("清洗转发消息 — 目标频道: %s", cfg.TARGET_CHAT_ID)
    if limit:
        log.info("（限制: 最多 %s 条）", limit)
    log.info("（顺序: %s）", "从旧到新" if reverse else "从新到旧")
    log.info("=" * 60)

    # 第一步：扫描，收集所有转发消息的 ID 和类型
    log.info("正在扫描频道中的转发消息...")
    fwd_items = []  # [(msg_id, has_media), ...]
    async for msg in client.iter_messages(entity, limit=limit, reverse=reverse):
        if msg.fwd_from is not None:
            fwd_items.append((msg.id, _has_media(msg)))

    total = len(fwd_items)
    if total == 0:
        log.info("未发现转发消息，无需清洗。")
        await client.disconnect()
        return
    log.info("发现 %s 条转发消息，开始逐条清洗（间隔 2s）...", total)

    # 第二步：逐条处理
    processed = 0
    failed = 0
    delay = 2  # 每条间隔秒数，防止触发风控

    for msg_id, has_media in fwd_items:
        # 重新获取最新消息对象（file_reference 可能已变化）
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            log.warning("[SKIP] [%s] 消息已不存在", msg_id)
            continue

        if has_media:
            try:
                sent = await client.send_file(
                    entity,
                    file=msg,
                    caption=msg.message,
                    formatting_entities=msg.entities,
                    silent=True,
                )
                sent_id = getattr(sent, "id", "?")
                await client.delete_messages(entity, [msg_id])
                processed += 1
                log.info("[OK] [%s/%s] [%s] -> [%s] (%s)",
                         processed, total, msg_id, sent_id,
                         type(msg.media).__name__)
            except FloodWaitError as e:
                log.warning("[WARN] 限流 %ss，等待...", e.seconds)
                await asyncio.sleep(e.seconds + 1)
                try:
                    sent = await client.send_file(
                        entity, file=msg, caption=msg.message,
                        formatting_entities=msg.entities, silent=True,
                    )
                    await client.delete_messages(entity, [msg_id])
                    processed += 1
                    log.info("[OK] [%s/%s] [%s] 重试成功", processed, total, msg_id)
                except RPCError as e2:
                    failed += 1
                    log.error("[FAIL] [%s] 重试也失败: %s", msg_id, e2)
            except RPCError as e:
                failed += 1
                log.error("[FAIL] [%s] 处理失败: %s", msg_id, e)
        else:
            try:
                await client.send_message(
                    entity,
                    message=msg.message or "",
                    formatting_entities=msg.entities,
                    silent=True,
                )
                await client.delete_messages(entity, [msg_id])
                processed += 1
                log.info("[OK] [%s/%s] [%s] 文本转发已清洗",
                         processed, total, msg_id)
            except RPCError as e:
                failed += 1
                log.error("[FAIL] [%s] 文本清洗失败: %s", msg_id, e)

        await asyncio.sleep(delay)

    log.info("=" * 60)
    log.info("清洗完成: 处理 %s / 失败 %s / 总计 %s",
             processed, failed, total)

    await client.disconnect()


async def cmd_probe(link: str, upload: bool = False) -> None:
    """探测单条消息链接：解析 → 拉取源消息 → 下载，打印结果。

    默认仅下载到 downloads/ 不上传（安全排查用）；加 --upload 才真正发到 TARGET。
    用于排查"某条链接能不能解析/能不能下（还是受限频道会卡）"。
    """
    log.info("=" * 60)
    log.info("tg-video-keeper 链接探测")
    log.info("配置摘要:\n%s", cfg.summary())
    log.info("=" * 60)

    parsed = parse_tg_link(link)
    if not parsed:
        log.error("✗ 无法解析为消息链接: %s", link)
        return
    log.info("解析结果: peer=%s msg_id=%s", parsed[0], parsed[1])

    client = make_client()
    await client.start()
    await init_state(client)

    try:
        msg = await _resolve_source_message(client, parsed)
        if msg is None:
            return
        log.info("源消息: id=%s media=%s caption=%s",
                 msg.id, type(msg.media).__name__, (msg.message or "")[:50])

        peer, _ = parsed
        sub_dir = os.path.join(cfg.DOWNLOADS_DIR, f"probe_{peer}_{msg.id}")
        path = await _download_to_dir(client, msg, sub_dir)
        if not path:
            log.warning("✗ 下载失败或超时（可能为受限频道）")
            return

        size = os.path.getsize(path) if os.path.exists(path) else 0
        log.info("✓ 下载完成: %s (%.2f MB)", path, size / 1024 / 1024)

        # 走压缩决策（与正式流程一致），便于排查阈值是否合理
        final_path = await _maybe_compress(path, msg)

        if upload:
            ok = await _send_local_file(client, final_path, msg)
            log.info("上传结果: %s", "成功" if ok else "失败")
            _cleanup_local_files([path, final_path], sub_dir)
        else:
            log.info("未启用 --upload，跳过上传。文件保留在: %s", final_path)
            if final_path != path:
                log.info("  （原文件: %s）", path)
    finally:
        await client.disconnect()


# =====================================================================
# 十一、入口
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="tg-video-keeper 私密媒体克隆守护进程")
    parser.add_argument("--login", action="store_true", help="首次登录，生成 session 文件")
    parser.add_argument("--check", action="store_true", help="健康检查：打印账号 + 配置，不监听")
    parser.add_argument("--probe", metavar="LINK",
                        help="探测单条消息链接：解析 + 下载（默认不上传，加 --upload 才发到 TARGET）")
    parser.add_argument("--upload", action="store_true",
                        help="配合 --probe，下载后真正上传到目标频道")
    parser.add_argument("--clean-forwards", action="store_true",
                        help="清洗目标频道中的转发消息，重新上传为自主消息")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="配合 --clean-forwards，限制最多处理 N 条消息")
    parser.add_argument("--reverse", action="store_true",
                        help="配合 --clean-forwards，从旧到新处理（默认从新到旧）")
    args = parser.parse_args()

    try:
        if args.login:
            asyncio.run(cmd_login())
        elif args.check:
            asyncio.run(cmd_check())
        elif args.probe:
            asyncio.run(cmd_probe(args.probe, upload=args.upload))
        elif args.clean_forwards:
            if args.limit:
                log.info("启用限制模式：最多处理 %s 条", args.limit)
            asyncio.run(cmd_clean_forwards(
                limit=args.limit, reverse=args.reverse,
            ))
        else:
            asyncio.run(run_with_reconnect())
    except KeyboardInterrupt:
        log.info("已退出。")
    except (AuthKeyError, UserDeactivatedError) as e:
        log.critical("✗ 账号认证失败，无法继续: %s", e)
        sys.exit(2)
    except RuntimeError as e:
        log.critical("✗ 启动失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
