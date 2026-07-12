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
def _require(name: str) -> str:
    """读取必填环境变量，缺失即抛错。"""
    v = os.getenv(name)
    if not v:
        raise RuntimeError(
            f"缺少必填环境变量 {name}。请执行 `cp .env.example .env` 并填写后再运行。"
        )
    return v


API_ID: int = int(_require("API_ID"))
API_HASH: str = _require("API_HASH")
TARGET_CHAT_ID: str = _require("TARGET_CHAT_ID")  # 保持字符串，由 Telethon 解析


# --------------------- 来源聊天（监听白名单） ---------------------
def _parse_chat_id(s: str):
    """把配置项解析为 Telethon 可识别的 chat 标识：数字则转 int，否则保留字符串（'me'=收藏夹）。"""
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return s


_source_raw = os.getenv("SOURCE_CHATS", "me")
SOURCE_CHAT_IDS = [x for x in (_parse_chat_id(s) for s in _source_raw.split(",")) if x is not None]
if not SOURCE_CHAT_IDS:
    raise RuntimeError("SOURCE_CHATS 不能为空，至少需要配置一个来源（'me' 或频道 ID）")

# --------------------- 用户白名单 ---------------------
_allowed_raw = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set = set()
for s in _allowed_raw.split(","):
    s = s.strip()
    if s:
        try:
            ALLOWED_USER_IDS.add(int(s))
        except ValueError:
            pass  # 忽略非法值

# --------------------- 行为开关 ---------------------
# 来源为收藏夹时，克隆后是否删除原转发消息（保持收藏夹干净）
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
        f"SOURCE_CHAT_IDS={SOURCE_CHAT_IDS}\n"
        f"ALLOWED_USER_IDS={ALLOWED_USER_IDS or '(不限，依赖来源白名单)'}\n"
        f"DELETE_ORIGINAL_FROM_SAVED={DELETE_ORIGINAL_FROM_SAVED}\n"
        f"SESSION_NAME={SESSION_NAME}\n"
        f"SILENT_SEND={SILENT_SEND}\n"
        f"LOG_LEVEL={LOG_LEVEL}"
    )
