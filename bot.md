# Pump.fun Post-Graduation Momentum System — Strategy & Design Specification (v2.5)

**Status:** Revised Viability Baseline — Systematic Friction-Busting & Dynamic Liquidity Architecture (Fixes 1–5 Integrated)  
**Supersedes:** v2.4, v2.3, v2.2, v2.1, v2.0, and v1.0  
**Scope:** Canonical SOL-quoted PumpSwap pools only (non-canonical and USDC pools excluded).  
**Objective:** Research and develop an automated post-graduation momentum system on PumpSwap with mathematically exact cost accounting, Friction-Busting 90/10 Scale-Out, discrete structural candle-low trailing stops, Volume Consolidation + Structural Breakout entries, real-time AMM order flow dynamics, dynamic liquidity-based sizing, and uncompromised paper simulation fidelity.  
**Epistemic Posture:** All profitability and edge hypotheses are unproven until demonstrated across out-of-sample paper testing. Targets and thresholds represent **conditional viability hurdles**, not performance guarantees.  

---

## 1. Executive Summary & Scope Delimitation

### 1.1 Strategic Pivot & Core Invariant Architecture
* **Sub-Minute Sniping Abandoned:** Micro-trades ($5–$10) on raw bonding curves fail due to fixed-cost drag (priority fees, Jito tips, ATA rent lockup), sub-second adversarial competition from low-latency searchers/bundlers, and discontinuous gap risk on developer dumps.
* **Invariant 2 (Path A+ is Dead for < $50 — Replaced by Friction-Busting 90/10 Scale-Out & Hard Runner Time Kill):** Path A+ (100% trailing single exit) requires a +16% to +22% move just to lock in +8% net, producing an abysmal 0.14:1 Risk-to-Reward ratio where one loses $3.50 on stops to make $0.50 on winners. For positions < $50, Path A+ is dead. Furthermore, holding a 20% runner indefinitely is bagholder logic (if price reverses, it bleeds all profits away). Replaced by **Friction-Busting 90/10 Scale-Out**:
  * **TP1 (The Friction Buster):** Sell 90% at a modest, achievable target of **+8% to +10% gross** (+9% default). On a $20 trade, 90% ($18) $\times$ +9% = +$1.62 gross, which strictly locks in net profit after covering the ~$1.50 round-trip friction for the whole position.
  * **The Runner (10% Lottery Runner):** The remaining 10% ($2) is purely a psychological moonshot ticket. If it dumps to zero, gross loss is only -$0.38 (vs -$2.56 on 80/20 and -$3.50 on a 100% stop).
  * **Hard Time Kill on Runner:** If the 10% runner does not achieve **+30% gross within 3 minutes of TP1**, market dump it immediately. Do not let it bleed sideways while LP evaporates. Cut the exposure ruthlessly.
* **Invariant 3 (8-Minute Cooldown is Dead — Replaced by Volume Exhaustion Consolidation, Baseline Demand Floor + Breakout):** Time is irrelevant; market structure is everything. Entering strictly at minute 8 means buying the exact moment early snipers dump. The 8-minute hard wait is dead. It is replaced by:
  * **Minimum 3-minute cooldown:** Bypasses the initial protocol BOOST cliff (minutes 0–3).
  * **Volume Consolidation Trigger:** Wait for 1-minute volume to drop to < 20% of peak 1-minute volume from the graduation spike (minutes 0–3), confirming sniper exhaustion.
  * **Baseline Demand Floor (Dead Token Protection):** Volume drop is necessary but not sufficient. Absolute 1-minute volume must remain $\ge \$100\text{ USD/min}$ during consolidation. If volume drops to $\$10/\text{min}$, retail has abandoned the ticker; it is hard-rejected as a dead token to avoid entering isolated whale sweeps. If it drops to $\$150/\text{min}$ but is $< 20\%$ of peak, that is healthy consolidation.
  * **Structural Breakout:** Enter only when a candle closes above the spike resistance line with buy volume > 1.5x the consolidation average.
