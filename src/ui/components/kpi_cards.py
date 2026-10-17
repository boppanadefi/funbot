"""
KPI Metric Cards Component for Telemetry Terminal.
"""

import streamlit as st


def render_metric_card(label: str, value: str, delta: str = None, delta_type: str = "up"):
    delta_html = ""
    if delta:
        arrow = "↑" if delta_type == "up" else ("↓" if delta_type == "down" else "→")
        delta_html = f'<div class="metric-delta delta-{delta_type}">{arrow} {delta}</div>'

    html = f"""
    <div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {delta_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_falsification_kpis(
    candidates_per_day: float,
    depth_pass_rate_pct: float,
    avg_friction_pct: float,
    active_window_count: int,
):
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        delta_type = "up" if candidates_per_day >= 3.0 else ("warn" if candidates_per_day >= 1.0 else "down")
        status = "PASS (≥3.0)" if candidates_per_day >= 3.0 else "KILL (<1.0)"
        render_metric_card("Candidate Supply", f"{candidates_per_day:.1f}/day", delta=status, delta_type=delta_type)

    with c2:
        delta_type = "up" if depth_pass_rate_pct >= 20.0 else "down"
        status = "PASS (≥20%)" if depth_pass_rate_pct >= 20.0 else "KILL (<20%)"
        render_metric_card("Depth Pass Rate ($20)", f"{depth_pass_rate_pct:.1f}%", delta=status, delta_type=delta_type)

    with c3:
        delta_type = "up" if avg_friction_pct <= 7.0 else "down"
        status = "PASS (≤7.0%)" if avg_friction_pct <= 7.0 else "EXCESSIVE"
        render_metric_card("Avg Round-Trip Friction", f"{avg_friction_pct:.2f}%", delta=status, delta_type=delta_type)

    with c4:
        render_metric_card("Active In 8–25m Window", f"{active_window_count}", delta="Tracking", delta_type="up")
