"""
Heartbeat Pulse Publisher and Resource Watchdog for Funbot Production Orchestrator.
Maintains high-frequency liveliness status in JSON and SQLite WAL journal,
and provides staleness / memory ceiling health verification.
"""

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("Heartbeat")

DEFAULT_HEARTBEAT_PATH = "data/telemetry/heartbeat.json"
DEFAULT_PULSE_INTERVAL_SECONDS = 30.0
DEFAULT_STALE_THRESHOLD_SECONDS = 60.0
DEFAULT_MEMORY_CEILING_MB = 512.0


def get_process_rss_mb(pid: Optional[int] = None) -> float:
    """
    Returns the resident set size (RSS) memory in megabytes for the given PID
    (or current process if None) using cross-platform methods with zero dependencies.
    """
    target_pid = pid if pid is not None else os.getpid()

    # 1. Check if psutil is available
    try:
        import psutil
        proc = psutil.Process(target_pid)
        return proc.memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        pass

    # 2. Windows ctypes implementation
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            # PROCESS_QUERY_INFORMATION (0x0400) | PROCESS_VM_READ (0x0010)
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            desired_access = 0x0410 | PROCESS_QUERY_LIMITED_INFORMATION

            handle = ctypes.windll.kernel32.OpenProcess(desired_access, False, target_pid)
            if handle:
                try:
                    counters = PROCESS_MEMORY_COUNTERS()
                    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                    success = ctypes.windll.psapi.GetProcessMemoryInfo(
                        handle, ctypes.byref(counters), counters.cb
                    )
                    if success:
                        return float(counters.WorkingSetSize) / (1024.0 * 1024.0)
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        except Exception as e:
            logger.debug(f"Failed to query Windows process memory via ctypes: {e}")

    # 3. Linux /proc/<pid>/statm
    elif sys.platform.startswith("linux"):
        try:
            statm_path = Path(f"/proc/{target_pid}/statm")
            if statm_path.exists():
                with open(statm_path, "r", encoding="utf-8") as f:
                    parts = f.read().split()
                    if len(parts) >= 2:
                        pages = int(parts[1])
                        page_size_kb = os.sysconf("SC_PAGE_SIZE") / 1024.0
                        return (pages * page_size_kb) / 1024.0
        except Exception as e:
            logger.debug(f"Failed to read /proc memory: {e}")

    # Fallback default
    return 0.0


def is_process_running(pid: int) -> bool:
    """Checks whether a process with the given PID is currently active."""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


@dataclass
class HeartbeatPulse:
    """Snapshot data emitted by the heartbeat publisher."""

    timestamp: float
    pid: int
    uptime_seconds: float
    memory_rss_mb: float
    cpu_percent: float
    active_positions: int
    is_kill_switch_tripped: bool
    is_circuit_breaker_halted: bool
    last_slot: int
    mode: str = "DAEMON"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WatchdogStatus:
    """Health evaluation emitted by the HeartbeatWatchdog."""

    is_healthy: bool
    reason: str
    memory_rss_mb: float
    age_seconds: float
    is_stale: bool
    memory_breached: bool
    process_alive: bool
    last_pulse: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HeartbeatPublisher:
    """
    Publishes regular liveliness pulses to a local atomic JSON file and the SQLite WAL database.
    """

    def __init__(
        self,
        persistence_manager: Optional[Any] = None,
        filepath: str = DEFAULT_HEARTBEAT_PATH,
        interval_seconds: float = DEFAULT_PULSE_INTERVAL_SECONDS,
        active_positions_provider: Optional[Callable[[], int]] = None,
        kill_switch_provider: Optional[Callable[[], bool]] = None,
        circuit_breaker_provider: Optional[Callable[[], bool]] = None,
        slot_provider: Optional[Callable[[], int]] = None,
        mode: str = "DAEMON",
    ):
        self.persistence = persistence_manager
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.interval_seconds = interval_seconds

        self._active_positions_provider = active_positions_provider or (lambda: 0)
        self._kill_switch_provider = kill_switch_provider or (lambda: False)
        self._circuit_breaker_provider = circuit_breaker_provider or (lambda: False)
        self._slot_provider = slot_provider or (lambda: 0)
        self.mode = mode

        self.start_time = time.time()
        self._is_running = False
        self._task = None

    def create_pulse(self) -> HeartbeatPulse:
        """Constructs a HeartbeatPulse snapshot from current process state."""
        now = time.time()
        pid = os.getpid()
        uptime = max(0.0, now - self.start_time)
        rss_mb = get_process_rss_mb(pid)

        return HeartbeatPulse(
            timestamp=now,
            pid=pid,
            uptime_seconds=uptime,
            memory_rss_mb=round(rss_mb, 2),
            cpu_percent=0.0,
            active_positions=self._active_positions_provider(),
            is_kill_switch_tripped=self._kill_switch_provider(),
            is_circuit_breaker_halted=self._circuit_breaker_provider(),
            last_slot=self._slot_provider(),
            mode=self.mode,
        )

    def publish_pulse(self) -> HeartbeatPulse:
        """Emits a single heartbeat pulse to disk and SQLite WAL atomically."""
        pulse = self.create_pulse()
        data = pulse.to_dict()

        # 1. Atomic write to JSON file: write to temp file then replace
        temp_file = self.filepath.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, self.filepath)
        except Exception as e:
            logger.error(f"Failed writing heartbeat file to {self.filepath}: {e}")
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

        # 2. Persist to SQLite WAL journal if manager is configured
        if self.persistence:
            try:
                self.persistence.save_heartbeat(data)
            except Exception as e:
                logger.error(f"Failed persisting heartbeat to WAL database: {e}")

        logger.debug(f"Heartbeat pulse emitted: PID={pulse.pid}, Uptime={pulse.uptime_seconds:.1f}s, Mem={pulse.memory_rss_mb}MB")
        return pulse

    async def run_loop(self):
        """Asynchronous execution loop publishing at regular intervals."""
        import asyncio

        self._is_running = True
        logger.info(f"HeartbeatPublisher loop started (interval={self.interval_seconds}s, file={self.filepath})")
        try:
            while self._is_running:
                self.publish_pulse()
                await asyncio.sleep(self.interval_seconds)
        except asyncio.CancelledError:
            logger.info("HeartbeatPublisher loop received cancellation.")
        finally:
            self._is_running = False

    def stop(self):
        """Signals the background loop to stop."""
        self._is_running = False