* **Invariant 5 (Cluster/Burner History Checks are Dead — Replaced by Slot-Level 400ms Order Flow Dynamics):** 2-hop funding graphs and <3 transaction heuristics are dead (bypassed by syndicates using aged wallets and mixers). Furthermore, 1-minute rolling windows (VUW and Aggression) are lagging by 150 blocks and trivially spoofable by syndicates (firing 20 fresh wallets in block N, triggering bot entry in block N+1, and dumping in block N+2). Replaced by **Slot-Level (400ms) Real-Time Order Flow Dynamics**:
  * **Slot-Level Delta Unique Buyers (last 5 blocks):** Requires positive progression of new distinct buyers entering across the last 5 blocks (400ms slots ~ 2.0s): $\Delta\text{Unique Buyers}_{\text{last 5 blocks}} \ge 1$. Defeats block N syndicate spoofs by requiring continuous organic buyer inflow.
  * **Breakout Candle Taker Buy Dominance (> 70%):** Requires that $> 70\%$ of the SOL moved in the breakout candle was Taker Buy (`Taker Buy SOL / Total SOL Volume > 0.70`). Confirms curve momentum is overwhelmingly aggressive market-buy led, defeating balanced wash trades and simultaneous syndicate distribution.
* **Capital Allocation (Fixed $10–$25 Sizing is Dead — Replaced by Dynamic Liquidity-Based Sizing Capped at $25):** Hard-rejecting pools with >2% impact for a static $20 swap starves the bot (95% of post-graduation pools lack $20 depth at minute 8). Fixed $10–$25 sizing is dead. Position size is a dependent variable of pool liquidity:
  * $\text{Max Safe Swap SOL} = 0.030 \times \text{current\_sol\_reserve}$ (exact swap size yielding $\le 1.5\%$ price impact).
  * $\text{Position Size} = \min(\text{Calculated Max Safe Swap}, \$25.00\text{ Active Wallet Limit})$.
  * **Hard Reject Floor:** If $\text{Calculated Max Safe Swap} < \$3.00$ (minimum needed to overcome fixed Solana transaction overhead), hard-reject the pool.
  * Pools with thinner reserves (which cannot handle $20) auto-resize (e.g. to $6.00) and enter cleanly.

### 1.2 Core Scope Boundaries (v2.5)
1. **Quote Asset:** Strictly **SOL-quoted pairs**. USDC-quoted PumpSwap pools are excluded.
2. **Venue:** Strictly **Canonical PumpSwap pools**. Non-canonical pools remain disabled by default.
3. **Capital Limits:** $100 total bankroll ($25 active trading wallet, $75 segregated cold reserve). Exactly **1 active position at a time** (dynamically sized up to $25 active limit).
4. **Execution Strategy:** **Friction-Busting 90/10 Scale-Out** (TP1 sells 90% @ +8%–+10% gross; 10% lottery runner with 3m hard time kill).

### 1.3 The 7 Core Architectural Mitigations (Invariant Baseline)

1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. Secondary momentum observed **3 to 25 minutes** post-graduation.
2. **Invariant 2 (Path A+ is Dead for < $50 — Replaced by Friction-Busting 90/10 Scale-Out & Structural Exits):**
   * **Structure-Based Trailing Stop:** Stop Loss = Lowest Low of previous two completed 1-minute candles (absorbs 30%–40% single-block wicks without clipping while macro 1m trend holds higher lows).
   * **Momentum Time-Decay Exit:** If 2 consecutive 1-minute candles pass without new local high AND volume drops >40% from breakout volume, exit 100% immediately.
   * **Liquidity Collapse Exit:** Every 30 seconds, simulate sell slippage; if > 5.0%, market dump immediately.
   * **Friction-Busting 90/10 Scale-Out:** TP1 sells 90% at +8%–+10% gross (+9% default) to lock in net profit after covering round-trip friction.
   * **Runner Hard Time Kill:** If the remaining 10% lottery runner does not achieve +30% gross within 3 minutes of TP1, market dump immediately. Cut exposure ruthlessly without letting it bleed sideways while LP evaporates.
3. **Invariant 3 (8-Minute Cooldown is Dead — Volume Exhaustion Consolidation + Baseline Demand Floor + Breakout):** Minimum 3-minute cooldown (bypasses 0–3m protocol cliff), 1-minute volume dropping to < 20% of peak spike volume, absolute 1m volume holding $\ge \$100\text{ USD/min}$ baseline demand floor (hard-rejects dead tokens $< \$100/\text{min}$), and candle close above spike resistance with buy volume > 1.5x consolidation average.
4. **Invariant 4 (Pre-Flight Double Gate Friction Caps Remain — Ratchet Triggers Replaced by TP1 Friction Buster):** Friction caps remain (**Expected friction $\le 7.0\%$ in Regime A, $\le 6.0\%$ in Regime B; Stressed cost $\le 10.0\%$**). Ratchet triggers are replaced by the +8% to +10% TP1 Friction Buster.
5. **Invariant 5 (Cluster/Burner History Checks are Dead — Replaced by Slot-Level 400ms Order Flow Dynamics):**
   * **Slot-Level Delta Unique Buyers (last 5 blocks):** $\Delta\text{Unique Buyers}_{\text{last 5 blocks}} \ge 1$ (400ms slots $\approx 2.0\text{s}$). Replaces lagging 1m VUW to defeat single-block syndicate batch spoofs in block N by requiring continuous organic buyer inflow.
   * **Breakout Candle Taker Buy Dominance (> 70%):** $> 70\%$ of the SOL moved in the breakout candle must be Taker Buy (`Taker Buy SOL / Total SOL Volume > 0.70`). Confirms curve momentum is overwhelmingly aggressive market-buy led, defeating balanced wash trades and simultaneous syndicate distribution.
