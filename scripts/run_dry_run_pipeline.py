"""
Operator CLI Utility: Full End-to-End Zero-Capital Dry-Run Pipeline.
Executes Phase 10 verification:
1. Sub-Wallet Status & Section 7.1 Bankroll Isolation Check (Simulated Mode).
2. Multi-Region Network Latency Baselining (Helius + 5 Jito endpoints).
3. Live Blockhash Fetching from Helius Mainnet RPC.
4. Pure-Python Wire Transaction Construction & Ed25519 Signing.
5. Dual-Path Simulation:
   - Path 1: Helius RPC simulateTransaction
   - Path 2: Jito Bundle Payload & Invariant 6 Validation
6. Formatted ASCII Execution Receipt (Zero Capital at Risk).
"""

import logging
from pathlib import Path
import sys
import time

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.live.dry_run_simulator import DryRunSimulator, SimulationOutcome
from src.live.network_latency_profiler import NetworkLatencyProfiler
from src.live.wallet_manager import WalletManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DryRunCLI")

# Canonical test pool: "8wXQ...pump" or default demo pool
DEMO_POOL_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
DEMO_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # Bonk mint for layout test


def run_dry_run_pipeline():
    print("\n" + "=" * 70)
    print("  FUNBOT SYSTEMATIC SOLANA MOMENTUM ENGINE — PHASE 10 DRY-RUN PIPELINE  ")
    print("=" * 70)

    # 1. Initialize Simulator
    wallet_mgr = WalletManager()
    simulator = DryRunSimulator(wallet_mgr=wallet_mgr)

    print(simulator.sim_context.banner())
    print("\n[STEP 1/4] Checking Sub-Wallet & Bankroll Isolation Status...")
    print(f"  Wallet Mode: {'LIVE (REAL CAPITAL)' if wallet_mgr.active_wallet.is_live else 'SIMULATED (NO PRIVATE KEY / ZERO RISK)'}")
    print(f"  Pubkey:      {simulator.sim_context.pubkey}")
    print(f"  Balance:     {simulator.sim_context.balance_sol:.5f} SOL (${simulator.sim_context.balance_usd:.2f})")
    print(f"  Cold Locked: ${simulator.sim_context.cold_reserve_usd:.2f} (Programmatic Access: REVOKED)")
    print(f"  Exit Floor:  {simulator.sim_context.emergency_reserve_sol:.4f} SOL (Dedicated Exit Reserve)")

    # 2. Network Latency Baselining
    print("\n[STEP 2/4] Profiling Network Latency Across Helius RPC & 5 Jito Regions...")
    profiler = NetworkLatencyProfiler()
    baseline = profiler.run_full_baseline()

    print(f"  Helius RPC Latency:    {baseline.helius_rpc_latency_ms:.2f} ms ({'HEALTHY' if baseline.helius_healthy else 'DEGRADED'})")
    print(f"  Fastest Jito Region:   {baseline.fastest_jito_region.upper()} ({baseline.fastest_jito_latency_ms:.2f} ms)")
    print("  Ranked Jito Endpoints:")
    for metric in baseline.jito_regions_ranked:
        status_str = "ONLINE" if metric.is_healthy else "OFFLINE"
        print(f"    - {metric.region.ljust(10)}: {metric.latency_ms:6.2f} ms [{status_str}]")

    # 3. Blockhash Fetching
    print("\n[STEP 3/4] Querying Fresh Mainnet Blockhash from Helius RPC...")
    recent_bh = simulator.blockhash_mgr.get_recent_blockhash()
    print(f"  Recent Blockhash:      {recent_bh}")

    # 4. Dual-Path Simulation Execution
    print("\n[STEP 4/4] Assembling Wire Transaction & Executing Dual-Path Simulation...")
    print(f"  Target Pool ID:        {DEMO_POOL_ID}")
    print(f"  Target Mint:           {DEMO_MINT}")
    print("  Trade Notional:        $3.00 (Test Micro-Swap)")
    print("  Jito Tip:              100,000 lamports (0.0001 SOL)")

    receipt = simulator.execute_dry_run_swap(
        pool_id=DEMO_POOL_ID,
        mint=DEMO_MINT,
        notional_usd=3.00,
        side="BUY",
        tip_lamports=100_000,
    )

    # Print Receipt
    print("\n" + "-" * 70)
    print("                    DRY-RUN SIMULATION RECEIPT                        ")
    print("-" * 70)
    print(f"  Simulation ID:         {receipt.simulation_id}")
    print(f"  Outcome:               {receipt.outcome.value}")
    print(f"  Helius Sim Latency:    {receipt.helius_sim_latency_ms:.2f} ms")
    print(f"  Compute Units (CU):    {receipt.units_consumed}")
    print(f"  Jito Validation:       {receipt.jito_validation_status}")
    print(f"  Jito Tip Recipient:    {receipt.tip_account}")
    print(f"  Tip % of Notional:     {receipt.tip_cap_pct:.3f}% (Cap: <= 1.50%)")

    if receipt.error_message:
        print(f"  Simulation Details:    {receipt.error_message}")

    print("-" * 70)
    if receipt.jito_validation_status == "PASSED_INVARIANT_6":
        print("  VERDICT: INVARIANT 6 PASSED & READY FOR ZERO-CAPITAL DEPLOYMENT")
    else:
        print("  VERDICT: VERIFICATION FLAGGED ISSUES")
    print("=" * 70 + "\n")

    return receipt


if __name__ == "__main__":
    run_dry_run_pipeline()
