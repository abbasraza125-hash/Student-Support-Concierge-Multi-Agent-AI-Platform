"""
Root Agent + Gemini LLM wrapper + Student DB loader (ADK-Compatible)
"""

import os
import csv
import json
import logging
from pathlib import Path
from typing import Optional, Any

# Enable clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# --------------------------------------------------------------
# Gemini LLM Wrapper
# --------------------------------------------------------------

class GeminiLLM:
    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model
        self.client: Optional[Any] = None
        self.available = False

        api_key = os.getenv("GEMINI_API_KEY")

        try:
            from google import genai  # load safely
            if api_key:
                # some SDK versions accept api_key param; others rely on ADC
                try:
                    self.client = genai.Client(api_key=api_key)
                except TypeError:
                    self.client = genai.Client()
            else:
                self.client = genai.Client()  # ADC fallback
            self.available = True
            logging.info("GeminiLLM: Successfully initialized.")
        except Exception as e:
            logging.warning(f"GeminiLLM: Initialization failed: {e}")
            self.available = False
            self.client = None

    def generate(self, prompt: str) -> str:
        if not self.available or self.client is None:
            return self._mock_response(prompt)

        try:
            # Try modern Responses API
            if hasattr(self.client, "responses") and hasattr(self.client.responses, "create"):
                resp = self.client.responses.create(model=self.model, input=prompt)
                if hasattr(resp, "output") and resp.output:
                    return "\n".join(
                        getattr(piece, "content", str(piece)) for piece in resp.output
                    )
                return str(resp)

            # Fallback older SDK pattern
            if hasattr(self.client, "models") and hasattr(self.client.models, "generate_content"):
                resp = self.client.models.generate_content(model=self.model, contents=prompt)
                text = getattr(resp, "text", None)
                if isinstance(text, str) and text:
                    return text
                out = getattr(resp, "output", None)
                if isinstance(out, (list, tuple)) and len(out) > 0:
                    return getattr(out[0], "content", str(out[0]))
                return str(resp)

            logging.warning("GeminiLLM: Client API shape not recognized; using mock")
            return self._mock_response(prompt)

        except Exception as e:
            logging.error(f"GeminiLLM: API call failed: {e}")
            return self._mock_response(prompt)

    def _mock_response(self, prompt: str) -> str:
        """Local fallback when Gemini isn't available or fails."""
        p = (prompt or "").lower()
        if "orientation" in p:
            return "Follow the LMS orientation module and complete the orientation steps."
        if "lockdown" in p:
            return "Install LockDown Browser and follow your course's exam instructions."
        if "ms365" in p or "office" in p:
            return "Sign in at portal.office.com using your college email."
        return f"(Mock) I don't have Gemini access here. You asked: {prompt}"


# --------------------------------------------------------------
# Student DB Loader
# --------------------------------------------------------------

def load_student_db():
    base = Path(__file__).parent.parent / "samples" / "data"
    base.mkdir(parents=True, exist_ok=True)

    csv_file = base / "student_db.csv"

    # Write a small default DB if missing
    if not csv_file.exists():
        csv_file.write_text(
            "username,email,orientation_done,access_codes\n"
            "alice,alice@example.com,yes,AC-111\n"
            "bob,bob@example.com,no,AC-222\n",
            encoding="utf-8",
        )

    db = {}
    with open(csv_file, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if "username" in row and row["username"]:
                db[row["username"]] = row
    return db


# --------------------------------------------------------------
# Root Agent Builder (ADK compatible)
# --------------------------------------------------------------

# local imports of project modules (these should exist in your package)
from .agents import (
    OrientationAgent,
    TechSupportAgent,
    ProgressAgent,
    FAQAgent,
    ParallelAgent,
    SequentialAgent,
)
from .tools import Tools
from .memory import MemoryStore
from .longrunning import LongRunningManager


def build_root_agent():
    """Factory that constructs the full root ADK-style agent."""
    logging.info("build_root_agent: starting...")

    # Load DB and initialize components
    student_db = load_student_db()
    memory = MemoryStore()
    tools = Tools(student_db=student_db, memory=memory)
    llm = GeminiLLM()

    # Create subagents
    orientation = OrientationAgent(llm, tools, memory)
    tech = TechSupportAgent(llm, tools, memory)
    progress = ProgressAgent(llm, tools, memory)
    faq = FAQAgent(llm, tools, memory)

    # Composite agents
    sequential = SequentialAgent([orientation, progress])
    parallel = ParallelAgent([tech, faq])

    # Root orchestrator
    class RootAgent:
        def __init__(self):
            self.memory = memory
            self.tools = tools
            self.subagents = {
                "orientation": orientation,
                "tech": tech,
                "progress": progress,
                "faq": faq,
                "seq": sequential,
                "par": parallel,
            }
            self.lr_manager = LongRunningManager(memory)

        def route(self, sid: str, message: str) -> str:
            text = (message or "").lower()
            # simple routing heuristics
            if any(k in text for k in ["orientation", "onboarding"]):
                agent = self.subagents["orientation"]
            elif any(k in text for k in ["lockdown", "respondus", "ms365", "login", "password"]):
                agent = self.subagents["tech"]
            elif any(k in text for k in ["access code", "activate", "course status"]):
                agent = self.subagents["progress"]
            else:
                agent = self.subagents["faq"]

            # Persist user message and assistant response to memory (safe)
            try:
                self.memory.append_history(sid, "user", message)
            except Exception:
                logging.exception("Failed to append user history")

            try:
                resp = agent.handle(sid, message)
            except Exception as e:
                logging.exception("Agent handle() raised an exception")
                resp = "I'm sorry — something went wrong while handling your request."

            try:
                self.memory.append_history(sid, "assistant", resp)
            except Exception:
                logging.exception("Failed to append assistant history")

            return resp

    return RootAgent()


# --------------------------------------------------------------
# Global ADK root_agent (defensive initialization)
# --------------------------------------------------------------

root_agent = None
try:
    root_agent = build_root_agent()
    logging.info("root_agent: initialization succeeded")
except Exception as e:
    logging.exception("root_agent: initialization failed; module still importable; call build_root_agent() later if needed")
    root_agent = None