6. **Capital Allocation & Tip Caps (Fixed $10–$25 Sizing is Dead — Replaced by Dynamic Liquidity Sizing Capped at $25):**
   * Dynamic position sizing: $\min(\text{Calculated Max Safe Swap at 1.5\% impact}, \$25.00\text{ Limit})$. Hard floor: reject if $< \$3.00$.
   * 1.5% notional entry tip cap; Jito tip instruction placed strictly after swap instruction; 0.005 SOL emergency exit budget.
7. **Pre-Trade Risk Capacity & Hard Checks:** Verify Stressed Loss + Exit Reserve fits into $10 daily loss ceiling; 20-minute fallback holding timeout.
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
A momentum trade is strictly evaluated under the **Friction-Busting 90/10 Scale-Out** architecture:

| Path Identifier | Scenario Description | Swap Sequence | Legs Modeled | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Friction-Busting 90/10 Scale-Out** | 90% TP1 @ +8%–+10% gross, 10% Lottery Runner (3m Hard Kill) | Entry $\rightarrow$ TP1 (90%) $\rightarrow$ Runner Exit (10%) | **Up to 3 swaps** | **MANDATORY (< $50)** |
| **Path C: Entry Failure** | Transaction reverts or expires | Entry attempt only | 0 swaps (network fees only) | Active Guardrail |
| ~~**Path A+: 100% Ratchet**~~ | ~~100% trailing single exit with +16%–+22% hurdle~~ | ~~Entry $\rightarrow$ 100% Trailing Exit~~ | ~~2 swaps~~ | **DEAD (< $50)** |
| ~~**Path B: 50/50 Scaled Exit**~~ | ~~Normal 50% TP1 exit~~ | ~~Entry $\rightarrow$ 50% TP1 $\rightarrow$ 50% Runner~~ | ~~3 swaps~~ | **PROHIBITED** |

> [!IMPORTANT]
> **Path A+ is Dead (< $50):** The 100% trailing ratchet requires an unattainable +16% to +22% gross move just to lock in +8% net, producing an unacceptable 0.14:1 R:R ratio ($3.50 loss on stops vs $0.50 gain on winners). Friction-Busting 90/10 Scale-Out is mandatory for all positions < $50. Sells 90% at +8%–+10% gross to lock in net profit after friction; 10% runner is strictly hard-killed if not >= +30% in 3 minutes of TP1.

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
Before entry, costs are calculated across the planned trade:

$$\text{Expected Cost (SOL)} = \text{Impact}_{\text{roundtrip}} + \text{VenueFees}_{\text{roundtrip}} + \sum_{i=1}^{\text{legs}} \text{TxOverhead}_{i,\text{p50}}$$

$$\text{Stress Cost (SOL)} = \text{Expected Cost} + \text{AdverseSlippageBuffer (1.0\%)} + \text{FailedTxPenalty} + \sum_{i=1}^{\text{legs}} (\text{TxOverhead}_{i,\text{p75}} - \text{TxOverhead}_{i,\text{p50}})$$

#### Pre-Flight Double-Gate Rule (v2.5):
A candidate passes the cost hurdle **only if all three conditions are satisfied**:
1. $C_{\%,\text{expected}} \le \text{GateCap}(T)$, where GateCap varies by regime (**Regime A: 7.0%, Regime B: 6.0%**).
2. $C_{\%,\text{stress}} \le 10.0\%$ (hard safety ceiling).
3. **Invariant 4: Ratchet Triggers are Dead. Replaced by the +8% to +10% TP1 Friction Buster:** Candidate must support entry into the achievable **+8.0% to +10.0% TP1 Friction Buster** target (+9.0% default), which pays roundtrip friction on the 90% tranche without requiring extreme +16% to +22% moves.

