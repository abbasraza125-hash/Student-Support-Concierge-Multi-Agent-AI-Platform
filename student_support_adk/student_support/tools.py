"""
Tools module
FEATURE: Tools (MCP, custom tools, built-in tools stub, OpenAPI tool, Code Execution)
"""
import json
import time
import re
from typing import Any, Dict, Optional
from .memory import MemoryStore


class Tools:
    """Collection of tools available to agents (CSV lookup, search stub, code exec, OpenAPI stub)."""

    def __init__(self, student_db: Dict[str, Dict[str, str]], memory: MemoryStore):
        self.student_db = student_db or {}
        self.memory = memory

    # FEATURE: custom tool - CSV lookup (MCP equivalent)
    def csv_lookup(self, username: str) -> Dict[str, str]:
        if not username:
            return {}
        return self.student_db.get(username, {})

    # Helper normalizers for search
    def _normalize(self, text: str) -> str:
        t = (text or "").lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    # FEATURE: built-in tool stub - Google Search (replace with real search API)
    def google_search(self, query: str) -> str:
        # Stubbed quick answers (extend as needed)
        faqs = {
            "how to take exam": "Open LockDown Browser, go to module, click Start Exam.",
            "ms365": "Sign in at portal.office.com using your college email.",
            "how to login": "Use your college username and password; reset via the portal if needed.",
        }

        qnorm = self._normalize(query)
        if not qnorm:
            return "No query provided."

        # token overlap matching
        qtokens = set(qnorm.split())
        best_score = 0.0
        best_answer = None
        for k, v in faqs.items():
            ktoks = set(self._normalize(k).split())
            if not ktoks:
                continue
            score = len(qtokens & ktoks) / max(len(ktoks), 1)
            if score > best_score:
                best_score = score
                best_answer = v
            if score == 1.0:
                return v

        if best_score >= 0.5 and best_answer:
            return best_answer

        # substring fallback
        for k, v in faqs.items():
            if self._normalize(k) in qnorm:
                return v

        return "No direct FAQ hit. Try specifics or provide username."

    # FEATURE: Code Execution tool (very limited; unsafe for untrusted code)
    def execute_code(self, code: str) -> Dict[str, Any]:
        try:
            # minimal sandbox — extremely limited
            globals_dict = {"__builtins__": {"len": len, "range": range}}
            locals_dict: Dict[str, Any] = {}
            exec(code, globals_dict, locals_dict)
            return {"ok": True, "locals": locals_dict}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # FEATURE: OpenAPI tool stub (replace base_url and auth)
    def openapi_call(self, path: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Stubbed: return a canned response to show tool integration
        return {"ok": True, "path": path, "method": method, "payload": payload}

    # FEATURE: MCP-style tool for messaging to external system (demo)
    def mcp_send(self, channel: str, message: str) -> Dict[str, Any]:
        rec = {"ts": time.time(), "channel": channel, "message": message}
        history = self.memory.get_global("mcp") or []
        history.append(rec)
        self.memory.set_global("mcp", history)
        return {"ok": True}
