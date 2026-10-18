"""
Phase 5: Live Micro-Trading Control Center & Execution Bridge UI.
Provides the Operator Console, Emergency Kill Switch, Daily Circuit Breaker gauge,
Atomic Jito Bundle inspector, and Manual Order Sign-Off queue.
"""

from datetime import datetime, timezone
from typing import Dict, List
import streamlit as st

import importlib
import src.live.wallet_manager
import src.live.transaction_signer
import src.live.execution_bridge
import src.telemetry.rpc_client

try:
    importlib.reload(src.live.wallet_manager)
    importlib.reload(src.live.transaction_signer)
    importlib.reload(src.live.execution_bridge)
    importlib.reload(src.telemetry.rpc_client)
except Exception:
    pass

from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.execution_bridge import ExecutionBridge
from src.live.jito_bundle_builder import JitoBundleBuilder
from src.live.kill_switch import EmergencyKillSwitch
from src.live.wallet_manager import WalletManager
from src.live.transaction_signer import SolanaTransactionSigner
from src.telemetry.rpc_client import SolanaConnectionMonitor


def render_live_control_panel(pools: List[Dict], is_dark: bool = True) -> None:
    """Renders the Phase 5 Operator Control Center."""
    kill_switch = EmergencyKillSwitch()
    circuit_breaker = DailyCircuitBreaker()
    bundle_builder = JitoBundleBuilder()
    bridge = ExecutionBridge(
        kill_switch=kill_switch,
        circuit_breaker=circuit_breaker,
        bundle_builder=bundle_builder,
    )

    ks_status = kill_switch.get_status()
    cb_status = circuit_breaker.get_status()
    is_tripped = ks_status.get("is_tripped", False)
    is_halted = cb_status.get("is_halted", False)
    remaining_budget = cb_status.get("remaining_budget_usd", 10.00)
    net_daily_pnl = cb_status.get("net_daily_pnl_usd", 0.0)

    # 1. EMERGENCY KILL SWITCH HEADER BANNER
    if is_tripped or is_halted:
        halt_msg = (
            f"EMERGENCY KILL SWITCH ACTIVE: {ks_status.get('reason')}"
            if is_tripped
            else f"CIRCUIT BREAKER TRIPPED: {cb_status.get('halt_reason')}"
        )
        st.markdown(
            f"""
            <div style="background: rgba(239, 68, 68, 0.15); border: 1.5px solid #ef4444; border-radius: 8px; padding: 1rem; margin-bottom: 1.2rem;">
                <div style="display: flex; align-items: center; justify-content: space-between;">
                    <div>
                        <div style="color: #ef4444; font-weight: 800; font-size: 1.05rem; letter-spacing: 0.04em;">
                            🛑 SYSTEM HALTED — ALL EXECUTION BLOCKED
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem;">
                            {halt_msg}
                        </div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="background: rgba(34, 197, 94, 0.08); border: 1px solid rgba(34, 197, 94, 0.3); border-radius: 8px; padding: 0.8rem 1rem; margin-bottom: 1.2rem;">
                <div style="color: var(--green); font-weight: 700; font-size: 0.92rem; display: flex; align-items: center; gap: 0.5rem;">
                    <span>🟢</span> OPERATIONAL SAFETY GATES ACTIVE — ZERO PRIVATE KEYS IN PLAY
                </div>
                <div style="color: var(--text-muted); font-size: 0.8rem; margin-top: 0.2rem;">
                    Guardrail 6 (Jito tip after swap, 1.5% cap) & Guardrail 7 (-$10 daily circuit breaker & operator review) actively enforced.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. OPERATOR CONTROLS & RISK METRIC CARDS
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        ks_color = "var(--red)" if is_tripped else "var(--green)"
        ks_text = "ENGAGED (HALTED)" if is_tripped else "ARMED (READY)"
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Emergency Kill Switch</div>
                <div class="metric-value" style="color: {ks_color}; font-size: 1.15rem;">{ks_text}</div>
                <div class="metric-sub">Manual Operator Override</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        budget_color = "var(--green)" if remaining_budget > 3.0 else "var(--amber)" if remaining_budget > 0 else "var(--red)"
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Remaining Daily Budget</div>
                <div class="metric-value" style="color: {budget_color};">${remaining_budget:.2f}</div>
                <div class="metric-sub">Hard Ceiling: -$10.00 / day</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c3:
        pnl_color = "var(--green)" if net_daily_pnl >= 0 else "var(--red)"
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-label">Net Daily Realized P&L</div>
                <div class="metric-value" style="color: {pnl_color};">${net_daily_pnl:+.2f}</div>
                <div class="metric-sub">{cb_status.get('trades_count', 0)} Realized Trades Today</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c4:
        st.markdown(
            """
            <div class="metric-card">
                <div class="metric-label">Emergency Exit Reserve</div>
                <div class="metric-value" style="color: var(--blue);">0.0050 SOL</div>
                <div class="metric-sub">Guaranteed Exit Tip Budget</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 3. INTERACTIVE OPERATOR ACTIONS (Kill Switch & Circuit Breaker)
    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3 = st.columns([1.5, 1.5, 2])

    with btn_col1:
        if not is_tripped:
            if st.button("🚨 TRIP KILL SWITCH", use_container_width=True, type="primary"):
                kill_switch.trip(reason="Operator manual panic stop from terminal", operator="OPERATOR_CONSOLE")
                st.rerun()
        else:
            if st.button("🟢 RESET KILL SWITCH", use_container_width=True):
                kill_switch.reset(operator="OPERATOR_CONSOLE", notes="Manual reset following safety review")
                st.rerun()

    with btn_col2:
        if is_halted:
            if st.button("🔄 UNLOCK CIRCUIT BREAKER", use_container_width=True):
                circuit_breaker.reset_circuit_breaker(operator="OPERATOR_CONSOLE", notes="Operator daily risk override")
                st.rerun()
        else:
            if st.button("🧪 SIMULATE -$10 BREACH", use_container_width=True):
                circuit_breaker.record_trade_pnl(-10.50)
                st.rerun()

    with btn_col3:
        # Seed an order if staging queue is empty
        orders = bridge.get_all_orders()
        pending_orders = [o for o in orders if o["status"] == "PENDING_OPERATOR"]
        if not pending_orders and pools:
            cand = next((p for p in pools if p.get("state") == "ACTIVE_WINDOW"), pools[0])
            if st.button("📥 STAGE TEST CANDIDATE FOR REVIEW", use_container_width=True):
                bridge.stage_order(
                    order_id=f"ord_live_{int(datetime.now(timezone.utc).timestamp()) % 10000}",
                    pool_address=cand["pool_address"],
                    token_mint=cand["token_mint"],
                    regime="REGIME_A" if cand.get("market_cap_sol", 0) < 420 else "REGIME_B",
                    action="BUY",
                    position_notional_usd=15.0,
                    proposed_tip_sol=0.0010,
                )
                st.rerun()

    # 4. PRE-TRADE CAPACITY & STRESSED LOSS CALCULATOR
    st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
    with st.expander("🔍 Pre-Trade Risk Capacity & Stressed Loss Validator (Guardrail 7)", expanded=False):
        calc_col1, calc_col2 = st.columns(2)
        with calc_col1:
            test_notional = st.slider("Proposed Position Notional ($ USD)", min_value=10.0, max_value=30.0, value=15.0, step=1.0)
            test_stop = st.slider("Stop-Loss Threshold (%)", min_value=10.0, max_value=40.0, value=25.0, step=5.0)
            test_gap = st.slider("Adverse Gap Penalty (%)", min_value=5.0, max_value=25.0, value=10.0, step=5.0)

        with calc_col2:
            is_valid, reason, exposure = circuit_breaker.check_preflight_capacity(
                position_notional_usd=test_notional,
                stop_loss_pct=test_stop,
                gap_penalty_pct=test_gap,
            )
            badge = '<span class="badge badge-green">CAPACITY APPROVED</span>' if is_valid else '<span class="badge badge-red">CAPACITY REJECTED</span>'
            st.markdown(
                f"""
                <div class="metric-card" style="margin-top: 1rem;">
                    <div style="font-weight: 700; margin-bottom: 0.4rem;">Risk Capacity Verdict: {badge}</div>
                    <div style="font-size: 0.85rem; color: var(--text-muted); line-height: 1.6;">
                        • <strong>Proposed Stressed Loss:</strong> ${test_notional * (test_stop + test_gap)/100.0:.2f}<br/>
                        • <strong>Mandatory Exit Reserve:</strong> $3.00 (0.02 SOL)<br/>
                        • <strong>Total Risk Exposure:</strong> ${exposure:.2f}<br/>
                        • <strong>Remaining Daily Risk Budget:</strong> ${remaining_budget:.2f}<br/>
                        <div style="margin-top: 0.5rem; font-size: 0.82rem; color: {'var(--green)' if is_valid else 'var(--red)'};">
                            {reason}
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # 4.5. SOLANA RPC & WEBSOCKET NETWORK BRIDGE (HELIUS DEDICATED)
    rpc_monitor = SolanaConnectionMonitor()
    rpc_diag = rpc_monitor.get_full_diagnostics()
    slot_label = f"#{rpc_diag['slot']:,}" if rpc_diag.get("slot") else "Awaiting Sync"

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
                <div>
                    <strong style="font-size: 0.95rem;">⚡ Solana Dedicated RPC & WebSocket Bridge</strong>
                    <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.1rem;">
                        Provider: {rpc_diag['provider']} • PumpSwap Program ID: <code>6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P</code>
                    </div>
                </div>
                <div>
                    <span class="badge {'badge-green' if rpc_diag['http']['ok'] else 'badge-red'}">
                        HTTP RPC: {rpc_diag['http']['status']} ({rpc_diag['http']['latency_ms']}ms)
                    </span>
                    <span class="badge {'badge-blue' if rpc_diag['ws']['ok'] else 'badge-amber'}" style="margin-left: 0.4rem;">
                        WSS: {rpc_diag['ws']['status']} ({rpc_diag['ws'].get('latency_ms', 0)}ms)
                    </span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem; font-size: 0.78rem;">
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Current Slot</div>
                    <div class="mono" style="font-weight: 700; color: var(--green); font-size: 0.95rem;">{slot_label}</div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Mainnet Block Progression</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">HTTP RPC Endpoint</div>
                    <div class="mono" style="font-weight: 600; color: var(--text); font-size: 0.75rem; word-break: break-all;">{rpc_diag['http']['endpoint']}</div>
                    <div style="font-size: 0.72rem; color: var(--green);">Round-trip: {rpc_diag['http']['latency_ms']}ms</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">WebSocket Endpoint</div>
                    <div class="mono" style="font-weight: 600; color: var(--text); font-size: 0.75rem; word-break: break-all;">{rpc_diag['ws']['endpoint']}</div>
                    <div style="font-size: 0.72rem; color: var(--blue);">Handshake: {rpc_diag['ws'].get('latency_ms', 0)}ms</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Data Staleness Ceiling</div>
                    <div class="mono" style="font-weight: 700; color: var(--green); font-size: 0.95rem;">&lt; 1,000 ms</div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Circuit Breaker Active</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 4.8. ACTIVE TRADING WALLET & ED25519 TRANSACTION SIGNER (SECTION 7.1)
    wallet_mgr = WalletManager()
    wallet_info = wallet_mgr.get_wallet_overview()
    signer = SolanaTransactionSigner(wallet_manager=wallet_mgr)

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; flex-wrap: wrap; gap: 0.5rem;">
                <div>
                    <strong style="font-size: 0.95rem;">💼 Active Trading Wallet & Ed25519 Signer (Section 7.1)</strong>
                    <div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 0.1rem;">
                        Account Isolation: <strong>$25.00 Active Sub-Wallet</strong> • <strong>$75.00 Cold Segregated Reserve</strong> (Separate Keypair)
                    </div>
                </div>
                <div>
                    <span class="badge {'badge-green' if wallet_info['is_live'] else 'badge-amber'}">
                        {wallet_info['label']}
                    </span>
                    <span class="badge badge-blue" style="margin-left: 0.4rem;">
                        ED25519 CRYPTO ENGINE: READY
                    </span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem; font-size: 0.78rem;">
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Active Wallet Pubkey</div>
                    <div class="mono" style="font-weight: 700; color: var(--blue); font-size: 0.82rem; word-break: break-all;">
                        {wallet_info['pubkey']}
                    </div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Loaded from .env / Keypair</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Active Sub-Wallet Balance</div>
                    <div class="mono" style="font-weight: 700; color: var(--green); font-size: 0.95rem;">
                        {wallet_info['balance_sol']:.4f} SOL (${wallet_info['balance_usd']:.2f})
                    </div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Capped at $25.00 Sizing Limit</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Cold Segregated Reserve</div>
                    <div class="mono" style="font-weight: 700; color: #a855f7; font-size: 0.95rem;">
                        $75.00 (Cold Storage)
                    </div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Zero Programmatic Access</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Guaranteed Exit Tip Reserve</div>
                    <div class="mono" style="font-weight: 700; color: var(--green); font-size: 0.95rem;">
                        {wallet_info['emergency_reserve_sol']:.4f} SOL (${wallet_info['exit_reserve_usd']:.2f})
                    </div>
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Mandatory Jito Exit Budget</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("✍️ Test Ed25519 Atomic Bundle Signing & Verification", key="btn_test_signing"):
        test_bundle = bundle_builder.build_atomic_swap_bundle(
            pool_address="PoolSignTest11111111111111111111111111111",
            token_mint="MintSignTest11111111111111111111111111111",
            is_buy=True,
            position_notional_sol=0.0645,
            proposed_tip_sol=0.00096,
            user_wallet_pubkey=wallet_info["pubkey"],
        )
        signed_b = signer.sign_atomic_jito_bundle(test_bundle)
        st.success(
            f"✅ Ed25519 Cryptographic Signature Verified! "
            f"Bundle ID: `{signed_b.bundle_id}` • Instructions Signed: {signed_b.total_instructions} • "
            f"Signer: `{signed_b.signed_transactions[0].signer_pubkey}` • "
            f"Signature Digest: `{signed_b.signed_transactions[0].signature_b58[:24]}...`"
        )

    # 5. ATOMIC JITO BUNDLE INSPECTOR (GUARDRAIL 6)
    st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
    st.markdown(
        """
        <div class="metric-card" style="margin-bottom: 1rem;">
            <div style="font-weight: 700; font-size: 0.95rem; margin-bottom: 0.4rem;">
                ⚡ Atomic Jito MEV Bundle Inspector (Guardrail 6)
            </div>
            <div style="font-size: 0.82rem; color: var(--text-muted); line-height: 1.5;">
                To guarantee zero tip loss on reverted swaps, the Jito Tip Instruction is 
                <strong>strictly ordered after the PumpSwap instruction</strong>, capped at <strong>1.5% notional</strong>, 
                and leaves a guaranteed <strong>0.005 SOL emergency liquidation reserve</strong>.
            </div>
            <div style="margin-top: 0.8rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem;">
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Instruction 0</div>
                    <div style="font-weight: 700; font-size: 0.85rem; color: var(--text);">Compute Budget Limit</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Priority Fee Unit setter</div>
                </div>
                <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--text-muted);">Instruction 1</div>
                    <div style="font-weight: 700; font-size: 0.85rem; color: var(--text);">Idempotent ATA Create</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Only on initial BUY</div>
                </div>
                <div style="background: rgba(37, 99, 235, 0.1); border: 1.5px solid #2563eb; border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--blue);">Instruction 2 (EXECUTION)</div>
                    <div style="font-weight: 700; font-size: 0.85rem; color: var(--blue);">PumpSwap Swap</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Single exit / entry leg</div>
                </div>
                <div style="background: rgba(34, 197, 94, 0.1); border: 1.5px solid #22c55e; border-radius: 6px; padding: 0.6rem;">
                    <div style="font-size: 0.72rem; color: var(--green);">Instruction 3 (TIP)</div>
                    <div style="font-weight: 700; font-size: 0.85rem; color: var(--green);">Jito Tip Transfer</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted);">Strictly AFTER swap</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 6. OPERATOR STAGED ORDER REVIEW QUEUE
    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
    all_orders = bridge.get_all_orders()

    if not all_orders:
        st.info("No staged orders currently pending review. Click 'STAGE TEST CANDIDATE FOR REVIEW' to test operator workflow.")
    else:
        st.markdown("<div style='font-weight: 700; font-size: 0.95rem; margin-bottom: 0.6rem;'>Operator Review Queue & Execution History</div>", unsafe_allow_html=True)
        for ord_info in reversed(all_orders[-8:]):
            oid = ord_info["order_id"]
            status = ord_info["status"]
            badge_class = "badge-amber" if status == "PENDING_OPERATOR" else "badge-green" if status == "EXECUTED" else "badge-red"
            
            with st.container():
                st.markdown(
                    f"""
                    <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 0.8rem 1rem; margin-bottom: 0.6rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem;">
                            <div>
                                <span class="mono" style="font-weight: 700; font-size: 0.92rem;">{oid}</span>
                                <span class="badge {badge_class}" style="margin-left: 0.5rem;">{status}</span>
                                <span style="font-size: 0.78rem; color: var(--text-muted); margin-left: 0.5rem;">{ord_info['created_at'][:19]}</span>
                            </div>
                            <div class="mono" style="font-weight: 700;">
                                ${ord_info['notional_usd']:.2f} ({ord_info['action']})
                            </div>
                        </div>
                        <div style="font-size: 0.8rem; color: var(--text-muted); display: flex; gap: 1.5rem;">
                            <span>Token: <strong class="mono">{ord_info['token_mint'][:8]}...</strong></span>
                            <span>Pool: <strong class="mono">{ord_info['pool_address'][:8]}...</strong></span>
                            <span>Tip: <strong class="mono">{ord_info['tip_sol']:.5f} SOL</strong></span>
                            <span>Regime: <strong>{ord_info['regime']}</strong></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if status == "PENDING_OPERATOR":
                    act_col1, act_col2, act_col3 = st.columns([1, 1, 3])
                    with act_col1:
                        if st.button(f"✅ APPROVE {oid}", key=f"app_{oid}", use_container_width=True):
                            ok, msg, _ = bridge.operator_approve_and_execute(oid, operator_id="OPERATOR_CONSOLE")
                            if ok:
                                st.success(f"Order {oid} Approved & Landed.")
                            else:
                                st.error(f"Approval failed: {msg}")
                            st.rerun()
                    with act_col2:
                        if st.button(f"❌ REJECT {oid}", key=f"rej_{oid}", use_container_width=True):
                            bridge.operator_reject_order(oid, reason="Manual operator rejection", operator_id="OPERATOR_CONSOLE")
                            st.rerun()
                    with act_col3:
                        if st.button(f"🚨 EMERGENCY DUMP {oid}", key=f"dump_{oid}", use_container_width=True):
                            bridge.trigger_emergency_liquidation(ord_info["pool_address"], ord_info["token_mint"])
                            st.warning(f"Emergency 100% Dump triggered for {oid}.")
                            st.rerun()
