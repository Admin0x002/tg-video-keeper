# -*- coding: utf-8 -*-
"""
config.py — 配置加载层

从 .env 读取所有配置；缺失必填项则抛 RuntimeError，绝不使用占位值默认运行。
"""
import os

from dotenv import load_dotenv

# 加载 .env（若不存在则用环境变量，方便 Docker / systemd 注入）
load_dotenv()

# --------------------- 路径 ---------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# 确保目录存在
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# --------------------- 必填项校验 ---------------------
def _strip_quotes(s: str) -> str:
    """去除 .env 中可能误加的引号（单引号/双引号）。"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _require(name: str) -> str:
    """读取必填环境变量，缺失即抛错。"""
    v = os.getenv(name)
    if not v:
        raise RuntimeError(
            f"缺少必填环境变量 {name}。请执行 `cp .env.example .env` 并填写后再运行。"
        )
    return _strip_quotes(v)


API_ID: int = int(_require("API_ID"))
API_HASH: str = _require("API_HASH")
TARGET_CHAT_ID: int = int(_require("TARGET_CHAT_ID"))  # 转 int，Telethon 按 ID 查缓存；字符串会被当名称搜索


# --------------------- 行为开关 ---------------------
# 克隆后是否删除收藏夹原消息（保持收藏夹干净）
DELETE_ORIGINAL_FROM_SAVED: bool = os.getenv("DELETE_ORIGINAL_FROM_SAVED", "true").lower() == "true"

# Session 文件名（不含扩展名，Telethon 自动加 .session）
SESSION_NAME: str = os.getenv("SESSION_NAME", "keeper")

# 日志级别
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# 克隆时是否静默（不触发目标频道通知）
SILENT_SEND: bool = os.getenv("SILENT_SEND", "true").lower() == "true"


def summary() -> str:
    """生成配置摘要（脱敏），用于 --check 输出。"""
    return (
        f"API_ID={API_ID}\n"
        f"API_HASH={'***' + API_HASH[-4:] if API_HASH else '(空)'}\n"
        f"TARGET_CHAT_ID={TARGET_CHAT_ID}\n"
        f"DELETE_ORIGINAL_FROM_SAVED={DELETE_ORIGINAL_FROM_SAVED}\n"
        f"SESSION_NAME={SESSION_NAME}\n"
        f"SILENT_SEND={SILENT_SEND}\n"
        f"LOG_LEVEL={LOG_LEVEL}"
    )
