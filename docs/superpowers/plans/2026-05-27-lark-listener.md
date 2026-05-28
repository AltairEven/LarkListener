# LarkListener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local background service that polls Feishu for unread messages, analyzes them with AI, and sends summaries via Bot private chat + macOS notification.

**Architecture:** Python service with 5 modules (config, state, fetcher, analyzer, notifier) orchestrated by a scheduler loop in main.py. Calls lark-cli via subprocess for all Feishu API interactions. AI analysis via configurable provider (claude/openai/ollama).

**Tech Stack:** Python 3.9+, pyyaml, anthropic/openai SDK (optional), lark-cli, terminal-notifier, macOS launchd

---

## File Structure

```
LarkListener/
├── lark_listener/
│   ├── __init__.py          # Package init, version
│   ├── main.py              # Entry point, scheduler loop, signal handling
│   ├── config.py            # Load & validate ~/.lark_listener/config.yaml
│   ├── state.py             # Persist last_poll_time + processed_message_ids
│   ├── fetcher.py           # Call lark-cli to fetch 3 categories of messages, deduplicate
│   ├── analyzer.py          # AI analysis: relevance, urgency, summary (multi-provider)
│   └── notifier.py          # Build rich text, send via bot, trigger macOS notification
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_state.py
│   ├── test_fetcher.py
│   ├── test_analyzer.py
│   └── test_notifier.py
├── config.example.yaml      # Config template
├── install.sh               # Setup script (dirs, plist, deps)
├── requirements.txt         # Python dependencies
└── pyproject.toml           # Project metadata
```

---

### Task 1: Project Scaffolding & Config Module

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `config.example.yaml`
- Create: `lark_listener/__init__.py`
- Create: `lark_listener/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create requirements.txt**

```
pyyaml>=6.0
anthropic>=0.30.0
openai>=1.30.0
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[project]
name = "lark-listener"
version = "0.1.0"
description = "Local background service that polls Feishu for unread messages and sends AI-analyzed summaries"
requires-python = ">=3.9"

[project.scripts]
lark-listener = "lark_listener.main:main"
```

- [ ] **Step 3: Create config.example.yaml**

```yaml
# Polling interval in seconds
poll_interval: 300

# Keywords to watch for in group messages
keywords:
  - 部署
  - 故障
  - 发版

# AI model configuration
ai:
  provider: claude        # claude / openai / ollama
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""            # For openai-compatible or ollama endpoints

# Notification target
notify:
  user_id: ou_xxxxxxxxxxxx
  bot_chat_id: oc_xxxxxxxxxxxx
```

- [ ] **Step 4: Create lark_listener/__init__.py**

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Write the failing test for config loading**

Create `tests/test_config.py`:

```python
import os
import tempfile
import pytest
from lark_listener.config import load_config


SAMPLE_CONFIG = """\
poll_interval: 120
keywords:
  - 部署
  - 故障
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""
notify:
  user_id: ou_test123
  bot_chat_id: oc_test456
"""


