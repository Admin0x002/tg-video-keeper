# -*- coding: utf-8 -*-
"""
test_compress.py — 压缩决策纯函数单测

覆盖 source_bitrate_mbps / should_compress / skip_reason。
不调用 ffmpeg,只测纯逻辑(用户举的例:200MB/1min 压,200MB/30min 跳过)。

运行：python -m pytest tests/test_compress.py -v
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import compress as cmp  # noqa: E402


def _probe(size_mb: float, duration: float, w=1920, h=1080) -> cmp.MediaProbe:
    """size_mb 按十进制 MB(1e6 字节),与日常"200MB"口径一致。"""
    return cmp.MediaProbe(
        path="/fake", size_bytes=int(size_mb * 1_000_000),
        duration=duration, width=w, height=h, video_codec="h264", is_video=True,
    )


class TestSourceBitrate:
    def test_one_minute_200mb(self):
        # 200MB / 60s ≈ 26.7 Mbps
        assert abs(cmp.source_bitrate_mbps(_probe(200, 60)) - 26.67) < 0.1

    def test_thirty_minute_200mb(self):
        # 200MB / 1800s ≈ 0.89 Mbps
        assert abs(cmp.source_bitrate_mbps(_probe(200, 1800)) - 0.89) < 0.05

    def test_zero_duration(self):
        assert cmp.source_bitrate_mbps(_probe(200, 0)) == 0.0


class TestShouldCompress:
    def test_bloated_compresses(self):
        # 200MB/1min ≈ 26Mbps → 压
        assert cmp.should_compress(_probe(200, 60)) is True

    def test_efficient_skips(self):
        # 200MB/30min ≈ 0.9Mbps → 跳过
        assert cmp.should_compress(_probe(200, 1800)) is False

    @mock.patch.object(cmp.cfg, "COMPRESS_BITRATE_THRESHOLD", 6)
    def test_borderline_below_threshold_skips(self):
        # 5Mbps 略低于阈值 6 → 跳过
        # 5Mbps = 5e6 bps;60s → 5e6*60/8 = 37.5MB
        with mock.patch.object(cmp.cfg, "COMPRESS_MIN_SIZE_MB", 20):
            assert cmp.should_compress(_probe(37.5, 60)) is False

    @mock.patch.object(cmp.cfg, "COMPRESS_BITRATE_THRESHOLD", 6)
    def test_borderline_above_threshold_compresses(self):
        # 7Mbps > 6 → 压 ; 7Mbps*60/8 = 52.5MB
        with mock.patch.object(cmp.cfg, "COMPRESS_MIN_SIZE_MB", 20):
            assert cmp.should_compress(_probe(52.5, 60)) is True

    @mock.patch.object(cmp.cfg, "COMPRESS_MIN_SIZE_MB", 20)
    def test_too_small_skips(self):
        # 10MB(<20MB)即使码率高也跳过
        assert cmp.should_compress(_probe(10, 1)) is False

    def test_unknown_duration_skips(self):
        assert cmp.should_compress(_probe(500, 0)) is False

    def test_non_video_skips(self):
        p = cmp.MediaProbe("/x", 300 * 1024 * 1024, 60, 0, 0, "", is_video=False)
        assert cmp.should_compress(p) is False


class TestSkipReason:
    def test_efficient_reason(self):
        r = cmp.skip_reason(_probe(200, 1800))
        assert "码率已高效" in r and "0.9" in r

    def test_small_reason(self):
        assert "文件过小" in cmp.skip_reason(_probe(10, 60))

    def test_unknown_duration_reason(self):
        assert "时长未知" in cmp.skip_reason(_probe(500, 0))

    def test_non_video_reason(self):
        p = cmp.MediaProbe("/x", 300 * 1024 * 1024, 60, 0, 0, "", is_video=False)
        assert "非视频" in cmp.skip_reason(p)


class TestProgressFormat:
    """SavedMessagesProgress._format 是纯静态方法，无需 client。"""
    def test_with_pct_and_sizes(self):
        from keeper import SavedMessagesProgress as P
        assert P._format("⏳ 下载中", 42, 50.0, 120.0) == "⏳ 下载中 42% (50.0/120.0MB)"

    def test_with_pct_only(self):
        from keeper import SavedMessagesProgress as P
        assert P._format("⏳ 下载中", 7, None, None) == "⏳ 下载中 7%"

    def test_label_only(self):
        from keeper import SavedMessagesProgress as P
        assert P._format("⏳ 正在压缩...", None, None, None) == "⏳ 正在压缩..."


class TestShortTitle:
    """keeper._short_title：折叠换行 + 截前 6 字符。用 fake msg，无需 Telethon。"""
    def _msg(self, file_name=None, message=""):
        from types import SimpleNamespace as NS
        media = NS()  # type().__name__ == "SimpleNamespace"
        m = NS(message=message, media=media)
        m.file = NS(name=file_name) if file_name is not None else None
        return m

    def test_filename_truncated_to_6(self):
        from keeper import _short_title
        # "my long video.mp4" → 前 6 = "my lon"
        assert _short_title(self._msg(file_name="my long video.mp4", message="cap")) == "my lon"

    def test_caption_newlines_collapsed(self):
        from keeper import _short_title
        m = self._msg(file_name=None, message="line1\nline2 long")
        assert _short_title(m) == "line1 "  # "line1 line2 long"[:6]

    def test_short_kept_full(self):
        from keeper import _short_title
        assert _short_title(self._msg(file_name="abc", message="")) == "abc"

    def test_fallback_to_media_type(self):
        from keeper import _short_title
        # 无文件名无文本 → 媒体类型名前 6
        assert _short_title(self._msg(file_name=None, message="")) == "Simple"
