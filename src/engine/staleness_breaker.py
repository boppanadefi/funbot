"""
Data Staleness Circuit Breaker for Real-Time Solana Event Streams.
Enforces the mandatory rule:
If time_since_last_ws_message > 1000ms, pause the engine. Do not make decisions on stale state.
"""

from datetime import datetime, timezone
import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger("StalenessBreaker")


class DataStalenessCircuitBreaker:
    """
    Guards the trading engine against making entries, exits, or trailing stop calculations
    on delayed, disconnected, or buffered WebSocket state.
    Threshold: 1000ms (1.0 second).
    """

    DEFAULT_MAX_STALENESS_MS: float = 1000.0

    def __init__(self, max_staleness_ms: float = DEFAULT_MAX_STALENESS_MS):
        self.max_staleness_ms = max_staleness_ms
        self._last_message_monotonic: Optional[float] = None
        self._last_message_datetime: Optional[datetime] = None
        self._last_slot: Optional[int] = None
        self._is_stale: bool = True  # Starts stale until first verified WS message arrives
        self._tripped_count: int = 0
        self._total_decision_checks: int = 0
        self._stale_rejections_count: int = 0
        self._last_trip_time: Optional[datetime] = None
        self._last_recovery_time: Optional[datetime] = None
        self._suppress_log_until_monotonic: float = 0.0

    @property
    def is_stale(self) -> bool:
        """Current staleness evaluation."""
        stale, _ = self.check_staleness()
        return stale

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since last verified WebSocket message."""
        if self._last_message_monotonic is None:
            return 999_999.0
        return (time.monotonic() - self._last_message_monotonic) * 1000.0

    @property
    def last_message_time(self) -> Optional[datetime]:
        return self._last_message_datetime

    @property
    def last_slot(self) -> Optional[int]:
        return self._last_slot

    @property
    def tripped_count(self) -> int:
        return self._tripped_count

    def record_message_received(self, slot: Optional[int] = None, timestamp: Optional[datetime] = None) -> None:
        """
        Called on EVERY incoming WebSocket message (swapEvent, accountChange, heartbeat).
        Resets latency timers and clears the staleness pause if previously tripped.
        """
        now_mono = time.monotonic()
        now_dt = timestamp or datetime.now(timezone.utc)

        was_stale = self._is_stale
        self._last_message_monotonic = now_mono
        self._last_message_datetime = now_dt
        if slot:
            self._last_slot = max(self._last_slot or 0, slot)
        self._is_stale = False

        if was_stale:
            self._last_recovery_time = now_dt
            logger.info(
                "Data Staleness Circuit Breaker RECOVERED: Fresh WS message received (slot=%s). Engine UNPAUSED.",
                slot or "N/A"
            )

    def check_staleness(self) -> Tuple[bool, float]:
        """
        Evaluates elapsed latency against max_staleness_ms threshold.
        Returns: (is_stale, elapsed_ms)
        """
        if self._last_message_monotonic is None:
            self._is_stale = True
            return True, 999_999.0

        now_mono = time.monotonic()
        elapsed_ms = (now_mono - self._last_message_monotonic) * 1000.0

        if elapsed_ms > self.max_staleness_ms:
            if not self._is_stale:
                # Newly tripped
                self._is_stale = True
                self._tripped_count += 1
                self._last_trip_time = datetime.now(timezone.utc)
                logger.warning(
                    "DATA STALENESS CIRCUIT BREAKER TRIPPED: %.1fms elapsed since last WS message (> %.0fms threshold)! Engine PAUSED.",
                    elapsed_ms, self.max_staleness_ms
                )
                self._suppress_log_until_monotonic = now_mono + 5.0
            elif now_mono >= self._suppress_log_until_monotonic:
                # Log periodic reminder every 5s while stalled
                logger.warning(
                    "Data stream stalled: %.1fms since last WS message. Engine remains PAUSED.",
                    elapsed_ms
                )
                self._suppress_log_until_monotonic = now_mono + 5.0

            return True, elapsed_ms

        self._is_stale = False
        return False, elapsed_ms

    @property
    def total_decision_checks(self) -> int:
        return self._total_decision_checks

    @property
    def stale_rejections_count(self) -> int:
        return self._stale_rejections_count

    @property
    def stale_rejection_rate_pct(self) -> float:
        """Percentage of potential decisions rejected due to stale WebSocket state."""
        if self._total_decision_checks == 0:
            return 0.0
        return round((self._stale_rejections_count / self._total_decision_checks) * 100.0, 2)

    def can_execute_decision(self) -> Tuple[bool, str]:
        """
        Mandatory pre-flight check called before every strategy evaluation:
        - Candidate pool gate validation
        - Entry order submission
        - Take-profit execution
        - Trailing stop updates
        """
        self._total_decision_checks += 1
        stale, elapsed = self.check_staleness()
        if stale:
            self._stale_rejections_count += 1
            return (
                False,
                f"Data Staleness Circuit Breaker TRIPPED: {elapsed:.1f}ms since last WS message (> {self.max_staleness_ms:.0f}ms threshold). Decision execution paused."
            )
        return True, f"Stream healthy ({elapsed:.1f}ms latency)"

    def force_reset_for_testing(self) -> None:
        """Forces circuit breaker to healthy state for unit testing."""
        self._last_message_monotonic = time.monotonic()
        self._last_message_datetime = datetime.now(timezone.utc)
        self._is_stale = False