---

## 4. Friction-Busting 90/10 Scale-Out & Discrete Structural Exits

### 4.1 Friction-Busting 90/10 Mechanics (Kill the Free Runner)
Under Friction-Busting 90/10 Scale-Out, the system addresses micro-position friction and eliminates bagholder drag:

1. **Two-Stage Execution:**
   * **TP1 (The Friction Buster):** Sells 90% of the position at an achievable **+8% to +10% gross** (+9% default). On a $20 trade, 90% ($18) $\times$ +9% = +$1.62 gross, which completely pays for the ~$1.50 round-trip friction and locks in net profit.
   * **The Runner (10% Lottery Runner):** The remaining 10% ($2) is purely a psychological lottery ticket. If it dumps to zero, gross trade loss is strictly -$0.38 (vs -$2.56 on 80/20 and -$3.50 on a 100% stop).
2. **Hard Time Kill on Runner (Anti-Bagholder Execution):**
   * **The Problem:** The free runner trailing candle lows often bleeds all the way down on 70% of meme coin reversals, giving back all TP1 profits.
   * **The Fix:** If the 10% runner does not achieve **+30% gross within 3 minutes of TP1**, market dump it immediately. Do not let it bleed sideways while LP evaporates. Cut exposure ruthlessly.
3. **Time-Sliced TP1 Execution (Anti-MEV / Sandwich Attack Defense):**
   * **The MEV Piñata Problem:** In Solana micro-cap AMMs, dumping 90% in a single market sell transaction *is the market*. Searchers sandwich the large order, causing a 5%–8% AMM curve cascade that degrades fills from +9% down to +2%.
   * **The Fix (Time-Sliced TP1):** Never market-sell 90% in one transaction. The 90% TP1 tranche is split into **3 equal sells (30% of original position each)** spaced **2 to 3 blocks apart (~800ms–1.2s)**.
   * **Why It Works:**
     * **Minimizes Self-Impact:** Each 30% slice restricts per-block curve displacement to $\approx 0.20\%$ (compared to 5%–8% on a single dump).
     * **Exogenous Liquidity Replenishment:** Slices allow organic retail buy flow to absorb sales between blocks.
     * **Defeats Sandwich Attacks:** Each slice is submitted in an atomic Jito bundle with tips placed after the swap; small 30% footprints prevent searchers from profitably sandwiching the order.
     * **Preserves +9% Target:** The volume-weighted average fill price ($VWAP_{\text{TP1}}$) lands cleanly at the +8.5% to +9.0% target, securing net profit before unlocking the lottery runner.
4. **Discrete Structural Trailing Stop:** Continuous percentage trails (e.g. 10%–15% from peak) fail on Solana AMM micro-liquidity because single-block wicks (30%–40%) trigger false exits at the bottom of temporary cascades before trend resumption. Instead, the stop trails based on candle lows:
   $$\text{StructureStopPrice}(t) = \min(\text{Low}_{\text{candle}[-1]}, \text{Low}_{\text{candle}[-2]})$$
   The stop ratchets upward monotonically and holds as long as the macro 1-minute trend preserves higher lows.
5. **Momentum Time-Decay Exit:** If 2 consecutive 1-minute candles pass without making a new local high AND volume drops **> 40%** from the entry-breakout volume, exit 100% immediately without waiting for the trailing stop to be tagged (liquidity silently evaporates).
6. **Liquidity Collapse Exit:** Every 30 seconds, simulate a sell quote on remaining tokens; if estimated slippage exceeds **5.0%**, market dump immediately (LP drain defense).

### 4.2 Net Portfolio Cash-Flow Formula (Scale-Out Architecture)
$$R_{\text{net}} = (\text{SOL}_{\text{TP1}} + \text{SOL}_{\text{Runner}}) - \text{SOL}_{\text{spent}} - \sum_{i=1}^{\text{legs}} \text{TxOverhead}_i - \text{FailedTxPenalties}$$

### 4.3 Exit Priority Hierarchy & Fallback Holding Time
When multiple exit signals occur, actions are resolved in strict priority order:

