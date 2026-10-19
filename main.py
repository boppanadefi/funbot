"""
Funbot Systematic Solana Momentum Trading Engine — Root CLI Entry Point.
Supported Commands:
  daemon    - Runs the MasterTradingOrchestrator async production event loop
  terminal  - Launches the Streamlit operational terminal UI
  dry-run   - Executes zero-capital dry-run pipeline with live RPC blockhash
  status    - Displays wallet mode, bankroll isolation, circuit breaker, & RPC health
  baseline  - Benchmarks network latency across Helius RPC & 5 Jito global regions
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

# Ensure project root is in python path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FunbotMain")


def cmd_status(args):
    """Prints current wallet mode, bankroll limits, and RPC health."""
    from src.live.wallet_manager import WalletManager
    from src.live.circuit_breaker import DailyCircuitBreaker
    from src.live.kill_switch import EmergencyKillSwitch
    from src.live.network_latency_profiler import NetworkLatencyProfiler

    wm = WalletManager()
    cb = DailyCircuitBreaker()
    ks = EmergencyKillSwitch()
    profiler = NetworkLatencyProfiler()
    helius_metric = profiler.ping_helius_rpc()

    print("\n" + "=" * 65)
    print("        FUNBOT SYSTEMATIC ENGINE — OPERATIONAL STATUS         ")
    print("=" * 65)
    print(f"  Wallet Mode:         {'LIVE ARMED KEYPAIR' if wm.active_wallet.is_live else 'SIMULATED (NO PRIVATE KEY)'}")
    print(f"  Active Trading PK:   {wm.active_wallet.pubkey}")
    print(f"  Sub-Wallet Balance:  {wm.active_wallet.balance_sol:.5f} SOL (${wm.active_wallet.balance_usd:.2f})")
    print(f"  Active Allocation:   ${wm.ACTIVE_SUB_WALLET_LIMIT_USD:.2f}")
    print(f"  Cold Reserve:        ${wm.COLD_RESERVE_USD:.2f} (Locked)")
    print(f"  Exit Reserve Floor:  {wm.EMERGENCY_EXIT_RESERVE_SOL:.4f} SOL")
    print("-" * 65)
    print(f"  Circuit Breaker:     {'HALTED' if cb.is_halted else 'ACTIVE (NORMAL)'}")
    print(f"  Daily Realized PnL:  ${cb.net_daily_pnl:.2f} (Limit: -${cb.MAX_DAILY_LOSS_USD:.2f})")
    print(f"  Emergency Kill:      {'TRIPPED' if ks.is_tripped else 'CLEAR (READY)'}")
    print("-" * 65)
    print(f"  Helius RPC Health:   {'ONLINE' if helius_metric.is_healthy else 'OFFLINE'} ({helius_metric.latency_ms:.2f} ms)")
    print("=" * 65 + "\n")


def cmd_baseline(args):
    """Benchmarks and ranks network latencies across Helius and Jito."""
    from src.live.network_latency_profiler import NetworkLatencyProfiler

    print("\nProfiling network latencies across Helius and 5 Jito endpoints...")
    profiler = NetworkLatencyProfiler()
    report = profiler.run_full_baseline()

    print("\n" + "=" * 65)
    print("                 NETWORK LATENCY BASELINE REPORT               ")
    print("=" * 65)
    print(f"  Helius RPC Latency:    {report.helius_rpc_latency_ms:.2f} ms ({'HEALTHY' if report.helius_healthy else 'OFFLINE'})")
    print(f"  Fastest Jito Region:   {report.fastest_jito_region.upper()} ({report.fastest_jito_latency_ms:.2f} ms)")
    print("  Ranked Endpoints:")
    for m in report.jito_regions_ranked:
        status_tag = "ONLINE" if m.is_healthy else "OFFLINE"
        print(f"    - {m.region.ljust(12)}: {m.latency_ms:6.2f} ms [{status_tag}]")
    print("=" * 65 + "\n")


def cmd_dry_run(args):
    """Executes zero-capital dry-run simulation pipeline."""
    from scripts.run_dry_run_pipeline import run_dry_run_pipeline
    run_dry_run_pipeline()


def cmd_terminal(args):
    """Launches the Streamlit operational terminal UI."""
    app_path = PROJECT_ROOT / "src" / "ui" / "app.py"
    port = str(args.port or 8501)
    print(f"\nStarting Funbot Operational Terminal on http://localhost:{port} ...")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", port]
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nStreamlit Terminal stopped cleanly.")


def cmd_notify(args):
    """Inspects or tests Discord and Telegram notification dispatchers."""
    from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity

    discord_url = os.getenv("DISCORD_WEBHOOK_URL")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    print("\n" + "=" * 65)
    print("        FUNBOT SYSTEMATIC ENGINE -- NOTIFICATION CHANNELS        ")
    print("=" * 65)
    print(f"  Discord Webhook:     {'CONFIGURED' if discord_url else 'UNSET / MOCK'}")
    print(f"  Telegram Bot Token:  {'CONFIGURED' if tg_token else 'UNSET / MOCK'}")
    print(f"  Telegram Chat ID:    {'CONFIGURED' if tg_chat_id else 'UNSET / MOCK'}")
    print("-" * 65)

    if args.test:
        print("  Dispatching test notification embed...")
        dispatcher = AlertDispatcher(
            discord_webhook_url=discord_url,
            telegram_bot_token=tg_token,
            telegram_chat_id=tg_chat_id,
            mock_mode=bool(not discord_url and not tg_token),
        )
        test_event = AlertEvent(
            event_type="ENTRY_EXECUTED",
            severity=AlertSeverity.INFO,
            title="Funbot Test Notification",
            message="Verification test notification from Phase 11 telemetry CLI.",
            metadata={
                "position_id": "test_pos_001",
                "token_mint": "So11111111111111111111111111111111111111112",
                "pool_address": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                "entry_price_sol": 0.00045,
                "dynamic_size_usd": 15.00,
                "expected_friction_pct": 5.25,
                "liquidity_sol": 62.4,
                "checks_passed": 12,
            },
        )
        asyncio.run(dispatcher.dispatch(test_event))
        print("  [OK] Test alert dispatched successfully.")
    print("=" * 65 + "\n")


def cmd_daemon(args):
    """Runs the MasterTradingOrchestrator async production loop."""
    from src.orchestrator.main_loop import MasterTradingOrchestrator

    is_regime_test = bool(args.regime_test)
    print("\n" + "=" * 65)
    print("        FUNBOT SYSTEMATIC ENGINE — DAEMON ORCHESTRATOR        ")
    print(f"  Regime Test Mode:    {is_regime_test}")
    print("=" * 65)

    orchestrator = MasterTradingOrchestrator(is_regime_test=is_regime_test)
    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        print("\nOrchestrator daemon shut down cleanly.")


def cmd_supervisor(args):
    """Manages the ProductionSupervisor lifecycle or inspects watchdog status."""
    from src.orchestrator.supervisor import ProductionSupervisor, SupervisorConfig
    from src.orchestrator.heartbeat import HeartbeatWatchdog, is_process_running

    if getattr(args, "run", False):
        child_args = ["daemon"]
        if getattr(args, "regime_test", False):
            child_args.append("--regime-test")
        config = SupervisorConfig(child_args=child_args)
        supervisor = ProductionSupervisor(config=config)
        print("\n" + "=" * 65)
        print("        FUNBOT SYSTEMATIC ENGINE — PRODUCTION SUPERVISOR       ")
        print(f"  Target Command:      python main.py {' '.join(child_args)}")
        print(f"  Memory Ceiling:      {config.memory_ceiling_mb:.1f} MB")
        print(f"  Heartbeat Stale:     >{config.stale_threshold_seconds:.1f}s")
        print(f"  Crash Loop Window:   5 crashes / {config.window_seconds / 60.0:.0f}m")
        print("=" * 65 + "\n")
        try:
            asyncio.run(supervisor.run())
        except KeyboardInterrupt:
            print("\nSupervisor stopped by operator.")
    else:
        # Default or --status: inspect current status
        watchdog = HeartbeatWatchdog()
        pulse = watchdog.read_latest_pulse()
        status = watchdog.check_health()

        print("\n" + "=" * 65)
        print("     FUNBOT SYSTEMATIC ENGINE — SUPERVISOR & DAEMON STATUS     ")
        print("=" * 65)
        if not pulse:
            print("  Daemon Status:       OFFLINE (No heartbeat.json found)")
            print("  Action:              Run 'python main.py supervisor --run' to start.")
        else:
            pid = pulse.get("pid", 0)
            alive = is_process_running(pid) if pid else False
            status_tag = "ONLINE (HEALTHY)" if status.is_healthy else f"DEGRADED / {status.reason}"
            if not alive:
                status_tag = "OFFLINE (PROCESS DEAD)"

            print(f"  Daemon Status:       {status_tag}")
            print(f"  Child Process PID:   {pid} ({'ACTIVE' if alive else 'TERMINATED'})")
            print(f"  Heartbeat Age:       {status.age_seconds:.1f}s (Threshold: <=60.0s)")
            print(f"  Memory RSS:          {status.memory_rss_mb:.2f} MB (Ceiling: 512.0 MB)")
            print(f"  Uptime:              {pulse.get('uptime_seconds', 0.0):.1f}s")
            print(f"  Active Positions:    {pulse.get('active_positions', 0)}")
            print(f"  Kill Switch:         {'TRIPPED' if pulse.get('is_kill_switch_tripped') else 'CLEAR'}")
            print(f"  Circuit Breaker:     {'HALTED' if pulse.get('is_circuit_breaker_halted') else 'ACTIVE'}")
            print(f"  Last Observed Slot:  {pulse.get('last_slot', 0)}")
        print("=" * 65 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Funbot Systematic Solana Momentum Trading Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # status
    p_status = subparsers.add_parser("status", help="Display wallet, risk, and network status")
    p_status.set_defaults(func=cmd_status)

    # baseline
    p_baseline = subparsers.add_parser("baseline", help="Benchmark Helius & Jito network latencies")
    p_baseline.set_defaults(func=cmd_baseline)

    # dry-run
    p_dryrun = subparsers.add_parser("dry-run", help="Run zero-capital dry-run simulation pipeline")
    p_dryrun.set_defaults(func=cmd_dry_run)

    # notify
    p_notify = subparsers.add_parser("notify", help="Inspect or test notification webhooks")
    p_notify.add_argument("--test", action="store_true", help="Send test notification embed")
    p_notify.set_defaults(func=cmd_notify)

    # terminal
    p_terminal = subparsers.add_parser("terminal", help="Launch Streamlit operational terminal")
    p_terminal.add_argument("--port", type=int, default=8501, help="Port for Streamlit server (default: 8501)")
    p_terminal.set_defaults(func=cmd_terminal)

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Run MasterTradingOrchestrator async daemon")
    p_daemon.add_argument("--regime-test", action="store_true", help="Enable regime test mode (up to 3 concurrent active positions)")
    p_daemon.set_defaults(func=cmd_daemon)

    # supervisor
    p_sup = subparsers.add_parser("supervisor", help="Run ProductionSupervisor parent watcher or inspect status")
    p_sup.add_argument("--run", action="store_true", help="Start the ProductionSupervisor in the foreground")
    p_sup.add_argument("--status", action="store_true", help="Display real-time heartbeat and watchdog status")
    p_sup.add_argument("--regime-test", action="store_true", help="Enable regime test mode for child daemon")
    p_sup.set_defaults(func=cmd_supervisor)

    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
