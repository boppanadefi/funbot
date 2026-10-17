"""
Zinc Design System & CSS Theming for Funbot Telemetry Terminal.
Adheres strictly to resources/shared_design_system.md and resources/streamlit_framework.md.
"""

def get_terminal_css(is_dark: bool = True) -> str:
    """Returns CSS block with theme-aware variables and component styles."""
    bg = "#09090b" if is_dark else "#ffffff"
    bg_subtle = "#0c0c0f" if is_dark else "#f9fafb"
    card = "#0c0c0f" if is_dark else "#ffffff"
    card_hover = "#131316" if is_dark else "#f4f4f5"
    border = "#1e1e24" if is_dark else "#e4e4e7"
    border_subtle = "#16161a" if is_dark else "#f0f0f2"
    text = "#fafafa" if is_dark else "#09090b"
    text_muted = "#71717a"
    text_dim = "#52525b" if is_dark else "#a1a1aa"
    accent = "#2563eb"
    green = "#22c55e" if is_dark else "#16a34a"
    green_muted = "rgba(34,197,94,0.12)" if is_dark else "rgba(22,163,74,0.08)"
    red = "#ef4444" if is_dark else "#dc2626"
    red_muted = "rgba(239,68,68,0.12)" if is_dark else "rgba(220,38,38,0.08)"
    amber = "#f59e0b" if is_dark else "#d97706"
    amber_muted = "rgba(245,158,11,0.12)" if is_dark else "rgba(217,119,6,0.08)"
    shadow = "none" if is_dark else "0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.03)"

    return f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,100..1000;1,9..40,100..1000&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap');

    :root {{
        --bg: {bg};
        --bg-subtle: {bg_subtle};
        --card: {card};
        --card-hover: {card_hover};
        --border: {border};
        --border-subtle: {border_subtle};
        --text: {text};
        --text-muted: {text_muted};
        --text-dim: {text_dim};
        --accent: {accent};
        --green: {green};
        --green-muted: {green_muted};
        --red: {red};
        --red-muted: {red_muted};
        --amber: {amber};
        --amber-muted: {amber_muted};
        --shadow: {shadow};
        --radius: 10px;
    }}

    /* Minimal Chrome: Hide default Streamlit toolbars & footers */
    header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="stStatusWidget"], .stDeployButton,
    div[data-testid="stSidebarCollapsedControl"] {{
        display: none !important;
    }}

    html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, .block-container, section[data-testid="stMain"] {{
        background-color: var(--bg) !important;
        color: var(--text) !important;
        font-family: 'DM Sans', -apple-system, sans-serif !important;
    }}

    .block-container {{
        padding: 1.5rem 2.0rem 3rem !important;
        max-width: 1440px !important;
    }}

    /* Monospace for Crypto Addresses & Financial Numbers */
    .mono {{
        font-family: 'JetBrains Mono', monospace !important;
    }}

    /* Card Containers */
    .metric-card {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 1.1rem 1.3rem;
        box-shadow: var(--shadow);
    }}
    .metric-label {{
        font-size: 0.78rem;
        color: var(--text-muted);
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }}
    .metric-value {{
        font-size: 1.7rem;
        font-weight: 700;
        color: var(--text);
        letter-spacing: -0.03em;
        font-family: 'JetBrains Mono', monospace;
        margin-top: 0.2rem;
    }}
    .metric-delta {{
        font-size: 0.75rem;
        font-weight: 500;
        margin-top: 0.35rem;
        padding: 2px 8px;
        border-radius: 6px;
        display: inline-flex;
        align-items: center;
        gap: 3px;
    }}
    .delta-up {{ color: var(--green); background: var(--green-muted); }}
    .delta-down {{ color: var(--red); background: var(--red-muted); }}
    .delta-warn {{ color: var(--amber); background: var(--amber-muted); }}

    /* Status Badges */
    .badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.72rem;
        font-weight: 600;
        font-family: 'JetBrains Mono', monospace;
    }}
    .badge-green {{ color: var(--green); background: var(--green-muted); }}
    .badge-red {{ color: var(--red); background: var(--red-muted); }}
    .badge-amber {{ color: var(--amber); background: var(--amber-muted); }}
    .badge-blue {{ color: var(--accent); background: rgba(37,99,235,0.12); }}

    /* Data Table (HTML) */
    .table-container {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        overflow: hidden;
    }}
    .data-table {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 0.82rem;
    }}
    .data-table th {{
        text-align: left;
        padding: 0.65rem 0.9rem;
        color: var(--text-muted);
        font-weight: 600;
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        border-bottom: 1px solid var(--border);
        background: var(--bg-subtle);
    }}
    .data-table td {{
        padding: 0.7rem 0.9rem;
        color: var(--text);
        border-bottom: 1px solid var(--border-subtle);
    }}
    .data-table tr:hover td {{
        background: var(--card-hover);
    }}

    /* Pill-style tabs */
    button[data-baseweb="tab"] {{
        background: transparent !important;
        color: var(--text-muted) !important;
        font-size: 0.835rem !important;
        font-weight: 500 !important;
        padding: 0.55rem 1rem !important;
        border: 1px solid transparent !important;
        border-radius: 7px !important;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: var(--text) !important;
        background: var(--card) !important;
        border-color: var(--border) !important;
    }}
    [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{
        display: none !important;
    }}
    [data-baseweb="tab-list"] {{
        gap: 4px !important;
        background: var(--bg-subtle) !important;
        border: 1px solid var(--border) !important;
        border-radius: 10px !important;
        padding: 4px;
        margin-bottom: 1.25rem !important;
    }}

    /* Chart Wrap */
    .chart-wrap {{
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 1.2rem 1.2rem 0.6rem;
        box-shadow: var(--shadow);
    }}
    .chart-title {{
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text);
    }}
    .chart-subtitle {{
        font-size: 0.72rem;
        color: var(--text-dim);
        margin-bottom: 0.6rem;
    }}

    /* Chat Thought Pill */
    .thought-pill {{
        background: var(--bg-subtle);
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 0.75rem;
        color: var(--text-dim);
        font-family: 'JetBrains Mono', monospace;
        margin-bottom: 8px;
    }}
    </style>
    """
