"""
Thread-safe SSE Event Bus for Vega 2.0.
Publishes real-time events to all connected clients (browser, HUD, telemetry).
"""

import json
import queue
import threading
from typing import Dict, Any

_SUBSCRIBERS = []
_LOCK = threading.Lock()


def subscribe() -> queue.Queue:
    """Subscribes an SSE connection to event broadcasts."""
    q = queue.Queue(maxsize=100)
    with _LOCK:
        _SUBSCRIBERS.append(q)
    return q


def unsubscribe(q: queue.Queue):
    """Unsubscribes an SSE connection when closed."""
    with _LOCK:
        if q in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(q)


def broadcast(event_type: str, data: Any):
    """Broadcasts an event message formatted as SSE string to all subscribers."""
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _LOCK:
        dead = []
        for q in _SUBSCRIBERS:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # If subscriber buffer is full, remove it
                dead.append(q)
            except Exception:
                dead.append(q)
        for d in dead:
            if d in _SUBSCRIBERS:
                _SUBSCRIBERS.remove(d)

