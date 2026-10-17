"""
Candidate Table Component — Displays active candidates with JetBrains Mono numbers and status badges.
Renders raw HTML using st.html to prevent Markdown indentation parser bugs.
"""

from typing import Dict, List
import streamlit as st


def render_candidate_table(pools: List[Dict]):
    if not pools:
        empty_html = """
        <div class="metric-card" style="text-align: center; color: var(--text-muted); padding: 2rem;">
            No pools currently recorded in the telemetry database.
        </div>
        """
        if hasattr(st, "html"):
            st.html(empty_html)
        else:
            st.markdown(empty_html, unsafe_allow_html=True)
        return

    # Filter Controls
    filter_col1, filter_col2 = st.columns([8, 2])
    with filter_col1:
        view_filter = st.radio(
            "Filter candidates by status:",
            ["Active Window (8–25m)", "All Monitored Pools", "Cooling (<8m)"],
            horizontal=True,
            label_visibility="collapsed",
        )

    filtered_pools = pools
    if view_filter == "Active Window (8–25m)":
        filtered_pools = [p for p in pools if p.get("state") == "ACTIVE_WINDOW" or (8.0 <= p.get("age_minutes", 0) <= 25.0)]
    elif view_filter == "Cooling (<8m)":
        filtered_pools = [p for p in pools if p.get("state") == "STABILIZING" or (p.get("age_minutes", 0) < 8.0)]

    if not filtered_pools:
        empty_filtered = f"""
        <div class="metric-card" style="text-align: center; color: var(--text-muted); padding: 1.5rem; margin-top: 0.5rem;">
            No candidates matching filter: <strong>{view_filter}</strong>. Switch to 'All Monitored Pools' to view incoming graduations.
        </div>
        """
        if hasattr(st, "html"):
            st.html(empty_filtered)
        else:
            st.markdown(empty_filtered, unsafe_allow_html=True)
        return

    rows = []
    for p in filtered_pools:
        mint = p.get("token_mint", "")
        short_mint = f"{mint[:6]}...{mint[-4:]}" if len(mint) > 10 else mint
        state = p.get("state", "STABILIZING")
        age = float(p.get("age_minutes", 0.0))

        # Re-verify state from age if needed
        if 8.0 <= age <= 25.0:
            badge = '<span class="badge badge-green">ACTIVE (8-25m)</span>'
        elif age < 8.0:
            badge = '<span class="badge badge-amber">COOLING (<8m)</span>'
        else:
            badge = '<span class="badge badge-blue">EXPIRED (>25m)</span>'

        sol_res = float(p.get("current_sol_reserve", 0.0))
        vol_60s = float(p.get("vol_60s_sol", 0.0))
        impact = float(p.get("price_impact_20usd_pct", 0.0))
        friction = float(p.get("modeled_friction_pct", 0.0))

        impact_color = "var(--green)" if impact < 2.0 else "var(--red)"
        friction_color = "var(--green)" if friction <= 7.0 else "var(--amber)"
        mint_link = f"<a href='https://dexscreener.com/solana/{mint}' target='_blank' style='color: var(--blue); text-decoration: none; font-weight: 700;'>{short_mint} ↗</a>"

        row_html = (
            f"<tr>"
            f"<td class='mono'>{mint_link}</td>"
            f"<td>{badge}</td>"
            f"<td class='mono'>{age:.1f}m</td>"
            f"<td class='mono'>{sol_res:.1f} SOL</td>"
            f"<td class='mono'>{vol_60s:.2f} SOL</td>"
            f"<td class='mono' style='color: {impact_color};'>{impact:.2f}%</td>"
            f"<td class='mono' style='color: {friction_color};'>{friction:.2f}%</td>"
            f"</tr>"
        )
        rows.append(row_html)

    table_html = (
        f"<div class='table-container' style='margin-top: 0.5rem;'>"
        f"<table class='data-table'>"
        f"<thead>"
        f"<tr>"
        f"<th>Token Mint</th>"
        f"<th>State</th>"
        f"<th>Age</th>"
        f"<th>SOL Reserve</th>"
        f"<th>60s Volume</th>"
        f"<th>Depth Impact ($20)</th>"
        f"<th>Modeled Friction</th>"
        f"</tr>"
        f"</thead>"
        f"<tbody>"
        f"{''.join(rows)}"
        f"</tbody>"
        f"</table>"
        f"</div>"
    )

    if hasattr(st, "html"):
        st.html(table_html)
    else:
        st.markdown(table_html, unsafe_allow_html=True)
