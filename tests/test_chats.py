import json
from unittest.mock import patch, MagicMock

from lark_listener.chats import ChatClass, ChatRegistry, classify_chat


# --- classify_chat 纯函数 ---

def test_classify_p2p_always_normal():
    assert classify_chat("oc_x", "p2p", {"oc_x"}, True) is ChatClass.NORMAL
    assert classify_chat("oc_x", "p2p", None, True) is ChatClass.NORMAL


def test_classify_muted_group():
    assert classify_chat("oc_a", "group", {"oc_b"}, True) is ChatClass.MUTED
    assert classify_chat("oc_a", "group", set(), False) is ChatClass.MUTED


def test_classify_unmuted_group_normal_when_special_disabled():
    assert classify_chat("oc_a", "group", {"oc_a"}, False) is ChatClass.NORMAL


def test_classify_unmuted_group_special_when_enabled():
    assert classify_chat("oc_a", "group", {"oc_a"}, True) is ChatClass.SPECIAL


def test_classify_degraded_none_means_all_muted():
    """首刷失败（unmuted=None）→ 群一律按勿扰（宁可少收）。"""
    assert classify_chat("oc_a", "group", None, True) is ChatClass.MUTED


# --- ChatRegistry.refresh ---

def _chat_list_page(chats, has_more=False, page_token=""):
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"ok": True, "data": {
        "chats": chats, "has_more": has_more, "page_token": page_token}})
    return mock


@patch("lark_listener.chats.subprocess.run")
def test_refresh_collects_unmuted_groups(mock_run):
    mock_run.return_value = _chat_list_page(
        [{"chat_id": "oc_vip", "name": "VIP群"}])
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    assert reg.classify("oc_vip", "group") is ChatClass.SPECIAL
    assert reg.classify("oc_other", "group") is ChatClass.MUTED
    assert reg.special_chat_ids() == ["oc_vip"]
    assert reg.name_of("oc_vip") == "VIP群"


@patch("lark_listener.chats.subprocess.run")
def test_refresh_paginates(mock_run):
    mock_run.side_effect = [
        _chat_list_page([{"chat_id": "oc_1", "name": "一"}], has_more=True, page_token="t2"),
        _chat_list_page([{"chat_id": "oc_2", "name": "二"}]),
    ]
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    assert sorted(reg.special_chat_ids()) == ["oc_1", "oc_2"]
    # 第二次调用带 page-token
    second_args = mock_run.call_args_list[1][0][0]
    assert "--page-token" in second_args and "t2" in second_args


@patch("lark_listener.chats.subprocess.run")
def test_refresh_failure_keeps_last_result(mock_run):
    mock_run.return_value = _chat_list_page([{"chat_id": "oc_vip", "name": "VIP群"}])
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    bad = MagicMock(); bad.returncode = 1; bad.stdout = ""
    mock_run.return_value = bad
    assert reg.refresh() is False
    # 沿用上一轮结果
    assert reg.classify("oc_vip", "group") is ChatClass.SPECIAL


@patch("lark_listener.chats.subprocess.run")
def test_refresh_first_failure_degrades_to_all_muted(mock_run):
    bad = MagicMock(); bad.returncode = 1; bad.stdout = ""
    mock_run.return_value = bad
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is False
    assert reg.classify("oc_any", "group") is ChatClass.MUTED
    assert reg.special_chat_ids() == []


def test_special_chat_ids_empty_when_disabled():
    reg = ChatRegistry(special_enabled=False)
    reg._unmuted = {"oc_vip": "VIP群"}
    assert reg.special_chat_ids() == []
    assert reg.classify("oc_vip", "group") is ChatClass.NORMAL


@patch("lark_listener.binaries.subprocess.run")
def test_name_of_falls_back_to_chats_get(mock_run):
    """勿扰群不在未免打扰列表里，补名走单群查询（经 binaries.get_chat_name）。"""
    mock = MagicMock(); mock.returncode = 0
    mock.stdout = '"某勿扰群"\n'
    mock_run.return_value = mock
    reg = ChatRegistry()
    reg._unmuted = {}
    assert reg.name_of("oc_muted") == "某勿扰群"


@patch("lark_listener.binaries.subprocess.run")
def test_name_of_failure_returns_empty(mock_run):
    mock_run.side_effect = OSError("no cli")
    reg = ChatRegistry()
    reg._unmuted = {}
    assert reg.name_of("oc_x") == ""


@patch("lark_listener.chats.subprocess.run")
def test_refresh_success_with_zero_unmuted(mock_run):
    """成功刷到空列表 ≠ 降级态：所有群按勿扰，但 refresh 返回 True。"""
    mock_run.return_value = _chat_list_page([])
    reg = ChatRegistry(special_enabled=True)
    assert reg.refresh() is True
    assert reg.classify("oc_any", "group") is ChatClass.MUTED
    assert reg.special_chat_ids() == []
