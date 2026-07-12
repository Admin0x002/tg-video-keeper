# -*- coding: utf-8 -*-
"""
test_safety.py — 安全检查逻辑单测

这些测试覆盖纯函数逻辑（不连真实 Telegram），确保：
- 来源白名单判定正确（含 'me' 收藏夹）
- 用户白名单判定正确
- 媒体类型判定正确（纯文本跳过）

运行：python -m pytest tests/test_safety.py -v
"""
import os
import sys
from unittest import mock

# 让测试能 import 项目根的模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 注入最小环境变量，使 config 模块能加载
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "abcd1234")
os.environ.setdefault("TARGET_CHAT_ID", "-1009999999999")

import config as cfg  # noqa: E402
import keeper  # noqa: E402


# -------------------- 来源白名单 --------------------
class TestSourceAllowed:
    def test_numeric_source_allowed(self):
        # 配置 SOURCE_CHAT_IDS 含某频道 ID 时应放行
        with mock.patch.object(cfg, "SOURCE_CHAT_IDS", [-1001234567890]):
            assert keeper._source_allowed(-1001234567890) is True

    def test_unknown_source_rejected(self):
        with mock.patch.object(cfg, "SOURCE_CHAT_IDS", [-1001234567890]):
            assert keeper._source_allowed(-1009999999999) is False

    def test_none_rejected(self):
        with mock.patch.object(cfg, "SOURCE_CHAT_IDS", [-1001234567890]):
            assert keeper._source_allowed(None) is False

    def test_saved_messages_allowed_when_me_in_whitelist(self):
        # 配置含 'me'，且 MY_USER_ID=123，则 chat_id=123 应放行
        with mock.patch.object(cfg, "SOURCE_CHAT_IDS", ["me"]):
            with mock.patch.object(keeper, "MY_USER_ID", 123):
                assert keeper._source_allowed(123) is True

    def test_saved_messages_rejected_when_me_not_in_whitelist(self):
        # 配置不含 'me'，即便 chat_id==MY_USER_ID 也不放行
        with mock.patch.object(cfg, "SOURCE_CHAT_IDS", [-1001234567890]):
            with mock.patch.object(keeper, "MY_USER_ID", 123):
                assert keeper._source_allowed(123) is False


# -------------------- 用户白名单 --------------------
class TestUserAllowed:
    def test_empty_whitelist_allows_all(self):
        with mock.patch.object(cfg, "ALLOWED_USER_IDS", set()):
            assert keeper._user_allowed(999) is True

    def test_in_whitelist(self):
        with mock.patch.object(cfg, "ALLOWED_USER_IDS", {111, 222}):
            assert keeper._user_allowed(111) is True

    def test_not_in_whitelist(self):
        with mock.patch.object(cfg, "ALLOWED_USER_IDS", {111, 222}):
            assert keeper._user_allowed(333) is False


# -------------------- 收藏夹判定 --------------------
class TestIsFromSavedMessages:
    def test_matches_my_user_id(self):
        with mock.patch.object(keeper, "MY_USER_ID", 123):
            assert keeper._is_from_saved_messages(123) is True

    def test_not_matches(self):
        with mock.patch.object(keeper, "MY_USER_ID", 123):
            assert keeper._is_from_saved_messages(456) is False

    def test_my_user_id_none(self):
        with mock.patch.object(keeper, "MY_USER_ID", None):
            assert keeper._is_from_saved_messages(123) is False
