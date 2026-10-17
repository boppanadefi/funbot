# Pump.fun Post-Graduation Momentum System — Strategy & Design Specification (v2.5)

**Status:** Revised Viability Baseline — Path A+ Dynamic Trailing Architecture  
**Supersedes:** v2.4, v2.3, v2.2, v2.1, v2.0, and v1.0  
**Scope:** Canonical SOL-quoted PumpSwap pools only (non-canonical and USDC pools excluded).  
**Objective:** Research and develop an automated post-graduation momentum system on PumpSwap with mathematically exact cost accounting, single-exit execution modeling, dynamic trailing stops for 100% notional moonshot capture, dynamic risk budgets, and uncompromised paper simulation fidelity.  
**Epistemic Posture:** All profitability and edge hypotheses are unproven until demonstrated across out-of-sample paper testing. Targets and thresholds represent **conditional viability hurdles**, not performance guarantees.  

---

## 1. Executive Summary & Scope Delimitation

### 1.1 Strategic Pivot Summary
* **Rejection of Sub-Minute Sniping:** Micro-trades ($5–$10) on raw bonding curves fail due to fixed-cost drag (priority fees, Jito tips, ATA rent lockup), sub-second adversarial competition from low-latency searchers/bundlers, and discontinuous gap risk on developer dumps.
* **Rejection of Tranche Scaling (Path B):** Tranche exits impose a ~99.6% win-rate requirement on micro-sizes due to 50% tranche dilution and 3rd-leg transaction friction.
* **Rejection of Hard-Capped Exits:** Prematurely dumping 100% at a static +20% target forfeits fat-tail memecoin moonshots (+100% to +500%).
* **Adoption of Path A+ (100% Dynamic Trailing Single Exit):** Retains 100% of tokens to capture uncapped tail upside. Once the initial profit hurdle is crossed, the stop on 100% of the position is ratcheted into guaranteed net profit and trails the peak price. The trade executes in **exactly 2 swaps (Entry + 1 Exit)**, completely eliminating tranche dilution and 3rd-leg fees.
* **Target Universe:** Post-migration tokens on **canonical SOL-quoted PumpSwap pools** observed **8 to 25 minutes** post-graduation. This window avoids the protocol-sponsored BOOST buyback cliff (0–5 minutes) and targets secondary organic momentum.

### 1.2 Core Scope Boundaries (v2.5)
1. **Quote Asset:** Strictly **SOL-quoted pairs**. USDC-quoted PumpSwap pools operate under different market-cap thresholds and liquidity mechanics; they are excluded from this specification to eliminate cross-currency ambiguity.
2. **Venue:** Strictly **Canonical PumpSwap pools**. Non-canonical pools (0.30% fee) remain **disabled by default** until automated mint/freeze authority checks, LP burn/lock verification, and depth screening are built and validated in future phases.
3. **Capital Limits:** $100 total bankroll ($25 active trading wallet, $75 segregated cold reserve). Exactly **1 active position at a time** ($10–$25 target notional).
4. **Execution Path:** **Path A+ (100% Dynamic Trailing Single Exit) mandatory**. Tranche scaling prohibited for positions <$50.

### 1.3 The 7 Core Architectural Mitigations (Invariant Baseline)

1. **Fix for Sub-Minute Sniping (Strategy Pivot):** Sub-second bonding curve sniping is completely abandoned. The strategy pivots strictly to secondary momentum on canonical PumpSwap pools observed **8 to 25 minutes** post-graduation.
2. **Fix for Tranche Dilution & Capped Upside: Path A+ (100% Dynamic Trailing Single Exit):** Tranche / scaled exits (Path B) are strictly prohibited for positions < $50. Rather than dumping at a fixed cap, **Path A+ holds 100% of tokens and trails the peak price**. When price crosses the initial hurdle (+16% to +22% in Regime A), the stop on 100% of the position is ratcheted to lock in net profit (+8% to +10% net). The stop then trails 10%–15% below peak executable price. This captures massive multi-hundred-percent runs on the **entire position** while strictly incurring only 2 transactions total (1 entry + 1 exit).
3. **Fix for Protocol Buyback Cliff (Cooldown Window):** The entry engine enforces an **8-minute stabilization cooldown** post-graduation. By delaying entries to the 8–25 minute window, the bot entirely bypasses the 0–5 and 5–8 minute BOOST buyback expiration cliff where volume evaporates.
4. **Fix for Excessive Friction (Pre-Flight Double Gate):** Candidate tokens must pass a strict pre-flight **Dual-Hurdle Double Gate** before execution:
   * **Friction Cap:** Expected friction is capped at **7.0%** in Regime A and **6.0%** in Regime B.
   * **Gross Profit Hurdle (Ratchet Trigger):** Required target returns are set higher (**+16% to +22%** in Regime A; **+12% to +16%** in Regime B) to ensure gross proceeds clear all stressed costs and leave a 10%–14% net profit buffer before trailing begins.
