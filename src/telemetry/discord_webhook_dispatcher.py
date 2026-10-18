"""
Discord Zinc Webhook Notification Dispatcher.
Formats rich visual embeds styled with the dark Zinc aesthetic for live Solana trade events.
"""

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("DiscordWebhookDispatcher")


class DiscordZincTheme:
    """Zinc dark theme colors for Discord embeds."""
    BACKGROUND = 0x09090B    # Zinc 950
    CARD = 0x18181B          # Zinc 900
    TEXT = 0xFAFAFA          # Zinc 50
    MUTED = 0xA1A1AA         # Zinc 400
    GREEN = 0x22C55E         # Emerald 500 (Profits / Entries)
    RED = 0xEF4444           # Red 500 (Losses / Risk Halts)
    BLUE = 0x3B82F6          # Blue 500 (Info / Ratchet updates)
    AMBER = 0xF59E0B         # Amber 500 (Timeouts / Warnings)
    PURPLE = 0xA855F7        # Purple 500 (Jito / MEV)


class DiscordWebhookDispatcher:
    """Formats and dispatches specialized Discord webhook embeds with Zinc styling."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout_seconds: float = 4.0,
        mock_mode: bool = False,
    ):
        self.webhook_url = webhook_url
        self.timeout = timeout_seconds
        self.mock_mode = mock_mode
        self.dispatched_payloads: List[Dict[str, Any]] = []

    def _format_timestamp(self, ts: Optional[float] = None) -> str:
        """Returns ISO 8601 UTC timestamp string for Discord embed."""
        t = ts or time.time()
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def build_entry_embed(
        self,
        position_id: str,
        token_mint: str,
        pool_address: str,
        entry_price_sol: float,
        dynamic_size_usd: float,
        expected_friction_pct: float,
        liquidity_sol: float,
        regime: str = "REGIME_POST_GRAD",
        checks_passed: int = 12,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Constructs an embed for a newly opened trade position."""
        mint_short = f"{token_mint[:4]}...{token_mint[-4:]}"
        pool_short = f"{pool_address[:4]}...{pool_address[-4:]}"
        
        fields = [
            {"name": "Token Mint", "value": f"[{mint_short}](https://solscan.io/token/{token_mint})", "inline": True},
            {"name": "Pool Address", "value": f"[{pool_short}](https://dexscreener.com/solana/{pool_address})", "inline": True},
            {"name": "Position Size", "value": f"**${dynamic_size_usd:.2f}**", "inline": True},
            {"name": "Entry Price", "value": f"`{entry_price_sol:.8f} SOL`", "inline": True},
            {"name": "Expected Friction", "value": f"`{expected_friction_pct:.2f}%`", "inline": True},
            {"name": "Pool Liquidity", "value": f"`{liquidity_sol:.1f} SOL`", "inline": True},
            {"name": "Gate Verification", "value": f"✅ {checks_passed}/12 Checks Passed", "inline": True},
            {"name": "Regime Tag", "value": f"`{regime}`", "inline": True},
            {"name": "Position ID", "value": f"`{position_id}`", "inline": True},
        ]

        embed = {
            "title": f"🟢 ENTRY EXECUTED — {mint_short}",
            "description": f"Post-graduation momentum breakout confirmed. Entering safe dynamic position with micro-capital isolation.",
            "color": DiscordZincTheme.GREEN,
            "fields": fields,
            "footer": {"text": "Funbot Systematic Momentum Engine • Solana Mainnet"},
            "timestamp": self._format_timestamp(timestamp),
        }
        return {"embeds": [embed]}

    def build_ratchet_embed(
        self,
        position_id: str,
        token_mint: str,
        peak_price_sol: float,
        new_stop_price_sol: float,
        locked_gain_pct: float,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Constructs an embed when trailing ratchet floor adjusts upwards."""
        mint_short = f"{token_mint[:4]}...{token_mint[-4:]}"
        fields = [
            {"name": "Token Mint", "value": f"[{mint_short}](https://solscan.io/token/{token_mint})", "inline": True},
            {"name": "Peak Price", "value": f"`{peak_price_sol:.8f} SOL`", "inline": True},
            {"name": "New Stop Floor", "value": f"`{new_stop_price_sol:.8f} SOL`", "inline": True},
            {"name": "Locked-In Gain", "value": f"**+{locked_gain_pct:.2f}%**", "inline": True},
            {"name": "Position ID", "value": f"`{position_id}`", "inline": True},
        ]

        embed = {
            "title": f"🎯 TRAILING RATCHET ADJUSTED — {mint_short}",
            "description": "Peak high refreshed. Dynamic stop loss raised to protect unrealized profits.",
            "color": DiscordZincTheme.BLUE,
            "fields": fields,
            "footer": {"text": "Funbot Dynamic Trailing Engine • Solana Mainnet"},
            "timestamp": self._format_timestamp(timestamp),
        }
        return {"embeds": [embed]}

    def build_tp1_embed(
        self,
        position_id: str,
        token_mint: str,
        net_sol_received: float,
        gross_return_pct: float,
        runner_tokens_remaining: float,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Constructs an embed when 90% TP1 scale-out is executed."""
        mint_short = f"{token_mint[:4]}...{token_mint[-4:]}"
        fields = [
            {"name": "Token Mint", "value": f"[{mint_short}](https://solscan.io/token/{token_mint})", "inline": True},
            {"name": "Gross Return", "value": f"**+{gross_return_pct:.2f}%**", "inline": True},
            {"name": "SOL Realized", "value": f"`+{net_sol_received:.4f} SOL`", "inline": True},
            {"name": "Runner Retained", "value": f"`10% ({runner_tokens_remaining:,.0f} tok)`", "inline": True},
            {"name": "Hard Kill Timeout", "value": "`3.0 Minutes`", "inline": True},
            {"name": "Position ID", "value": f"`{position_id}`", "inline": True},
        ]

        embed = {
            "title": f"💰 TP1 SCALE-OUT EXECUTED (90%) — {mint_short}",
            "description": "Friction-busting 90% tranche closed into momentum spike. Runner leg active with 3-minute hard kill.",
            "color": DiscordZincTheme.GREEN,
            "fields": fields,
            "footer": {"text": "Funbot Scale-Out Protocol • Solana Mainnet"},
            "timestamp": self._format_timestamp(timestamp),
        }
        return {"embeds": [embed]}

    def build_exit_embed(
        self,
        position_id: str,
        token_mint: str,
        exit_reason: str,
        net_return_pct: float,
        net_pnl_usd: float,
        hold_duration_seconds: float,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Constructs an embed when a position is fully closed."""
        mint_short = f"{token_mint[:4]}...{token_mint[-4:]}"
        is_profit = net_pnl_usd >= 0.0
        color = DiscordZincTheme.GREEN if is_profit else DiscordZincTheme.RED
        pnl_str = f"+${net_pnl_usd:.2f}" if is_profit else f"-${abs(net_pnl_usd):.2f}"
        ret_str = f"+{net_return_pct:.2f}%" if net_return_pct >= 0 else f"{net_return_pct:.2f}%"

        fields = [
            {"name": "Token Mint", "value": f"[{mint_short}](https://solscan.io/token/{token_mint})", "inline": True},
            {"name": "Exit Reason", "value": f"`{exit_reason}`", "inline": True},
            {"name": "Net Return", "value": f"**{ret_str}**", "inline": True},
            {"name": "Net PnL (USD)", "value": f"**{pnl_str}**", "inline": True},
            {"name": "Hold Duration", "value": f"`{hold_duration_seconds:.1f}s`", "inline": True},
            {"name": "Position ID", "value": f"`{position_id}`", "inline": True},
        ]

        embed = {
            "title": f"{'✅' if is_profit else '🛑'} POSITION CLOSED — {mint_short}",
            "description": f"Trade concluded via {exit_reason}. Capital reclaimed into bankroll.",
            "color": color,
            "fields": fields,
            "footer": {"text": "Funbot Execution Ledger • Solana Mainnet"},
            "timestamp": self._format_timestamp(timestamp),
        }
        return {"embeds": [embed]}

    def build_risk_halt_embed(
        self,
        halt_type: str,
        reason: str,
        tripped_at: Optional[str] = None,
        operator_action_required: bool = True,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Constructs a high-priority risk halt embed (Circuit Breaker or Kill Switch)."""
        fields = [
            {"name": "Halt Mechanism", "value": f"**{halt_type}**", "inline": True},
            {"name": "Trigger Reason", "value": f"`{reason}`", "inline": False},
            {"name": "Tripped Timestamp", "value": f"`{tripped_at or self._format_timestamp(timestamp)}`", "inline": True},
            {"name": "Action Required", "value": "⚠️ **Manual Operator Sign-off Required**" if operator_action_required else "Automatic Cooldown", "inline": True},
        ]

        embed = {
            "title": "🚨 CRITICAL RISK HALT ENGAGED",
            "description": "Automated trading suspended by safety guardrail. Zero real capital can be deployed until reset.",
            "color": DiscordZincTheme.RED,
            "fields": fields,
            "footer": {"text": "Funbot Safety Guardrail • Solana Mainnet"},
            "timestamp": self._format_timestamp(timestamp),
        }
        return {"embeds": [embed]}

    def send_payload(self, payload: Dict[str, Any]) -> bool:
        """Transmits webhook payload to Discord endpoint or records in mock mode."""
        self.dispatched_payloads.append(payload)

        if self.mock_mode or not self.webhook_url:
            logger.debug("Mock mode active or webhook URL omitted. Embed payload recorded locally.")
            return True

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            if resp.status_code in (200, 204):
                logger.info("Successfully delivered Discord webhook notification.")
                return True
            else:
                logger.warning("Discord webhook returned status %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("Failed to post Discord webhook: %s", e)
            return False
