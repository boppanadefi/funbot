"""
Phase 3: Dynamic Cost Engine & Pre-Flight Double Gate Streamlit Panel.
Visualizes Guardrail 4 (Dual-Hurdle Double Gate) and Guardrail 6 (Jito Tip Caps).
"""

from typing import Dict, List
import streamlit as st
from src.cost.double_gate import PreFlightDoubleGate
from src.cost.fee_tiers import FeeTierLookup


def render_cost_panel(pools: List[Dict]) -> None:
    """Renders the Dynamic Cost Engine & Pre-Flight Double Gate view."""
    st.markdown(
        """
        <div style="margin-bottom: 0.8rem; font-size: 0.82rem; color: var(--text-muted);">
            <strong>Phase 3 Guardrails 4 & 6:</strong> Pre-flight cost gate enforcing expected friction caps 
            (7.0% Regime A, 6.0% Regime B), stressed cost cap (10.0%), and gross profit hurdles (+16% to +22% in Regime A) 
            guaranteeing a 10%–14% net profit buffer before dynamic trailing stop activation.
        </div>
        """,
        unsafe_allow_html=True,
    )

    gate = PreFlightDoubleGate(sol_price_usd=155.0)

    # Position sizing selector & dynamic sizing toggle
    sim_col1, sim_col2 = st.columns([1, 2])
    with sim_col1:
        trade_size = st.select_slider(
            "Max Test Position Size ($ USD)",
            options=[10.0, 15.0, 20.0, 25.0],
            value=25.0,
            format_func=lambda x: f"${x:.0f} USD",
        )
    with sim_col2:
        use_dyn = st.toggle(
            "Dynamic Liquidity Sizing (Fix 5: Auto-Resize & Floor $3.00)",
            value=True,
            help="Sizes position = min(Max Safe Swap at 1.5% impact, $25 wallet limit). Hard rejects if < $3.00.",
        )
        st.caption("Auto-resizes thinner pools (e.g. $6) instead of starving bot with static $20 check.")

    # Evaluate active secondary candidates
    active_pools = [p for p in pools if p.get("state") == "ACTIVE_WINDOW"]
    display_pools = active_pools if active_pools else pools[:8]

    eval_results = []
    for p in display_pools:
        mc = p.get("market_cap_sol", 400.0)
        reserve = p.get("current_sol_reserve", p.get("sol_reserve", 85.0))
        is_canon = bool(p.get("is_canonical", True))
        res = gate.evaluate(
            pool_address=p["pool_address"],
            token_mint=p["token_mint"],
            market_cap_sol=mc,
            current_sol_reserve=reserve,
            position_notional_usd=trade_size,
            is_canonical=is_canon,
            use_dynamic_sizing=use_dyn,
        )
        eval_results.append((p, res))

    # Top KPI Metrics
    pass_count = sum(1 for _, r in eval_results if r.is_passed)
    total_eval = len(eval_results)
    pass_rate = (pass_count / total_eval * 100.0) if total_eval > 0 else 0.0
    avg_exp = sum(r.expected_cost_pct for _, r in eval_results) / total_eval if total_eval > 0 else 5.2
    avg_stress = sum(r.stress_cost_pct for _, r in eval_results) / total_eval if total_eval > 0 else 7.4

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Double Gate Pass Rate</div>
                <div class="metric-value" style="color: var(--green);">{pass_rate:.1f}%</div>
                <div class="metric-sub">{pass_count} / {total_eval} Candidates Cleared</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi2:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Avg Expected Friction</div>
                <div class="metric-value" style="color: {'var(--green)' if avg_exp <= 7.0 else 'var(--amber)'};">{avg_exp:.2f}%</div>
                <div class="metric-sub">Cap &le; 7.0% (Regime A)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi3:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Avg Stressed Cost</div>
                <div class="metric-value" style="color: {'var(--green)' if avg_stress <= 10.0 else 'var(--red)'};">{avg_stress:.2f}%</div>
                <div class="metric-sub">Hard Ceiling &le; 10.0%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kpi4:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-label">Dynamic Sizing Mode</div>
                <div class="metric-value" style="color: var(--blue);">1.5% Impact</div>
                <div class="metric-sub">Floor: $3.00 | Cap: $25.00</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Candidate Double Gate Table
    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    table_rows = []
    for p, r in eval_results:
        token_short = f"{r.token_mint[:6]}...{r.token_mint[-4:]}"
        mc_val = p.get("market_cap_sol", 400.0)
        res_val = float(p.get("current_sol_reserve") or p.get("sol_reserve") or 0.0)

        regime_badge = (
            '<span class="badge badge-amber">REGIME A (T1)</span>'
            if r.regime.value == "REGIME_A"
            else '<span class="badge badge-blue">REGIME B (T2+)</span>'
        )

        exp_color = "var(--green)" if r.expected_cost_pct <= 7.0 else "var(--red)"
        stress_color = "var(--green)" if r.stress_cost_pct <= 10.0 else "var(--red)"

        if r.is_passed:
            verdict = '<span class="badge badge-green">PASSED</span>'
        else:
            reason = r.rejection_reasons[0] if r.rejection_reasons else "Gate Cap Breached"
            verdict = f'<span class="badge badge-red">BLOCKED</span> <span style="font-size: 0.72rem; color: var(--red);">{reason}</span>'

        size_display = f"${r.dynamic_position_size_usd:.2f}" if r.dynamic_position_size_usd > 0 else f"${trade_size:.2f}"

        row = (
            f"<tr>"
            f"<td class='mono'><strong>{token_short}</strong></td>"
            f"<td class='mono'>{mc_val:.1f} SOL</td>"
            f"<td class='mono'>{res_val:.1f} SOL</td>"
            f"<td class='mono' style='color: var(--blue);'><strong>{size_display}</strong></td>"
            f"<td>{regime_badge}</td>"
            f"<td class='mono'>{r.roundtrip_venue_fee_pct:.3f}%</td>"
            f"<td class='mono' style='color: {exp_color};'><strong>{r.expected_cost_pct:.2f}%</strong></td>"
            f"<td class='mono' style='color: {stress_color};'>{r.stress_cost_pct:.2f}%</td>"
            f"<td class='mono' style='color: var(--blue);'><strong>+{r.tp1_target_gross_pct:.1f}%</strong></td>"
            f"<td>{verdict}</td>"
            f"</tr>"
        )
        table_rows.append(row)

    table_body = "".join(table_rows)
    table_html = (
        f"<div class='metric-card' style='overflow-x: auto;'>"
        f"<div style='font-weight: 700; font-size: 0.95rem; margin-bottom: 0.6rem;'>Pre-Flight Double Gate & Dynamic Sizing (Max Cap ${trade_size:.0f})</div>"
        f"<table class='data-table'>"
        f"<thead>"
        f"<tr>"
        f"<th>Token Mint</th>"
        f"<th>Market Cap</th>"
        f"<th>SOL Reserve</th>"
        f"<th>Position Size</th>"
        f"<th>Fee Regime</th>"
        f"<th>Venue Fee</th>"
        f"<th>Expected Friction</th>"
        f"<th>Stressed Cost</th>"
        f"<th>TP1 Friction Buster</th>"
        f"<th>Status</th>"
        f"</tr>"
        f"</thead>"
        f"<tbody>{table_body}</tbody>"
        f"</table>"
        f"</div>"
    )
    st.html(table_html)
