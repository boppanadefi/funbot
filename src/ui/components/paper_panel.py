"""
Phase 4: Paper Trading Pipeline & 24-Hour Infrastructure & Execution Smoke Test.
Visualizes:
1. Trade Accelerator Configuration & Concurrency Override (3 concurrent positions, VUW=1.05, demand=$50/min, min size=$2.00, REGIME_TEST).
2. 24-Hour Gate Scorecard (Pass/Fail criteria across 6 engineering metrics).
3. Stress Injection Protocol (Tests A–D edge-case validations).
4. Strict Mathematical Accounting Ledger & Standard Overview.
"""

from datetime import datetime, timezone, timedelta
import json
import random
from typing import Dict, List
import streamlit as st
import plotly.graph_objects as go

import importlib
import src.engine.paper_ledger
import src.engine.live_paper_trader
import src.engine.trade_accelerator
import src.engine.amm_drift_monitor
import src.engine.stress_injection
import src.engine.gate_evaluator_24h
import src.engine.gate_evaluator_28d
import src.engine.staleness_breaker

# Force reload to pick up runtime updates in active long-running Streamlit processes
try:
    importlib.reload(src.engine.paper_ledger)
    importlib.reload(src.engine.live_paper_trader)
    importlib.reload(src.engine.trade_accelerator)
    importlib.reload(src.engine.amm_drift_monitor)
    importlib.reload(src.engine.stress_injection)
    importlib.reload(src.engine.gate_evaluator_24h)
    importlib.reload(src.engine.gate_evaluator_28d)
    importlib.reload(src.engine.staleness_breaker)
except Exception:
    pass

from src.engine.amm_drift_monitor import AMMDriftMonitor
from src.engine.gate_evaluator_24h import GateEvaluator24Hour
from src.engine.gate_evaluator_28d import GateEvaluator28Day, GoNoGo28DayReport
from src.engine.hit_rate_surface import HitRateSurfaceAnalyzer
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.stress_injection import StressInjectionProtocol
from src.engine.trade_accelerator import TradeAcceleratorConfig
from src.engine.validator import OutOfSampleValidator


def get_strict_records_safe(ledger: PaperLedger) -> List[Dict]:
    """Safely retrieves strict accounting records with graceful fallback parsing."""
    if hasattr(ledger, "get_strict_accounting_records"):
        return ledger.get_strict_accounting_records()

    trades = ledger.get_all_trades()
    records = []
    for t in trades:
        tp1_prices = json.loads(t["tp1_fill_prices"]) if t.get("tp1_fill_prices") else []
        records.append({
            "position_id": t["position_id"],
            "pool_address": t["pool_address"],
            "token_mint": t["token_mint"],
            "signal_ts": t.get("signal_ts") or t["entry_ts"],
            "entry_sim_ts": t.get("entry_sim_ts") or t["entry_ts"],
            "entry_price": t.get("entry_price") or t["entry_price_sol"],
            "position_size_sol": t.get("position_size_sol") or t["sol_spent"],
            "tp1_fill_prices": tp1_prices,
            "runner_exit_price": t.get("runner_exit_price"),
            "net_pnl_sol": t["net_pnl_sol"],
            "net_pnl_usd": t["net_pnl_usd"],
            "all_tx_fees_sol": t.get("all_tx_fees_sol") or (t.get("venue_fees_sol", 0.0) + t.get("priority_fees_sol", 0.0)),
            "all_jito_tips_sol": t.get("all_jito_tips_sol") or t.get("tips_sol", 0.0),
            "exit_reason": t.get("exit_reason", "UNKNOWN"),
        })
    return records


def fmt_sol_price(val: float) -> str:
    """Adaptive precision formatting for Solana token prices."""
    if val <= 0:
        return "0.000000"
    if val < 0.0000001:
        return f"{val:.2e}"
    if val < 0.0001:
        return f"{val:.8f}"
    return f"{val:.6f}"


