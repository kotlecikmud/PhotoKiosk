"""Copies selected photos into the temporary session folder, one at a time."""
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Set

from devices.session_manager import get_temp_session_dir

logger = logging.getLogger(__name__)


class SessionStager:
    def __init__(self, session_id: int):
        self.session_id = session_id
        self.staged_files: Dict[str, str] = {}
        self._queued: Set[str] = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stager")

    def stage(self, filepath: str) -> None:
        with self._lock:
            if filepath in self._queued:
                return
            self._queued.add(filepath)
        self._executor.submit(self._copy, filepath)

    def _copy(self, filepath: str) -> None:
        try:
            dest = get_temp_session_dir(self.session_id) / Path(filepath).name
            if not dest.exists():
                shutil.copy2(filepath, dest)
            self.staged_files[filepath] = str(dest)
        except Exception:
            logger.warning("Could not stage %s", filepath, exc_info=True)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)  # let queued copies finish
