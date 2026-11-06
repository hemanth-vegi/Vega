"""
Background Thread Supervisor for Vega 2.0.
Monitors background daemons, logs tracebacks, and automatically restarts crashed workers
with exponential backoff and pub/sub event alerts.
"""

import threading
import time
import traceback
import logging
from typing import Callable, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class SupervisedTask:
    """Supervises a background daemon thread and recovers automatically from crashes."""

    def __init__(
        self,
        name: str,
        target: Callable[[threading.Event], None],
        base_delay: float = 2.0,
        max_retries: int = 5,
        on_restart_callback: Optional[Callable[[str, int, Exception], None]] = None
    ):
        self.name = name
        self.target = target
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.on_restart_callback = on_restart_callback
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.retries = 0
        self.is_alive = False

    def start(self):
        """Starts the supervisor thread."""
        self.stop_event.clear()
        self.retries = 0
        self.thread = threading.Thread(target=self._run_loop, name=f"Supervisor-{self.name}", daemon=True)
        self.thread.start()

    def stop(self):
        """Signals worker to stop cleanly."""
        self.stop_event.set()
        self.is_alive = False

    def _run_loop(self):
        self.is_alive = True
        while not self.stop_event.is_set():
            try:
                logging.info(f"[{self.name}] Worker started.")
                self.target(self.stop_event)
                # If target returns cleanly, we do not restart unless requested
                break
            except Exception as exc:
                self.retries += 1
                tb = traceback.format_exc()
                logging.error(f"[{self.name}] Worker crashed (Attempt {self.retries}/{self.max_retries}): {exc}\n{tb}")

                if self.on_restart_callback:
                    try:
                        self.on_restart_callback(self.name, self.retries, exc)
                    except Exception:
                        pass

                if self.retries >= self.max_retries:
                    logging.critical(f"[{self.name}] Maximum retries reached. Worker disabled.")
                    self.is_alive = False
                    break

                delay = self.base_delay * (2 ** (self.retries - 1))
                time.sleep(delay)
        self.is_alive = False

