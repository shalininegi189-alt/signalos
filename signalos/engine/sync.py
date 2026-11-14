"""OS MODULE 2 - Process synchronisation.

* CountingSemaphore / Mutex  - tick-driven (non-blocking) semaphores with a FIFO
  wait queue. Every road segment is a counting semaphore (its capacity) and every
  intersection box is a mutex. Their wait queues feed the deadlock detector.
* BoundedBuffer              - classic producer/consumer buffer built on real
  threading.Condition objects. Gate threads (producers) feed the simulation
  loop (consumer); the DB writer is a second producer/consumer pair.
* RWLock                     - readers/writer lock (writer preference) that keeps
  the simulation state consistent between the tick thread and API threads.
"""
from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager


class CountingSemaphore:
    """Counting semaphore for the single-threaded, tick-driven simulation.

    try_acquire() never blocks: if the resource is exhausted the caller is put on
    the FIFO wait queue and False is returned ("P" operation that would block).
    """

    def __init__(self, name: str, value: int):
        self.name = name
        self.capacity = value
        self.value = value
        self.holders: list = []
        self.waiters: deque = deque()

    def try_acquire(self, owner) -> bool:
        if self.value > 0:
            self.value -= 1
            self.holders.append(owner)
            if owner in self.waiters:
                self.waiters.remove(owner)
            return True
        if owner not in self.waiters:
            self.waiters.append(owner)
        return False

    def release(self, owner) -> bool:  # "V" operation
        if owner in self.holders:
            self.holders.remove(owner)
            self.value += 1
            return True
        return False

    def cancel_wait(self, owner) -> None:
        if owner in self.waiters:
            self.waiters.remove(owner)

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "capacity": self.capacity,
            "holders": list(self.holders),
            "waiters": list(self.waiters),
        }


class Mutex(CountingSemaphore):
    """Binary semaphore guarding an intersection box."""

    def __init__(self, name: str):
        super().__init__(name, 1)

    @property
    def owner(self):
        return self.holders[0] if self.holders else None


class BoundedBuffer:
    """Producer/consumer bounded buffer (real threads, Condition variables)."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._items: deque = deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self.produced = 0
        self.consumed = 0
        self.blocked_puts = 0
        self.dropped = 0

    def put(self, item, timeout: float | None = None) -> bool:
        """Blocking put. Returns False if it timed out while the buffer was full."""
        with self._not_full:
            if len(self._items) >= self.capacity:
                self.blocked_puts += 1
            while len(self._items) >= self.capacity:
                if not self._not_full.wait(timeout):
                    self.dropped += 1
                    return False
            self._items.append(item)
            self.produced += 1
            self._not_empty.notify()
            return True

    def put_nowait(self, item) -> bool:
        with self._lock:
            if len(self._items) >= self.capacity:
                self.dropped += 1
                return False
            self._items.append(item)
            self.produced += 1
            self._not_empty.notify()
            return True

    def get(self, timeout: float | None = None):
        with self._not_empty:
            while not self._items:
                if not self._not_empty.wait(timeout):
                    return None
            item = self._items.popleft()
            self.consumed += 1
            self._not_full.notify()
            return item

    def drain(self, max_items: int = 1_000_000) -> list:
        out = []
        with self._lock:
            while self._items and len(out) < max_items:
                out.append(self._items.popleft())
                self.consumed += 1
            if out:
                self._not_full.notify_all()
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def stats(self) -> dict:
        return {
            "size": len(self),
            "capacity": self.capacity,
            "produced": self.produced,
            "consumed": self.consumed,
            "blocked_puts": self.blocked_puts,
            "dropped": self.dropped,
        }


class RWLock:
    """Readers-writer lock with writer preference (prevents writer starvation)."""

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def acquire_read(self):
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        with self._cond:
            self._writers_waiting += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._writers_waiting -= 1
            self._writer = True

    def release_write(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()

    @contextmanager
    def read(self):
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write(self):
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()
