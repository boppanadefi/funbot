"""
Phase 4: Paper Trading Pipeline & Out-of-Sample Validation Streamlit Panel.
Visualizes Guardrail 2 (Path A+ 100% Dynamic Trailing Single Exit) and Guardrail 7 ($10 Max Drawdown).
"""

from datetime import datetime, timezone, timedelta
import random
from typing import Dict, List
import streamlit as st
import plotly.graph_objects as go
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.validator import OutOfSampleValidator
from src.engine.hit_rate_surface import HitRateSurfaceAnalyzer
from src.engine.live_paper_trader import LivePaperTradingEngine


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
    """Renders the Paper Trading, Ledger, and Out-of-Sample Performance view."""
    st.markdown(
        """
        <div style="margin-bottom: 0.8rem; font-size: 0.82rem; color: var(--text-muted);">
            <strong>Phase 4 Guardrails 2 & 7:</strong> Evaluates simulated Local-Structure & Time-Decay Exits with Friction-Busting 90/10 Scale-Out 
            (Stop Loss = Lowest Low of previous two 1m candles; 2-candle stagnation + >40% vol drop time-decay; 30s >5% slippage LP drain dump; TP1 sells 90% @ +8%–+10% gross; 10% runner hard-dumped if not >= +30% in 3m) 
            across 60% in-sample and 40% out-of-sample datasets under a hard $10.00 max drawdown ceiling.
        </div>
        """,
        unsafe_allow_html=True,
    )

    paper_engine = LivePaperTradingEngine()
    active_pos = paper_engine.get_active_position()

    # --- 1. LIVE ACTIVE POSITION MONITOR (REAL-TIME TICKER) ---
    if active_pos:
        pnl_usd = active_pos.get("unrealized_pnl_usd", 0.0)
        pnl_pct = active_pos.get("unrealized_return_pct", 0.0)
        pnl_color = "var(--green)" if pnl_usd >= 0 else "var(--red)"
        is_runner = active_pos.get("state") == "RUNNER_ACTIVE" or active_pos.get("tp1_activated")
        struct_stop = active_pos.get("structure_stop_price_sol")
        lowest_low = active_pos.get("lowest_low_2m_candles_sol")

        if is_runner:
            badge_html = '<span class="badge badge-green">TP1 LOCKED (90% @ +9%) • LOTTERY RUNNER (10% | 3m Kill)</span>'
            if struct_stop:
                stop_label = f"{fmt_sol_price(struct_stop)} SOL (Lowest Low 2m Structure)"
            else:
                stop_label = f"{fmt_sol_price(active_pos['current_stop_price_sol'])} SOL (Runner Trailing)"
            stop_color = "var(--green)"
        else:
            badge_html = '<span class="badge badge-amber">TARGETING TP1 (+9.0%)</span>'
            if struct_stop:
                stop_label = f"{fmt_sol_price(struct_stop)} SOL (Lowest Low 2m Structure)"
            else:
                stop_label = f"{fmt_sol_price(active_pos['current_stop_price_sol'])} SOL (Hard Stop -10%)"
            stop_color = "var(--amber)"

        tp1_price = active_pos.get("tp1_price_sol", active_pos.get("entry_price_sol", 0.0) * 1.09)

        st.markdown(
            f"""
            <div style="background: rgba(37, 99, 235, 0.08); border: 1.5px solid #2563eb; border-radius: 8px; padding: 1rem; margin-bottom: 1.2rem;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem;">
                    <div>
                        <span style="font-weight: 800; font-size: 1.05rem; color: var(--blue);">🔵 LIVE OPEN PAPER POSITION:</span>
                        <span class="mono" style="font-weight: 700; margin-left: 0.5rem;">{active_pos['position_id']}</span>
                        <span style="margin-left: 0.6rem;">{badge_html}</span>
                    </div>
                    <div class="mono" style="font-size: 1.15rem; font-weight: 800; color: {pnl_color};">
                        {pnl_pct:+.2f}% (${pnl_usd:+.2f} USD)
                    </div>
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.8rem; font-size: 0.82rem; color: var(--text-muted);">
                    <div>Token: <a href="https://dexscreener.com/solana/{active_pos['token_mint']}" target="_blank" style="color: var(--blue); text-decoration: none;"><strong class="mono">{active_pos['token_mint'][:10]}... ↗</strong></a></div>
                    <div>Entry: <strong class="mono">{fmt_sol_price(active_pos['entry_price_sol'])} SOL</strong></div>
                    <div>TP1 Target: <strong class="mono" style="color: var(--green);">{fmt_sol_price(tp1_price)} SOL (+9%)</strong></div>
                    <div>Current: <strong class="mono">{fmt_sol_price(active_pos['current_price_sol'])} SOL</strong></div>
                    <div>Peak: <strong class="mono">{fmt_sol_price(active_pos['peak_price_sol'])} SOL</strong></div>
                    <div>Stop: <strong class="mono" style="color: {stop_color};">{stop_label}</strong></div>
                    <div>Lowest Low (2m): <strong class="mono">{fmt_sol_price(lowest_low) if lowest_low else 'Forming 2m...'} SOL</strong></div>
                    <div>Elapsed: <strong>{active_pos.get('elapsed_minutes', 0.0)}m / 20.0m</strong></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        close_col1, close_col2 = st.columns([1.5, 3])
        with close_col1:
            if st.button("🚨 CLOSE PAPER POSITION (MARKET DUMP)", use_container_width=True, type="primary"):
                paper_engine.manual_close_active_position(reason="OPERATOR_MANUAL_DUMP")
                st.rerun()
    else:
        status_col1, status_col2 = st.columns([3, 1.2])
        with status_col1:
            st.markdown(
                """
                <div style="background: rgba(34, 197, 94, 0.06); border: 1px solid rgba(34, 197, 94, 0.25); border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1.2rem;">
                    <div style="color: var(--green); font-weight: 700; font-size: 0.88rem;">
                        🟢 LIVE PAPER SCANNER ACTIVE — MONITORING 8–25m SECONDARY WINDOW
                    </div>
                    <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.2rem;">
                        Automatically opens a $15.00 Path A+ position (1 max concurrent) as new tokens enter the active secondary window and pass the Double Gate.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with status_col2:
            active_cands = [p for p in pools if p.get("state") == "ACTIVE_WINDOW"]
            if active_cands:
                if st.button("🎯 OPEN PAPER TRADE NOW", use_container_width=True):
                    paper_engine.on_market_tick(active_cands)
                    st.rerun()

    ledger = PaperLedger()
    trades = ledger.get_all_trades()

    if not trades:
        st.info("No closed paper trades recorded yet. The live on-chain runner is actively monitoring incoming Solana tokens.")
        return

    validator = OutOfSampleValidator()
    in_sample, out_of_sample = validator.split_chronological_dataset(trades, in_sample_ratio=0.60)
    report_oos = validator.evaluate_dataset(out_of_sample, "OUT_OF_SAMPLE")

    # KPI Summary Cards
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Out-of-Sample Win Rate</div>
                <div class="metric-value" style="color: var(--green);">{report_oos.win_rate_pct:.1f}%</div>
                <div class="metric-sub">{report_oos.winning_trades}W / {report_oos.losing_trades}L (Hurdle &ge; 70%)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Net P&L (Out-of-Sample)</div>
                <div class="metric-value" style="color: {'var(--green)' if report_oos.net_pnl_usd >= 0 else 'var(--red)'};">${report_oos.net_pnl_usd:+.2f}</div>
                <div class="metric-sub">Expectancy: ${report_oos.net_expectancy_per_trade_usd:+.2f} / trade</div>
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
                <div class="metric-sub">Hard Ceiling &lt; $10.00</div>
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

    # Cumulative Equity Curve Chart
    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    sorted_trades = sorted(trades, key=lambda t: t["entry_ts"])
    cum_pnl = []
    running = 0.0
    dates = []
    for t in sorted_trades:
        running += t["net_pnl_usd"]
        cum_pnl.append(running)
        dates.append(t["entry_ts"][:16].replace("T", " "))

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=cum_pnl,
            mode="lines+markers",
            name="Cumulative Net P&L ($)",
            line=dict(color="#10b981", width=2.5),
            marker=dict(size=6, color="#10b981"),
            fill="tozeroy",
            fillcolor="rgba(16, 185, 129, 0.08)",
        )
    )
    bg_color = "#0c0c0f" if is_dark else "#ffffff"
    grid_color = "#1e1e24" if is_dark else "#f4f4f5"
    text_color = "#fafafa" if is_dark else "#09090b"

    fig.update_layout(
        title="Simulated Cumulative Net Equity Curve (Post All Fees & Tips)",
        paper_bgcolor=bg_color,
        plot_bgcolor=bg_color,
        font=dict(family="DM Sans, sans-serif", color=text_color, size=11),
        xaxis=dict(showgrid=True, gridcolor=grid_color),
        yaxis=dict(showgrid=True, gridcolor=grid_color, title="Net P&L ($ USD)"),
        margin=dict(l=40, r=20, t=40, b=30),
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Trade Ledger Table
    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
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
