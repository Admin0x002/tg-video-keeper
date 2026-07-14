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
TARGET_PEER = None                      # 目标频道实体缓存
MY_USER_ID: Optional[int] = None        # 本人 user id（用于判定收藏夹）


# =====================================================================
# 四、工具函数
# =====================================================================
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

    @client.on(events.NewMessage(chats=["me"], incoming=True))
    async def on_new_message(event):
        """处理单条新消息。相册成员跳过（交给 Album 处理）。"""
        msg: Message = event.message

        # 相册成员跳过（避免与 Album 重复克隆）
        if msg.grouped_id is not None:
            log.debug("跳过相册成员 msg_id=%s grouped_id=%s（交由 Album 处理）",
                      msg.id, msg.grouped_id)
            return

        # 纯文本无媒体 → 不处理
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


# =====================================================================
# 十一、入口
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="tg-video-keeper 私密媒体克隆守护进程")
    parser.add_argument("--login", action="store_true", help="首次登录，生成 session 文件")
    parser.add_argument("--check", action="store_true", help="健康检查：打印账号 + 配置，不监听")
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
        log.critical("✗ 启动失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