5. **Fix for Sybil Syndicates & Fake Volume (Phase 2 Cluster Analyzer):** On-chain data is filtered using a dedicated risk-scoring engine:
   * **Fresh Burner Exclusion:** Buyers with fewer than 3 lifetime pre-trade transactions are flagged and excluded from unique buyer counts.
   * **Shared Funding Graph:** Traces wallet funding 2 hops back and blocks candidate tokens if **>30%** of recent buyers originate from a common parent address.
6. **Fix for Congestion & Uncapped Tips (Tip Caps & Structuring):**
   * **Entry Tip Cap:** Enforces a maximum entry tip cap of **1.5% of position notional** ($0.15 on $10; $0.38 on $25); entries requiring higher tips are skipped.
   * **Instruction Order:** Places the Jito tip instruction *after* the swap instruction in the transaction so that if the swap fails, the tip is never charged.
   * **Emergency Exit Budget:** Uncaps exit tips with a dedicated emergency budget of up to **0.005 SOL (~$0.80)** to ensure liquidations succeed during volatility.
7. **Fix for Tail Risk & Slippage Gaps (Risk Sizing & Hard Checks):**
   * **Pre-Trade Stressed Capacity Check:** Before opening a trade, the engine calculates `Stressed Loss + Exit Reserve` and verifies it fits within the remaining **$10 daily risk budget**. If a position risks breaching the ceiling under adverse slippage, it is auto-resized or rejected.
   * **On-Chain Authority Audits:** Automated checks verify mint/freeze authority and LP burn/lock status prior to trading.
   * **Fallback Time Limit:** Positions that do not trigger a stop or target within **20 minutes** are unconditionally closed to eliminate tail exposure.

---

## 2. Official Fee Schedule & Venue Dissection

### 2.1 Canonical SOL-Quoted PumpSwap Tiered Fee Structure
PumpSwap fees for canonical SOL pools are determined dynamically on-chain by the token's market capitalization. 

> [!NOTE]
> **Market Cap Calculation Standard:** On PumpSwap, market capitalization for fee-tier evaluation is strictly defined as $\text{Token Price} \times 10^9$ (total fixed supply of 1 billion tokens). It does not use circulating supply or non-pool holder supply.

| Market Cap Tier (SOL) | Total Swap Fee | Creator Fee | Protocol Fee | LP Fee | Strategy Classification |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1 (0 – 420 SOL)** | **1.250%** | 0.300% | 0.930% | 0.020% | **Regime A: High-Fee Early Momentum** |
| **Tier 2 (420 – 1,470 SOL)** | **1.200%** | 0.950% | 0.050% | 0.200% | **Regime B: Expansion & Continuation** |
| **Tier 3 (1,470 – 2,460 SOL)**| **1.150%** | 0.900% | 0.050% | 0.200% | **Regime B: Established Trend** |
| **Tier 4 (2,460 – 3,440 SOL)**| **1.100%** | 0.850% | 0.050% | 0.200% | **Regime B: Mature Momentum** |
| **Tier 5 (3,440 – 4,420 SOL)**| **1.050%** | 0.800% | 0.050% | 0.200% | **Regime B: Mature Momentum** |
| **Tier 6 (> 4,420 SOL)** | **Steps to 0.300%** | Tiered | Tiered | Tiered | **Regime B: Mature Momentum** |
| **Non-Canonical Pools** | **0.300%** | 0.000% | 0.050% | 0.250% | **Regime C: Disabled by Default** |

*Fees are deducted in the input/output asset on every swap. Because a token's market capitalization changes between entry and exit, **the applicable fee tier must be recalculated dynamically at each execution leg**.*

---

## 3. Monetary Cost Accounting & Multi-Leg Execution Paths

### 3.1 Unit Consistency & Normalization Standard
Percentages and absolute SOL values must never be summed directly. All execution costs are computed in the quote asset (SOL) first, and then normalized against the initial position cash outflow:

