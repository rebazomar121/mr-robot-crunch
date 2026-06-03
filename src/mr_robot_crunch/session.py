"""Session persistence under ~/.mr-robot-crunch for stop/resume support."""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(os.path.expanduser("~")) / ".mr-robot-crunch"
STATE_FILE = "state.json"


def base_dir() -> Path:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    return BASE_DIR


def _slug(words: List[str]) -> str:
    raw = "-".join(words)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    return safe[:40] or "words"


def new_session_id(words: List[str], stamp: str) -> str:
    return f"{_slug(words)}_{stamp}"


def session_dir(session_id: str) -> Path:
    d = base_dir() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def shard_path(session_id: str, worker_id: int) -> Path:
    return session_dir(session_id) / f"shard_{worker_id}.txt"


def cursor_path(session_id: str, worker_id: int) -> Path:
    return session_dir(session_id) / f"cursor_{worker_id}.txt"


def read_cursor(session_id: str, worker_id: int) -> int:
    """Return the number of bases this worker has already completed."""
    p = cursor_path(session_id, worker_id)
    try:
        return int(p.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_cursor(session_id: str, worker_id: int, value: int) -> None:
    cursor_path(session_id, worker_id).write_text(str(value))


def save_state(state: Dict) -> None:
    sid = state["session_id"]
    state = dict(state)
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (session_dir(sid) / STATE_FILE).write_text(json.dumps(state, indent=2))


def load_state(session_id: str) -> Optional[Dict]:
    p = session_dir(session_id) / STATE_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def list_sessions() -> List[Dict]:
    """Return saved sessions (most recent first) that are still resumable."""
    sessions = []
    if not BASE_DIR.exists():
        return sessions
    for child in BASE_DIR.iterdir():
        if not child.is_dir():
            continue
        state = load_state(child.name)
        if state and state.get("status") == "stopped":
            sessions.append(state)
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


def cleanup_session(session_id: str) -> None:
    """Remove shard/cursor files after a successful completion."""
    d = session_dir(session_id)
    for f in d.glob("shard_*.txt"):
        f.unlink(missing_ok=True)
    for f in d.glob("cursor_*.txt"):
        f.unlink(missing_ok=True)
    (d / STATE_FILE).unlink(missing_ok=True)
    try:
        d.rmdir()
    except OSError:
        pass
