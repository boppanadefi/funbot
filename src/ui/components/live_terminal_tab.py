"""
Streamlit Live Operator Terminal Component (Phase 11).
Displays real-time SQLite WAL data streams, active position trailing stops, and interactive safety controls.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import streamlit as st
import time
from typing import Any, Dict, List, Optional

from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.telemetry.wal_streamer import SqliteWalStreamer


def render_live_operator_terminal_tab(is_dark: bool = True) -> None:
    """Renders the Live Operator Terminal (Tab 10)."""
    streamer = SqliteWalStreamer()
    cb = DailyCircuitBreaker()
    ks = EmergencyKillSwitch()

    st.markdown(
        """
        <div style="margin-bottom: 1rem;">
            <div style="font-size: 1.15rem; font-weight: 700; color: var(--text);">
                ⚡ Autonomous Production Operator Terminal (Phase 11)
            </div>
            <div style="font-size: 0.82rem; color: var(--text-muted); margin-top: 0.2rem;">
                Real-time SQLite WAL streaming, interactive emergency controls, and dynamic trailing ratchet telemetry.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Top Controls Bar: Refresh Rate & Manual Controls
    ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([2, 2, 2, 2])

    with ctrl_col1:
        refresh_rate = st.selectbox(
            "Auto-Refresh Stream",
            ["Off", "2s", "5s", "10s"],
            index=1,
            key="terminal_refresh_rate",
        )

    # Risk state
    risk = streamer.get_risk_status()
    ks_tripped = risk["kill_switch"].get("is_tripped", False)
    cb_halted = risk["circuit_breaker"].get("is_halted", False)

    with ctrl_col2:
        st.write("")
        st.write("")
        if st.button("🚨 PANIC STOP (KILL)", type="primary", use_container_width=True):
            ks.trip(operator="TERMINAL_OPERATOR", reason="Operator manual panic stop from Live Terminal")
            st.warning("Kill switch tripped! Trading halted.")
            st.rerun()

    with ctrl_col3:
        st.write("")
        st.write("")
        if st.button("✅ RESET KILL SWITCH", use_container_width=True, disabled=not ks_tripped):
            ks.reset(operator="TERMINAL_OPERATOR", notes="Manual reset from Live Terminal")
            st.success("Kill switch reset successfully.")
            st.rerun()

    with ctrl_col4:
        st.write("")
        st.write("")
        if st.button("🔄 RESET CIRCUIT BREAKER", use_container_width=True, disabled=not cb_halted):
            cb.reset_circuit_breaker(operator="TERMINAL_OPERATOR", notes="Reset daily loss from Live Terminal")
            st.success("Circuit breaker reset successfully.")
            st.rerun()

    st.markdown("---")

    # 2. Key System Health & Risk Metric Cards
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)

    status_badge = "🚨 HALTED" if ks_tripped else ("🛑 CB HALT" if cb_halted else "🟢 ACTIVE")
    status_color = "var(--red)" if (ks_tripped or cb_halted) else "var(--green)"

    cb_status = cb.get_status()
    rem_budget = cb_status.get("remaining_budget_usd", 10.00)
    net_pnl = cb_status.get("net_daily_pnl_usd", 0.00)
    db_metrics = streamer.get_database_metrics()

    with m_col1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Execution Guardrail State</div>
                <div class="metric-value" style="color: {status_color};">{status_badge}</div>
                <div class="metric-sub">Kill Switch: {'TRIPPED' if ks_tripped else 'ARMED'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m_col2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Remaining Loss Budget</div>
                <div class="metric-value" style="color: var(--blue);">${rem_budget:.2f}</div>
                <div class="metric-sub">Ceiling: -$10.00 | Net: ${net_pnl:.2f}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m_col3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Section 7.1 Capital Isolation</div>
                <div class="metric-value" style="color: var(--green);">$25.00 Active</div>
                <div class="metric-sub">Cold Reserve: $75.00 Locked</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with m_col4:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">SQLite WAL Journal</div>
                <div class="metric-value" style="color: var(--text); font-size: 0.95rem;">{db_metrics['telemetry_db_size']}</div>
                <div class="metric-sub">WAL Mode: {'ACTIVE' if db_metrics['telemetry_wal_active'] else 'STANDBY'} ({db_metrics['last_synced_utc']})</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)

    # 3. Active Positions Telemetry
    st.markdown("### 📊 Active Position & Trailing Ratchet Monitor")
    positions = streamer.get_active_positions()

    if not positions:
        st.markdown(
            """
            <div class="metric-card" style="text-align: center; padding: 2rem; color: var(--text-muted);">
                <strong>No Active Open Positions.</strong><br/>
                Engine is monitoring canonical PumpSwap candidate pools 8 to 25 minutes post-graduation.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        for pos in positions:
            p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns(5)
            mint = pos.get("token_mint", "N/A")
            short_mint = f"{mint[:4]}...{mint[-4:]}" if len(mint) > 8 else mint
            ret = pos.get("unrealized_return_pct", 0.0)
            sign = "+" if ret >= 0 else ""
            ret_color = "var(--green)" if ret >= 0 else "var(--red)"

            with p_col1:
                st.markdown(f"**Token:** `{short_mint}`")
                st.caption(f"Regime: {pos.get('regime', 'REGIME_POST_GRAD')}")
            with p_col2:
                st.markdown(f"**Entry Price:** `{pos.get('entry_price_sol', 0.0):.8f} SOL`")
                st.caption(f"Current: `{pos.get('current_price_sol', 0.0):.8f} SOL`")
            with p_col3:
                st.markdown(f"**Peak Price:** `{pos.get('peak_price_sol', 0.0):.8f} SOL`")
                st.caption(f"Stop Floor: `{pos.get('current_stop_price_sol', 0.0):.8f} SOL`")
            with p_col4:
                st.markdown(f"**Unrealized:** <span style='color: {ret_color}; font-weight: 700;'>{sign}{ret:.2f}%</span>", unsafe_allow_html=True)
                st.caption(f"PnL: ${pos.get('unrealized_pnl_usd', 0.0):.2f}")
            with p_col5:
                tp1_stat = "Locked (Runner Active)" if pos.get("tp1_activated") else "Seeking +9% TP1"
                st.markdown(f"**TP1 State:** `{tp1_stat}`")
                st.caption(f"Elapsed: {pos.get('elapsed_minutes', 0.0)}m")
            st.divider()

    # 4. Recent Real-Time Events Feed
    st.markdown("### 📋 Real-Time SQLite WAL Audit Feed")
    recent_events = streamer.get_recent_trade_events(limit=8)

    if not recent_events:
        st.markdown(
            """
            <div style="font-size: 0.85rem; color: var(--text-muted); font-style: italic;">
                No recent database transactions recorded in active WAL session.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        table_html = """
        <table class="data-table">
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Event / Position</th>
                    <th>Type</th>
                    <th>Net PnL</th>
                    <th>Exit Reason</th>
                </tr>
            </thead>
            <tbody>
        """
        for ev in recent_events:
            ts = ev.get("timestamp") or ev.get("entry_time") or ev.get("exit_time") or "N/A"
            pos_id = ev.get("position_id") or ev.get("pool_address") or "Event"
            pnl = ev.get("realized_net_pnl_usd") or ev.get("net_pnl_usd") or 0.0
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            pnl_color = "var(--green)" if pnl >= 0 else "var(--red)"
            reason = ev.get("exit_reason") or ev.get("action") or "EXECUTED"

            table_html += f"""
            <tr>
                <td class="mono">{ts}</td>
                <td class="mono">{pos_id[:16]}...</td>
                <td><span class="badge badge-blue">TRADE</span></td>
                <td class="mono" style="color: {pnl_color}; font-weight: 600;">{pnl_str}</td>
                <td><code>{reason}</code></td>
            </tr>
            """
        table_html += "</tbody></table>"
        st.markdown(table_html, unsafe_allow_html=True)

    # 5. Handle Auto-Refresh Loop
    if refresh_rate != "Off":
        secs = int(refresh_rate.replace("s", ""))
        time.sleep(secs)
        st.rerun()
