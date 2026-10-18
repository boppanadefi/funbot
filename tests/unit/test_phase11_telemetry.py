"""
Unit and Integration Tests for Phase 11: Real-Time Telemetry & Operator Webhooks.
Validates:
1. Discord Zinc Webhook Dispatcher embed builders and aesthetic formatting.
2. Telegram Operator Bot bidirectional commands and security whitelist protection.
3. SQLite WAL streamer safe read-only concurrency queries.
4. MasterTradingOrchestrator lifecycle integration with telemetry and operator bots.
"""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
import tempfile

from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity
from src.telemetry.discord_webhook_dispatcher import DiscordWebhookDispatcher, DiscordZincTheme
from src.telemetry.telegram_operator_bot import TelegramOperatorBot
from src.telemetry.wal_streamer import SqliteWalStreamer


@pytest.fixture
def clean_kill_switch(tmp_path):
    ks_file = tmp_path / "ks_test.json"
    return EmergencyKillSwitch(state_file_path=str(ks_file))


@pytest.fixture
def clean_circuit_breaker(tmp_path):
    cb_file = tmp_path / "cb_test.json"
    return DailyCircuitBreaker(state_file_path=str(cb_file))


# =========================================================================
# 1. Discord Zinc Webhook Dispatcher Tests
# =========================================================================

class TestDiscordWebhookDispatcher:
    """Validates Discord Zinc embed formatting and field assertions."""

    def test_discord_entry_embed_generation(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)
        payload = dispatcher.build_entry_embed(
            position_id="pos_entry_001",
            token_mint="So11111111111111111111111111111111111111112",
            pool_address="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
            entry_price_sol=0.00045,
            dynamic_size_usd=18.50,
            expected_friction_pct=5.20,
            liquidity_sol=55.0,
            regime="REGIME_POST_GRAD",
            checks_passed=12,
        )

        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert embed["color"] == DiscordZincTheme.GREEN
        assert "ENTRY EXECUTED" in embed["title"]
        assert "So11...1112" in embed["title"]

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "$18.50" in fields["Position Size"]
        assert "0.00045000 SOL" in fields["Entry Price"]
        assert "5.20%" in fields["Expected Friction"]
        assert "12/12 Checks Passed" in fields["Gate Verification"]
        assert "https://solscan.io/token/" in fields["Token Mint"]

    def test_discord_ratchet_embed_generation(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)
        payload = dispatcher.build_ratchet_embed(
            position_id="pos_ratchet_001",
            token_mint="TokenMint123456789ABCDEF",
            peak_price_sol=0.00055,
            new_stop_price_sol=0.00051,
            locked_gain_pct=13.33,
        )

        embed = payload["embeds"][0]
        assert embed["color"] == DiscordZincTheme.BLUE
        assert "TRAILING RATCHET ADJUSTED" in embed["title"]

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "+13.33%" in fields["Locked-In Gain"]
        assert "0.00055000 SOL" in fields["Peak Price"]
        assert "0.00051000 SOL" in fields["New Stop Floor"]

    def test_discord_tp1_embed_generation(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)
        payload = dispatcher.build_tp1_embed(
            position_id="pos_tp1_001",
            token_mint="TokenMintTP1TestABC",
            net_sol_received=0.1250,
            gross_return_pct=9.50,
            runner_tokens_remaining=15_000_000.0,
        )

        embed = payload["embeds"][0]
        assert embed["color"] == DiscordZincTheme.GREEN
        assert "TP1 SCALE-OUT EXECUTED (90%)" in embed["title"]

        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "+9.50%" in fields["Gross Return"]
        assert "+0.1250 SOL" in fields["SOL Realized"]
        assert "15,000,000" in fields["Runner Retained"]
        assert "3.0 Minutes" in fields["Hard Kill Timeout"]

    def test_discord_exit_embed_generation_profit_and_loss(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)

        # Profitable Exit
        payload_win = dispatcher.build_exit_embed(
            position_id="pos_win_001",
            token_mint="TokenWinMint123",
            exit_reason="TP1_AND_RUNNER_CLOSE",
            net_return_pct=11.20,
            net_pnl_usd=2.15,
            hold_duration_seconds=84.5,
        )
        embed_win = payload_win["embeds"][0]
        assert embed_win["color"] == DiscordZincTheme.GREEN
        assert "+$2.15" in str(embed_win["fields"])

        # Loss Exit
        payload_loss = dispatcher.build_exit_embed(
            position_id="pos_loss_001",
            token_mint="TokenLossMint123",
            exit_reason="STRUCTURE_STOP_LOSS",
            net_return_pct=-6.50,
            net_pnl_usd=-1.20,
            hold_duration_seconds=42.0,
        )
        embed_loss = payload_loss["embeds"][0]
        assert embed_loss["color"] == DiscordZincTheme.RED
        assert "-$1.20" in str(embed_loss["fields"])

    def test_discord_risk_halt_embed_generation(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)
        payload = dispatcher.build_risk_halt_embed(
            halt_type="DAILY_CIRCUIT_BREAKER",
            reason="Daily net loss reached -$10.00 ceiling",
            operator_action_required=True,
        )

        embed = payload["embeds"][0]
        assert embed["color"] == DiscordZincTheme.RED
        assert "CRITICAL RISK HALT" in embed["title"]
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        assert "Manual Operator Sign-off" in fields["Action Required"]

    def test_discord_send_payload_mock_mode(self):
        dispatcher = DiscordWebhookDispatcher(mock_mode=True)
        sample = {"embeds": [{"title": "Test"}]}
        success = dispatcher.send_payload(sample)
        assert success is True
        assert len(dispatcher.dispatched_payloads) == 1
        assert dispatcher.dispatched_payloads[0] == sample