def test_load_config_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_CONFIG)
        f.flush()
        config = load_config(f.name)

    assert config["poll_interval"] == 120
    assert config["keywords"] == ["部署", "故障"]
    assert config["ai"]["provider"] == "claude"
    assert config["notify"]["user_id"] == "ou_test123"
    os.unlink(f.name)


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_load_config_defaults():
    """Config with only required fields gets defaults for optional ones."""
    minimal = """\
keywords:
  - test
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: TEST_KEY
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(minimal)
        f.flush()
        config = load_config(f.name)

    assert config["poll_interval"] == 300  # default
    assert config["ai"]["base_url"] == ""  # default
    os.unlink(f.name)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.config'`

- [ ] **Step 7: Implement config.py**

Create `lark_listener/config.py`:

```python
import yaml
from pathlib import Path
from typing import Any

DEFAULTS = {
    "poll_interval": 300,
    "ai": {
        "base_url": "",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config from YAML file, applying defaults for missing fields."""
    if path is None:
        path = str(Path.home() / ".lark_listener" / "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    return _deep_merge(DEFAULTS, user_config)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/altair/Documents/Projects/LarkListener && python -m pytest tests/test_config.py -v`
Expected: 3 tests PASS

- [ ] **Step 9: Commit**

```bash
git add requirements.txt pyproject.toml config.example.yaml lark_listener/__init__.py lark_listener/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: add project scaffolding and config module"
```

---

### Task 2: State Module

**Files:**
- Create: `lark_listener/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_state.py`:

```python
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from lark_listener.state import State

TZ = timezone(timedelta(hours=8))


def test_state_fresh_start():
    """First run: no state file, should use default."""
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    state = State(path)
    assert state.last_poll_time is None
    assert state.processed_message_ids == set()


def test_state_save_and_load():
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    state = State(path)
    now = datetime.now(TZ)
    state.last_poll_time = now
    state.add_processed_ids(["msg_001", "msg_002"])
    state.save()

    state2 = State(path)
    assert state2.last_poll_time.isoformat() == now.isoformat()
    assert state2.processed_message_ids == {"msg_001", "msg_002"}


def test_state_processed_ids_cap():
    """Should keep only the most recent 1000 IDs."""
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    state = State(path)
    ids = [f"msg_{i:05d}" for i in range(1100)]
    state.add_processed_ids(ids)
    state.save()

    state2 = State(path)
    assert len(state2.processed_message_ids) == 1000


def test_state_is_processed():
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    state = State(path)
    state.add_processed_ids(["msg_001"])
    assert state.is_processed("msg_001") is True
    assert state.is_processed("msg_999") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.state'`

- [ ] **Step 3: Implement state.py**

Create `lark_listener/state.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

MAX_PROCESSED_IDS = 1000


class State:
    def __init__(self, path: str | None = None):
        if path is None:
            path = str(Path.home() / ".lark_listener" / "state.json")
        self._path = Path(path)
        self.last_poll_time: datetime | None = None
        self.processed_message_ids: set[str] = set()
        self._ordered_ids: list[str] = []
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("last_poll_time"):
            self.last_poll_time = datetime.fromisoformat(data["last_poll_time"])
        ids = data.get("processed_message_ids", [])
        self._ordered_ids = ids
        self.processed_message_ids = set(ids)

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_poll_time": self.last_poll_time.isoformat() if self.last_poll_time else None,
            "processed_message_ids": self._ordered_ids[-MAX_PROCESSED_IDS:],
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Sync in-memory set with capped list
        self.processed_message_ids = set(self._ordered_ids[-MAX_PROCESSED_IDS:])

    def add_processed_ids(self, ids: list[str]):
        for msg_id in ids:
            if msg_id not in self.processed_message_ids:
                self._ordered_ids.append(msg_id)
                self.processed_message_ids.add(msg_id)

    def is_processed(self, msg_id: str) -> bool:
        return msg_id in self.processed_message_ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_state.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lark_listener/state.py tests/test_state.py
git commit -m "feat: add state persistence module"
```

---

### Task 3: Fetcher Module

**Files:**
- Create: `lark_listener/fetcher.py`
- Create: `tests/test_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetcher.py`:

```python
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from lark_listener.fetcher import Fetcher, MessageCategory

TZ = timezone(timedelta(hours=8))

SAMPLE_SEARCH_RESULT = json.dumps({
    "ok": True,
    "items": [
        {
            "message_id": "msg_001",
            "chat_id": "oc_chat1",
            "chat_name": "技术群",
            "sender": {"name": "张三"},
            "body": {"content": "线上服务挂了"},
            "create_time": "1716796800",
        },
        {
            "message_id": "msg_002",
            "chat_id": "oc_chat2",
            "chat_name": "",
            "sender": {"name": "李四"},
            "body": {"content": "明天开会"},
            "create_time": "1716796900",
        },
    ]
})

EMPTY_RESULT = json.dumps({"ok": True, "items": []})


def _mock_run(results: list[str]):
    """Return a side_effect function that yields results in order."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = results[idx]
        return mock

    return side_effect


@patch("lark_listener.fetcher.subprocess.run")
def test_fetch_returns_three_categories(mock_run):
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,  # p2p
        EMPTY_RESULT,          # at_me
        EMPTY_RESULT,          # keyword "部署"
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0
    assert len(result[MessageCategory.KEYWORD]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_dedup_across_categories(mock_run):
    """Same message_id in p2p and at_me — p2p wins."""
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,  # p2p: msg_001, msg_002
        SAMPLE_SEARCH_RESULT,  # at_me: msg_001, msg_002 (same)
        EMPTY_RESULT,          # keyword
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids=set())

    assert len(result[MessageCategory.P2P]) == 2
    assert len(result[MessageCategory.AT_ME]) == 0


@patch("lark_listener.fetcher.subprocess.run")
def test_skip_processed_ids(mock_run):
    mock_run.side_effect = _mock_run([
        SAMPLE_SEARCH_RESULT,
        EMPTY_RESULT,
        EMPTY_RESULT,
    ])
    fetcher = Fetcher(keywords=["部署"])
    start = datetime(2026, 5, 27, 0, 0, 0, tzinfo=TZ)
    end = datetime(2026, 5, 27, 12, 0, 0, tzinfo=TZ)
    result = fetcher.fetch(start, end, processed_ids={"msg_001"})

    assert len(result[MessageCategory.P2P]) == 1
    assert result[MessageCategory.P2P][0]["message_id"] == "msg_002"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.fetcher'`

- [ ] **Step 3: Implement fetcher.py**

Create `lark_listener/fetcher.py`:

```python
import json
import subprocess
from datetime import datetime
from enum import Enum
from typing import Any


class MessageCategory(Enum):
    P2P = "p2p"
    AT_ME = "at_me"
    KEYWORD = "keyword"


class Fetcher:
    def __init__(self, keywords: list[str] | None = None):
        self.keywords = keywords or []

    def fetch(
        self,
        start: datetime,
        end: datetime,
        processed_ids: set[str],
    ) -> dict[MessageCategory, list[dict[str, Any]]]:
        seen_ids: set[str] = set(processed_ids)
        result = {cat: [] for cat in MessageCategory}

        # Priority order: P2P > AT_ME > KEYWORD
        # 1. Private messages
        p2p_msgs = self._search(start, end, chat_type="p2p")
        for msg in p2p_msgs:
            mid = msg["message_id"]
            if mid not in seen_ids:
                result[MessageCategory.P2P].append(msg)
                seen_ids.add(mid)

        # 2. @me messages in groups
        at_msgs = self._search(start, end, chat_type="group", is_at_me=True)
        for msg in at_msgs:
            mid = msg["message_id"]
            if mid not in seen_ids:
                result[MessageCategory.AT_ME].append(msg)
                seen_ids.add(mid)

        # 3. Keyword matches
        for keyword in self.keywords:
            kw_msgs = self._search(start, end, query=keyword)
            for msg in kw_msgs:
                mid = msg["message_id"]
                if mid not in seen_ids:
                    msg["matched_keyword"] = keyword
                    result[MessageCategory.KEYWORD].append(msg)
                    seen_ids.add(mid)

        return result

    def _search(
        self,
        start: datetime,
        end: datetime,
        chat_type: str | None = None,
        is_at_me: bool = False,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        cmd = [
            "lark-cli", "im", "+messages-search",
            "--start", start.isoformat(),
            "--end", end.isoformat(),
            "--format", "json",
            "--page-all",
        ]
        if chat_type:
            cmd.extend(["--chat-type", chat_type])
        if is_at_me:
            cmd.append("--is-at-me")
        if query:
            cmd.extend(["--query", query])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return []

        data = json.loads(proc.stdout)
        if not data.get("ok"):
            return []

        return data.get("items", [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fetcher.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lark_listener/fetcher.py tests/test_fetcher.py
git commit -m "feat: add fetcher module for polling Feishu messages"
```

---

### Task 4: Analyzer Module

**Files:**
- Create: `lark_listener/analyzer.py`
- Create: `tests/test_analyzer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_analyzer.py`:

```python
import json
from unittest.mock import patch, MagicMock
from lark_listener.analyzer import Analyzer, AnalysisResult
from lark_listener.fetcher import MessageCategory

SAMPLE_AI_RESPONSE = json.dumps([
    {
        "message_id": "msg_001",
        "relevance": "high",
        "urgency": "urgent",
        "summary": "线上服务故障求助",
    },
    {
        "message_id": "msg_002",
        "relevance": "medium",
        "urgency": "normal",
        "summary": "会议时间变更",
    },
])

SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        {"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "线上服务挂了"}},
    ],
    MessageCategory.AT_ME: [
        {"message_id": "msg_002", "sender": {"name": "李四"}, "body": {"content": "明天开会改时间"}},
    ],
    MessageCategory.KEYWORD: [],
}


@patch("lark_listener.analyzer.Analyzer._call_ai")
def test_analyze_returns_results(mock_call_ai):
    mock_call_ai.return_value = json.loads(SAMPLE_AI_RESPONSE)

    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="",
        keywords=["故障"],
    )
    results = analyzer.analyze(SAMPLE_MESSAGES)

    assert "msg_001" in results
    assert results["msg_001"].urgency == "urgent"
    assert results["msg_001"].summary == "线上服务故障求助"
    assert "msg_002" in results


def test_analyze_empty_messages():
    analyzer = Analyzer(
        provider="claude",
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        base_url="",
        keywords=["故障"],
    )
    empty = {cat: [] for cat in MessageCategory}
    results = analyzer.analyze(empty)
    assert results == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_analyzer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.analyzer'`

- [ ] **Step 3: Implement analyzer.py**

Create `lark_listener/analyzer.py`:

```python
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

from lark_listener.fetcher import MessageCategory

SYSTEM_PROMPT = "你是消息分析助手。请严格输出 JSON 数组，不要输出其他内容。"

USER_PROMPT_TEMPLATE = """\
用户关注的关键词：{keywords}

请对以下消息进行分析，对每条消息输出：
1. message_id: 消息 ID
2. relevance: 与关键词的语义相关度（high/medium/low）
3. urgency: 紧急度（urgent/normal/low）
4. summary: 一句话提炼核心内容

输出格式为 JSON 数组：
[{{"message_id": "...", "relevance": "...", "urgency": "...", "summary": "..."}}]

消息列表：
{messages}"""


@dataclass
class AnalysisResult:
    message_id: str
    relevance: str
    urgency: str
    summary: str


class Analyzer:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key_env: str,
        base_url: str,
        keywords: list[str],
    ):
        self.provider = provider
        self.model = model
        self.api_key = os.environ.get(api_key_env, "")
        self.base_url = base_url
        self.keywords = keywords

    def analyze(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
    ) -> dict[str, AnalysisResult]:
        all_msgs = []
        for msgs in categorized.values():
            all_msgs.extend(msgs)

        if not all_msgs:
            return {}

        # Build simplified message list for the prompt
        prompt_msgs = []
        for msg in all_msgs:
            sender = msg.get("sender", {}).get("name", "未知")
            content = msg.get("body", {}).get("content", "")
            prompt_msgs.append(f"[{msg['message_id']}] {sender}: {content}")

        user_prompt = USER_PROMPT_TEMPLATE.format(
            keywords="、".join(self.keywords),
            messages="\n".join(prompt_msgs),
        )

        raw_results = self._call_ai(user_prompt)

        results = {}
        for item in raw_results:
            results[item["message_id"]] = AnalysisResult(
                message_id=item["message_id"],
                relevance=item.get("relevance", "medium"),
                urgency=item.get("urgency", "normal"),
                summary=item.get("summary", ""),
            )
        return results

    def _call_ai(self, user_prompt: str) -> list[dict]:
        if self.provider == "claude":
            return self._call_claude(user_prompt)
        elif self.provider == "openai":
            return self._call_openai(user_prompt)
        elif self.provider == "ollama":
            return self._call_ollama(user_prompt)
        else:
            raise ValueError(f"Unknown AI provider: {self.provider}")

    def _call_claude(self, user_prompt: str) -> list[dict]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return json.loads(response.content[0].text)

    def _call_openai(self, user_prompt: str) -> list[dict]:
        import openai

        client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url or None,
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return json.loads(response.choices[0].message.content)

    def _call_ollama(self, user_prompt: str) -> list[dict]:
        url = (self.base_url or "http://localhost:11434") + "/api/chat"
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return json.loads(data["message"]["content"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_analyzer.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lark_listener/analyzer.py tests/test_analyzer.py
git commit -m "feat: add AI analyzer module with multi-provider support"
```

---

### Task 5: Notifier Module

**Files:**
- Create: `lark_listener/notifier.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_notifier.py`:

```python
import json
from unittest.mock import patch, MagicMock, call
from lark_listener.notifier import Notifier, build_summary_text
from lark_listener.fetcher import MessageCategory
from lark_listener.analyzer import AnalysisResult


SAMPLE_MESSAGES = {
    MessageCategory.P2P: [
        {"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "线上挂了"}},
    ],
    MessageCategory.AT_ME: [
        {"message_id": "msg_002", "sender": {"name": "李四"}, "chat_name": "技术群", "body": {"content": "@你 review PR"}},
    ],
    MessageCategory.KEYWORD: [
        {"message_id": "msg_003", "sender": {"name": "王五"}, "chat_name": "运维群", "body": {"content": "部署流水线挂了"}, "matched_keyword": "部署"},
    ],
}

SAMPLE_ANALYSIS = {
    "msg_001": AnalysisResult("msg_001", "high", "urgent", "线上故障求助"),
    "msg_002": AnalysisResult("msg_002", "high", "normal", "请求代码审查"),
    "msg_003": AnalysisResult("msg_003", "high", "normal", "CI/CD 流水线故障"),
}


def test_build_summary_text():
    text = build_summary_text(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30")
    assert "私聊消息" in text
    assert "张三" in text
    assert "查看原文" in text
    assert "@我" in text
    assert "关键词命中" in text
    assert "部署" in text


def test_build_summary_empty():
    empty = {cat: [] for cat in MessageCategory}
    text = build_summary_text(empty, {}, "15:00", "15:30")
    assert text == ""


@patch("lark_listener.notifier.subprocess.run")
def test_notify_sends_message_and_notification(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout='{"ok": true}')
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    notifier.notify(SAMPLE_MESSAGES, SAMPLE_ANALYSIS, "15:00", "15:30")

    # Should call subprocess twice: lark-cli send + terminal-notifier
    assert mock_run.call_count == 2


@patch("lark_listener.notifier.subprocess.run")
def test_notify_skips_when_no_messages(mock_run):
    notifier = Notifier(user_id="ou_test", bot_chat_id="oc_test")
    empty = {cat: [] for cat in MessageCategory}
    notifier.notify(empty, {}, "15:00", "15:30")

    mock_run.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.notifier'`

- [ ] **Step 3: Implement notifier.py**

Create `lark_listener/notifier.py`:

```python
import json
import subprocess
from typing import Any
from urllib.parse import quote

from lark_listener.analyzer import AnalysisResult
from lark_listener.fetcher import MessageCategory

URGENCY_ICON = {
    "urgent": "🔴 [紧急]",
    "normal": "⚪",
    "low": "⚪",
}

MESSAGE_LINK_TEMPLATE = "https://applink.feishu.cn/client/message/link/open?body=%s"


def _message_link(message_id: str) -> str:
    body = json.dumps({"token": message_id})
    return MESSAGE_LINK_TEMPLATE % quote(body)


def _format_msg_line(
    msg: dict[str, Any],
    analysis: AnalysisResult | None,
    category: MessageCategory,
) -> str:
    sender = msg.get("sender", {}).get("name", "未知")
    content = msg.get("body", {}).get("content", "")[:50]
    link = _message_link(msg["message_id"])
    icon = URGENCY_ICON.get(analysis.urgency, "⚪") if analysis else "⚪"
    summary_line = f"     ➜ {analysis.summary}" if analysis else ""

    if category == MessageCategory.P2P:
        header = f"{icon} {sender}：{content}"
    elif category == MessageCategory.AT_ME:
        chat_name = msg.get("chat_name", "群聊")
        header = f"{icon} {sender} @ {chat_name}：{content}"
    else:
        chat_name = msg.get("chat_name", "群聊")
        keyword = msg.get("matched_keyword", "")
        header = f"{icon} {sender} @ {chat_name}：{content}（命中：{keyword}）"

    lines = [header]
    if summary_line:
        lines.append(f"{summary_line}  👉 [查看原文]({link})")
    else:
        lines.append(f"     👉 [查看原文]({link})")
    return "\n".join(lines)


def build_summary_text(
    categorized: dict[MessageCategory, list[dict[str, Any]]],
    analysis: dict[str, AnalysisResult],
    start_time: str,
    end_time: str,
) -> str:
    total = sum(len(msgs) for msgs in categorized.values())
    if total == 0:
        return ""

    sections = []
    sections.append(f"📬 LarkListener 消息汇总（{start_time} - {end_time}）\n")

    category_config = [
        (MessageCategory.P2P, "私聊消息"),
        (MessageCategory.AT_ME, "@我 / @所有人"),
        (MessageCategory.KEYWORD, "关键词命中"),
    ]

    for cat, label in category_config:
        msgs = categorized[cat]
        if not msgs:
            continue
        # Sort urgent first
        msgs_sorted = sorted(
            msgs,
            key=lambda m: 0 if analysis.get(m["message_id"], None) and analysis[m["message_id"]].urgency == "urgent" else 1,
        )
        sections.append(f"━━ {label}（{len(msgs)} 条）━━")
        for msg in msgs_sorted:
            ar = analysis.get(msg["message_id"])
            sections.append(_format_msg_line(msg, ar, cat))
        sections.append("")

    return "\n".join(sections).strip()


class Notifier:
    def __init__(self, user_id: str, bot_chat_id: str):
        self.user_id = user_id
        self.bot_chat_id = bot_chat_id

    def notify(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
        analysis: dict[str, AnalysisResult],
        start_time: str,
        end_time: str,
    ):
        text = build_summary_text(categorized, analysis, start_time, end_time)
        if not text:
            return

        self._send_bot_message(text)
        self._send_macos_notification(categorized)

    def _send_bot_message(self, text: str):
        content = json.dumps({"text": text})
        cmd = [
            "lark-cli", "im", "+messages-send",
            "--user-id", self.user_id,
            "--msg-type", "text",
            "--content", content,
            "--as", "bot",
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def _send_macos_notification(
        self,
        categorized: dict[MessageCategory, list[dict[str, Any]]],
    ):
        counts = []
        p2p_count = len(categorized[MessageCategory.P2P])
        at_count = len(categorized[MessageCategory.AT_ME])
        kw_count = len(categorized[MessageCategory.KEYWORD])
        if p2p_count:
            counts.append(f"{p2p_count}条私聊")
        if at_count:
            counts.append(f"{at_count}条@我")
        if kw_count:
            counts.append(f"{kw_count}条关键词命中")
        message = "、".join(counts)

        open_url = f"https://applink.feishu.cn/client/chat/open?openChatId={self.bot_chat_id}"

        cmd = [
            "terminal-notifier",
            "-title", "LarkListener",
            "-subtitle", "有新消息汇总",
            "-message", message,
            "-open", open_url,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_notifier.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lark_listener/notifier.py tests/test_notifier.py
git commit -m "feat: add notifier module with bot message and macOS notification"
```

---

### Task 6: Main Entry Point & Scheduler

**Files:**
- Create: `lark_listener/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_main.py`:

```python
import os
import tempfile
from unittest.mock import patch, MagicMock
from lark_listener.main import poll_once
from lark_listener.fetcher import MessageCategory


SAMPLE_CONFIG = """\
poll_interval: 60
keywords:
  - 部署
ai:
  provider: claude
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY
  base_url: ""
notify:
  user_id: ou_test
  bot_chat_id: oc_test
"""


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_full_cycle(MockFetcher, MockAnalyzer, MockNotifier):
    # Setup fetcher mock
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {
        MessageCategory.P2P: [{"message_id": "msg_001", "sender": {"name": "张三"}, "body": {"content": "hello"}}],
        MessageCategory.AT_ME: [],
        MessageCategory.KEYWORD: [],
    }

    # Setup analyzer mock
    mock_analyzer = MockAnalyzer.return_value
    mock_analyzer.analyze.return_value = {
        "msg_001": MagicMock(urgency="normal", summary="打招呼"),
    }

    # Setup notifier mock
    mock_notifier = MockNotifier.return_value

    # Create temp config and state
    config_dir = tempfile.mkdtemp()
    config_path = os.path.join(config_dir, "config.yaml")
    state_path = os.path.join(config_dir, "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)

    poll_once(config_path, state_path)

    mock_fetcher.fetch.assert_called_once()
    mock_analyzer.analyze.assert_called_once()
    mock_notifier.notify.assert_called_once()


@patch("lark_listener.main.Notifier")
@patch("lark_listener.main.Analyzer")
@patch("lark_listener.main.Fetcher")
def test_poll_once_no_messages_skips_analysis(MockFetcher, MockAnalyzer, MockNotifier):
    mock_fetcher = MockFetcher.return_value
    mock_fetcher.fetch.return_value = {cat: [] for cat in MessageCategory}

    mock_analyzer = MockAnalyzer.return_value
    mock_notifier = MockNotifier.return_value

    config_dir = tempfile.mkdtemp()
    config_path = os.path.join(config_dir, "config.yaml")
    state_path = os.path.join(config_dir, "state.json")
    with open(config_path, "w") as f:
        f.write(SAMPLE_CONFIG)

    poll_once(config_path, state_path)

    mock_analyzer.analyze.assert_not_called()
    mock_notifier.notify.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lark_listener.main'`

- [ ] **Step 3: Implement main.py**

Create `lark_listener/main.py`:

```python
import logging
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from lark_listener.analyzer import Analyzer
from lark_listener.config import load_config
from lark_listener.fetcher import Fetcher, MessageCategory
from lark_listener.notifier import Notifier
from lark_listener.state import State

TZ = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("lark_listener")

_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s, shutting down...", signum)
    _running = False


def poll_once(config_path: str | None = None, state_path: str | None = None):
    config = load_config(config_path)
    state = State(state_path)

    now = datetime.now(TZ)
    start = state.last_poll_time or (now - timedelta(seconds=config["poll_interval"]))
    end = now

    fetcher = Fetcher(keywords=config.get("keywords", []))
    categorized = fetcher.fetch(start, end, processed_ids=state.processed_message_ids)

    total = sum(len(msgs) for msgs in categorized.values())
    logger.info("Fetched %d new messages", total)

    if total == 0:
        state.last_poll_time = now
        state.save()
        return

    ai_cfg = config["ai"]
    analyzer = Analyzer(
        provider=ai_cfg["provider"],
        model=ai_cfg["model"],
        api_key_env=ai_cfg["api_key_env"],
        base_url=ai_cfg.get("base_url", ""),
        keywords=config.get("keywords", []),
    )
    analysis = analyzer.analyze(categorized)

    notify_cfg = config["notify"]
    notifier = Notifier(
        user_id=notify_cfg["user_id"],
        bot_chat_id=notify_cfg["bot_chat_id"],
    )
    notifier.notify(
        categorized,
        analysis,
        start.strftime("%H:%M"),
        end.strftime("%H:%M"),
    )

    # Update state
    all_ids = []
    for msgs in categorized.values():
        all_ids.extend(m["message_id"] for m in msgs)
    state.add_processed_ids(all_ids)
    state.last_poll_time = now
    state.save()

    logger.info("Summary sent successfully")


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    home = Path.home() / ".lark_listener"
    config_path = str(home / "config.yaml")
    state_path = str(home / "state.json")

    logger.info("LarkListener starting...")

    while _running:
        try:
            config = load_config(config_path)
            interval = config.get("poll_interval", 300)
            poll_once(config_path, state_path)
        except Exception:
            logger.exception("Error during poll cycle")

        # Sleep in small increments for responsive shutdown
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)

    logger.info("LarkListener stopped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: 2 tests PASS

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All 15 tests PASS

- [ ] **Step 6: Commit**

```bash
git add lark_listener/main.py tests/test_main.py
git commit -m "feat: add main entry point with scheduler loop"
```

---

### Task 7: Install Script & launchd plist

**Files:**
- Create: `install.sh`

- [ ] **Step 1: Create install.sh**

```bash
#!/bin/bash
set -euo pipefail

LISTENER_HOME="$HOME/.lark_listener"
PLIST_NAME="com.larklistener.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"

echo "=== LarkListener Installer ==="

# 1. Create directories
mkdir -p "$LISTENER_HOME/logs"
echo "✓ Created $LISTENER_HOME"

# 2. Copy example config if none exists
if [ ! -f "$LISTENER_HOME/config.yaml" ]; then
    cp "$PROJECT_DIR/config.example.yaml" "$LISTENER_HOME/config.yaml"
    echo "✓ Copied config.example.yaml → $LISTENER_HOME/config.yaml"
    echo "  ⚠️  Please edit config.yaml with your user_id and bot_chat_id"
else
    echo "✓ Config already exists, skipping"
fi

# 3. Install Python dependencies
pip3 install -r "$PROJECT_DIR/requirements.txt" --quiet
echo "✓ Installed Python dependencies"

# 4. Check external dependencies
if ! command -v lark-cli &>/dev/null; then
    echo "⚠️  lark-cli not found. Install: npm install -g @nicholaschen/lark-cli"
fi
if ! command -v terminal-notifier &>/dev/null; then
    echo "⚠️  terminal-notifier not found. Install: brew install terminal-notifier"
fi

# 5. Write launchd plist
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.larklistener</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>lark_listener.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$LISTENER_HOME/logs/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$LISTENER_HOME/logs/stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONPATH</key>
        <string>$PROJECT_DIR</string>
    </dict>
</dict>
</plist>
PLIST
echo "✓ Wrote $PLIST_PATH"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit ~/.lark_listener/config.yaml"
echo "  2. Start: launchctl load $PLIST_PATH"
echo "  3. Stop:  launchctl unload $PLIST_PATH"
echo "  4. Logs:  tail -f ~/.lark_listener/logs/stdout.log"
```

- [ ] **Step 2: Make executable and commit**

```bash
chmod +x install.sh
git add install.sh
git commit -m "feat: add install script with launchd plist generation"
```

---

### Task 8: Integration Smoke Test

**Files:**
- None (manual verification)

- [ ] **Step 1: Install dependencies**

Run: `cd /Users/altair/Documents/Projects/LarkListener && pip3 install -r requirements.txt`

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All 15 tests PASS

- [ ] **Step 3: Dry-run the service**

Run manually to verify startup and graceful shutdown:

```bash
# Create minimal test config
mkdir -p ~/.lark_listener
cp config.example.yaml ~/.lark_listener/config.yaml
# Edit config with real values, then:
python -m lark_listener.main
# Press Ctrl+C after one cycle to verify graceful shutdown
```

Expected: Logs show "LarkListener starting...", one poll cycle, then "Received signal ... shutting down..."

- [ ] **Step 4: Commit any fixes from smoke test**

```bash
git add -A
git commit -m "fix: adjustments from integration smoke test"
```
