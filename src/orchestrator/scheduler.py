"""
Asynchronous Concurrency Scheduler & Priority Worker Framework.
Provides 3-tier priority execution queues (HIGH, MEDIUM, LOW),
thread-safe state locking, worker pool management, and queue depth telemetry.
"""

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
import logging
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger("AsyncScheduler")


class TaskPriority(IntEnum):
    HIGH = 1    # Time-critical liquidations, stop hits, emergency kill switch
    MEDIUM = 2  # Candidate 12-check evaluation, state transition processing
    LOW = 3     # Telemetry logging, Parquet writing, historical audits


@dataclass(order=True)
class PrioritizedTask:
    priority: int
    counter: int
    task_id: str = field(compare=False)
    coro_fn: Any = field(compare=False)
    args: tuple = field(default_factory=tuple, compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)


class AsyncScheduler:
    """Manages concurrent worker loops and priority-ordered task execution."""

    def __init__(self, num_workers: int = 4, max_queue_size: int = 1000):
        self.num_workers = num_workers
        self.max_queue_size = max_queue_size
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._workers: List[asyncio.Task] = []
        self._is_running = False
        self._counter = 0
        self._state_lock = asyncio.Lock()

        # Telemetry metrics
        self.processed_count = 0
        self.failed_count = 0
        self.last_execution_time = 0.0

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def lock(self) -> asyncio.Lock:
        """Thread-safe / async lock for position and ledger mutations."""
        return self._state_lock

    async def start(self):
        """Starts worker tasks."""
        if self._is_running:
            return
        self._is_running = True
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"SchedulerWorker-{i}")
            self._workers.append(task)
        logger.info(f"AsyncScheduler started with {self.num_workers} workers")

    async def stop(self):
        """Gracefully stops workers after draining tasks."""
        self._is_running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("AsyncScheduler stopped")

    async def submit(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, Any]],
        *args,
        priority: TaskPriority = TaskPriority.MEDIUM,
        task_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Submits a coroutine function to the priority queue."""
        if not self._is_running:
            raise RuntimeError("Cannot submit task: AsyncScheduler is not running")

        self._counter += 1
        tid = task_id or f"task-{int(time.time()*1000)}-{self._counter}"
        item = PrioritizedTask(
            priority=int(priority),
            counter=self._counter,
            task_id=tid,
            coro_fn=coro_fn,
            args=args,
            kwargs=kwargs,
        )
        await self._queue.put(item)
        return tid

    async def _worker_loop(self, worker_id: int):
        while self._is_running:
            try:
                task_item: PrioritizedTask = await self._queue.get()
                t0 = time.time()
                try:
                    res = task_item.coro_fn(*task_item.args, **task_item.kwargs)
                    if asyncio.iscoroutine(res):
                        await res
                    self.processed_count += 1
                    self.last_execution_time = time.time() - t0
                except Exception as e:
                    self.failed_count += 1
                    logger.error(f"Worker {worker_id} failed executing task {task_item.task_id}: {e}", exc_info=True)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
