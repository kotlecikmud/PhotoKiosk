"""Background order jobs with real progress, so the browser can show a progress bar."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

JOB_TTL_SECONDS = 600


class OrderJobs:
    """Runs orders one at a time (CPU bound, single SQLite writer) and tracks their state."""

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orders")
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._by_request: Dict[str, str] = {}

    def submit(
        self,
        work: Callable[[Callable[[int, int], None]], Dict[str, Any]],
        request_id: Optional[str] = None,
    ) -> str:
        """``work(progress)`` must return a result dict; ``progress(done, total)`` is optional."""
        with self._lock:
            self._gc()
            if request_id and request_id in self._by_request:  # double tap on "Submit"
                return self._by_request[request_id]
            job_id = uuid.uuid4().hex[:16]
            self._jobs[job_id] = {"status": "queued", "done": 0, "total": 0, "created": time.time()}
            if request_id:
                self._by_request[request_id] = job_id
        self._pool.submit(self._run, job_id, work)
        return job_id

    def _run(self, job_id: str, work) -> None:
        def progress(done: int, total: int) -> None:
            with self._lock:
                self._jobs[job_id].update(done=done, total=total)

        with self._lock:
            self._jobs[job_id]["status"] = "running"
        try:
            result = work(progress)
            with self._lock:
                self._jobs[job_id].update(status="completed", result=result)
        except Exception as exc:  # reported to the browser, order stays FAILED in the DB
            with self._lock:
                self._jobs[job_id].update(status="failed", error=str(exc))

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _gc(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for jid in [j for j, v in self._jobs.items() if v["created"] < cutoff and v["status"] in ("completed", "failed")]:
            del self._jobs[jid]
        live = set(self._jobs)
        for rid in [r for r, j in self._by_request.items() if j not in live]:
            del self._by_request[rid]
