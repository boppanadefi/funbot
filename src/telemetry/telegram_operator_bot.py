"""
Interactive Telegram Operator Bot for Remote Solana Trading Management.
Provides bidirectional, authenticated command routing for engine oversight:
/status, /kill, /reset, /circuit_breaker, /positions, /help.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set
import requests

from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity

logger = logging.getLogger("TelegramOperatorBot")


class TelegramOperatorBot:
    """
    Authenticated Telegram Operator Bot.
    Runs an asynchronous polling loop or handles commands directly via internal interface.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        allowed_chat_ids: Optional[List[str]] = None,
        circuit_breaker: Optional[DailyCircuitBreaker] = None,
        kill_switch: Optional[EmergencyKillSwitch] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        positions_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        mock_mode: bool = False,
    ):
        self.bot_token = bot_token
        self.allowed_chat_ids: Set[str] = set(str(cid) for cid in (allowed_chat_ids or []))
        self.circuit_breaker = circuit_breaker or DailyCircuitBreaker()
        self.kill_switch = kill_switch or EmergencyKillSwitch()
        self.alerts = alert_dispatcher or AlertDispatcher()
        self.status_provider = status_provider
        self.positions_provider = positions_provider
        self.mock_mode = mock_mode

        self.last_update_id: int = 0
        self._is_polling = False
        self._polling_task: Optional[asyncio.Task] = None
        self.command_audit_log: List[Dict[str, Any]] = []

    def _fire_alert_event(self, event: AlertEvent) -> None:
        """Safely dispatches an alert without crashing if outside an active event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.alerts.dispatch(event))
        except RuntimeError:
            try:
                asyncio.run(self.alerts.dispatch(event))
            except Exception as e:
                logger.debug("Failed to dispatch alert synchronously: %s", e)

    def is_authorized(self, chat_id: str) -> bool:
        """Verifies if the incoming chat ID belongs to an authorized operator."""
        if not self.allowed_chat_ids:
            # If no chat IDs are explicitly configured, allow in mock/testing mode if mock_mode=True
            return self.mock_mode
        return str(chat_id) in self.allowed_chat_ids

    def handle_command(
        self,
        command_text: str,
        chat_id: str,
        sender_username: str = "operator",
    ) -> str:
        """
        Parses and executes an operator command string.
        Returns the formatted response message for the user.
        """
        text = (command_text or "").strip()
        chat_id = str(chat_id)

        # Security Check
        if not self.is_authorized(chat_id):
            warning_msg = f"Unauthorized access attempt to Telegram Operator Bot from Chat ID: {chat_id} (User: {sender_username})"
            logger.warning(warning_msg)
            self._fire_alert_event(
                AlertEvent(
                    event_type="UNAUTHORIZED_ACCESS",
                    severity=AlertSeverity.WARNING,
                    title="Unauthorized Telegram Access Attempt",
                    message=warning_msg,
                    metadata={"chat_id": chat_id, "user": sender_username, "command": text},
                )
            )
            return "⛔ *Access Denied:* Your chat ID is not authorized to control the Funbot engine."

        parts = text.split()
        if not parts:
            return self.cmd_help()

        cmd = parts[0].lower().split("@")[0]  # Strip @botname if present
        args = parts[1:]

        # Audit log entry
        self.command_audit_log.append({
            "command": cmd,
            "args": args,
            "chat_id": chat_id,
            "sender": sender_username,
            "timestamp": time.time(),
        })

        if cmd in ("/status", "status"):
            return self.cmd_status()
        elif cmd in ("/kill", "kill"):
            reason = " ".join(args) if args else f"Remote operator Telegram /kill stop by @{sender_username}"
            return self.cmd_kill(operator=sender_username, reason=reason)
        elif cmd in ("/reset", "reset"):
            notes = " ".join(args) if args else f"Manual reset via Telegram by @{sender_username}"
            return self.cmd_reset(operator=sender_username, notes=notes)
        elif cmd in ("/circuit_breaker", "/cb", "circuit_breaker", "cb"):
            action = args[0].lower() if args else "status"
            return self.cmd_circuit_breaker(action=action, operator=sender_username)
        elif cmd in ("/positions", "/pos", "positions", "pos"):
            return self.cmd_positions()
        elif cmd in ("/help", "help", "/start", "start"):
            return self.cmd_help()
        else:
            return f"❓ Unknown command `{cmd}`. Type `/help` for available operator commands."

    def cmd_status(self) -> str:
        """Formats comprehensive status report."""
        cb_status = self.circuit_breaker.get_status()
        ks_tripped = self.kill_switch.is_tripped()
        ks_state = self.kill_switch.get_status()

        state_badge = "🚨 *HALTED (KILL SWITCH)*" if ks_tripped else ("🛑 *HALTED (CIRCUIT BREAKER)*" if cb_status.get("is_halted") else "🟢 *OPERATIONAL / ACTIVE*")

        positions = self.positions_provider() if self.positions_provider else []
        active_pos_count = len(positions)

        extra_info = self.status_provider() if self.status_provider else {}
        helius_slot = extra_info.get("current_slot", "Synced")
        fastest_jito = extra_info.get("fastest_jito_region", "Default")

        lines = [
            "◆ *FUNBOT LIVE OPERATOR STATUS*",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"• *Engine State:* {state_badge}",
            f"• *Active Positions:* `{active_pos_count}`",
            f"• *Bankroll Active:* `$25.00` (Max)",
            f"• *Cold Reserve:* `$75.00` (Protected)",
            "",
            "🛡️ *Risk & Circuit Breakers*",
            f"• *Daily PnL:* `${cb_status.get('net_daily_pnl_usd', 0.0):.2f}`",
            f"• *Daily Budget Remaining:* `${cb_status.get('remaining_budget_usd', 10.0):.2f}` / `$10.00`",
            f"• *Daily Trades:* `{cb_status.get('trades_count', 0)}`",
            f"• *Kill Switch:* `{'TRIPPED' if ks_tripped else 'ARMED (NORMAL)'}`",
            "",
            "⚡ *Infrastructure*",
            f"• *Helius Slot:* `{helius_slot}`",
            f"• *Jito Region:* `{fastest_jito}`",
            f"• *Timestamp:* `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`",
        ]
        if ks_tripped:
            lines.extend([
                "",
                f"⚠️ *Halt Reason:* _{ks_state.get('reason')}_",
                "Type `/reset` following verification to restore trading.",
            ])
        return "\n".join(lines)

    def cmd_kill(self, operator: str, reason: str) -> str:
        """Engages the emergency kill switch."""
        state = self.kill_switch.trip(operator=f"TELEGRAM_{operator.upper()}", reason=reason)
        # Dispatch notification
        self._fire_alert_event(
            AlertEvent(
                event_type="KILL_SWITCH_TRIPPED",
                severity=AlertSeverity.CRITICAL,
                title="Telegram Remote Kill Switch Engaged",
                message=f"Halted immediately by @{operator}: {reason}",
                metadata={"operator": operator, "reason": reason, "tripped_at": state.get("tripped_at")},
            )
        )
        return (
            "🚨 *EMERGENCY KILL SWITCH TRIPPED!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Operator:* `@{operator}`\n"
            f"• *Reason:* _{reason}_\n"
            f"• *Status:* Programmatic execution **FROZEN**.\n"
            "All new trade entries are rejected until `/reset` is signed off."
        )

    def cmd_reset(self, operator: str, notes: str) -> str:
        """Resets the emergency kill switch."""
        state = self.kill_switch.reset(operator=f"TELEGRAM_{operator.upper()}", notes=notes)
        self._fire_alert_event(
            AlertEvent(
                event_type="KILL_SWITCH_RESET",
                severity=AlertSeverity.INFO,
                title="Telegram Remote Kill Switch Reset",
                message=f"Reset by @{operator}: {notes}",
                metadata={"operator": operator, "notes": notes},
            )
        )
        return (
            "✅ *EMERGENCY KILL SWITCH RESET!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Operator:* `@{operator}`\n"
            f"• *Notes:* _{notes}_\n"
            "• *Status:* Execution state **RESTORED**."
        )

    def cmd_circuit_breaker(self, action: str, operator: str) -> str:
        """Inspects or resets the daily circuit breaker."""
        if action == "reset":
            state = self.circuit_breaker.reset_circuit_breaker(
                operator=f"TELEGRAM_{operator.upper()}",
                notes="Manual daily loss reset from Telegram bot",
            )
            return (
                "✅ *CIRCUIT BREAKER RESET!*\n"
                f"• *Daily Budget Restored:* `${self.circuit_breaker.MAX_DAILY_LOSS_USD:.2f}`\n"
                f"• *Reset by:* `@{operator}`"
            )

        status = self.circuit_breaker.get_status()
        halted = status.get("is_halted", False)
        return (
            "🛡️ *DAILY CIRCUIT BREAKER STATUS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *State:* `{'HALTED' if halted else 'HEALTHY'}`\n"
            f"• *Net Realized Loss:* `${status.get('realized_daily_loss_usd', 0.0):.2f}`\n"
            f"• *Net Realized Gain:* `${status.get('realized_daily_gain_usd', 0.0):.2f}`\n"
            f"• *Net PnL:* `${status.get('net_daily_pnl_usd', 0.0):.2f}`\n"
            f"• *Remaining Budget:* `${status.get('remaining_budget_usd', 10.0):.2f}` / `$10.00`\n"
            f"• *Total Trades:* `{status.get('trades_count', 0)}`\n\n"
            "To reset daily loss budget, use `/cb reset`."
        )

    def cmd_positions(self) -> str:
        """Returns details on currently active positions."""
        positions = self.positions_provider() if self.positions_provider else []
        if not positions:
            return "📭 *No Active Positions:* The engine is currently monitoring the 8–25m post-graduation window."

        lines = [
            f"📊 *ACTIVE POSITIONS ({len(positions)})*",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        for p in positions:
            mint = p.get("token_mint", "N/A")
            short_mint = f"{mint[:4]}...{mint[-4:]}" if len(mint) > 8 else mint
            ret = p.get("unrealized_return_pct", 0.0)
            sign = "+" if ret >= 0 else ""
            lines.extend([
                f"• *{short_mint}* ({p.get('regime', 'REGIME_POST_GRAD')}):",
                f"   - Position Size: `${p.get('position_notional_usd', 15.0):.2f}`",
                f"   - Entry: `{p.get('entry_price_sol', 0.0):.8f} SOL`",
                f"   - Stop: `{p.get('current_stop_price_sol', 0.0):.8f} SOL`",
                f"   - Unrealized: *{sign}{ret:.2f}%* (`${p.get('unrealized_pnl_usd', 0.0):.2f}`)",
                f"   - TP1 State: `{'Triggered (Runner)' if p.get('tp1_activated') else 'Seeking TP1'}`",
                "",
            ])
        return "\n".join(lines).strip()

    def cmd_help(self) -> str:
        """Returns list of supported commands."""
        return (
            "🤖 *FUNBOT OPERATOR COMMANDS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• `/status` — View engine state, open trades, and risk budgets\n"
            "• `/positions` — View live open positions, stops, and PnL\n"
            "• `/kill [reason]` — 🚨 Emergency panic stop (freezes execution)\n"
            "• `/reset [notes]` — ✅ Clear emergency kill switch\n"
            "• `/cb [reset]` — Check or reset -$10 daily circuit breaker\n"
            "• `/help` — Display this command reference"
        )

    async def poll_updates_once(self) -> int:
        """Performs one non-blocking getUpdates query and processes commands."""
        if not self.bot_token or self.mock_mode:
            return 0

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 2}

        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(None, lambda: requests.get(url, params=params, timeout=5))
            if resp.status_code != 200:
                return 0

            data = resp.json()
            results = data.get("result", [])
            for update in results:
                self.last_update_id = max(self.last_update_id, update.get("update_id", 0))
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "")
                user = msg.get("from", {}).get("username", "operator")

                if text:
                    reply_text = self.handle_command(text, chat_id=chat_id, sender_username=user)
                    send_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                    payload = {"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"}
                    await loop.run_in_executor(None, lambda: requests.post(send_url, json=payload, timeout=3))

            return len(results)
        except Exception as e:
            logger.debug("Telegram polling exception: %s", e)
            return 0

    async def start_polling(self, interval_seconds: float = 3.0) -> None:
        """Starts asynchronous background polling loop."""
        self._is_polling = True
        logger.info("TelegramOperatorBot started polling loop (Interval: %.1fs).", interval_seconds)
        while self._is_polling:
            await self.poll_updates_once()
            await asyncio.sleep(interval_seconds)

    def stop_polling(self) -> None:
        """Stops asynchronous polling loop."""
        self._is_polling = False
        if self._polling_task:
            self._polling_task.cancel()
        logger.info("TelegramOperatorBot stopped polling.")