$$C_{\%} = 100 \times \frac{\sum \text{Modeled Execution Costs (SOL)}}{\text{Original Position Deployed (SOL)}}$$

### 3.2 Execution Path Taxonomy (v2.5)
A momentum trade is strictly restricted to **two distinct paths**:

| Path Identifier | Scenario Description | Swap Sequence | Legs Modeled |
| :--- | :--- | :--- | :--- |
| **Path A+: Dynamic Trailing Exit** | Momentum trade with 100% trailing stop | Entry $\rightarrow$ 100% Trailing Exit | **2 swaps total** |
| **Path C: Entry Failure** | Transaction reverts or expires | Entry attempt only | 0 swaps (network fees only) |
| ~~**Path B: Scaled Exit**~~ | ~~Normal winning trade~~ | ~~Entry $\rightarrow$ 50% TP1 Exit $\rightarrow$ 50% Runner Exit~~ | ~~3 swaps~~ **(PROHIBITED)** |
| ~~**Path D: Exit Escalation**~~ | ~~Primary exit fails/retries~~ | ~~Entry $\rightarrow$ Failed Exit $\rightarrow$ Retried Exit~~ | ~~2+ swaps~~ **(PROHIBITED)** |

> [!WARNING]
> **Tranche Scaling Prohibition:** For any position size < $50, exit tranche weights $w_i < 1.0$ require TP1 gross target $\ge (C_{\%,\text{stress}} + B)/w_i$. At $w=0.5$, this equals **+38% gross** — outside realistic momentum windows. **Tranche exits are therefore banned for micro-position strategies.**

### 3.3 Transaction Overhead Components
For every transaction submission $i$, total transaction overhead in SOL is:

$$\text{TxOverhead}_i = \text{BaseTxFee} + \text{ComputeBudgetPriorityFee}_i + \text{JitoTip}_i$$

* **Base Solana Tx Fee:** Fixed at `0.000005 SOL`.
* **Compute Budget Priority Fee:** Dynamic microlamports per compute unit queried from recent block performance.
* **Jito Tip:** Dynamic tip based on 50th/75th percentile landing conditions.

### 3.4 Jito Bundle Submission & 5-Outcome Accounting
Receiving a Jito bundle ID is not a guarantee of block inclusion. The simulator and live engine must track five mutually exclusive outcomes:

```text
                        BUNDLE SUBMISSION
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
[NOT LANDED / EXPIRED]  [LANDED SUCCESS]    [LANDED REVERT]
• Tip NOT charged       • Base fee paid     • Base fee paid
• Compute fee NOT paid  • Compute fee paid  • Compute fee paid
• Position: None        • Tip paid          • Tip paid
                        • Swap executed     • Swap failed (loss)
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
[UNKNOWN / TIMEOUT]                     [UNBLED REBROADCAST EXCEPTION]
• Requires block engine reconciliation   • Fork/uncle block edge case
• Halts new entries until resolved      • Logged as exceptional event
```

*Note: To ensure tips are not charged on dropped bundles or failing transactions, **the Jito tip instruction must be included inside the same transaction as the swap instruction**, placed after the swap so execution failure halts the transaction before the tip transfer.*

### 3.5 Jito Tip Caps & Emergency Exit Budgets
* **Entry Tip Cap:** Maximum allowable tip for an entry transaction is **1.5% of position notional** ($0.15 on $10; $0.38 on $25). If market congestion demands higher tips, the entry is skipped.
* **Emergency Exit Budget:** The entry cap must **never** prevent an exit. Exits have a separate, bounded emergency budget of up to **0.005 SOL (~$0.80)** to ensure capital can be liquidated during sudden volatility.

### 3.6 Dual-Hurdle Double Gate (Expected vs. Stress Cost)
Before entry, costs are calculated across the planned **Path A+ (2-leg dynamic trailing exit)**:

$$\text{Expected Cost (SOL)} = \text{Impact}_{\text{roundtrip}} + \text{VenueFees}_{\text{roundtrip}} + \sum_{i=1}^2 \text{TxOverhead}_{i,\text{p50}}$$

$$\text{Stress Cost (SOL)} = \text{Expected Cost} + \text{AdverseSlippageBuffer (1.0\%)} + \text{FailedTxPenalty} + \sum_{i=1}^2 (\text{TxOverhead}_{i,\text{p75}} - \text{TxOverhead}_{i,\text{p50}})$$

