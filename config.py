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
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")  # 收藏夹链接下载的临时文件目录

# 确保目录存在
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

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

# 收藏夹链接下载的整体超时（秒）。大文件慢链路下需留足时间；
# 配合 LINK_STALL_TIMEOUT 防止真受限频道挂起时白等。默认 3600s（1h）。
LINK_DOWNLOAD_TIMEOUT: int = int(os.getenv("LINK_DOWNLOAD_TIMEOUT", "3600"))

# 下载停滞超时（秒）：进度回调超过此秒无任何进展即判定挂起/断流，取消跳过。
# 受限频道(noforwards)的 download_media 进度回调永不触发，靠此快速跳出。默认 60s。
LINK_STALL_TIMEOUT: int = int(os.getenv("LINK_STALL_TIMEOUT", "60"))

# 上传后是否保留本地临时文件（用于用 ffmpeg/ffprobe 核对视频元数据/可播放性）。
# true=保留本地文件与下载子目录，不清理；false=上传后清理（默认，保持干净）。
KEEP_LOCAL_FILE_AFTER_UPLOAD: bool = os.getenv("KEEP_LOCAL_FILE_AFTER_UPLOAD", "false").lower() == "true"


# --------------------- 视频压缩 ---------------------
# 下载后是否按码率/时长自动压缩再上传（仅对视频生效）
COMPRESS_VIDEO: bool = os.getenv("COMPRESS_VIDEO", "true").lower() == "true"

# 压缩触发阈值：源码率(Mbps)超过此值才压；低于则认为已高效、跳过。
# 例：200MB/1min≈26Mbps(压)，200MB/30min≈0.9Mbps(跳过)。
COMPRESS_BITRATE_THRESHOLD: int = int(os.getenv("COMPRESS_BITRATE_THRESHOLD", "6"))

# 甜点编码参数：H.264 CRF + 码率上限 + 音频码率 + 预设
COMPRESS_CRF: int = int(os.getenv("COMPRESS_CRF", "24"))
COMPRESS_MAXRATE: str = os.getenv("COMPRESS_MAXRATE", "5M")
COMPRESS_BUFSIZE: str = os.getenv("COMPRESS_BUFSIZE", "8M")
COMPRESS_PRESET: str = os.getenv("COMPRESS_PRESET", "veryfast")
COMPRESS_AUDIO_BITRATE: str = os.getenv("COMPRESS_AUDIO_BITRATE", "128k")

# 小于此大小(MB)不压缩(不值得)
COMPRESS_MIN_SIZE_MB: int = int(os.getenv("COMPRESS_MIN_SIZE_MB", "20"))


def summary() -> str:
    """生成配置摘要（脱敏），用于 --check 输出。"""
    return (
        f"API_ID={API_ID}\n"
        f"API_HASH={'***' + API_HASH[-4:] if API_HASH else '(空)'}\n"
        f"TARGET_CHAT_ID={TARGET_CHAT_ID}\n"
        f"DELETE_ORIGINAL_FROM_SAVED={DELETE_ORIGINAL_FROM_SAVED}\n"
        f"SESSION_NAME={SESSION_NAME}\n"
        f"SILENT_SEND={SILENT_SEND}\n"
        f"LINK_DOWNLOAD_TIMEOUT={LINK_DOWNLOAD_TIMEOUT}\n"
        f"LINK_STALL_TIMEOUT={LINK_STALL_TIMEOUT}\n"
        f"KEEP_LOCAL_FILE_AFTER_UPLOAD={KEEP_LOCAL_FILE_AFTER_UPLOAD}\n"
        f"COMPRESS_VIDEO={COMPRESS_VIDEO}\n"
        f"COMPRESS_BITRATE_THRESHOLD={COMPRESS_BITRATE_THRESHOLD}\n"
        f"COMPRESS_CRF={COMPRESS_CRF} / MAXRATE={COMPRESS_MAXRATE} / PRESET={COMPRESS_PRESET}\n"
        f"LOG_LEVEL={LOG_LEVEL}"
    )
