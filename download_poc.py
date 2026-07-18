#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""POC: 通过 Playwright 打开 Telegram Web，提取受限频道的媒体 stream URL 并下载。

受限频道（禁止保存/转发）的媒体无法通过 MTProto download_media 下载
（服务端直接无视 upload.getFile 请求）。但官方客户端能播放——播放走的是
Telegram CDN 的 HTTP stream 通道，stream URL 存在于 Web 客户端的 DOM 中。

本脚本用 Playwright 启动真实 Chromium，复用本地 Chrome 登录态，导航到
消息链接，提取 <audio>/<video> 的 src，再 HTTP Range 下载。

用法：
    uv run python download_poc.py "https://t.me/username/123" [保存路径]

首次运行 headless=False，在弹出窗口中完成 Telegram Web 登录；
登录态持久化到 pw_data/，后续可改 headless=True 无头运行。

详细背景与失败分析见 docs/restricted-channel-download-attempts.md
"""
import asyncio
import json
import os
import re
import sys
from urllib.parse import unquote

import requests
from playwright.async_api import async_playwright

MSG_LINK = sys.argv[1] if len(sys.argv) > 1 else None
SAVE_TO = sys.argv[2] if len(sys.argv) > 2 else "/tmp/tg_downloaded"

USER_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pw_data")
os.makedirs(USER_DATA, exist_ok=True)

WEB_URL = "https://web.telegram.org/a/"


async def main():
    if not MSG_LINK:
        print('用法: python download_poc.py "https://t.me/username/123" [保存路径]')
        return

    print(f"目标消息: {MSG_LINK}")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            USER_DATA,
            headless=False,  # 首次设 False 以便手动登录；登录后可改 True
            args=["--no-sandbox"],
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()

        # 登录检查：看页面里有没有聊天列表（已登录的标志）
        await page.goto(WEB_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)
        logged_in = await page.evaluate(
            "() => !!document.querySelector('#column-left, .chat-list, [class*=LeftColumn]')"
        )
        if not logged_in:
            print("⚠ 未检测到登录态，请在弹出的浏览器中完成 Telegram Web 登录。")
            print("  登录完成后按 Enter 继续...")
            input()
            await page.goto(WEB_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(5)

        # 解析消息链接 t.me/username/123 或 telegram.me/username/123
        m = re.match(r"(?:https?://)?(?:t\.me|telegram\.me)/([^/]+)/(\d+)", MSG_LINK)
        if not m:
            print(f"无法解析消息链接: {MSG_LINK}")
            await ctx.close()
            return
        chat, msg_id = m.group(1), m.group(2)

        # 导航到消息页面（Telegram Web A 版用 hash 路由）
        msg_url = f"https://web.telegram.org/a/#{chat}/{msg_id}"
        print(f"打开消息页面: {msg_url}")
        await page.goto(msg_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(5)  # 等媒体元素加载

        # 提取 audio/video 的 stream URL
        stream_url = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('audio, video')) {
                if (el.src && el.src.includes('stream/')) {
                    return el.src;
                }
            }
            return null;
        }""")

        if not stream_url:
            print("[FAIL] 未找到媒体 stream URL。可能：消息无音视频 / 未加载完 / 需滚动定位")
            await ctx.close()
            return

        print(f"[OK] stream URL: {stream_url[:120]}...")

        # 解析文件名/MIME
        try:
            info_raw = stream_url.split("stream/")[1].split("?")[0]
            info = json.loads(unquote(info_raw))
            file_name = info.get("fileName", "download")
            mime_type = info.get("mimeType", "application/octet-stream")
            print(f"  文件名: {file_name} / MIME: {mime_type}")
        except Exception:
            file_name = "download"
            print("  无法解析文件名，使用默认名")

        # HTTP 下载（伪装 Firefox UA + Range）
        print("正在下载...")
        headers = {
            "Referer": "https://web.telegram.org/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
        }
        resp = requests.get(stream_url, headers=headers, stream=True, timeout=600)
        resp.raise_for_status()

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        output = os.path.join(SAVE_TO, file_name) if os.path.isdir(SAVE_TO) else SAVE_TO
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

        with open(output, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    print(f"\r  进度: {downloaded/total*100:.1f}% "
                          f"({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)", end="")
                else:
                    print(f"\r  已下载: {downloaded/1024/1024:.1f} MB", end="")
        print(f"\n[OK] 下载完成: {output}")

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