#### Pre-Flight Double-Gate Rule (v2.5):
A candidate passes the cost hurdle **only if all three conditions are satisfied**:
1. $C_{\%,\text{expected}} \le \text{GateCap}(T)$, where GateCap varies by regime (Regime A: 7.0%, Regime B: 6.0%)
2. $C_{\%,\text{stress}} \le 10.0\%$
3. The projected Ratchet Trigger satisfies: $\text{RatchetTrigger}_{\text{gross}} \ge C_{\%,\text{stress}} + \text{NetProfitBuffer}$  
   *(where `NetProfitBuffer` is 10%–14% for Regime A and 8%–10% for Regime B)*.

---

## 4. Path A+ Economics: 100% Dynamic Trailing Single Exit

### 4.1 Capturing Moonshots on 100% Notional (No Tranche Dilution)
Under Path A+, the system never caps upside with a hard fixed-profit limit, nor does it dilute winners with partial sales:

1. **Full Position Maintained:** The entire $10–$25 position is retained through the initial move.
2. **Profit Ratchet Activation:** When executable price crosses the **Ratchet Trigger** (+16% to +22% in Regime A; +12% to +16% in Regime B), **zero tokens are sold**. Instead:
   * The protective stop on **100% of the position** is ratcheted up to lock in guaranteed net profit (+8% to +10% net, covering all entry and projected exit friction).
3. **Dynamic Trailing Stop:** Once the ratchet is activated, the stop trails the highest observed executable price ($P_{\text{peak}}$) by **10% to 15%**:
   $$\text{TrailingStopPrice}(t) = \max\left(\text{RatchetFloorPrice}, P_{\text{peak}}(t) \times (1 - \text{TrailPct})\right)$$
4. **Moonshot Capture:** If the token runs to +100%, +200%, or +500%, the **entire 100% notional captures the gain**:
   * *Example on $15 position:* A +200% move yields **+$30.00 gross** on 100% notional (vs. only +$15.00 under a 50% tranche model).
5. **Single Liquidation Leg:** When the trailing stop or momentum decay triggers, **100% of tokens are sold in a single transaction**. The trade incurs strictly **2 swaps total (Entry + Exit)**, completely avoiding the 3rd-leg transaction overhead.

### 4.2 Net Portfolio Cash-Flow Formula (Path A+)
$$R_{\text{net}} = \text{SOL}_{\text{received}} - \text{SOL}_{\text{spent}} - \sum_{i=1}^2 \text{TxOverhead}_i - \text{FailedTxPenalties}$$

Expanding fully:
$$R_{\text{net}} = \left(\text{SOL}_{\text{spent}} \times \frac{P_{\text{exit}}}{P_{\text{entry}}} \times (1 - \text{VenueFee}_{\text{exit}})\right) - \text{SOL}_{\text{spent}}(1 + \text{VenueFee}_{\text{entry}}) - \sum_{i=1}^2 \text{TxOverhead}_i$$

### 4.3 Exit Priority Hierarchy & Fallback Holding Time
When multiple exit signals occur, actions are resolved in strict priority order:

1. **Emergency Risk / Circuit Breaker:** Daily loss ceiling hit or manual operator kill-switch $\rightarrow$ Immediate market dump.
2. **Route Failure / Severe Liquidity Collapse:** Pool reserves drop $>30\%$ or sell quotes fail $\rightarrow$ Immediate exit attempt via emergency tip budget.
3. **Stale Data / Feed Disconnect:** Real-time data feed interrupted $>15\text{ seconds}$ $\rightarrow$ Liquidate open exposure.
4. **Hard Stop / Trailing Stop:** Initial protective threshold or active dynamic trailing stop hit $\rightarrow$ Execute 100% exit.
5. **Momentum Decay:** 1-minute volume drops $<30\%$ of entry volume or unique buyers decline for 2 consecutive windows.
6. **Maximum Holding Time Fallback:** If a position neither triggers a stop nor advances within **20 minutes post-entry**, it is closed unconditionally to eliminate idle exposure.

### 4.4 Operational Cleanup (SPL Token Account Close)
Reclaiming the `~0.00204 SOL` ATA rent is a **best-effort post-trade background task**. It must never block or delay swap execution. A dedicated asynchronous routine sweeps closed token positions and issues `CloseAccount` instructions when network congestion is low.

---

## 5. Sub-Strategy Regimes: Tier 1 vs. Tier 2+

