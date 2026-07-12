#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg-video-keeper — 私密 Telegram 媒体克隆守护进程

=====================================================================
工作逻辑（务必先读 AGENTS.md §6）
=====================================================================
1. 监听来源聊天（收藏夹 'me' 和/或私密频道）的新消息。
2. 检测到媒体（视频/图片/文档/音频）→ 用 send_message(file=msg) 克隆到目标备份频道，
   生成**不带 "转发自" 头部**的独立消息；Telegram 全局去重，原频道被封后备份仍可访问。
3. 来源=收藏夹 且 DELETE_ORIGINAL_FROM_SAVED=true → 克隆后删除原消息，保持收藏夹干净。
4. 相册（同一 grouped_id 的多条媒体）由 events.Album 聚合后整体克隆，
   NewMessage 中按 grouped_id 跳过相册成员，避免重复克隆。

安全：仅处理配置的来源 + 用户白名单，不对外提供任何服务。
=====================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import List, Optional

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyError,
    FloodWaitError,
    RPCError,
    UserDeactivatedError,
)
from telethon.tl.custom import Message
from telethon.tl.types import MessageMediaEmpty

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
# 目标频道的 InputPeer 缓存（避免每条消息都解析实体）
TARGET_PEER = None
# 本人的 user id（用于判定消息是否来自收藏夹）
MY_USER_ID: Optional[int] = None


def _is_from_saved_messages(event_or_chat_id) -> bool:
    """判定消息来源是否为收藏夹（Saved Messages）。

    Telethon 中，收藏夹对话的 chat_id 等于本人 user id。
    """
    if MY_USER_ID is None:
        return False
    chat_id = getattr(event_or_chat_id, "chat_id", event_or_chat_id)
    return chat_id == MY_USER_ID


# =====================================================================
# 四、安全校验
# =====================================================================
def _source_allowed(chat_id) -> bool:
    """来源聊天是否在白名单内。'me' 收藏夹由 MY_USER_ID 兜底。"""
    if chat_id is None:
        return False
    for sid in cfg.SOURCE_CHAT_IDS:
        if sid == chat_id:
            return True
        # 'me' 在配置中是字符串，实际 chat_id 是本人 user id
        if sid == "me" and _is_from_saved_messages(chat_id):
            return True
    return False


def _user_allowed(user_id) -> bool:
    """发送者是否在用户白名单内。白名单为空时放行（仍受来源白名单保护）。"""
    if not cfg.ALLOWED_USER_IDS:
        return True
    return user_id in cfg.ALLOWED_USER_IDS


def _has_media(message: Message) -> bool:
    """消息是否携带媒体（视频/图片/文档/音频等）。纯文本/空媒体返回 False。"""
    return message.media is not None and not isinstance(message.media, MessageMediaEmpty)


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
            file=message,                           # Message 对象 → Telethon 提取媒体
            caption=message.message,                # 保留原 caption 文本（可为 None）
            formatting_entities=message.entities,   # 保留原格式化实体（粗体/链接等）
            silent=cfg.SILENT_SEND,
        )
        sent_id = getattr(sent, "id", "?")
        log.info(
            "✓ 单条克隆成功  src_msg_id=%s → target_msg_id=%s  media=%s",
            message.id, sent_id, type(message.media).__name__,
        )
        return True
    except FloodWaitError as e:
        # 必须遵守 Telegram 限流，不可绕过
        log.warning("⚠ 限流，等待 %ss 后重试 (msg_id=%s)", e.seconds, message.id)
        await asyncio.sleep(e.seconds + 1)
        return await clone_single(client, message)
    except RPCError as e:
        log.error("✗ 克隆失败(RPC) msg_id=%s: %s", message.id, e)
        return False
    except Exception as e:  # noqa: BLE001
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
            file=messages,                # 消息列表 → 作为相册发送
            caption=captions,
            silent=cfg.SILENT_SEND,
        )
        # 相册返回 list，取首条 id
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
    except Exception as e:  # noqa: BLE001
        log.exception("✗ 相册克隆失败(未知): %s", e)
        return False


# =====================================================================
# 六、收尾删除（仅收藏夹来源）
# =====================================================================
async def maybe_delete_originals(
    client: TelegramClient,
    source_chat_id,
    message_ids: List[int],
) -> None:
    """若来源是收藏夹且配置开启删除，则删除原消息。

    私密频道来源保留原消息（用户未要求删除）。
    """
    if not cfg.DELETE_ORIGINAL_FROM_SAVED:
        return
    if not _is_from_saved_messages(source_chat_id):
        return
    try:
        # 删除收藏夹中的消息；收藏夹实体用 'me'
        await client.delete_messages("me", message_ids)
        log.info("✓ 已删除收藏夹原消息  ids=%s", message_ids)
    except RPCError as e:
        log.error("✗ 删除原消息失败(RPC) ids=%s: %s", message_ids, e)
    except Exception as e:  # noqa: BLE001
        log.exception("✗ 删除原消息失败(未知) ids=%s: %s", message_ids, e)


