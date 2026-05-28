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