```text
                       CANONICAL POOL DETECTED
                                  │
                 ┌────────────────┴────────────────┐
                 ▼                                 ▼
          Market Cap < 420 SOL              Market Cap >= 420 SOL
          ────────────────────              ─────────────────────
               REGIME A                           REGIME B
          (Tier 1 Early Momentum)           (Tier 2+ Expansion)
          • Venue Fee: 1.250% per swap      • Venue Fee: 1.200% down to 0.300%
          • Window: 8–25 min post-mig       • Window: 10–30+ min post-mig
          • Volume Accel: > 2.0x vs 3m      • Volume Accel: > 1.5x vs 3m
          • Max Price Impact: < 1.5%        • Max Price Impact: < 2.0%
          • Top 10 Concentration: < 25%     • Top 10 Concentration: < 30%
          • Ratchet Trigger: +16% to +22%   • Ratchet Trigger: +12% to +16%
```

### 5.1 Regime A: Tier 1 Early Secondary Momentum (0 – 420 SOL Market Cap)
* **Context:** Operates in the 8–25 minute post-graduation window (after BOOST buyback cliff). Highest volatility, earliest entry, but carries a 1.250% venue fee per swap (2.50% round-trip drag).
* **Entry Requirements:** Strictest hurdles:
  * 1-minute volume acceleration $> 2.0\times$ 3-minute baseline.
  * Price impact $< 1.5\%$ for target trade size ($10–$25).
  * Top 10 non-pool holders hold $< 25\%$ of total supply.
* **Ratchet Trigger (Trailing Activation):** Set to $\text{StressCost} + 10\%\text{ to }14\%$ (typically **+16% to +22% gross**). Crossing this threshold does not sell tokens; it moves the stop to lock in +8% to +10% net profit on 100% of tokens and activates the 10%–15% trailing stop.

### 5.2 Regime B: Tier 2+ Continuation & Expansion ($\ge$ 420 SOL Market Cap)
* **Context:** Tokens breaking above 420 SOL into Tier 2 (1.200%) and Tier 3 (1.150%). Deeper reserves and lower fee drag.
* **Entry Requirements:**
  * 1-minute volume acceleration $> 1.5\times$ 3-minute baseline.
  * Price impact $< 2.0\%$.
  * Top 10 non-pool holders hold $< 30\%$ of total supply.
* **Ratchet Trigger (Trailing Activation):** Set to $\text{StressCost} + 8\%\text{ to }10\%$ (typically **+12% to +16% gross**). Activates net profit lock (+6% to +8% net) and dynamic trailing stop.

---

## 6. Demand-Quality & Wallet-Cluster Risk Estimation

> [!CAUTION]
> On-chain heuristics cannot prove that buyers are independent retail traders. Sophisticated launch syndicates distribute SOL across hundreds of aged, multi-transaction burner wallets. The metrics below are **probabilistic risk filters and testable hypotheses**, not safety guarantees.

### 6.1 Operational Metrics & Definitions
1. **Wallet Age:** Defined as the timestamp of the wallet's **earliest observed transaction on-chain** relative to the trade evaluation block.
2. **Pre-Trade Activity Snapshot:** A buyer is flagged as `FRESH_BURNER` if they have $< 3$ lifetime transactions prior to the evaluation block. Fresh burners are excluded from the `unique_buyers` metric.
3. **Shared Funding Graph Traversal:** 
   * Traces funding sources up to 2 hops back.
   * If $> 30\%$ of recent buying wallets trace back to a common parent address within the last 4 hours (excluding recognized CEX hot wallets and liquidity routers), the candidate is assigned a high `cluster_risk_score` and blocked.
4. **Unknown History Handling:** Wallets with obscured or unresolvable funding history are treated as **correlated risk**, never assumed to be independent.
5. **Holder Concentration Denominator:** Top-holder concentration is strictly calculated against the official total supply of **1,000,000,000 tokens**, with the canonical PumpSwap pool address explicitly excluded from the holder ranking.

---

## 7. Capital Allocation, Daily Risk Budget, and Severe Loss Modeling

