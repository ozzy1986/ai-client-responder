"""Защита модели от перегрузки на публичном сайте.

Модель одна (Ollama на ноутбуке, запросы обрабатываются по очереди), поэтому:
  - одновременно идёт не больше одного анализа — остальным сразу «модель занята»;
  - с одного IP — не больше N анализов в час.
Состояние в памяти процесса: uvicorn запущен с одним воркером, этого достаточно.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager


class ModelBusy(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after_s: int):
        super().__init__(retry_after_s)
        self.retry_after_s = retry_after_s


class AnalyzeGate:
    def __init__(self, per_ip_per_hour: int, window_s: int = 3600):
        self.limit = per_ip_per_hour
        self.window_s = window_s
        self._busy = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._hits_lock = threading.Lock()

    def _check_rate(self, ip: str) -> None:
        now = time.monotonic()
        with self._hits_lock:
            hits = self._hits[ip]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                raise RateLimited(int(self.window_s - (now - hits[0])) + 1)
            hits.append(now)

    @contextmanager
    def slot(self, ip: str) -> Iterator[None]:
        if not self._busy.acquire(blocking=False):
            raise ModelBusy
        try:
            self._check_rate(ip)
            yield
        finally:
            self._busy.release()