1. **Emergency Risk / Circuit Breaker:** Daily loss ceiling ($10.00) hit or manual operator kill-switch $\rightarrow$ Immediate market dump.
2. **Liquidity Collapse Exit:** Every 30 seconds, simulate a sell quote on the remaining position. If estimated slippage exceeds **5.0%**, market dump immediately. The LP is draining; the system does not wait for a price stop.
3. **Momentum Time-Decay Exit:** If 2 consecutive 1-minute candles pass without making a new local high AND volume drops **> 40%** from the entry-breakout volume, exit 100% immediately without waiting for the trailing stop to be tagged.
4. **Structure-Based Trailing Stop:** Candle close or tick penetrates below the lowest low of the previous two completed 1-minute candles $\rightarrow$ Market exit remaining runner tokens.
5. **Maximum Holding Time Fallback:** If a position neither hits TP1 nor exits within **20 minutes post-entry**, it is closed unconditionally to eliminate idle exposure.

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
          • Window: Structural Breakout     • Window: Structural Breakout
          • Volume Consolidation: < 20%     • Volume Consolidation: < 20%
          • Max Price Impact: <= 1.5%       • Max Price Impact: <= 1.5%
          • Order Flow: VUW >= 1.20         • Order Flow: VUW >= 1.20
          • Aggression Ratio: > 2.00        • Aggression Ratio: > 2.00
          • TP1 Friction Buster: +9.0%      • TP1 Friction Buster: +9.0%
