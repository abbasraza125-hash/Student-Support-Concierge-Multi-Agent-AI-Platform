"""
Memory module
FEATURE: Sessions & Memory, InMemorySessionService, Long-term Memory (Memory Bank), Context compaction
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

# file-backed storage for demo samples
DATA_DIR = Path(__file__).parent.parent / "samples" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_FILE = DATA_DIR / "memory.json"


class MemoryStore:
    def __init__(self):
        if not MEMORY_FILE.exists():
            self._data = {"sessions": {}, "long_term": {}, "globals": {}}
            self._flush()
        else:
            self._data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))

    def _flush(self):
        MEMORY_FILE.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    # Session APIs
    def create_session(self, username: str) -> str:
        sid = f"sess_{username}_{int(datetime.utcnow().timestamp())}"
        self._data["sessions"][sid] = {
            "username": username,
            "history": [],
            "state": {},
            "created": datetime.utcnow().isoformat(),
        }
        self._flush()
        return sid

    def append_history(self, sid: str, role: str, text: str):
        s = self._data["sessions"].get(sid)
        if s is None:
            raise KeyError("unknown session")
        s["history"].append({"ts": datetime.utcnow().isoformat(), "role": role, "text": text})
        # FEATURE: context compaction: keep last 10 items
        s["history"] = s["history"][-10:]
        self._flush()

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        return self._data["sessions"].get(sid)

    def set_session_field(self, sid: str, key: str, value: Any):
        s = self._data["sessions"].get(sid)
        if not s:
            raise KeyError("unknown session")
        s["state"][key] = value
        self._flush()

    # Long term memory access
    def set_long_term(self, key: str, value: Any):
        self._data["long_term"][key] = value
        self._flush()

    def get_long_term(self, key: str):
        return self._data["long_term"].get(key)

    # Simple global store for tools (e.g. MCP logs)
    def set_global(self, key: str, value: Any):
        self._data["globals"][key] = value
        self._flush()

    def get_global(self, key: str):
        return self._data["globals"].get(key)