class HeartbeatWatchdog:
    """
    Watches process heartbeat files and memory usage to diagnose stale, hung, or leaking processes.
    """

    def __init__(
        self,
        filepath: str = DEFAULT_HEARTBEAT_PATH,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
        memory_ceiling_mb: float = DEFAULT_MEMORY_CEILING_MB,
    ):
        self.filepath = Path(filepath)
        self.stale_threshold_seconds = stale_threshold_seconds
        self.memory_ceiling_mb = memory_ceiling_mb

    def read_latest_pulse(self) -> Optional[Dict[str, Any]]:
        """Reads latest heartbeat JSON data if available and valid."""
        if not self.filepath.exists():
            return None
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read heartbeat file {self.filepath}: {e}")
            return None

    def check_health(self, child_pid: Optional[int] = None) -> WatchdogStatus:
        """
        Evaluates health of the target process based on:
        - Heartbeat existence and age <= stale_threshold_seconds
        - Process memory RSS <= memory_ceiling_mb
        - Liveness of child_pid (if specified)
        """
        pulse = self.read_latest_pulse()
        now = time.time()

        if not pulse:
            # No heartbeat written yet
            target_pid = child_pid or 0
            is_alive = is_process_running(target_pid) if target_pid else False
            return WatchdogStatus(
                is_healthy=False,
                reason="MISSING_HEARTBEAT_FILE",
                memory_rss_mb=get_process_rss_mb(target_pid) if target_pid else 0.0,
                age_seconds=float("inf"),
                is_stale=True,
                memory_breached=False,
                process_alive=is_alive,
                last_pulse=None,
            )

        pulse_timestamp = float(pulse.get("timestamp", 0.0))
        age_sec = max(0.0, now - pulse_timestamp)
        is_stale = age_sec > self.stale_threshold_seconds

        target_pid = child_pid if child_pid is not None else pulse.get("pid", 0)
        is_alive = is_process_running(target_pid) if target_pid else False

        # Query real-time memory or fallback to pulse recorded memory
        measured_rss_mb = get_process_rss_mb(target_pid) if (target_pid and is_alive) else float(pulse.get("memory_rss_mb", 0.0))
        memory_breached = measured_rss_mb > self.memory_ceiling_mb

        if not is_alive and target_pid > 0:
            return WatchdogStatus(
                is_healthy=False,
                reason="PROCESS_NOT_RUNNING",
                memory_rss_mb=measured_rss_mb,
                age_seconds=age_sec,
                is_stale=is_stale,
                memory_breached=memory_breached,
                process_alive=False,
                last_pulse=pulse,
            )

        if is_stale:
            return WatchdogStatus(
                is_healthy=False,
                reason=f"HEARTBEAT_STALE ({age_sec:.1f}s > {self.stale_threshold_seconds}s)",
                memory_rss_mb=measured_rss_mb,
                age_seconds=age_sec,
                is_stale=True,
                memory_breached=memory_breached,
                process_alive=is_alive,
                last_pulse=pulse,
            )

        if memory_breached:
            return WatchdogStatus(
                is_healthy=False,
                reason=f"MEMORY_CEILING_BREACHED ({measured_rss_mb:.1f}MB > {self.memory_ceiling_mb}MB)",
                memory_rss_mb=measured_rss_mb,
                age_seconds=age_sec,
                is_stale=False,
                memory_breached=True,
                process_alive=is_alive,
                last_pulse=pulse,
            )

        return WatchdogStatus(
            is_healthy=True,
            reason="HEALTHY",
            memory_rss_mb=measured_rss_mb,
            age_seconds=age_sec,
            is_stale=False,
            memory_breached=False,
            process_alive=is_alive,
            last_pulse=pulse,
        )