```

### 5.1 Regime A: Tier 1 Early Secondary Momentum (0 – 420 SOL Market Cap)
* **Context:** Operates after the 3-minute minimum cooldown once Volume Exhaustion Consolidation and Structural Breakout are confirmed. Venue fee is 1.250% per swap (2.50% round-trip drag).
* **Entry Requirements:**
  * 1-minute volume dropping to < 20% of initial spike peak, followed by candle close above spike resistance with buy volume > 1.5x consolidation average.
  * Real-Time Order Flow: $\text{VUW} \ge 1.20$ and $\text{Aggression Ratio} > 2.00$.
  * Price impact $\le 1.5\%$ via Dynamic Liquidity Sizing.
* **TP1 Friction Buster Target (+8% to +10% Gross):** Replaces the dead Ratchet Trigger (+16% to +22%). TP1 sells 90% at +9% default (+8% to +10% range) to lock in ~+$1.62 and cover round-trip friction.

### 5.2 Regime B: Tier 2+ Continuation & Expansion ($\ge$ 420 SOL Market Cap)
* **Context:** Tokens breaking above 420 SOL into Tier 2 (1.200%) and Tier 3 (1.150%). Deeper reserves and lower fee drag.
* **Entry Requirements:**
  * Structural breakout with volume exhaustion consolidation.
  * Real-Time Order Flow: $\text{VUW} \ge 1.20$ and $\text{Aggression Ratio} > 2.00$.
  * Price impact $\le 1.5\%$ via Dynamic Liquidity Sizing.
* **TP1 Friction Buster Target:** +8% to +10% gross (+9% default) on 90% tranche, leaving 10% Lottery Runner (with 3-minute hard time kill).

---

## 6. Demand-Quality & Real-Time Order Flow Dynamics (Fix 4: Defeating Sybil Heuristics)

> [!IMPORTANT]
> **Cluster/Burner History Checks are Dead:** Historical 2-hop funding graphs and <3 transaction heuristics are trivially bypassed by syndicates using aged wallets and mixers. Looking at wallet history gives false confidence to manipulated books. Funbot scraps wallet genealogy in favor of **real-time AMM order flow dynamics**.

### 6.1 Real-Time Order Flow Metrics
1. **Velocity of Unique Wallets (VUW):**
   $$\text{VUW} = \frac{\text{Unique Buyers}_{\text{Current 1m}}}{\text{Unique Buyers}_{\text{Prior 1m}}}$$
   * **Mechanics:** Wash trading syndicates repeatedly rotate the same 5–10 wallets, resulting in a flat VUW ($\approx 1.0$). Genuine retail FOMO brings an accelerating wave of new participants ($\text{VUW} > 1.5$).
   * **Rule:** A candidate breakout is **hard rejected** if $\text{VUW} < 1.20$.
2. **Aggression Ratio:**
   $$\text{AggressionRatio} = \frac{\text{Taker Buy SOL (spent into AMM curve)}}{\text{Taker Sell SOL (pulled from curve)}}$$
   * **Mechanics:** Organic retail breakouts are heavily aggressive-buy led (market orders sweeping the AMM curve). Wash trading is often balanced ($\approx 1.0$) or uses limit-like pacing to minimize capital loss.
   * **Rule:** A candidate breakout requires $\text{AggressionRatio} > 2.00$ to be validated.
3. **On-Chain Token Authority Safeguards:** Automated verification that mint and freeze authorities are strictly revoked on the canonical pair. Candidate pools with active freeze keys are blocked unconditionally.

---

## 7. Capital Allocation, Daily Risk Budget, and Severe Loss Modeling

### 7.1 Bankroll Structure & Account Isolation
* **Total Dedicated Bankroll:** **$100.00** (denominated in SOL).
* **Active Trading Sub-Wallet:** **$25.00**.
* **Cold Segregated Reserve:** **$75.00** (stored in a separate keypair; zero programmatic withdrawal access; cannot be automatically drawn by the bot).
* **Position Sizing (Fixed $10–$25 Sizing is Dead — Replaced by Dynamic Liquidity-Based Sizing):**
  * Position size is a dependent variable of pool liquidity, capped at the **$25.00 Active Wallet Limit**.
  * $\text{Max Safe Swap SOL} = 0.030 \times \text{current\_sol\_reserve}$ (exact swap size yielding $\le 1.5\%$ price impact).
  * $\text{Position Size} = \min(\text{Calculated Max Safe Swap}, \$25.00\text{ Active Wallet Limit})$.
  * **Hard Floor Rejection:** If $\text{Calculated Max Safe Swap} < \$3.00$ (minimum to overcome fixed Solana transaction overhead), hard-reject the pool.
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

## 9. Complete Lifecycle State Machine (Friction-Busting 90/10 & Market Structure)

The execution engine operates as a strictly deterministic, finite state machine enforcing the 90/10 lifecycle:
`VOLUME_EXHAUSTION` → `BREAKOUT_CONFIRMED` → `POSITION_ACTIVE` → `TP1_FRICTION_BUSTER_HIT (90%)` → `RUNNER_ACTIVE (10%)` → `RUNNER_HARD_TIME_KILL / STRUCTURE_STOP_HIT` → `CLOSED`.

```text
                                GRADUATION DETECTED
                                         │
                                         ▼
                                   [DISCOVERED]
                                         │
                                         ▼
                            [COOLDOWN_CLIFF_BYPASS]
                        (Minutes 0–3 protocol cliff wait)
                                         │
                                         ▼
                            [VOLUME_EXHAUSTION_MONITOR]
                    (1m volume drops to < 20% of 0-3m spike peak
                     AND 1m volume >= $100 baseline demand floor)
                                         │
                      ┌──────────────────┴──────────────────┐
                      ▼                                     ▼
             [EXHAUSTION_FAILED / DEAD_TOKEN]      [VOLUME_EXHAUSTION]
             (Dump continues OR vol < $100/min)    (Healthy consolidation confirmed)
                                                            │
                                                            ▼
                                                   [BREAKOUT_MONITOR]
                                         (Price vs spike peak resistance +
                                          buy volume > 1.5x consolidation avg)
                                                            │
                                  ┌─────────────────────────┴─────────────────────────┐
                                  ▼                                                   ▼
                          [BREAKOUT_REJECT]                                  [BREAKOUT_CONFIRMED]
                          (No break / VUW < 1.2 / Agg < 2.0)                          │
                                                                                      ▼
                                                                             [ANALYSIS_PENDING]
                                                                        (Pre-Flight Double Gate:
                                                                         Expected <= 7%/6%, Stress <= 10%,
                                                                         Dynamic Sizing: $3.00 - $25.00)
                                                                                      │
                                                    ┌─────────────────────────────────┴─────────────────────────────────┐
                                                    ▼                                                                   ▼
                                             [GATE_REJECT]                                                       [ENTRY_PENDING]
                                       (Friction/Min Swap < $3 fail)                                                    │
                                                                                    ┌───────────────────────────────────┼───────────────────────────────────┐
                                                                                    ▼                                   ▼                                   ▼
                                                                             [ENTRY_FAILED]                      [ENTRY_EXPIRED]                     [ENTRY_CONFIRMED]
                                                                             (Tx revert)                         (Dropped bundle)                           │
                                                                                                                                                            ▼
                                                                                                                                                    [POSITION_ACTIVE]
                                                                                                                                             (100% position acquired: $3–$25)
                                                                                                                                                            │
                                                                               ┌────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────┐
                                                                               ▼                                                                                                                   ▼
                                                                  [TP1_FRICTION_BUSTER_HIT]                                                                                           [INITIAL_STOP_OR_DECAY_HIT]
                                                                 (+8% to +10% gross hit; default +9%)                                                                              (Hard -10% stop, time-decay 2m,
                                                                               │                                                                                                    or LP slippage > 5.0% tagged)
                                                                               ▼                                                                                                                   │
                                                                    [TP1_EXIT_CONFIRMED]                                                                                                           ▼
                                                                  (Time-sliced 90% sold in 3x30% slices; ~$1.62 gross secures                                                             [FULL_EXIT_PENDING]
                                                                   round-trip friction for entire trade)                                                                                   (100% position dumped)
                                                                               │                                                                                                                   │
                                                                               ▼                                                                                                                   ▼
                                                                        [RUNNER_ACTIVE]                                                                                                    [EXIT_CONFIRMED]
                                                                  (Remaining 10% lottery runner;                                                                                                   │
                                                                   trails lowest low of prev 2 completed 1m candles;                                                                               ▼
                                                                   hard time kill at 3m if < +30% gross)                                                                                   [CLOSED_PENDING_CLEANUP]
                                                                               │                                                                                                                   │
                                                    ┌──────────────────────────┼──────────────────────────┐                                                                                        ▼
                                                    ▼                          ▼                          ▼                                                                                [ARCHIVED_COMPLETE]
                                          [RUNNER_HARD_TIME_KILL]     [STRUCTURE_STOP_HIT]       [RUNNER_DECAY_HIT]                                                                       (ATA closed, rent swept)
                                          (3m elapsed without         (Candle-low structure      (2m no high + 40% vol drop
                                           hitting >= +30% gross)      breached)                  or LP drain > 5%)
                                                    │                          │                          │
                                                    └──────────────────────────┼──────────────────────────┘
                                                                               ▼
                                                                     [RUNNER_EXIT_PENDING]
                                                                      (Remaining 10% sold)
                                                                               │
                                                                               ▼
                                                                      [RUNNER_CONFIRMED]
                                                                               │
                                                                               ▼
                                                                    [CLOSED_PENDING_CLEANUP]
                                                                               │
                                                                               ▼
                                                                      [ARCHIVED_COMPLETE]
                                                                    (ATA closed, rent swept)
