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


from src.telemetry.discord_webhook_dispatcher import DiscordWebhookDispatcher, DiscordZincTheme


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
        mock_mode: bool = False,
    ):
        self.discord_url = discord_webhook_url
        self.tg_token = telegram_bot_token
        self.tg_chat_id = telegram_chat_id
        self.max_rate_per_min = max_rate_per_min
        self.mock_mode = mock_mode

        self.discord_dispatcher = DiscordWebhookDispatcher(
            webhook_url=discord_webhook_url,
            mock_mode=mock_mode,
        )
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
        meta = event.metadata
        ev_type = event.event_type.upper()

        if ev_type in ("ENTRY_SIGNAL", "ENTRY_EXECUTED", "POSITION_OPENED") and "token_mint" in meta:
            return self.discord_dispatcher.build_entry_embed(
                position_id=meta.get("position_id", "N/A"),
                token_mint=meta.get("token_mint", "Unknown"),
                pool_address=meta.get("pool_address", "Unknown"),
                entry_price_sol=float(meta.get("entry_price_sol", 0.0)),
                dynamic_size_usd=float(meta.get("dynamic_size_usd", 15.0)),
                expected_friction_pct=float(meta.get("expected_friction_pct", 5.0)),
                liquidity_sol=float(meta.get("liquidity_sol", 50.0)),
                regime=meta.get("regime", "REGIME_POST_GRAD"),
                checks_passed=int(meta.get("checks_passed", 12)),
                timestamp=event.timestamp,
            )
        elif ev_type in ("RATCHET_UPDATE", "RATCHET_ADJUSTED") and "token_mint" in meta:
            return self.discord_dispatcher.build_ratchet_embed(
                position_id=meta.get("position_id", "N/A"),
                token_mint=meta.get("token_mint", "Unknown"),
                peak_price_sol=float(meta.get("peak_price_sol", 0.0)),
                new_stop_price_sol=float(meta.get("new_stop_price_sol", 0.0)),
                locked_gain_pct=float(meta.get("locked_gain_pct", 0.0)),
                timestamp=event.timestamp,
            )
        elif ev_type in ("TP1_SCALE_OUT", "TP1_HIT") and "token_mint" in meta:
            return self.discord_dispatcher.build_tp1_embed(
                position_id=meta.get("position_id", "N/A"),
                token_mint=meta.get("token_mint", "Unknown"),
                net_sol_received=float(meta.get("net_sol_received", 0.0)),
                gross_return_pct=float(meta.get("gross_return_pct", 9.0)),
                runner_tokens_remaining=float(meta.get("runner_tokens_remaining", 0.0)),
                timestamp=event.timestamp,
            )
        elif ev_type in ("EXIT_CLOSED", "POSITION_CLOSED") and "token_mint" in meta:
            return self.discord_dispatcher.build_exit_embed(
                position_id=meta.get("position_id", "N/A"),
                token_mint=meta.get("token_mint", "Unknown"),
                exit_reason=meta.get("exit_reason", "STOP_LOSS"),
                net_return_pct=float(meta.get("net_return_pct", 0.0)),
                net_pnl_usd=float(meta.get("net_pnl_usd", 0.0)),
                hold_duration_seconds=float(meta.get("hold_duration_seconds", 0.0)),
                timestamp=event.timestamp,
            )
        elif ev_type in ("RISK_HALT", "CIRCUIT_BREAKER_TRIPPED", "KILL_SWITCH_TRIPPED"):
            return self.discord_dispatcher.build_risk_halt_embed(
                halt_type=meta.get("halt_type", event.title),
                reason=meta.get("reason", event.message),
                tripped_at=meta.get("tripped_at"),
                operator_action_required=meta.get("operator_action_required", True),
                timestamp=event.timestamp,
            )

        fields = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in list(event.metadata.items())[:8]
        ]
        return {
            "embeds": [
                {
                    "title": f"[{event.severity.value}] {event.title}",
                    "description": event.message,
                    "color": self.SEVERITY_COLORS.get(event.severity, DiscordZincTheme.CARD),
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
        should_send_discord = bool(self.discord_url or self.mock_mode or self.discord_dispatcher.mock_mode)
        should_send_telegram = bool(self.tg_token and self.tg_chat_id)

        loop = asyncio.get_running_loop() if asyncio.get_event_loop().is_running() else None
        if loop:
            if should_send_discord:
                loop.run_in_executor(None, self._send_discord_sync, event)
            if should_send_telegram:
                loop.run_in_executor(None, self._send_telegram_sync, event)
        else:
            if should_send_discord:
                self._send_discord_sync(event)
            if should_send_telegram:
                self._send_telegram_sync(event)

        return True

    def _send_discord_sync(self, event: AlertEvent):
        try:
            payload = self.format_discord_embed(event)
            self.discord_dispatcher.send_payload(payload)
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