### 7.1 Bankroll Structure & Account Isolation
* **Total Dedicated Bankroll:** **$100.00** (denominated in SOL).
* **Active Trading Sub-Wallet:** **$25.00**.
* **Cold Segregated Reserve:** **$75.00** (stored in a separate keypair; zero programmatic withdrawal access; cannot be automatically drawn by the bot).
* **Position Size:** Fixed **$10.00 – $25.00** per trade (strictly **1 active position at a time**).
* **Minimum Exit Reserve:** Active wallet must maintain a mandatory reserve of **0.02 SOL (~$3.00)** reserved exclusively for exit transaction fees and tips. New entries are blocked if free balance drops below `PositionSize + 0.02 SOL`.

### 7.2 The Daily Loss Ceiling as an Entry-Halt Trigger
* **The $10.00 Daily Limit is NOT a Loss Guarantee:** In fast-moving Solana tokens, a position can suffer gap-down execution or total liquidity loss that exceeds the stop threshold.
* **Pre-Trade Risk Capacity Check:** Before opening any position, the engine evaluates:
  $$\text{Stressed Loss on Proposed Trade} + \text{Exit Fee Reserve} \le \text{Remaining Daily Risk Budget}$$
  * *Example:* If realized daily loss is already $\$6.00$, the remaining budget is $\$4.00$. A $\$25.00$ trade with a $-25\%$ stop and $10\%$ gap penalty risks $\$8.75$ ($35\%$). This trade is **rejected** because its stressed loss exceeds $\$4.00$. The bot may only take a resized $\$10.00$ trade or halt for the day.
* **Post-Loss Protocol:** If realized daily loss reaches $\$10.00$, trading is halted until 00:00 UTC. Resuming trading requires manual operator sign-off; the bot does not automatically resume.

### 7.3 Severe Loss Scenario Modeling
The simulation engine must model catastrophic tail events:
* **Liquidity Rug / Total Abandonment:** Pool reserves drained or sell route fails completely $\rightarrow$ 100% loss of position capital plus failed exit transaction fees.
* **Adverse Gap Scenarios:** Evaluates outcomes where hard stops are filled at $-40\%$ to $-70\%$ due to cascading sells.
* **Freeze Authority Exploitation:** If token retains mint/freeze authority, position can be frozen (unsellable) → 100% loss + failed exit fees.
* **Token-2022 Extensions:** Transfer hooks, permanent delegates, or transfer-fee extensions can silently tax or brick every swap.

---

## 8. Simulation Fidelity & Uncertainty Principles

Paper trading must reflect real execution conditions to prevent generating illusory paper alpha:

1. **Empirical Latency Distributions:** Rather than a static delay, execution latency is sampled from real Solana block inclusion distributions:
   * Median condition: 400–800 ms (1–2 slots).
   * Slow-tail condition: 1,600–3,200 ms (4–8 slots) applied to 15% of trades.
