# -*- coding: utf-8 -*-
"""
test_x_download.py — download_x_video 单测(monkeypatch yt_dlp)

覆盖:无视频推文、下载成功、extract_info 异常、停滞超时、整体超时。
不依赖真实网络:monkeypatch 替换 x_downloader.yt_dlp.YoutubeDL。

运行:uv run python -m pytest tests/test_x_download.py -v
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import config as cfg  # noqa: E402
import x_downloader as xdl  # noqa: E402


class FakeProgressSink:
    """记录 set_text / report 调用的假进度接收器。"""

    def __init__(self):
        self.texts = []
        self.reports = []

    async def set_text(self, text):
        self.texts.append(text)

    def report(self, label, pct=None, received_mb=None, total_mb=None):
        self.reports.append((label, pct))


class FakeYDL:
    """脚本化的假 YoutubeDL。类属性控制行为:

    - info: extract_info(download=False) 返回的 dict
    - info_raise: 非 None 时第一阶段直接抛它
    - dl_delay: 第二阶段 sleep 秒数(模拟慢下载)
    - dl_file: 第二阶段应"下载"出的文件路径(测试需预先创建)
    """

    info = {"title": "test video", "vcodec": "h264"}
    info_raise = None
    dl_delay = 0.0
    dl_file = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if not download:
            if FakeYDL.info_raise is not None:
                raise FakeYDL.info_raise
            return FakeYDL.info
        if FakeYDL.dl_delay:
            time.sleep(FakeYDL.dl_delay)
        if FakeYDL.dl_file is not None:
            return {
                "title": "test video",
                "requested_downloads": [{"filepath": FakeYDL.dl_file}],
            }
        return {"title": "test video"}


def _run(coro):
    return asyncio.run(coro)


def _reset():
    FakeYDL.info = {"title": "test video", "vcodec": "h264"}
    FakeYDL.info_raise = None
    FakeYDL.dl_delay = 0.0
    FakeYDL.dl_file = None


class TestNoVideo:
    def test_no_video_returns_none(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.info = {"title": "仅图片", "vcodec": "none", "formats": []}
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path / "out"), sink))
        assert path is None
        assert any("没有可下载的视频" in t for t in sink.texts)


class TestSuccess:
    def test_download_returns_path(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        out = tmp_path / "out"
        out.mkdir()
        video = out / "video.mp4"
        video.write_bytes(b"fake-video-data")
        FakeYDL.dl_file = str(video)
        sink = FakeProgressSink()
        result = _run(xdl.download_x_video("https://x.com/a/status/1", str(out), sink))
        assert result == (str(video), "test video")
        assert any("准备下载" in t for t in sink.texts)


class TestInfoError:
    def test_extract_exception_returns_none(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.info_raise = RuntimeError("Unable to extract")
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(tmp_path / "out"), sink))
        assert path is None
        assert any("获取视频信息失败" in t for t in sink.texts)


class TestStall:
    def test_stall_timeout_aborts(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(cfg, "X_STALL_TIMEOUT", 0.1)
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.dl_delay = 0.4  # 第二阶段挂起,无进度回调
        out = tmp_path / "out"
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(out), sink))
        assert path is None
        assert any("停滞" in t for t in sink.texts)
        # 停滞取消后 out 目录应被 rmtree 清理(目录整体不存在)
        assert not out.exists()


class TestOverallTimeout:
    def test_overall_timeout_aborts(self, monkeypatch, tmp_path):
        _reset()
        monkeypatch.setattr(cfg, "X_DOWNLOAD_TIMEOUT", 0.3)
        monkeypatch.setattr(cfg, "X_STALL_TIMEOUT", 60)  # 停滞不触发,靠整体超时
        monkeypatch.setattr(xdl.yt_dlp, "YoutubeDL", FakeYDL)
        FakeYDL.dl_delay = 0.6
        out = tmp_path / "out"
        sink = FakeProgressSink()
        path = _run(xdl.download_x_video("https://x.com/a/status/1", str(out), sink))
        assert path is None
        assert any("超时" in t for t in sink.texts)
        assert not out.exists()