# =====================================================================
# 七、事件处理器注册
# =====================================================================
def register_handlers(client: TelegramClient) -> None:
    """注册 NewMessage + Album 事件处理器。"""

    @client.on(events.NewMessage(chats=cfg.SOURCE_CHAT_IDS, incoming=True))
    async def on_new_message(event):  # noqa: ANN001
        """处理单条新消息。相册成员跳过（交给 Album 处理）。"""
        msg: Message = event.message

        # 安全校验
        if not _source_allowed(event.chat_id):
            return
        if not _user_allowed(event.from_id):
            log.warning("⚠ 拒绝非白名单用户: from_id=%s", event.from_id)
            return

        # 相册成员跳过（避免与 Album 重复克隆）
        if msg.grouped_id is not None:
            log.debug("跳过相册成员 msg_id=%s grouped_id=%s（交由 Album 处理）",
                      msg.id, msg.grouped_id)
            return

        # 纯文本无媒体 → 不处理
        if not _has_media(msg):
            log.debug("跳过无媒体消息 msg_id=%s", msg.id)
            return

        log.info("▶ 收到单条媒体 msg_id=%s chat=%s media=%s",
                 msg.id, event.chat_id, type(msg.media).__name__)

        ok = await clone_single(client, msg)
        if ok:
            await maybe_delete_originals(client, event.chat_id, [msg.id])

    @client.on(events.Album(chats=cfg.SOURCE_CHAT_IDS))
    async def on_album(event):  # noqa: ANN001
        """处理相册：聚合多条 grouped_id 相同的媒体，整体克隆。"""
        msgs: List[Message] = event.messages

        # 安全校验（相册事件取首条消息的来源）
        if not msgs:
            return
        if not _source_allowed(msgs[0].chat_id):
            return

        log.info("▶ 收到相album 共 %s 条 grouped_id=%s",
                 len(msgs), msgs[0].grouped_id)

        ok = await clone_album(client, msgs)
        if ok:
            ids = [m.id for m in msgs]
            await maybe_delete_originals(client, msgs[0].chat_id, ids)


# =====================================================================
# 八、启动流程
# =====================================================================
async def init_state(client: TelegramClient) -> None:
    """启动后填充运行期状态：解析目标实体、获取本人 user id。"""
    global TARGET_PEER, MY_USER_ID

    # 解析目标频道为 InputPeer 并缓存（后续发送不再重复解析）
    TARGET_PEER = await client.get_input_entity(cfg.TARGET_CHAT_ID)
    log.info("目标备份频道已解析: %s", TARGET_PEER)

    me = await client.get_me()
    MY_USER_ID = me.id
    log.info("当前账号: id=%s username=%s name=%s",
             me.id, me.username, f"{me.first_name or ''} {me.last_name or ''}".strip())
    log.info("收藏夹(Saved Messages) chat_id = %s", MY_USER_ID)


async def run() -> None:
    """主运行流程：登录 → 初始化 → 注册处理器 → 挂机监听。"""
    log.info("=" * 60)
    log.info("tg-video-keeper 启动")
    log.info("配置摘要:\n%s", cfg.summary())
    log.info("=" * 60)

    client = make_client()

    # 首次运行会交互式要求输入手机号 + 验证码；后续自动用 session 文件
    await client.start()
    log.info("✓ Telegram 登录成功")

    await init_state(client)
    register_handlers(client)

    log.info("✓ 已注册事件处理器，开始监听来源聊天: %s", cfg.SOURCE_CHAT_IDS)
    log.info("  - 单条媒体: NewMessage")
    log.info("  - 相册:     Album")
    log.info("  - 收藏夹来源自动删除原消息: %s", cfg.DELETE_ORIGINAL_FROM_SAVED)
    log.info("守护进程运行中，按 Ctrl+C 退出...")

    # run_until_disconnected 阻塞直到断开；auto_reconnect 会自动处理临时断线
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
            # 正常退出（如收到停止信号）
            log.info("客户端已正常断开，退出。")
            return
        except (AuthKeyError, UserDeactivatedError) as e:
            # 不可恢复：session 失效或账号被封
            log.critical("✗ 不可恢复的认证错误，停止重试: %s", e)
            raise
        except KeyboardInterrupt:
            log.info("收到中断信号，退出。")
            return
        except Exception as e:  # noqa: BLE001
            # 可恢复异常：指数退避重试
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


# =====================================================================
# 十一、入口
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="tg-video-keeper 私密媒体克隆守护进程")
    parser.add_argument(
        "--login", action="store_true", help="首次登录，生成 session 文件"
    )
    parser.add_argument(
        "--check", action="store_true", help="健康检查：打印账号 + 配置，不监听"
    )
    args = parser.parse_args()

    try:
        if args.login:
            asyncio.run(cmd_login())
        elif args.check:
            asyncio.run(cmd_check())
        else:
            asyncio.run(run_with_reconnect())
    except KeyboardInterrupt:
        log.info("已退出。")
    except (AuthKeyError, UserDeactivatedError) as e:
        log.critical("✗ 账号认证失败，无法继续: %s", e)
        sys.exit(2)
    except RuntimeError as e:
        # 配置缺失等
        log.critical("✗ 启动失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
