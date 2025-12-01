"""
Long-running manager and LoopAgent
FEATURE: Long-running operations (pause/resume), Loop agents
"""
import threading
import time
from typing import Callable, Dict, Any


class LongRunningManager:
    """
    Simple demo manager that can start jobs, mark status, and allow
    pause/resume flags (cooperative pause must be implemented by the job).
    """
    def __init__(self, memory: Any = None):
        self.memory = memory
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def start_job(self, job_id: str, target: Callable, *args, **kwargs) -> str:
        job = {"status": "running", "thread": None}

        def wrapper():
            try:
                target(*args, **kwargs)
                job["status"] = "done"
            except Exception:
                job["status"] = "failed"

        t = threading.Thread(target=wrapper, daemon=True)
        job["thread"] = t
        self.jobs[job_id] = job
        t.start()
        return job_id

    def pause_job(self, job_id: str) -> bool:
        # Demo: set a status flag — actual cooperative pause requires the job to check the flag
        job = self.jobs.get(job_id)
        if job:
            job["status"] = "paused"
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.get("status") == "paused":
            job["status"] = "running"
            return True
        return False

    def get_status(self, job_id: str) -> str:
        job = self.jobs.get(job_id)
        if not job:
            return "not_found"
        return job.get("status", "unknown")


class LoopAgent:
    """
    LoopAgent runs a user-provided check function periodically until it returns False.
    The check_fn should return True to continue looping, False to stop.
    """
    def __init__(self, check_fn: Callable[[], bool], interval_seconds: int = 5):
        self.check_fn = check_fn
        self.interval = interval_seconds
        self._running = False
        self._thread: threading.Thread | None = None

    def _run_loop(self):
        self._running = True
        while self._running:
            try:
                cont = self.check_fn()
            except Exception:
                # If the check function fails, stop the loop
                cont = False
            if not cont:
                break
            time.sleep(self.interval)
        self._running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return  # already running
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
            self._thread = None