def render_paper_panel(pools: List[Dict], is_dark: bool = True) -> None:
    """Renders the Paper Trading, Ledger, and 24-Hour Smoke Test Gate view."""
    if "accelerator_enabled" not in st.session_state:
        st.session_state.accelerator_enabled = True
    if "stress_test_results" not in st.session_state:
        st.session_state.stress_test_results = None

    acc_config = TradeAcceleratorConfig(enabled=st.session_state.accelerator_enabled)
    paper_engine = LivePaperTradingEngine(accelerator_config=acc_config)

    # --- PHASE 4 HEADER & TRADE ACCELERATOR BANNER ---
    acc_status_badge = (
        '<span class="badge badge-green">ACCELERATOR ACTIVE (3 POSITIONS MAX • REGIME_TEST)</span>'
        if acc_config.enabled
        else '<span class="badge badge-amber">STANDARD MODE (1 POSITION MAX)</span>'
    )

    st.markdown(
        f"""
        <div style="background: rgba(16, 185, 129, 0.06); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 0.8rem 1rem; margin-bottom: 0.8rem; font-size: 0.8rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                <div>
                    <span style="font-weight: 800; font-size: 0.95rem; color: var(--green);">⚡ Phase 4: 24-Hour Infrastructure & Execution Smoke Test</span>
                    <span style="margin-left: 0.5rem;">{acc_status_badge}</span>
                </div>
                <div style="font-size: 0.75rem; color: var(--text-muted);">
                    Objective: Validate data pipeline integrity & simulation accuracy (P&L ignored).
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.6rem; font-size: 0.75rem; color: var(--text); padding-top: 0.4rem; border-top: 1px solid rgba(16, 185, 129, 0.15);">
                <div><strong>Concurrency Override:</strong> <span class="mono" style="color: var(--blue);">{paper_engine.max_concurrent_positions} Active Positions</span></div>
                <div><strong>Relaxed VUW Filter:</strong> <span class="mono" style="color: var(--amber);">{"1.05 (Relaxed)" if acc_config.enabled else "1.20 (Standard)"}</span></div>
                <div><strong>Demand Floor:</strong> <span class="mono" style="color: var(--amber);">{"$50.0/min" if acc_config.enabled else "$100.0/min"}</span></div>
                <div><strong>Dynamic Sizing Floor:</strong> <span class="mono" style="color: var(--green);">{"$2.00 (Tiny Pools)" if acc_config.enabled else "$3.00"}</span></div>
                <div><strong>Session Tag:</strong> <span class="mono" style="color: #c084fc;">{acc_config.session_regime_tag if acc_config.enabled else "REGIME_A / B"}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- ARCHITECTURE BAR: PHASES 1–4 ---
    st.markdown(
        """
        <div style="background: rgba(37, 99, 235, 0.04); border: 1px solid rgba(37, 99, 235, 0.2); border-radius: 6px; padding: 0.5rem 0.8rem; margin-bottom: 0.9rem; font-size: 0.74rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.4rem;">
                <div>
                    <span style="color: var(--green); font-weight: 700;">● Dedicated Helius WSS:</span>
                    <span style="color: var(--text-muted); margin-left: 0.2rem;">Live PumpSwap Stream (6EF8)</span>
                </div>
                <div>
                    <span style="color: var(--green); font-weight: 700;">● Virtual AMM Mirror:</span>
                    <span style="color: var(--text-muted); margin-left: 0.2rem;">x · y = k Reserves</span>
                </div>
                <div>
                    <span style="color: var(--blue); font-weight: 700;">● Friction Engine:</span>
                    <span style="color: var(--text-muted); margin-left: 0.2rem;">MEV Sandwich • 3-Slice TP1 • Slot Latency</span>
                </div>
                <div>
                    <span style="color: #a855f7; font-weight: 700;">● Strict Ledger:</span>
                    <span style="color: var(--text-muted); margin-left: 0.2rem;">Exact Net Cash Flow</span>
                </div>
                <div>
                    <span style="color: var(--amber); font-weight: 700;">● 24h Smoke Test:</span>
                    <span style="color: var(--text-muted); margin-left: 0.2rem;">6-Gate Verification</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- 1. ACTIVE POSITIONS DISPLAY (CONCURRENCY 1–3) ---
    all_active = paper_engine.get_all_active_positions()
    if all_active:
        st.markdown(
            f"<div style='font-weight: 700; font-size: 0.88rem; margin-bottom: 0.4rem; color: var(--blue);'>"
            f"🔵 OPEN PAPER POSITIONS ({len(all_active)} / {paper_engine.max_concurrent_positions} Active):"
            f"</div>",
            unsafe_allow_html=True,
        )
        for idx, pos in enumerate(all_active):
            pnl_usd = pos.get("unrealized_pnl_usd", 0.0)
            pnl_pct = pos.get("unrealized_return_pct", 0.0)
            pnl_color = "var(--green)" if pnl_usd >= 0 else "var(--red)"
            is_runner = pos.get("state") == "RUNNER_ACTIVE" or pos.get("tp1_activated")
            struct_stop = pos.get("structure_stop_price_sol")
            lowest_low = pos.get("lowest_low_2m_candles_sol")
            pos_id = pos["position_id"]

            if is_runner:
                badge_html = '<span class="badge badge-green">TP1 LOCKED (90% @ +9%) • RUNNER (10% | 3m Kill)</span>'
                stop_label = f"{fmt_sol_price(struct_stop)} SOL" if struct_stop else f"{fmt_sol_price(pos['current_stop_price_sol'])} SOL"
                stop_color = "var(--green)"
            else:
                badge_html = f'<span class="badge badge-amber">{pos.get("regime", "REGIME_TEST")}</span>'
                stop_label = f"{fmt_sol_price(struct_stop)} SOL" if struct_stop else f"{fmt_sol_price(pos['current_stop_price_sol'])} SOL"
                stop_color = "var(--amber)"

            tp1_price = pos.get("tp1_price_sol", pos.get("entry_price_sol", 0.0) * 1.09)

            st.markdown(
                f"""
                <div style="background: rgba(37, 99, 235, 0.07); border: 1px solid rgba(37, 99, 235, 0.4); border-radius: 6px; padding: 0.8rem; margin-bottom: 0.6rem;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                        <div>
                            <span class="mono" style="font-weight: 700; color: var(--blue);">Position #{idx+1}: {pos_id}</span>
                            <span style="margin-left: 0.5rem;">{badge_html}</span>
                        </div>
                        <div class="mono" style="font-size: 1.05rem; font-weight: 800; color: {pnl_color};">
                            {pnl_pct:+.2f}% (${pnl_usd:+.2f} USD)
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 0.5rem; font-size: 0.78rem; color: var(--text-muted);">
                        <div>Token: <a href="https://dexscreener.com/solana/{pos['token_mint']}" target="_blank" style="color: var(--blue); text-decoration: none;"><strong class="mono">{pos['token_mint'][:8]}... ↗</strong></a></div>
                        <div>Entry: <strong class="mono">{fmt_sol_price(pos['entry_price_sol'])} SOL</strong></div>
                        <div>TP1: <strong class="mono" style="color: var(--green);">{fmt_sol_price(tp1_price)} SOL (+9%)</strong></div>
                        <div>Current: <strong class="mono">{fmt_sol_price(pos['current_price_sol'])} SOL</strong></div>
                        <div>Peak: <strong class="mono">{fmt_sol_price(pos['peak_price_sol'])} SOL</strong></div>
                        <div>Stop: <strong class="mono" style="color: {stop_color};">{stop_label}</strong></div>
                        <div>Elapsed: <strong>{pos.get('elapsed_minutes', 0.0)}m / 20m</strong></div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        col_close, _ = st.columns([2, 3])
        with col_close:
            if st.button("🚨 CLOSE ALL POSITIONS (EMERGENCY DUMP)", use_container_width=True, type="primary"):
                for p in all_active:
                    paper_engine.manual_close_active_position(reason="OPERATOR_MANUAL_DUMP")
                st.rerun()

    # If capacity is available, offer trade launcher
    if paper_engine.has_capacity_for_new_trade():
        status_col1, status_col2 = st.columns([3, 1.2])
        with status_col1:
            st.markdown(
                f"""
                <div style="background: rgba(34, 197, 94, 0.06); border: 1px solid rgba(34, 197, 94, 0.25); border-radius: 6px; padding: 0.6rem 0.8rem; margin-bottom: 0.8rem;">
                    <div style="color: var(--green); font-weight: 700; font-size: 0.82rem;">
                        🟢 TRADE ACCELERATOR READY ({len(all_active)}/{paper_engine.max_concurrent_positions} active slots filled)
                    </div>
                    <div style="font-size: 0.74rem; color: var(--text-muted); margin-top: 0.1rem;">
                        Relaxed constraints active: VUW &ge; 1.05 • Demand &ge; $50/min • Sizing floor $2.00 • Tag: REGIME_TEST.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with status_col2:
            active_cands = [p for p in pools if p.get("state") == "ACTIVE_WINDOW"]
            if active_cands:
                if st.button("🎯 TRIGGER ACCELERATED TRADE", use_container_width=True):
                    paper_engine.on_market_tick(active_cands)
                    st.rerun()

    ledger = PaperLedger()
    trades = ledger.get_all_trades()

    # --- KPI SUMMARY CARDS ---
    if trades:
        validator = OutOfSampleValidator()
        in_sample, out_of_sample = validator.split_chronological_dataset(trades, in_sample_ratio=0.60)
        report_oos = validator.evaluate_dataset(out_of_sample, "OUT_OF_SAMPLE")

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Completed Paper Trades</div>
                    <div class="metric-value" style="color: var(--blue);">{len(trades)}</div>
                    <div class="metric-sub">Min Gate Target: &ge; 10</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi2:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Out-of-Sample Win Rate</div>
                    <div class="metric-value" style="color: var(--green);">{report_oos.win_rate_pct:.1f}%</div>
                    <div class="metric-sub">{report_oos.winning_trades}W / {report_oos.losing_trades}L</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi3:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Max Drawdown</div>
                    <div class="metric-value" style="color: var(--green);">${report_oos.max_drawdown_usd:.2f}</div>
                    <div class="metric-sub">Daily Loss Halt: -$10.00</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi4:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Profit Factor</div>
                    <div class="metric-value" style="color: var(--blue);">{report_oos.profit_factor:.2f}</div>
                    <div class="metric-sub">Gross Win / Gross Loss</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # --- TABS: GO/NO-GO GATE (PHASE 5) | 24H SCORECARD | STRESS INJECTION | STRICT LEDGER | OVERVIEW ---
    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
    tab_phase5, tab_gate, tab_stress, tab_strict, tab_overview = st.tabs([
        "🎯 28-Day Go/No-Go Gate (Phase 5)",
        "🛡️ 24-Hour Gate Scorecard (Phase 4)",
        "⚡ Stress Injection Protocol",
        "📊 Strict Accounting Ledger (Phase 3)",
        "📋 Standard Overview",
    ])

    # TAB 0: 28-DAY GO/NO-GO GATE (PHASE 5)
    with tab_phase5:
        st.markdown(
            """
            <div style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 0.6rem;">
                <strong>Phase 5 Go/No-Go Gate:</strong> At the end of 28 days of paper trading, the strategy is evaluated
                against 6 strict mathematical, execution, and infrastructure criteria to determine whether live Mainnet capital deployment is approved.
            </div>
            """,
            unsafe_allow_html=True,
        )

        col_sample, _ = st.columns([2.5, 2.5])
        with col_sample:
            use_sim_sample = st.toggle(
                "🧪 Load 28-Day Simulation Benchmark Dataset (20 Trades)",
                value=False,
                key="phase5_sim_toggle",
                help="Switch between live SQLite ledger data and a complete 28-day benchmark run.",
            )

        evaluator_28d = GateEvaluator28Day(ledger=ledger, staleness_breaker=paper_engine.staleness_breaker)

        if use_sim_sample:
            t_now = datetime.now(timezone.utc)
            bench_trades = []
            for i in range(20):
                e_ts = (t_now - timedelta(days=27 - int(i * 1.35))).isoformat()
                if i < 15:
                    bench_trades.append({
                        "position_id": f"bench_win_{i+1}",
                        "pool_address": f"PoolBenchWin_{i}",
                        "token_mint": f"MintBenchWin_{i}",
                        "regime": "REGIME_A",
                        "entry_ts": e_ts,
                        "entry_price": 0.00100,
                        "entry_price_sol": 0.00100,
                        "position_size_sol": 0.096774,
                        "tp1_fill_prices": json.dumps([0.00109, 0.00109, 0.00109]),
                        "runner_exit_price": 0.00130,
                        "exit_price_sol": 0.00111,
                        "sol_spent": 0.096774,
                        "sol_received": 0.107000,
                        "all_tx_fees_sol": 0.002,
                        "all_jito_tips_sol": 0.0025,
                        "net_pnl_sol": 0.005426,
                        "net_pnl_usd": 0.84,
                        "net_return_pct": 5.6,
                        "exit_reason": "RUNNER_STRUCTURE_EXIT",
                        "ratchet_activated": 1,
                        "duration_minutes": 8.5,
                        "avg_slippage_pct": 0.85,
                        "runner_pnl_sol": 0.001200,
                    })
                else:
                    bench_trades.append({
                        "position_id": f"bench_loss_{i+1}",
                        "pool_address": f"PoolBenchLoss_{i}",
                        "token_mint": f"MintBenchLoss_{i}",
                        "regime": "REGIME_A",
                        "entry_ts": e_ts,
                        "entry_price": 0.00100,
                        "entry_price_sol": 0.00100,
                        "position_size_sol": 0.096774,
                        "tp1_fill_prices": "[]",
                        "runner_exit_price": None,
                        "exit_price_sol": 0.00092,
                        "sol_spent": 0.096774,
                        "sol_received": 0.089000,
                        "all_tx_fees_sol": 0.002,
                        "all_jito_tips_sol": 0.0025,
                        "net_pnl_sol": -0.007742,
                        "net_pnl_usd": -1.20,
                        "net_return_pct": -8.0,
                        "exit_reason": "HARD_STOP",
                        "ratchet_activated": 0,
                        "duration_minutes": 3.2,
                        "avg_slippage_pct": 0.90,
                        "runner_pnl_sol": 0.0,
                    })
            gonogo_report = evaluator_28d.evaluate(trades=bench_trades)
        else:
            gonogo_report = evaluator_28d.evaluate()

        decision_badge = (
            f'<span class="badge badge-green" style="font-size: 0.88rem; padding: 0.35rem 0.75rem;">{gonogo_report.go_decision}</span>'
            if gonogo_report.is_overall_passed
            else f'<span class="badge badge-red" style="font-size: 0.88rem; padding: 0.35rem 0.75rem;">{gonogo_report.go_decision}</span>'
        )

        st.markdown(
            f"""
            <div class="metric-card" style="margin-bottom: 0.8rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
                    <div>
                        <strong style="font-size: 1.05rem;">Phase 5: The Go/No-Go Gate Decision</strong>
                        <div style="font-size: 0.76rem; color: var(--text-muted); margin-top: 0.1rem;">
                            Evaluation Window: 28 Days (Observed: {gonogo_report.days_elapsed}d • Trades: {gonogo_report.total_trades_evaluated}) • {gonogo_report.passed_metrics}/6 Criteria Passing
                        </div>
                    </div>
                    <div>{decision_badge}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 28-day KPI Cards
        p5_kpi1, p5_kpi2, p5_kpi3, p5_kpi4, p5_kpi5, p5_kpi6 = st.columns(6)
        stats = gonogo_report.summary_stats
        with p5_kpi1:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Net Expectancy</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['net_expectancy_sol'] > 0 else 'var(--red)'}; font-size: 1.0rem;">
                        {stats['net_expectancy_sol']:+.5f} SOL
                    </div>
                    <div class="metric-sub">Hurdle: &gt; 0.0 SOL</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with p5_kpi2:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">TP1 Win Rate</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['tp1_win_rate_pct'] >= 60.0 else 'var(--red)'}; font-size: 1.0rem;">
                        {stats['tp1_win_rate_pct']:.1f}%
                    </div>
                    <div class="metric-sub">Hurdle: &ge; 60.0%</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with p5_kpi3:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Avg Slippage</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['avg_slippage_pct'] < 2.0 else 'var(--red)'}; font-size: 1.0rem;">
                        {stats['avg_slippage_pct']:.2f}%
                    </div>
                    <div class="metric-sub">Hurdle: &lt; 2.0%</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with p5_kpi4:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Runner Contribution</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['runner_contribution_pct'] > 5.0 else 'var(--amber)'}; font-size: 1.0rem;">
                        {stats['runner_contribution_pct']:.1f}%
                    </div>
                    <div class="metric-sub">Hurdle: &gt; 5.0%</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with p5_kpi5:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Max Drawdown</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['max_drawdown_usd'] < 10.0 else 'var(--red)'}; font-size: 1.0rem;">
                        ${stats['max_drawdown_usd']:.2f}
                    </div>
                    <div class="metric-sub">Hurdle: &lt; $10.00</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with p5_kpi6:
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">Stale Feed Rejects</div>
                    <div class="metric-value" style="color: {'var(--green)' if stats['stale_feed_reject_pct'] < 5.0 else 'var(--red)'}; font-size: 1.0rem;">
                        {stats['stale_feed_reject_pct']:.2f}%
                    </div>
                    <div class="metric-sub">Hurdle: &lt; 5.0%</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # 28-day Scorecard Table
        p5_rows = []
        for m in gonogo_report.metrics:
            badge = '<span class="badge badge-green">PASS</span>' if m.is_passed else '<span class="badge badge-red">FAIL</span>'
            row = (
                f"<tr>"
                f"<td><strong>{m.metric_name}</strong></td>"
                f"<td class='mono'>{m.pass_threshold}</td>"
                f"<td class='mono'><strong>{m.actual_value}</strong></td>"
                f"<td>{badge}</td>"
                f"<td style='font-size: 0.75rem; color: var(--text-muted);'>{m.implication_if_failed}</td>"
                f"</tr>"
            )
            p5_rows.append(row)

        p5_table_html = (
            f"<div class='metric-card' style='overflow-x: auto; margin-top: 0.6rem;'>"
            f"<table class='data-table'>"
            f"<thead>"
            f"<tr>"
            f"<th>Metric</th>"
            f"<th>Pass Threshold</th>"
            f"<th>Actual Value</th>"
            f"<th>Status</th>"
            f"<th>Implication if Failed</th>"
            f"</tr>"
            f"</thead>"
            f"<tbody>{''.join(p5_rows)}</tbody>"
            f"</table>"
            f"</div>"
        )
        st.html(p5_table_html)

    # TAB 1: 24-HOUR GATE SCORECARD
    with tab_gate:
        evaluator = GateEvaluator24Hour(ledger=ledger)
        gate_report = evaluator.evaluate_gate(cached_stress_results=st.session_state.stress_test_results)

        gate_status_html = (
            '<span class="badge badge-green" style="font-size: 0.85rem; padding: 0.3rem 0.6rem;">OVERALL GATE STATUS: PASSED (6/6 METRICS MET)</span>'
            if gate_report.is_overall_passed
            else f'<span class="badge badge-amber" style="font-size: 0.85rem; padding: 0.3rem 0.6rem;">OVERALL GATE STATUS: PENDING ({gate_report.passed_metrics}/{gate_report.total_metrics} METRICS PASSING)</span>'
        )

        st.markdown(
            f"""
            <div class="metric-card" style="margin-bottom: 0.8rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                    <div>
                        <strong style="font-size: 1rem;">Phase 4: 24-Hour Gate Pass/Fail Criteria</strong>
                    </div>
                    <div>{gate_status_html}</div>
                </div>
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.6rem;">
                    Evaluated solely on engineering and execution metrics. Profitability metrics are explicitly quarantined.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        gate_rows = []
        for m in gate_report.metrics:
            badge = '<span class="badge badge-green">PASS</span>' if m.is_passed else '<span class="badge badge-red">FAIL / PENDING</span>'
            row = (
                f"<tr>"
                f"<td><strong>{m.metric_name}</strong></td>"
                f"<td class='mono'>{m.pass_threshold}</td>"
                f"<td class='mono'><strong>{m.actual_value}</strong></td>"
                f"<td>{badge}</td>"
                f"<td style='font-size: 0.75rem; color: var(--text-muted);'>{m.implication_if_failed}</td>"
                f"</tr>"
            )
            gate_rows.append(row)

        gate_table = (
            f"<div class='metric-card' style='overflow-x: auto;'>"
            f"<table class='data-table'>"
            f"<thead>"
            f"<tr>"
            f"<th>Metric</th>"
            f"<th>Pass Threshold</th>"
            f"<th>Actual Value</th>"
            f"<th>Status</th>"
            f"<th>Implication if Failed</th>"
            f"</tr>"
            f"</thead>"
            f"<tbody>{''.join(gate_rows)}</tbody>"
            f"</table>"
            f"</div>"
        )
        st.html(gate_table)

    # TAB 2: STRESS INJECTION PROTOCOL
    with tab_stress:
        st.markdown(
            """
            <div style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: 0.6rem;">
                <strong>Step 3: Stress Injection Protocol</strong> manually forces edge-case scenarios into the execution engine to verify safety gates:
                Daily loss halt (-$10), 30s liquidity collapse exit (>5% slippage), Token-2022 TransferFee rejection, and time-sliced TP1 with escalating MEV penalties.
            </div>
            """,
            unsafe_allow_html=True,
        )

        col_run, _ = st.columns([2, 3])
        with col_run:
            if st.button("⚡ EXECUTE ALL 4 STRESS INJECTION TESTS", use_container_width=True, type="primary"):
                protocol = StressInjectionProtocol(ledger=ledger)
                st.session_state.stress_test_results = protocol.run_all_stress_tests()
                st.rerun()

        stress_results = st.session_state.stress_test_results
        if not stress_results:
            st.info("Click the button above to execute Tests A–D against the active paper engine.")
        else:
            stress_cards = []
            for res in stress_results:
                badge = '<span class="badge badge-green">PASSED</span>' if res.passed else '<span class="badge badge-red">FAILED</span>'
                card = (
                    f"<div style='background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem;'>"
                    f"<div style='display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem;'>"
                    f"<div><strong>{res.test_id}: {res.test_name}</strong></div>"
                    f"<div>{badge}</div>"
                    f"</div>"
                    f"<div style='font-size: 0.78rem; color: var(--text); margin-bottom: 0.3rem;'>{res.summary}</div>"
                    f"<div class='mono' style='font-size: 0.72rem; color: var(--text-muted);'>{json.dumps(res.details)}</div>"
                    f"</div>"
                )
                stress_cards.append(card)

            st.html("".join(stress_cards))

    # TAB 3: STRICT ACCOUNTING LEDGER (PHASE 3)
    with tab_strict:
        strict_records = get_strict_records_safe(ledger)
        if not strict_records:
            st.info("No strict trades logged yet. Run simulated trades to populate strict accounting.")
        else:
            strict_rows = []
            for r in strict_records[:12]:
                pnl_sol = r["net_pnl_sol"]
                pnl_color = "var(--green)" if pnl_sol > 0 else "var(--red)"
                badge = (
                    '<span class="badge badge-green">WIN</span>'
                    if pnl_sol > 0
                    else '<span class="badge badge-red">LOSS</span>'
                )
                mint = r["token_mint"]
                token_short = f"{mint[:6]}...{mint[-4:]}"
                token_link = f"<a href='https://dexscreener.com/solana/{mint}' target='_blank' style='color: var(--blue); text-decoration: none; font-weight: 600;'>{token_short} ↗</a>"

                sig_t = r["signal_ts"][11:19] if len(r["signal_ts"]) >= 19 else r["signal_ts"]
                land_t = r["entry_sim_ts"][11:19] if len(r["entry_sim_ts"]) >= 19 else r["entry_sim_ts"]

                tp1_list = r["tp1_fill_prices"]
                if tp1_list:
                    tp1_str = f"[{', '.join(fmt_sol_price(p) for p in tp1_list)}]"
                else:
                    tp1_str = "<span style='color: var(--text-muted);'>N/A (Pre-TP1 Exit)</span>"

                runner_p = fmt_sol_price(r["runner_exit_price"]) if r["runner_exit_price"] else "<span style='color: var(--text-muted);'>N/A</span>"

                strict_row = (
                    f"<tr>"
                    f"<td class='mono'><strong>{r['position_id']}</strong></td>"
                    f"<td class='mono'>{token_link}</td>"
                    f"<td class='mono' style='font-size: 0.75rem;'>{sig_t}</td>"
                    f"<td class='mono' style='font-size: 0.75rem;'>{land_t}</td>"
                    f"<td class='mono'>{fmt_sol_price(r['entry_price'])}</td>"
                    f"<td class='mono'>{r['position_size_sol']:.5f}</td>"
                    f"<td class='mono' style='font-size: 0.72rem;'>{tp1_str}</td>"
                    f"<td class='mono'>{runner_p}</td>"
                    f"<td class='mono'>{r['all_tx_fees_sol']:.6f}</td>"
                    f"<td class='mono'>{r['all_jito_tips_sol']:.5f}</td>"
                    f"<td class='mono' style='color: {pnl_color};'><strong>{pnl_sol:+.6f} SOL</strong></td>"
                    f"<td>{badge}</td>"
                    f"</tr>"
                )
                strict_rows.append(strict_row)

            strict_body = "".join(strict_rows)
            strict_html = (
                f"<div class='metric-card' style='overflow-x: auto;'>"
                f"<div style='font-weight: 700; font-size: 0.95rem; margin-bottom: 0.6rem;'>Phase 3 Strict Mathematical Accounting Ledger</div>"
                f"<div style='font-size: 0.75rem; color: var(--text-muted); margin-bottom: 0.8rem;'>"
                f"Net P&L (SOL) = (SOL received across all exits) - (SOL spent on entry) - (All TX fees) - (All Jito tips) • Exact AMM fill prices & slot timestamps."
                f"</div>"
                f"<table class='data-table'>"
                f"<thead>"
                f"<tr>"
                f"<th>Position ID</th>"
                f"<th>Token Mint</th>"
                f"<th>Signal TS</th>"
                f"<th>Landed TS</th>"
                f"<th>Entry Fill (SOL)</th>"
                f"<th>Size (SOL)</th>"
                f"<th>TP1 Slices (3x 30%)</th>"
                f"<th>Runner Fill (10%)</th>"
                f"<th>All TX Fees (SOL)</th>"
                f"<th>All Tips (SOL)</th>"
                f"<th>Net P&L (SOL)</th>"
                f"<th>Outcome</th>"
                f"</tr>"
                f"</thead>"
                f"<tbody>{strict_body}</tbody>"
                f"</table>"
                f"</div>"
            )
            st.html(strict_html)

    # TAB 4: STANDARD OVERVIEW
    with tab_overview:
        if not trades:
            st.info("No closed paper trades recorded yet.")
        else:
            table_rows = []
            for t in trades[:12]:
                pnl = t["net_pnl_usd"]
                pnl_color = "var(--green)" if pnl > 0 else "var(--red)"
                badge = (
                    '<span class="badge badge-green">WIN</span>'
                    if pnl > 0
                    else '<span class="badge badge-red">LOSS</span>'
                )
                mint = t["token_mint"]
                token_short = f"{mint[:6]}...{mint[-4:]}"
                token_link = f"<a href='https://dexscreener.com/solana/{mint}' target='_blank' style='color: var(--blue); text-decoration: none; font-weight: 600;'>{token_short} ↗</a>"

                exit_r = t.get("exit_reason", "UNKNOWN")
                if exit_r in ("CLOSED_TIME_DECAY", "TIME_DECAY_STAGNATION"):
                    reason_badge = f"<span class='badge badge-amber'>{exit_r}</span>"
                elif exit_r in ("CLOSED_LIQUIDITY_DRAIN", "LIQUIDITY_COLLAPSE_DRAIN"):
                    reason_badge = f"<span class='badge badge-red'>{exit_r}</span>"
                elif exit_r in ("STRUCTURE_TRAILING_STOP",):
                    reason_badge = f"<span class='badge' style='background: rgba(147, 51, 234, 0.15); color: #c084fc; border: 1px solid rgba(147, 51, 234, 0.3);'>{exit_r}</span>"
                else:
                    reason_badge = f"<span class='badge badge-blue'>{exit_r}</span>"

                row = (
                    f"<tr>"
                    f"<td class='mono'><strong>{t['position_id']}</strong></td>"
                    f"<td class='mono'>{token_link}</td>"
                    f"<td>{t['regime']}</td>"
                    f"<td class='mono'>{fmt_sol_price(t['entry_price_sol'])}</td>"
                    f"<td class='mono'>{fmt_sol_price(t['exit_price_sol'])}</td>"
                    f"<td class='mono'>{t['duration_minutes']:.1f}m</td>"
                    f"<td>{reason_badge}</td>"
                    f"<td class='mono' style='color: {pnl_color};'><strong>{t['net_return_pct']:+.1f}%</strong></td>"
                    f"<td class='mono' style='color: {pnl_color};'><strong>${pnl:+.2f}</strong></td>"
                    f"<td>{badge}</td>"
                    f"</tr>"
                )
                table_rows.append(row)

            table_body = "".join(table_rows)
            table_html = (
                f"<div class='metric-card' style='overflow-x: auto;'>"
                f"<div style='font-weight: 700; font-size: 0.95rem; margin-bottom: 0.6rem;'>Friction-Busting Scale-Out Paper Trading Execution Ledger</div>"
                f"<table class='data-table'>"
                f"<thead>"
                f"<tr>"
                f"<th>Position ID</th>"
                f"<th>Token Mint</th>"
                f"<th>Regime</th>"
                f"<th>Entry (SOL)</th>"
                f"<th>Exit (SOL)</th>"
                f"<th>Duration</th>"
                f"<th>Exit Reason</th>"
                f"<th>Net Return</th>"
                f"<th>Net P&L</th>"
                f"<th>Outcome</th>"
                f"</tr>"
                f"</thead>"
                f"<tbody>{table_body}</tbody>"
                f"</table>"
                f"</div>"
            )
            st.html(table_html)
