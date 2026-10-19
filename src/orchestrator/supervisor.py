"""
Production Supervisor Subsystem for Funbot.
Dual-layer process supervisor managing the MasterTradingOrchestrator daemon lifecycle.
Provides crash isolation, exponential backoff, crash-loop circuit breaker,
and automated recycling on watchdog staleness or memory leaks.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity
from src.orchestrator.heartbeat import HeartbeatWatchdog, WatchdogStatus, is_process_running

logger = logging.getLogger("ProductionSupervisor")

DEFAULT_CRASH_WINDOW_SECONDS = 900.0  # 15 minutes
MAX_CRASH_RESTARTS_IN_WINDOW = 5
DEFAULT_BACKOFF_SCHEDULE = (5.0, 10.0, 20.0, 40.0, 60.0)
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 15.0


@dataclass
class SupervisorConfig:
    """Configuration options for ProductionSupervisor."""

    child_args: List[str] = field(default_factory=lambda: ["daemon"])
    max_restarts_in_window: int = MAX_CRASH_RESTARTS_IN_WINDOW
    window_seconds: float = DEFAULT_CRASH_WINDOW_SECONDS
    backoff_schedule: Tuple[float, ...] = DEFAULT_BACKOFF_SCHEDULE
    shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
    heartbeat_file: str = "data/telemetry/heartbeat.json"
    stale_threshold_seconds: float = 60.0
    memory_ceiling_mb: float = 512.0
    watchdog_poll_interval_seconds: float = 5.0
    python_executable: str = sys.executable


class ProductionSupervisor:
    """
    Parent process supervisor that spawns, monitors, and auto-heals
    the live trading daemon child process.
    """

    def __init__(
        self,
        config: Optional[SupervisorConfig] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
        watchdog: Optional[HeartbeatWatchdog] = None,
    ):
        self.config = config or SupervisorConfig()
        self.alerts = alert_dispatcher or AlertDispatcher()
        self.watchdog = watchdog or HeartbeatWatchdog(
            filepath=self.config.heartbeat_file,
            stale_threshold_seconds=self.config.stale_threshold_seconds,
            memory_ceiling_mb=self.config.memory_ceiling_mb,
        )

        self._process: Optional[subprocess.Popen] = None
        self._is_running = False
        self._should_terminate = False
        self._restart_timestamps: List[float] = []
        self._total_restarts = 0
        self._circuit_breaker_tripped = False
        self._consecutive_crashes = 0
        self.start_time = time.time()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def child_pid(self) -> Optional[int]:
        if self._process and self._process.poll() is None:
            return self._process.pid
        return None

    @property
    def is_circuit_breaker_tripped(self) -> bool:
        return self._circuit_breaker_tripped

    @property
    def total_restarts(self) -> int:
        return self._total_restarts

    def get_recent_restart_count(self) -> int:
        """Returns number of restarts within the sliding window."""
        now = time.time()
        cutoff = now - self.config.window_seconds
        self._restart_timestamps = [t for t in self._restart_timestamps if t >= cutoff]
        return len(self._restart_timestamps)

    def record_restart(self) -> bool:
        """
        Records a restart occurrence.
        Returns True if within safe bounds, False if circuit breaker tripped.
        """
        now = time.time()
        self._restart_timestamps.append(now)
        self._total_restarts += 1
        self._consecutive_crashes += 1

        recent_count = self.get_recent_restart_count()
        if recent_count >= self.config.max_restarts_in_window:
            self._circuit_breaker_tripped = True
            logger.critical(
                f"Crash loop circuit breaker TRIPPED: {recent_count} crashes in "
                f"{self.config.window_seconds / 60.0:.1f} minutes! Auto-restart halted."
            )
            return False
        return True

    def get_backoff_delay(self) -> float:
        """Calculates backoff delay based on consecutive crashes."""
        idx = min(self._consecutive_crashes - 1, len(self.config.backoff_schedule) - 1)
        if idx < 0:
            return 0.0
        return self.config.backoff_schedule[idx]

    def spawn_child(self) -> subprocess.Popen:
        """Spawns the MasterTradingOrchestrator daemon child process."""
        cmd = [self.config.python_executable, "main.py"] + self.config.child_args
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = "."

        logger.info(f"Spawning child process: {' '.join(cmd)}")
        self._process = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            env=env,
        )
        logger.info(f"Trading daemon child process active with PID: {self._process.pid}")
        return self._process

    def terminate_child(self, force: bool = False, timeout: Optional[float] = None) -> int:
        """
        Signals child process for graceful termination, escalating to SIGKILL if uncooperative.
        """
        if not self._process:
            return 0

        pid = self._process.pid
        wait_time = timeout or self.config.shutdown_timeout_seconds

        if self._process.poll() is not None:
            return self._process.returncode

        logger.info(f"Terminating child process (PID {pid}, force={force})...")
        try:
            if force:
                self._process.kill()
            else:
                self._process.terminate()

            # Wait for graceful exit
            return self._process.wait(timeout=wait_time)
        except subprocess.TimeoutExpired:
            logger.warning(f"Child process {pid} did not exit within {wait_time}s. Escalating to kill().")
            self._process.kill()
            try:
                return self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.error(f"Process {pid} could not be killed.")
                return -1

    async def run(self):
        """Main async supervisor control loop."""
        self._is_running = True
        self._should_terminate = False
        self._install_signals()

        await self.alerts.dispatch(
            AlertEvent(
                event_type="SUPERVISOR_START",
                severity=AlertSeverity.INFO,
                title="Production Supervisor Active",
                message=(
                    f"Production Supervisor started. Monitoring child command: "
                    f"`{' '.join(self.config.child_args)}`."
                ),
                metadata={"supervisor_pid": os.getpid()},
            )
        )

        try:
            while self._is_running and not self._should_terminate:
                # 1. Spawn child process if not running
                if not self._process or self._process.poll() is not None:
                    if self._circuit_breaker_tripped:
                        logger.error("Supervisor idle: crash loop circuit breaker is tripped.")
                        await asyncio.sleep(self.config.watchdog_poll_interval_seconds)
                        continue

                    self.spawn_child()
                    # Allow 3 seconds for initial boot & first heartbeat
                    await asyncio.sleep(3.0)

                # 2. Check child process status
                exit_code = self._process.poll()
                if exit_code is not None:
                    await self._handle_child_exit(exit_code)
                    continue

                # 3. Check watchdog health
                await self._check_watchdog_health()

                await asyncio.sleep(self.config.watchdog_poll_interval_seconds)

        finally:
            self._is_running = False
            if self._process and self._process.poll() is None:
                self.terminate_child(force=False)
            logger.info("Production Supervisor stopped.")

    async def _handle_child_exit(self, exit_code: int):
        """Processes unexpected child process termination."""
        pid = self._process.pid if self._process else "UNKNOWN"
        logger.warning(f"Child process {pid} exited with code {exit_code}.")

        if self._should_terminate or exit_code == 0:
            logger.info("Child exited cleanly. No restart triggered.")
            self._is_running = False
            return

        is_safe = self.record_restart()
        delay = self.get_backoff_delay()

        if not is_safe:
            # Tripped circuit breaker
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="CRASH_LOOP_CIRCUIT_BREAKER_TRIPPED",
                    severity=AlertSeverity.CRITICAL,
                    title="🚨 CRASH LOOP DETECTED",
                    message=(
                        f"Daemon crashed {self.get_recent_restart_count()} times in 15 minutes. "
                        f"Supervisor has halted automatic restarts to protect capital and RPC quotas."
                    ),
                    metadata={"exit_code": exit_code, "last_pid": pid},
                )
            )
            return

        await self.alerts.dispatch(
            AlertEvent(
                event_type="DAEMON_UNEXPECTED_EXIT",
                severity=AlertSeverity.WARNING,
                title="Trading Daemon Crashed",
                message=(
                    f"Child process (PID {pid}) exited unexpectedly with code {exit_code}. "
                    f"Auto-restart #{self._total_restarts} in {delay:.1f}s..."
                ),
                metadata={"exit_code": exit_code, "backoff_sec": delay, "recent_crashes": self.get_recent_restart_count()},
            )
        )

        logger.info(f"Applying backoff delay of {delay:.1f}s before restart...")
        await asyncio.sleep(delay)

    async def _check_watchdog_health(self):
        """Evaluates watchdog status and recycles unhealthy child."""
        if not self._process or self._process.poll() is not None:
            return

        status = self.watchdog.check_health(self._process.pid)
        if not status.is_healthy:
            logger.warning(f"Watchdog failure detected: {status.reason}")

            # Notify operator of watchdog intervention
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="SUPERVISOR_WATCHDOG_INTERVENTION",
                    severity=AlertSeverity.WARNING,
                    title="Watchdog Recycling Child Process",
                    message=f"Supervisor recycling child process due to health failure: {status.reason}",
                    metadata=status.to_dict(),
                )
            )

            # Initiate graceful recycle
            self.terminate_child(force=False, timeout=self.config.shutdown_timeout_seconds)
            self.record_restart()
            delay = self.get_backoff_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    def request_shutdown(self):
        """Requests supervisor and child to shut down gracefully."""
        logger.info("Supervisor shutdown requested.")
        self._should_terminate = True
        self._is_running = False
        if self._process and self._process.poll() is None:
            self.terminate_child(force=False)

    def reset_circuit_breaker(self):
        """Resets crash loop circuit breaker counters."""
        self._circuit_breaker_tripped = False
        self._restart_timestamps.clear()
        self._consecutive_crashes = 0
        logger.info("Supervisor crash loop circuit breaker manually reset.")

    def get_status(self) -> Dict[str, Any]:
        """Provides status summary dictionary for CLI and Streamlit display."""
        now = time.time()
        child_p = self.child_pid
        wd_status = self.watchdog.check_health(child_p)

        return {
            "supervisor_pid": os.getpid(),
            "child_pid": child_p,
            "is_running": self._is_running,
            "uptime_seconds": round(now - self.start_time, 1),
            "recent_restarts_15m": self.get_recent_restart_count(),
            "total_restarts": self._total_restarts,
            "circuit_breaker_tripped": self._circuit_breaker_tripped,
            "child_command": " ".join([self.config.python_executable, "main.py"] + self.config.child_args),
            "watchdog": wd_status.to_dict(),
        }

    def _install_signals(self):
        """Registers termination signals for graceful forwarding."""
        def _handler(signum, frame):
            logger.info(f"Supervisor received signal {signum}. Propagating shutdown...")
            self.request_shutdown()

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, _handler)
        except (ValueError, AttributeError):
            pass