```

### 9.1 State Transition Definitions

1. **`DISCOVERED` & `COOLDOWN_CLIFF_BYPASS`:**
   * Canonical SOL-quoted PumpSwap pool identified post-migration.
   * Enforces a mandatory 3-minute cooldown (minutes 0.0 to 3.0) to bypass the literal protocol buyback cliff where initial pump spikes are heavily dumped.
2. **`VOLUME_EXHAUSTION` & Baseline Demand Floor:**
   * Transition occurs when 1-minute volume drops to `< 20%` of the peak 1-minute volume recorded during the graduation spike (minutes 0–3).
   * **Baseline Demand Floor (Dead Token Protection):** Volume drop is necessary but not sufficient. Absolute 1-minute volume must remain $\ge \$100\text{ USD/min}$ during consolidation. If volume drops to $\$10/\text{min}$, retail has abandoned the token; it is flagged as a dead token and hard-rejected to prevent buying isolated whale sweeps on illiquid curves. If volume drops to $\$150/\text{min}$ but is $< 20\%$ of peak, that is healthy consolidation.
3. **`BREAKOUT_CONFIRMED`:**
   * Requires a 30-second candle close above the initial spike resistance level with buy volume `> 1.5x` the consolidation average.
   * Concurrently validates real-time slot-level (400ms) AMM order flow dynamics: Delta Unique Buyers over the last 5 blocks ($\Delta\text{Unique Buyers}_{\text{last 5 blocks}} \ge 1$) and Breakout Candle Taker Buy Dominance ($> 70\%$ of breakout candle SOL volume was Taker Buy), defeating 1m syndicate batch spoofs and balanced wash trades.
4. **`ANALYSIS_PENDING` (Pre-Flight Double Gate & Dynamic Sizing):**
   * Computes Dynamic Liquidity-Based Sizing: $\Delta x_{\text{sol}} = 0.030 \times \text{sol\_reserve}$ ($1.5\%$ max safe impact). Position size $= \min(\text{Max Safe Swap}, \$25.00)$.
   * Rejects immediately if Calculated Max Safe Swap $< \$3.00$.
   * Enforces Double Gate friction caps: Expected cost $\le 7.0\%$ (Regime A) or $\le 6.0\%$ (Regime B), and Stressed cost $\le 10.0\%$.
5. **`POSITION_ACTIVE`:**
   * 100% position acquired via Jito atomic bundle. Initial hard protective stop placed at $-10\%$.
   * Initiates continuous discrete candle-low tracking and LP liquidity monitoring.
6. **`TP1_FRICTION_BUSTER_HIT`:**
   * Triggers when market price hits $+8\%$ to $+10\%$ gross ($+9\%$ default).
   * Executes time-sliced sell of **90% of the position** (3 equal slices of 30% spaced 2-3 blocks / ~1.0s apart), locking in $\approx +\$1.62$ gross on a $\$20$ trade, which completely offsets the $\approx \$1.50$ round-trip friction for the entire position.
7. **`RUNNER_ACTIVE`:**
   * Retains the remaining **10% of the position** as a Lottery Runner ($2 on a $20 position).
   * **Hard Time Kill on Runner:** If the 10% runner does not achieve $\ge +30\%$ gross within 3 minutes of TP1, the engine market dumps it immediately, ruthlessly cutting exposure before liquidity evaporates.
   * If the runner achieves $\ge +30\%$ gross within 3 minutes, it survives the time kill and trails using discrete structural candle lows: Stop Loss = Lowest Low of the previous two completed 1-minute candles.
8. **`STRUCTURE_STOP_HIT` / `RUNNER_HARD_TIME_KILL` / `RUNNER_DECAY_HIT`:**
   * Triggered when the 3-minute hard time kill expires without hitting $+30\%$ gross, when a candle breaches the candle-low trailing floor, when 2 consecutive 1m candles fail to print a new high with volume dropping $>40\%$ (time-decay), or when simulated LP slippage exceeds $5.0\%$ (liquidity drain protection).
   * Liquidates the remaining 10% runner.
9. **`CLOSED_PENDING_CLEANUP` & `ARCHIVED_COMPLETE`:**
   * Position fully closed. Spl-token ATA is closed, reclaiming $\approx 0.00204\text{ SOL}$ rent into the active wallet.

---

## 10. Narrowed Phase 1 Implementation Scope

> [!IMPORTANT]
> **Phase 1 Decoupling:** Phase 1 produces **pure observational telemetry only**. It does not execute trades, does not hold private keys, and does **not** depend on the Phase 2 cluster analyzer or Phase 3 cost engine.

### 10.1 Phase 1 Responsibilities
1. **Canonical Pool Discovery:** Monitor the PumpSwap program for pool creation and migration events. Verify canonical status and identify quote asset (SOL vs USDC).
2. **State Tracking:** Track each pool through the secondary window (minutes 3 to 25 post-graduation). Record token price, reserve levels, and market-cap tier transitions (420 SOL and 1,470 SOL boundaries).
3. **Basic Flow Telemetry:** Ingest raw swap events; compute rolling 10s, 30s, 60s volume, buy/sell transaction counts, and raw unique wallet counts.
4. **Feed Reliability & Telemetry Audit:** Log feed completeness, stream disconnects, reconnection latency, and reserve reconciliation deltas.
5. **Observational Watchlist States:** Maintain pool states: `MIGRATED`, `CLIFF_BYPASS`, `VOLUME_EXHAUSTION`, `BREAKOUT_CONFIRMED`, `TRACKING`, `DROPPED`. Fields requiring Phase 2/3 engines (e.g. `cluster_score`, `stress_cost`) are recorded explicitly as `NOT_EVALUATED`.

### 10.2 Phase 1 Falsification Gate (Empirical Kill Criteria)
Phase 1 must arm itself with explicit kill criteria before any engineering spend on Phase 2/3:

| Criterion | Pass Threshold | Kill Threshold |
| :--- | :--- | :--- |
| **Candidate Supply** | ≥ 3 candidates/day survive 3m cooldown & form volume consolidation | < 1 candidate/day survives |
| **Executable Depth (Dynamic Sizing)** | ≥ 20% of candidates support Dynamic Sizing (Calculated Max Safe Swap ≥ $3.00 at ≤ 1.5% impact, capped at $25) | > 95% fail dynamic depth screen (Max Safe Swap < $3.00) |
| **Friction Feasibility** | Modeled friction ≤ 7.0% (Regime A) / ≤ 6.0% (Regime B) on ≥ 20% of candidates | Friction > gross upside on 100% of candidates |
| **Volume Exhaustion & Breakout Viability** | 1m volume drops to < 20% of 0–3m spike peak, confirming seller exhaustion and enabling structural breakout | Severe unexhausted selling dumps continuing past 25m without consolidation |

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
