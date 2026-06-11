import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from lark_listener.state import State

TZ = timezone(timedelta(hours=8))


def test_state_fresh_start(tmp_path):
    """First run: no state file, should use default."""
    path = str(tmp_path / "state.json")
    state = State(path)
    assert state.last_poll_time is None
    assert state.processed_message_ids == set()


def test_state_loads_naive_last_poll_time_as_local_tz(tmp_path):
    """A naive last_poll_time in state.json (legacy/hand-written) must be pinned to
    +08:00 on load, so it never mixes with aware datetimes downstream."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "last_poll_time": "2026-06-08T10:00:00",  # no offset
        "processed_message_ids": [],
    }))
    state = State(str(path))
    assert state.last_poll_time is not None
    assert state.last_poll_time.utcoffset() is not None
    assert state.last_poll_time.utcoffset().total_seconds() == 8 * 3600


def test_state_save_and_load(tmp_path):
    path = str(tmp_path / "state.json")
    state = State(path)
    now = datetime.now(TZ)
    state.last_poll_time = now
    state.add_processed_ids(["msg_001", "msg_002"])
    state.save()

    state2 = State(path)
    assert state2.last_poll_time.isoformat() == now.isoformat()
    assert state2.processed_message_ids == {"msg_001", "msg_002"}


def test_state_processed_ids_cap(tmp_path):
    """Should keep only the most recent 1000 IDs."""
    path = str(tmp_path / "state.json")
    state = State(path)
    ids = [f"msg_{i:05d}" for i in range(1100)]
    state.add_processed_ids(ids)
    state.save()

    state2 = State(path)
    assert len(state2.processed_message_ids) == 1000


def test_state_is_processed(tmp_path):
    path = str(tmp_path / "state.json")
    state = State(path)
    state.add_processed_ids(["msg_001"])
    assert state.is_processed("msg_001") is True
    assert state.is_processed("msg_999") is False


def test_state_corrupt_file_starts_fresh(tmp_path):
    """A corrupt state.json must not crash startup — start fresh instead."""
    path = tmp_path / "state.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    state = State(str(path))  # must not raise
    assert state.last_poll_time is None
    assert state.processed_message_ids == set()
    # And it should be able to recover by saving valid state afterwards
    state.add_processed_ids(["msg_001"])
    state.save()
    assert State(str(path)).processed_message_ids == {"msg_001"}


def test_state_save_is_atomic_no_tmp_left(tmp_path):
    """Atomic save should not leave a .tmp file behind."""
    path = tmp_path / "state.json"
    state = State(str(path))
    state.add_processed_ids(["msg_001"])
    state.save()
    assert not (tmp_path / "state.json.tmp").exists()
    assert json.loads(path.read_text())["processed_message_ids"] == ["msg_001"]


# --- 二轮 review：损坏 state 的形状兜底 + _ordered_ids 截断 ---


def test_state_wrong_shape_starts_fresh(tmp_path):
    """state.json 是合法 JSON 但顶层不是对象 → 必须按损坏处理重新开始，
    不能 AttributeError（State 每轮 poll 都会构造，崩了窗口永不推进）。"""
    p = tmp_path / "state.json"
    p.write_text("[1, 2, 3]")
    state = State(str(p))
    assert state.last_poll_time is None
    assert state.processed_message_ids == set()


def test_state_numeric_last_poll_starts_fresh(tmp_path):
    """last_poll_time 为数字 → fromisoformat TypeError，必须兜住。"""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_poll_time": 123, "processed_message_ids": []}))
    state = State(str(p))
    assert state.last_poll_time is None


def test_state_save_truncates_ordered_ids(tmp_path):
    """save() 截断必须同步 _ordered_ids：否则内存无界增长，且被挤出 set 的
    旧 id 再 add 时会在列表里重复。"""
    from lark_listener.state import MAX_PROCESSED_IDS
    s = State(str(tmp_path / "state.json"))
    s.add_processed_ids([f"m{i}" for i in range(MAX_PROCESSED_IDS + 5)])
    s.save()
    assert len(s._ordered_ids) == MAX_PROCESSED_IDS
    s.add_processed_ids(["m0"])  # m0 已被挤出 set，重新加入
    assert s._ordered_ids.count("m0") == 1


def test_state_default_path_respects_env(monkeypatch, tmp_path):
    """裸 State() 的默认路径必须尊重 LARK_LISTENER_HOME——否则 dev 隔离下
    任何新代码裸调 State() 会写穿生产 state.json。"""
    monkeypatch.setenv("LARK_LISTENER_HOME", str(tmp_path))
    s = State()
    assert str(s._path) == str(tmp_path / "state.json")
