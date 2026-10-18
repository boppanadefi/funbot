"""
Multi-Channel Alerting Subsystem & Webhook Notification Engine.
Broadcasts trade events, risk halts, and infrastructure warnings to
Discord, Telegram, and internal UI/terminal event feeds with rate limiting.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("AlertDispatcher")


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class AlertEvent:
    event_type: str
    severity: AlertSeverity
    title: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class AlertDispatcher:
    """Dispatches asynchronous alerts across Discord, Telegram, and event log."""

    # Color codes for Discord embeds
    SEVERITY_COLORS: Dict[AlertSeverity, int] = {
        AlertSeverity.INFO: 0x3498DB,     # Blue
        AlertSeverity.WARNING: 0xF39C12,  # Amber
        AlertSeverity.CRITICAL: 0xE74C3C, # Red
    }

    def __init__(
        self,
        discord_webhook_url: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        max_rate_per_min: int = 30,
    ):
        self.discord_url = discord_webhook_url
        self.tg_token = telegram_bot_token
        self.tg_chat_id = telegram_chat_id
        self.max_rate_per_min = max_rate_per_min

        self._history: List[AlertEvent] = []
        self._sent_timestamps: List[float] = []

    @property
    def history(self) -> List[AlertEvent]:
        return list(self._history)

    def _is_rate_limited(self) -> bool:
        now = time.time()
        self._sent_timestamps = [t for t in self._sent_timestamps if (now - t) < 60.0]
        return len(self._sent_timestamps) >= self.max_rate_per_min

    def format_discord_embed(self, event: AlertEvent) -> Dict[str, Any]:
        """Formats alert into a Discord webhook payload with Zinc aesthetic."""
        fields = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in list(event.metadata.items())[:8]
        ]
        return {
            "embeds": [
                {
                    "title": f"[{event.severity.value}] {event.title}",
                    "description": event.message,
                    "color": self.SEVERITY_COLORS.get(event.severity, 0x95A5A6),
                    "fields": fields,
                    "footer": {"text": "Funbot Momentum Engine • Solana Mainnet"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.timestamp)),
                }
            ]
        }

    def format_telegram_message(self, event: AlertEvent) -> str:
        """Formats alert into a Telegram markdown message."""
        icon = "🚨" if event.severity == AlertSeverity.CRITICAL else ("⚠️" if event.severity == AlertSeverity.WARNING else "ℹ️")
        lines = [
            f"{icon} *{event.title}*",
            f"_{event.message}_",
            "",
        ]
        for k, v in list(event.metadata.items())[:6]:
            lines.append(f"• *{k}*: `{v}`")
        return "\n".join(lines)

    async def dispatch(self, event: AlertEvent) -> bool:
        """Asynchronously dispatches alert to all active channels."""
        self._history.append(event)
        logger.info(f"Alert [{event.severity.value}] {event.title}: {event.message}")

        if self._is_rate_limited():
            logger.warning("Alert rate limit reached (30/min). Suppressing external webhook.")
            return False

        self._sent_timestamps.append(time.time())

        # Run non-blocking HTTP dispatches
        loop = asyncio.get_running_loop() if asyncio.get_event_loop().is_running() else None
        if loop:
            if self.discord_url:
                loop.run_in_executor(None, self._send_discord_sync, event)
            if self.tg_token and self.tg_chat_id:
                loop.run_in_executor(None, self._send_telegram_sync, event)
        else:
            if self.discord_url:
                self._send_discord_sync(event)
            if self.tg_token and self.tg_chat_id:
                self._send_telegram_sync(event)

        return True

    def _send_discord_sync(self, event: AlertEvent):
        try:
            payload = self.format_discord_embed(event)
            requests.post(self.discord_url, json=payload, timeout=3.0)
        except Exception as e:
            logger.warning(f"Failed to deliver Discord alert: {e}")

    def _send_telegram_sync(self, event: AlertEvent):
        try:
            text = self.format_telegram_message(event)
            url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
            payload = {"chat_id": self.tg_chat_id, "text": text, "parse_mode": "Markdown"}
            requests.post(url, json=payload, timeout=3.0)
        except Exception as e:
            logger.warning(f"Failed to deliver Telegram alert: {e}")