2. **Simulated Self-Impact + Exogenous Drift:** When the simulator executes a $20 buy, it must mathematically update the virtual pool reserves ($x \cdot y = k$). **Subsequent simulated sells and price queries must ALSO sample exogenous order flow** (other traders' buys/sells during the 400–3,200 ms latency window) to capture adverse selection.
3. **Strict No-Look-Ahead Timestamping:** Every decision step must log and enforce five distinct timestamps:
   * `signal_ts`: When market data satisfied entry criteria.
   * `decision_ts`: When the engine evaluated quotes and approved entry.
   * `submission_ts`: When the simulated transaction was broadcast.
   * `landing_ts`: Block slot and timestamp where inclusion occurred.
   * `confirm_ts`: State commitment timestamp.
   * *Decisions may only consume data with `event_ts <= decision_ts`.*
4. **Commitment Level Clarity:** Specify `processed` vs. `confirmed` vs. `finalized` for all state reads. Acting on `processed` exposes the bot to pre-finality fork abandonment.

---

## 9. Complete Lifecycle State Machine

```text
                  GRADUATION DETECTED
                           │
                           ▼
                      [DISCOVERED]
                           │
                           ▼
                     [STABILIZING] (8–25 min cooldown)
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
          [SCREEN_REJECT]      [SCREEN_PASS]
          (Depth/Holder fail)        │
                                     ▼
                            [ANALYSIS_PENDING]
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
          [GATE_REJECT]                           [ENTRY_PENDING]
          (Cost/Hurdle fail)                             │
                                        ┌────────────────┼────────────────┐
                                        ▼                ▼                ▼
                                 [ENTRY_FAILED]   [ENTRY_EXPIRED]  [ENTRY_CONFIRMED]
                                 (Tx revert)      (Dropped bundle)        │
                                                                          ▼
                                                                  [POSITION_ACTIVE]
                                                                          │
                                                         ┌────────────────┴────────────────┐
                                                         ▼                                 ▼
                                              [RATCHET_TRIGGERED]                 [INITIAL_STOP_HIT]
                                             (Stop moved to +8% net)                       │
                                                         │                                 ▼
                                                         ▼                         [EXIT_PENDING]
                                                 [TRAILING_ACTIVE]                         │
                                              (Trails peak by 10-15%)                      ▼
                                                         │                         [EXIT_CONFIRMED]
                                                         ▼                                 │
                                                [FULL_EXIT_PENDING]                        ▼
                                                (Trailing stop hit)               [CLOSED_PENDING_CLEANUP]
                                                         │                                 │
                                                         ▼                                 ▼
                                                 [EXIT_CONFIRMED]                 [ARCHIVED_COMPLETE]
                                                         │                      (ATA closed, rent swept)
                                                         ▼
                                                [CLOSED_PENDING_CLEANUP]
                                                         │
                                                         ▼
                                                  [ARCHIVED_COMPLETE]
                                                 (ATA closed, rent swept)
```

---

## 10. Narrowed Phase 1 Implementation Scope

> [!IMPORTANT]
> **Phase 1 Decoupling:** Phase 1 produces **pure observational telemetry only**. It does not execute trades, does not hold private keys, and does **not** depend on the Phase 2 cluster analyzer or Phase 3 cost engine.

### 10.1 Phase 1 Responsibilities
1. **Canonical Pool Discovery:** Monitor the PumpSwap program for pool creation and migration events. Verify canonical status and identify quote asset (SOL vs USDC).
2. **State Tracking:** Track each pool through the 8–25 minute stabilization window. Record token price, reserve levels, and market-cap tier transitions (420 SOL and 1,470 SOL boundaries).
3. **Basic Flow Telemetry:** Ingest raw swap events; compute rolling 10s, 30s, 60s volume, buy/sell transaction counts, and raw unique wallet counts.
4. **Feed Reliability & Telemetry Audit:** Log feed completeness, stream disconnects, reconnection latency, and reserve reconciliation deltas.
5. **Observational Watchlist States:** Maintain pool states: `MIGRATED`, `STABILIZING`, `TRACKING`, `DROPPED`. Fields requiring Phase 2/3 engines (e.g. `cluster_score`, `stress_cost`) are recorded explicitly as `NOT_EVALUATED`.

### 10.2 Phase 1 Falsification Gate (Critical Addition)
Phase 1 must arm itself with explicit kill criteria before any engineering spend on Phase 2/3:

| Criterion | Pass Threshold | Kill Threshold |
| :--- | :--- | :--- |
| Candidate Supply | ≥ 3 candidates/day survive post-buyback cliff | < 1 candidate/day survives |
| Executable Depth | ≥ 20% of candidates have depth for $20 with <2% impact | > 95% fail depth screen |
| Friction Feasibility | Modeled friction ≤ 7.0% on ≥ 20% of candidates | Friction > gross upside on 100% |
| Buyback Cliff Survival | Minimal volume collapse at minute 6–7 | Severe liquidity evaporation |

If kill thresholds are triggered: **TERMINATE PROJECT**. If pass thresholds are met: **PROCEED TO PHASE 2**.

### 10.3 Phase 1 Measurable Acceptance Benchmarks
Phase 1 is complete when the telemetry engine demonstrates:
* **Detection Lag:** Mean pool discovery latency $< 2.0\text{ seconds}$ from on-chain block timestamp.
* **Stream Resilience:** Automatic reconnection within $< 5.0\text{ seconds}$ of network dropout without process termination.
* **Reserve Accuracy:** Zero drift between locally tracked reserves and periodic on-chain RPC snapshots ($< 0.01\%$ discrepancy).
* **Zero Fictional Data:** Missing data or gaps are recorded as `GAP_DETECTED`, never interpolated as active volume.

---

## 11. Validation Standards & Performance Accounting

To determine whether an actual edge exists, simulation results in Phase 4 must adhere to professional quantitative standards:

1. **Accounting Metrics:**
   * **Return on Deployed Notional:** Net profit divided by $10–$25 position size.
   * **Return on Total Bankroll:** Net profit divided by the full $100 capital base.
   * **Business Net P&L:** Trading net profit minus fixed operating expenses (RPC subscription, VPS hosting, API access fees).

2. **Data Partitioning:**
   * **In-Sample Development Set:** 60% of recorded historical data used to tune feature parameters.
   * **Untouched Out-of-Sample Evaluation:** 40% chronologically contiguous test set evaluated once without parameter modification.

3. **Sample Size & Constraint Realism:**
   * The live constraint of **1 position at a time** and a **5-trade daily cap** means that 200 live-conforming trades represent **40 to 60 trading days**.
   * Research simulations testing all unconstrained signals must be reported separately from the constrained executable bankroll ledger.

4. **Hit-Rate Surface Visualization (§11.4):**
   * Required win rate $p^*$ plotted across $(C, R_L, R_W)$ space.
   * Strategy zones classified as **Viable** (p* < 70%), **Demanding** (70–85%), **Impossible** (p* > 90%).
   * Chart embedded as Vega-Lite to enable interactive exploration.

5. **Live Rollout Precondition:**
   * Positive net expectancy across untouched out-of-sample data after all tiered fees, stress tips, latency distributions, and gap losses.
   * Maximum simulated drawdown strictly $< \$10.00$.
   * Mandatory operator sign-off and manual execution review on the first 10–20 live micro-trades.

---

## 11.4 Hit-Rate Surface Analysis (Vega-Lite Chart)

```vega-lite
{
  "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
  "width": "container",
  "title": { "text": "Required Win-Rate Surface", "subtitle": "Strategy viability by friction, loss size, and gross gain" },
  "data": { "values": [
    { "friction_pct": 6.5, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 64.0, "zone": "Viable" },
    { "friction_pct": 7.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 67.0, "zone": "Viable" },
    { "friction_pct": 7.5, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 70.0, "zone": "Demanding" },
    { "friction_pct": 8.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 73.0, "zone": "Demanding" },
    { "friction_pct": 8.5, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 76.0, "zone": "Demanding" },
    { "friction_pct": 9.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 79.0, "zone": "Demanding" },
    { "friction_pct": 9.5, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 82.0, "zone": "Demanding" },
    { "friction_pct": 10.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 85.0, "zone": "Demanding" },
    { "friction_pct": 11.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 88.0, "zone": "Impossible" },
    { "friction_pct": 12.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 91.0, "zone": "Impossible" },
    { "friction_pct": 13.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 93.0, "zone": "Impossible" },
    { "friction_pct": 14.0, "loss_pct": 32.0, "gain_pct": 20.0, "win_rate_required": 95.0, "zone": "Impossible" }
  ] },
  "mark": { "type": "line", "point": true },
  "encoding": {
    "x": { "field": "friction_pct", "type": "quantitative", "title": "Total Friction (% of notional)" },
    "y": { "field": "win_rate_required", "type": "quantitative", "title": "Required Win Rate (%)", "axis": {"format": ".0f"} },
    "color": { "field": "zone", "type": "nominal", "legend": {"title": "Viability Zone"} }
  }
}
```

> [!NOTE]
> **Reading the Chart:** For a $15 Regime A trade with $R_L=-32\%$ (stop + gap + fees) and $R_W=+20\%$ (target):
> * At 6.5% friction (Path A, disciplined tipping): **64% win rate** required → viable.
> * At 8.9% friction (Path B, original design): **76%+ win rate** required → demanding.
> * At 8.9% friction + 9% tranche dilution: **99.6% win rate** required → impossible.

---

## 12. Decision Tree for Phase 1 Run Outcome

```text
                        PHASE 1 14-DAY TELEMETRY
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
   ┌──────────────────────┐                        ┌──────────────────────┐
   │ PASS CRITERIA        │                        │ KILL CRITERIA        │
   │ • >= 3 cand/day survive                        │ • < 1 cand/day survives
   │   post-buyback cliff                           │   post-buyback cliff
   │ • >= 20% candidates have                       │ • > 95% candidates suffer
   │   executable depth for $20                     │   immediate liquidity collapse
   │   with <2% impact                              │   at min 6–7
   │ • Modeled friction                             │ • Modeled friction exceeds
   │   <= 7.0% on >= 20%                            │   gross upside on 100%
   │   of candidates                                │   of candidates
   └──────────┬─────────────┘                        └──────────┬─────────────┘
              │                                               │
              ▼                                               ▼
       PROCEED TO PHASE 2                             TERMINATE PROJECT
       Build cluster analyzer                         Archive project,
       Build cost engine                              document learnings
```
