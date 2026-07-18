# -*- coding: utf-8 -*-
"""
test_link_parsing.py — parse_tg_link 纯函数单测

覆盖收藏夹链接解析：t.me 公开/私有、telegram.me、tg:// 自定义 scheme、
带 query、嵌入正文、多链接取首条、邀请链接与无效输入。

运行：python -m pytest tests/test_link_parsing.py -v
"""
import os
import sys

# 让测试能 import 项目根的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 注入最小环境变量，使 config 模块能加载
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import keeper  # noqa: E402


class TestPublicLink:
    def test_https_public(self):
        assert keeper.parse_tg_link("https://t.me/somechannel/123") == ("somechannel", 123)

    def test_no_scheme(self):
        assert keeper.parse_tg_link("t.me/somechannel/42") == ("somechannel", 42)

    def test_telegram_me(self):
        assert keeper.parse_tg_link("https://telegram.me/chan/7") == ("chan", 7)

    def test_case_insensitive_domain(self):
        assert keeper.parse_tg_link("https://T.Me/Chan/5") == ("Chan", 5)

    def test_with_query_single(self):
        # 相册单条 ?single 后缀
        assert keeper.parse_tg_link("https://t.me/chan/9?single") == ("chan", 9)

    def test_with_comment_query(self):
        assert keeper.parse_tg_link("https://t.me/chan/3?comment=88") == ("chan", 3)


class TestPrivateLink:
    def test_private_c_link(self):
        peer, mid = keeper.parse_tg_link("https://t.me/c/1234567890/5")
        assert peer == -1001234567890
        assert mid == 5

    def test_private_no_scheme(self):
        peer, _ = keeper.parse_tg_link("t.me/c/9876543210/77")
        assert peer == -1009876543210

    def test_private_with_topic(self):
        # /c/<channel>/<topic_id>/<msg_id> → 取首尾数字
        peer, mid = keeper.parse_tg_link("https://t.me/c/1234567890/12/34")
        assert peer == -1001234567890
        assert mid == 34


class TestTgScheme:
    def test_resolve(self):
        assert keeper.parse_tg_link("tg://resolve?domain=chan&post=15") == ("chan", 15)

    def test_privatepost(self):
        peer, mid = keeper.parse_tg_link("tg://privatepost?channel=1234567890&post=8")
        assert peer == -1001234567890
        assert mid == 8


class TestEmbeddingAndMulti:
    def test_embedded_in_text(self):
        text = "看这个视频 https://t.me/chan/250 真不错"
        assert keeper.parse_tg_link(text) == ("chan", 250)

    def test_multiple_takes_first(self):
        text = "https://t.me/first/1 再看 https://t.me/second/2"
        assert keeper.parse_tg_link(text) == ("first", 1)

    def test_contains_tg_link_true(self):
        assert keeper.contains_tg_link("hello t.me/chan/1") is True


class TestInvalid:
    def test_empty(self):
        assert keeper.parse_tg_link("") is None
        assert keeper.parse_tg_link(None) is None

    def test_no_link(self):
        assert keeper.parse_tg_link("普通文本，没有链接") is None
        assert keeper.contains_tg_link("普通文本") is False

    def test_joinchat_link(self):
        # 邀请链接不是消息链接
        assert keeper.parse_tg_link("https://t.me/joinchat/abcdefg") is None

    def test_missing_msg_id(self):
        assert keeper.parse_tg_link("https://t.me/chan") is None
        assert keeper.parse_tg_link("https://t.me/chan/") is None

    def test_non_numeric_msg_id(self):
        assert keeper.parse_tg_link("https://t.me/chan/abc") is None

    def test_private_missing_id(self):
        assert keeper.parse_tg_link("https://t.me/c/123") is None
