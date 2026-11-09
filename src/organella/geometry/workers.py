"""Worker processes for the heavy, single-threaded steps of meshing.

Meshing one instance is mostly numpy and C code that holds the interpreter or fights over memory bandwidth, and mesh
decimation is not safe in two threads at once (see ``surfaces._DECIMATION_LOCK``), so threads do not make it faster.
Separate processes have neither limit: a step handed to ``run`` is executed in one of a few worker processes while the
calling thread waits, and the lock only ever has one holder per process.

Off (``workers`` of 0) everything runs in the calling thread, as before. A worker never starts workers of its own.
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable

_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_size = 0


def available_cores() -> int:
    """Cores this process may use: what the scheduler gave a job, not what the node has."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def configure(workers: int) -> None:
    """How many worker processes later calls may use (0: none). A change closes the pool that was running."""
    global _size
    with _lock:
        if workers != _size:
            _shutdown_locked()
        _size = max(0, int(workers))


def _shutdown_locked() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=True, cancel_futures=True)
        _pool = None


def shutdown() -> None:
    with _lock:
        _shutdown_locked()


atexit.register(shutdown)


def in_worker() -> bool:
    return multiprocessing.current_process().name != "MainProcess"


def run(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``function(*args, **kwargs)`` in a worker process, or here when there are none. ``function`` must be importable."""
    global _pool
    if _size <= 0 or in_worker():
        return function(*args, **kwargs)
    with _lock:
        if _pool is None:      # one pool, however many threads ask for it at once
            _pool = ProcessPoolExecutor(_size, mp_context=multiprocessing.get_context("spawn"))
        pool = _pool
    return pool.submit(function, *args, **kwargs).result()


def pool_size() -> int:
    """How many worker processes there are (0: none), which is how many steps are worth having in flight."""
    return _size