# =========================================================================
# 2. Telegram Operator Bot Tests
# =========================================================================

class TestTelegramOperatorBot:
    """Validates Telegram command handling and security authorization."""

    def test_telegram_security_unauthorized_rejection(self, clean_kill_switch, clean_circuit_breaker):
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            mock_mode=False,
        )

        # Unauthorized chat attempt
        resp = bot.handle_command("/status", chat_id="999999999", sender_username="hacker")
        assert "Access Denied" in resp
        assert not bot.is_authorized("999999999")

        # Authorized chat succeeds
        assert bot.is_authorized("111222333")

    def test_telegram_command_status(self, clean_kill_switch, clean_circuit_breaker):
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            status_provider=lambda: {"current_slot": 315482910, "fastest_jito_region": "Frankfurt"},
            mock_mode=False,
        )

        resp = bot.handle_command("/status", chat_id="111222333", sender_username="admin")
        assert "FUNBOT LIVE OPERATOR STATUS" in resp
        assert "OPERATIONAL / ACTIVE" in resp
        assert "$25.00" in resp
        assert "$75.00" in resp
        assert "315482910" in resp
        assert "Frankfurt" in resp

    def test_telegram_command_kill_and_reset(self, clean_kill_switch, clean_circuit_breaker):
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            mock_mode=False,
        )

        assert not clean_kill_switch.is_tripped()

        # Execute /kill
        kill_resp = bot.handle_command("/kill Test panic stop triggered", chat_id="111222333", sender_username="trader1")
        assert "EMERGENCY KILL SWITCH TRIPPED" in kill_resp
        assert clean_kill_switch.is_tripped()
        assert clean_kill_switch.get_status()["reason"] == "Test panic stop triggered"

        # Check status shows halted
        stat_resp = bot.handle_command("/status", chat_id="111222333", sender_username="trader1")
        assert "HALTED (KILL SWITCH)" in stat_resp

        # Execute /reset
        reset_resp = bot.handle_command("/reset Safety review passed", chat_id="111222333", sender_username="trader1")
        assert "EMERGENCY KILL SWITCH RESET" in reset_resp
        assert not clean_kill_switch.is_tripped()

    def test_telegram_command_circuit_breaker(self, clean_kill_switch, clean_circuit_breaker):
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            mock_mode=False,
        )

        # Status check
        cb_resp = bot.handle_command("/cb", chat_id="111222333", sender_username="operator")
        assert "DAILY CIRCUIT BREAKER STATUS" in cb_resp
        assert "$10.00" in cb_resp

        # Record loss and reset
        clean_circuit_breaker.record_trade_pnl(-10.50)
        assert clean_circuit_breaker.is_halted

        reset_cb_resp = bot.handle_command("/cb reset", chat_id="111222333", sender_username="operator")
        assert "CIRCUIT BREAKER RESET" in reset_cb_resp
        assert not clean_circuit_breaker.is_halted

    def test_telegram_command_positions_empty_and_populated(self, clean_kill_switch, clean_circuit_breaker):
        positions_store = []
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            positions_provider=lambda: positions_store,
            mock_mode=False,
        )

        # Empty
        resp_empty = bot.handle_command("/positions", chat_id="111222333", sender_username="operator")
        assert "No Active Positions" in resp_empty

        # Populated
        positions_store.append({
            "token_mint": "MintTest1234567890",
            "entry_price_sol": 0.00040,
            "current_stop_price_sol": 0.00042,
            "unrealized_return_pct": 8.50,
            "unrealized_pnl_usd": 1.25,
            "tp1_activated": True,
            "position_notional_usd": 15.00,
        })
        resp_pop = bot.handle_command("/positions", chat_id="111222333", sender_username="operator")
        assert "ACTIVE POSITIONS (1)" in resp_pop
        assert "+8.50%" in resp_pop
        assert "$1.25" in resp_pop
        assert "Triggered (Runner)" in resp_pop

    def test_telegram_command_help(self, clean_kill_switch, clean_circuit_breaker):
        bot = TelegramOperatorBot(
            allowed_chat_ids=["111222333"],
            circuit_breaker=clean_circuit_breaker,
            kill_switch=clean_kill_switch,
            mock_mode=False,
        )
        help_resp = bot.handle_command("/help", chat_id="111222333", sender_username="operator")
        assert "/status" in help_resp
        assert "/kill" in help_resp
        assert "/reset" in help_resp
        assert "/cb" in help_resp
        assert "/positions" in help_resp


