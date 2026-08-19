# -*- coding: utf-8 -*-
"""
test_x_link_parsing.py — parse_x_link 纯函数单测

覆盖 x.com/twitter.com 推文链接识别:带 query、无 scheme、大小写、
x.com/i/status 结构、嵌入正文、多链接取首条;profile/搜索/t.co/无效输入不命中。

运行:uv run python -m pytest tests/test_x_link_parsing.py -v
"""
import os
import sys

# 让测试能 import 项目根的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 注入最小环境变量，使 config 模块能加载
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import x_downloader  # noqa: E402


class TestHit:
    def test_x_dot_com(self):
        assert x_downloader.parse_x_link(
            "https://x.com/elonmusk/status/1234567890123456789"
        ) == "https://x.com/elonmusk/status/1234567890123456789"

    def test_twitter_dot_com(self):
        assert x_downloader.parse_x_link(
            "https://twitter.com/foo_bar/status/42"
        ) == "https://twitter.com/foo_bar/status/42"

    def test_with_query(self):
        assert x_downloader.parse_x_link(
            "https://x.com/user/status/7?s=20"
        ) == "https://x.com/user/status/7?s=20"

    def test_no_scheme(self):
        assert x_downloader.parse_x_link(
            "x.com/foo/status/1"
        ) == "https://x.com/foo/status/1"

    def test_case_insensitive(self):
        assert x_downloader.parse_x_link(
            "https://X.COM/Foo/Status/5"
        ) == "https://X.COM/Foo/Status/5"

    def test_i_status_structure(self):
        # 新版 UI 结构:x.com/i/status/<id>
        assert x_downloader.parse_x_link(
            "https://x.com/i/status/88"
        ) == "https://x.com/i/status/88"

    def test_embedded_in_text(self):
        text = "看这个视频 https://x.com/a/status/1 好帅"
        assert x_downloader.parse_x_link(text) == "https://x.com/a/status/1"

    def test_multiple_takes_first(self):
        text = "https://x.com/first/status/1 再看 https://x.com/second/status/2"
        assert x_downloader.parse_x_link(text) == "https://x.com/first/status/1"

    def test_punctuation_after_url(self):
        # 链接后紧跟中文标点不应被吞进 URL
        assert x_downloader.parse_x_link(
            "https://x.com/a/status/1，好看"
        ) == "https://x.com/a/status/1"

    def test_video_deeplink_suffix(self):
        # 多视频推文的单视频 deep-link:/status/<id>/video/<n> 后缀必须保留
        assert x_downloader.parse_x_link(
            "https://x.com/hhc1030/status/2087771668043468988/video/1"
        ) == "https://x.com/hhc1030/status/2087771668043468988/video/1"

    def test_video_deeplink_with_query(self):
        assert x_downloader.parse_x_link(
            "https://x.com/user/status/9/video/2?s=20"
        ) == "https://x.com/user/status/9/video/2?s=20"


class TestMiss:
    def test_empty(self):
        assert x_downloader.parse_x_link("") is None
        assert x_downloader.parse_x_link(None) is None

    def test_no_link(self):
        assert x_downloader.parse_x_link("普通文本，没有链接") is None

    def test_profile_link(self):
        # profile 不是推文链接
        assert x_downloader.parse_x_link("https://x.com/elonmusk") is None

    def test_search_link(self):
        assert x_downloader.parse_x_link("https://x.com/search?q=video") is None

    def test_tco_short_link(self):
        # t.co 短链不处理(需额外展开请求,超出范围)
        assert x_downloader.parse_x_link("https://t.co/abc123") is None

    def test_non_numeric_status_id(self):
        assert x_downloader.parse_x_link("https://x.com/user/status/abc") is None

    def test_tg_link_not_affected(self):
        # t.me 链接不触发 X 逻辑
        assert x_downloader.parse_x_link("https://t.me/chan/1") is None
