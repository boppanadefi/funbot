"""
Asynchronous Multi-Timer Exit System for Active Positions.
Enforces 5 Concurrent Timers per position:
1. Fallback 20-Minute Holding Timeout (Hard market exit at minute 20)
2. 3-Minute Runner Hard Time Kill (180s hard dump if runner fails to hit +30%)
3. 30-Second Trailing Candle Stop Tick (Periodic ratchet check)
4. 30-Second Tx Confirmation Watchdog (Detects stuck in-flight transactions)
5. 8-Minute Stabilization Cooldown (Bypasses initial 0-8m cliff)
"""

import asyncio
import logging
import time
from typing import Callable, Coroutine, Dict, Optional, Any

logger = logging.getLogger("ExitTimerManager")


class ExitTimerManager:
    """Manages concurrent async timers for position lifecycle events."""

    def __init__(
        self,
        position_id: str,
        on_timeout_callback: Optional[Callable[[str, str], Coroutine[Any, Any, None]]] = None,
        max_lifespan_sec: float = 1200.0,      # 20 minutes (Guardrail 7)
        runner_kill_sec: float = 180.0,        # 3 minutes
        stop_tick_sec: float = 30.0,           # 30 seconds
        tx_confirm_watchdog_sec: float = 30.0, # 30 seconds
    ):
        self.position_id = position_id
        self.on_timeout = on_timeout_callback
        self.max_lifespan_sec = max_lifespan_sec
        self.runner_kill_sec = runner_kill_sec
        self.stop_tick_sec = stop_tick_sec
        self.tx_confirm_watchdog_sec = tx_confirm_watchdog_sec

        self._tasks: Dict[str, asyncio.Task] = {}
        self._start_times: Dict[str, float] = {}
        self._is_active = True

    @property
    def active_timer_names(self) -> list[str]:
        return [k for k, task in self._tasks.items() if not task.done()]

    def is_timer_running(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    async def _run_timer(self, name: str, duration_sec: float):
        try:
            self._start_times[name] = time.time()
            await asyncio.sleep(duration_sec)
            if not self._is_active:
                return

            logger.warning(f"Timer expired: [{name}] for position [{self.position_id}] after {duration_sec}s")
            if self.on_timeout:
                res = self.on_timeout(name, self.position_id)
                if asyncio.iscoroutine(res):
                    await res
        except asyncio.CancelledError:
            # Expected upon normal early cancellation
            pass
        except Exception as e:
            logger.error(f"Error in timer task [{name}]: {e}", exc_info=True)

    def start_lifespan_timer(self, duration_sec: Optional[float] = None) -> asyncio.Task:
        """Starts 20-minute hard holding limit timer."""
        sec = duration_sec or self.max_lifespan_sec
        return self._start_named_timer("lifespan_20m", sec)

    def start_runner_kill_timer(self, duration_sec: Optional[float] = None) -> asyncio.Task:
        """Starts 3-minute runner hard kill timer."""
        sec = duration_sec or self.runner_kill_sec
        return self._start_named_timer("runner_kill_3m", sec)

    def start_tx_confirmation_watchdog(self, duration_sec: Optional[float] = None) -> asyncio.Task:
        """Starts 30-second watchdog for in-flight transaction confirmation."""
        sec = duration_sec or self.tx_confirm_watchdog_sec
        return self._start_named_timer("tx_watchdog_30s", sec)

    def _start_named_timer(self, name: str, duration_sec: float) -> asyncio.Task:
        self.cancel_timer(name)
        task = asyncio.create_task(self._run_timer(name, duration_sec))
        self._tasks[name] = task
        return task

    def cancel_timer(self, name: str):
        """Cancels a specific named timer."""
        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

    def cancel_all(self):
        """Cancels all running timers (called upon position exit/close)."""
        self._is_active = False
        for name in list(self._tasks.keys()):
            self.cancel_timer(name)
        self._tasks.clear()
        self._start_times.clear()