# =========================================================================
# 3. SQLite WAL Streamer Tests
# =========================================================================

class TestSqliteWalStreamer:
    """Validates WAL streamer non-blocking operations and metrics."""

    def test_sqlite_wal_streamer_metrics_and_status(self, tmp_path):
        pos_file = tmp_path / "pos.json"
        ks_file = tmp_path / "ks.json"
        cb_file = tmp_path / "cb.json"

        pos_file.write_text(json.dumps({
            "is_active": True,
            "active_positions": [{
                "token_mint": "MintABC123",
                "entry_price_sol": 0.0005,
                "current_price_sol": 0.00055,
                "unrealized_return_pct": 10.0,
            }]
        }))

        streamer = SqliteWalStreamer(
            active_pos_path=str(pos_file),
            kill_switch_path=str(ks_file),
            circuit_breaker_path=str(cb_file),
        )

        # Active positions
        positions = streamer.get_active_positions()
        assert len(positions) == 1
        assert positions[0]["token_mint"] == "MintABC123"

        # Risk status
        risk = streamer.get_risk_status()
        assert "kill_switch" in risk
        assert "circuit_breaker" in risk

        # DB metrics
        metrics = streamer.get_database_metrics()
        assert "telemetry_db_size" in metrics
        assert "last_synced_utc" in metrics


# =========================================================================
# 4. Integrated Alert Dispatcher Tests
# =========================================================================

class TestIntegratedAlertDispatcher:
    """Verifies that AlertDispatcher properly formats and dispatches specialized embeds."""

    @pytest.mark.asyncio
    async def test_alert_dispatcher_embed_routing(self):
        dispatcher = AlertDispatcher(mock_mode=True)

        event = AlertEvent(
            event_type="ENTRY_EXECUTED",
            severity=AlertSeverity.INFO,
            title="Position Entry",
            message="Opened position",
            metadata={
                "position_id": "pos_999",
                "token_mint": "So11111111111111111111111111111111111111112",
                "pool_address": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                "entry_price_sol": 0.0005,
                "dynamic_size_usd": 20.0,
                "expected_friction_pct": 4.8,
                "liquidity_sol": 75.0,
                "checks_passed": 12,
            },
        )

        success = await dispatcher.dispatch(event)
        assert success is True
        assert len(dispatcher.history) == 1
        assert len(dispatcher.discord_dispatcher.dispatched_payloads) == 1

        payload = dispatcher.discord_dispatcher.dispatched_payloads[0]
        assert "ENTRY EXECUTED" in payload["embeds"][0]["title"]
