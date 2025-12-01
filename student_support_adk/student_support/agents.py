"""
Agents implementations: OrientationAgent, TechSupportAgent, ProgressAgent, FAQAgent
FEATURES: Multi-agent, Agent powered by LLM, A2A Protocol calls, Parallel/Sequential/Loop agents
"""
from typing import Dict, Any, List, Callable
from .memory import MemoryStore
from .tools import Tools
from .longrunning import LoopAgent
from .evaluation import evaluate_agent_response

# Small Agent-to-Agent helper (A2A)
class A2A:
    @staticmethod
    def call(agent: "BaseAgent", sid: str, message: str) -> str:
        return agent.handle(sid, message)


class BaseAgent:
    def __init__(self, llm: Any, tools: Tools, memory: MemoryStore):
        self.llm = llm
        self.tools = tools
        self.memory = memory

    def handle(self, sid: str, message: str) -> str:
        raise NotImplementedError


class OrientationAgent(BaseAgent):
    def handle(self, sid: str, message: str) -> str:
        # Use csv lookup tool to check if orientation completed
        username = self.memory.get_session(sid)["username"]
        rec = self.tools.csv_lookup(username)
        if rec.get("orientation_done", "no").lower() == "yes":
            return "You have completed the orientation. Check the Orientation module for your certificate."
        # Otherwise ask LLM for step-by-step or return helpful instructions
        prompt = f"orientation steps for user {username}: {message}"
        resp = self.llm.generate(prompt)
        return resp


class TechSupportAgent(BaseAgent):
    def handle(self, sid: str, message: str) -> str:
        m = message.lower()
        if "lockdown" in m or "respondus" in m:
            return self.llm.generate("lockdown browser steps")
        if "ms365" in m or "office" in m:
            return self.tools.google_search("ms365")
        if "can't login" in m or "forgot password" in m:
            return "Try resetting your password via the college portal password reset flow. If that fails, contact helpdesk@example.com."
        return self.llm.generate(message)


class ProgressAgent(BaseAgent):
    def handle(self, sid: str, message: str) -> str:
        m = message.lower()
        if "access code" in m:
            username = self.memory.get_session(sid)["username"]
            rec = self.tools.csv_lookup(username)
            code = rec.get("access_codes")
            if code:
                # Example of calling another agent for verification (A2A)
                # In a real system you'd call the TechSupportAgent or a verifier agent
                return f"Your access code: {code}"
            return "No access code on file. Please verify username."
        if any(tok in m for tok in ["activated", "activate", "course status", "activated?"]):
            return "I can check your course activation status if you give me the course name."
        return self.llm.generate(message)


class FAQAgent(BaseAgent):
    def handle(self, sid: str, message: str) -> str:
        # Use the google_search stub tool for FAQ-like answers
        return self.tools.google_search(message)


# ParallelAgent and SequentialAgent implementations
class ParallelAgent(BaseAgent):
    """
    Runs multiple agents in parallel threads and aggregates their responses.
    """
    def __init__(self, agents: List[BaseAgent]):
        # note: this agent does not use llm/tools/memory directly, but kept for API uniformity
        super().__init__(llm=None, tools=None, memory=None)  # type: ignore
        self.agents = agents

    def handle(self, sid: str, message: str) -> str:
        import threading

        results: List[str] = []
        lock = threading.Lock()

        def run_agent(agent: BaseAgent):
            try:
                r = agent.handle(sid, message)
            except Exception as e:
                r = f"Agent error: {e}"
            with lock:
                results.append(r)

        threads = [threading.Thread(target=run_agent, args=(a,)) for a in self.agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Combine results with separator
        return "\n---\n".join(results)


class SequentialAgent(BaseAgent):
    """
    Runs agents in sequence, passing the output of one as input to the next.
    """
    def __init__(self, agents: List[BaseAgent]):
        super().__init__(llm=None, tools=None, memory=None)  # type: ignore
        self.agents = agents

    def handle(self, sid: str, message: str) -> str:
        state = message
        for a in self.agents:
            state = a.handle(sid, state)
        return state

# Note: LoopAgent usage examples live in longrunning.py and can be integrated here.
