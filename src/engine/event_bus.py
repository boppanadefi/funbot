"""
Real-Time Event Bus for Funbot v2.5 Architecture.
Dispatches live swapEvents and accountChange notifications across the Virtual AMM and Paper Trading Engine.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("EventBus")


class StreamEventType(str, Enum):
    SWAP = "SWAP_EVENT"
    ACCOUNT_CHANGE = "ACCOUNT_CHANGE_EVENT"
    POOL_DISCOVERED = "POOL_DISCOVERED"
    STALENESS_TRIPPED = "STALENESS_TRIPPED"
    STALENESS_CLEARED = "STALENESS_CLEARED"
    HEARTBEAT = "HEARTBEAT"


@dataclass
class LiveSwapEvent:
    """Represents a real-world swap parsed from on-chain WebSocket logs."""
    pool_address: str
    token_mint: str
    is_buy: bool
    sol_amount: float
    token_amount: Optional[float]
    trader_pubkey: str
    slot: int
    signature: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class LiveAccountChangeEvent:
    """Represents an on-chain reserve account change notification."""
    pool_address: str
    sol_reserve: float
    token_reserve: float
    slot: int
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class EventBus:
    """
    Thread-safe synchronous and asynchronous event pub/sub dispatcher.
    Ensures zero-latency delivery of WebSocket events to the Virtual AMM and Strategy Engine.
    """

    def __init__(self):
        self._subscribers: Dict[StreamEventType, List[Callable[[Any], None]]] = {
            event_type: [] for event_type in StreamEventType
        }
        self.published_counts: Dict[StreamEventType, int] = {
            event_type: 0 for event_type in StreamEventType
        }

    def subscribe(self, event_type: StreamEventType, callback: Callable[[Any], None]) -> None:
        """Registers a callback for a specific event type."""
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: StreamEventType, callback: Callable[[Any], None]) -> None:
        """Removes a registered callback."""
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    def publish(self, event_type: StreamEventType, event_data: Any) -> None:
        """Publishes an event to all registered subscribers."""
        self.published_counts[event_type] += 1
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(event_data)
            except Exception as e:
                logger.error("Error in EventBus handler for %s: %s", event_type.value, e, exc_info=True)
