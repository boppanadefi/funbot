"""
Live Solana Market Feed Adapter.
Pulls real on-chain token graduations and price ticks from GeckoTerminal and DexScreener APIs.
Allows Funbot Paper Trading to execute against real Solana tokens and live market prices.
"""

from datetime import datetime, timezone
import json
import logging
from typing import Dict, List, Optional
import urllib.request
import urllib.error

logger = logging.getLogger("LiveMarketFeed")


class LiveMarketFeed:
    """
    Ingests real Solana market data:
    1. Newly created / graduated pools on Solana (Raydium, PumpSwap, Meteora).
    2. Real-time prices, reserves, and volume metrics.
    """

    GECKOTERMINAL_NEW_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
    DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def __init__(
        self,
        sol_price_usd: float = 155.0,
        timeout_seconds: int = 6,
        min_age_minutes: float = 8.0,
        max_age_minutes: float = 25.0,
    ):
        self.sol_price_usd = sol_price_usd
        self.timeout_seconds = timeout_seconds
        self.min_age_minutes = min_age_minutes
        self.max_age_minutes = max_age_minutes
        self._cached_pools: List[Dict] = []

    def _http_get(self, url: str) -> Optional[Dict]:
        """Performs a robust HTTP GET request with custom headers."""
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                if response.status == 200:
                    data = response.read().decode("utf-8")
                    return json.loads(data)
        except Exception as e:
            logger.warning("HTTP request to %s failed: %s", url, e)
            return None
        return None

    def fetch_dexscreener_solana_pools(self) -> List[Dict]:
        """
        Pulls real on-chain Solana tokens and pools from DexScreener search.
        Acts as a reliable, high-uptime fallback when GeckoTerminal rate limits (HTTP 429).
        """
        url = "https://api.dexscreener.com/latest/dex/search?q=solana%20pump"
        raw = self._http_get(url)
        if not raw or "pairs" not in raw:
            return []

        parsed_pools = []
        now = datetime.now(timezone.utc)
        pairs = raw.get("pairs", [])

        for pair in pairs:
            try:
                if pair.get("chainId") != "solana":
                    continue
                pool_address = pair.get("pairAddress")
                base_token = pair.get("baseToken", {})
                token_mint = base_token.get("address")
                base_symbol = base_token.get("symbol", "TOKEN")
                if not pool_address or not token_mint:
                    continue

                created_ms = pair.get("pairCreatedAt")
                if created_ms:
                    created_dt = datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc)
                    age_minutes = max(1.0, (now - created_dt).total_seconds() / 60.0)
                else:
                    created_dt = now
                    age_minutes = 12.0

                price_sol = float(pair.get("priceNative", 0.0) or 0.0)
                price_usd = float(pair.get("priceUsd", 0.0) or 0.0)
                liq_usd = float(pair.get("liquidity", {}).get("usd", 0.0) or 0.0)
                sol_reserve = max(5.0, liq_usd / self.sol_price_usd)
                fdv_usd = float(pair.get("fdv", 0.0) or 0.0)
                mc_sol = (fdv_usd / self.sol_price_usd) if fdv_usd > 0 else (sol_reserve * 4.0)

                if age_minutes < self.min_age_minutes:
                    state = "COOLING"
                elif self.min_age_minutes <= age_minutes <= self.max_age_minutes:
                    state = "ACTIVE_WINDOW"
                else:
                    state = "EXPIRED"

                swap_sol = 20.0 / self.sol_price_usd
                impact_pct = (swap_sol / (2.0 * sol_reserve)) * 100.0 if sol_reserve > 0 else 3.5
                modeled_friction = 2.50 + impact_pct + 0.50
                vol_24h = float(pair.get("volume", {}).get("h24", 0.0) or 0.0)

                parsed_pools.append({
                    "pool_address": pool_address,
                    "token_mint": token_mint,
                    "token_symbol": base_symbol,
                    "quote_mint": "So11111111111111111111111111111111111111112",
                    "is_canonical": True,
                    "graduation_slot": 300_000_000,
                    "graduation_ts": created_dt.isoformat(),
                    "minutes_post_graduation": round(age_minutes, 1),
                    "current_sol_reserve": round(sol_reserve, 2),
                    "initial_sol_reserve": round(sol_reserve, 2),
                    "initial_token_reserve": 1_000_000_000.0,
                    "current_token_reserve": 1_000_000_000.0,
                    "market_cap_sol": round(mc_sol, 1),
                    "state": state,
                    "price_sol": price_sol,
                    "price_usd": price_usd,
                    "price_impact_20usd_pct": round(impact_pct, 2),
                    "modeled_friction_pct": round(modeled_friction, 2),
                    "volume_24h_usd": vol_24h,
                    "dex_name": pair.get("dexId", "PumpSwap"),
                    "last_updated_ts": now.isoformat(),
                })
            except Exception:
                continue

        if parsed_pools:
            logger.info("Successfully fetched %d real Solana pools from DexScreener fallback", len(parsed_pools))
        return parsed_pools

    def fetch_newly_graduated_pools(self) -> List[Dict]:
        """
        Pulls the latest on-chain pool deployments on Solana.
        Tries GeckoTerminal first; if rate-limited (429) or unavailable, seamlessly falls back to DexScreener.
        """
        raw = self._http_get(self.GECKOTERMINAL_NEW_POOLS_URL)
        parsed_pools = []
        now = datetime.now(timezone.utc)

        if raw and "data" in raw:
            for item in raw.get("data", []):
                try:
                    attrs = item.get("attributes", {})
                    pool_address = attrs.get("address")
                    name = attrs.get("name", "Unknown/SOL")
                    symbols = name.split(" / ")
                    base_symbol = symbols[0] if symbols else "TOKEN"

                    rel_base = item.get("relationships", {}).get("base_token", {}).get("data", {})
                    token_mint = rel_base.get("id", "").replace("solana_", "") or pool_address

                    created_at_str = attrs.get("pool_created_at")
                    if created_at_str:
                        created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        age_minutes = max(1.0, (now - created_dt).total_seconds() / 60.0)
                    else:
                        created_dt = now
                        age_minutes = 10.0

                    price_usd_str = attrs.get("base_token_price_usd")
                    price_usd = float(price_usd_str) if price_usd_str else 0.000001
                    price_sol = price_usd / self.sol_price_usd

                    reserve_usd = float(attrs.get("reserve_in_usd", 0.0) or 0.0)
                    sol_reserve = max(1.0, reserve_usd / self.sol_price_usd)

                    fdv_usd = float(attrs.get("fdv_usd", 0.0) or 0.0)
                    mc_sol = (fdv_usd / self.sol_price_usd) if fdv_usd > 0 else (sol_reserve * 4.0)

                    if age_minutes < self.min_age_minutes:
                        state = "COOLING"
                    elif self.min_age_minutes <= age_minutes <= self.max_age_minutes:
                        state = "ACTIVE_WINDOW"
                    else:
                        state = "EXPIRED"

                    swap_sol = 20.0 / self.sol_price_usd
                    impact_pct = (swap_sol / (2.0 * sol_reserve)) * 100.0 if sol_reserve > 0 else 5.0
                    modeled_friction = 2.50 + impact_pct + 0.50
                    volume_usd = float(attrs.get("volume_usd", {}).get("h24", 0.0) or 0.0)

                    parsed_pools.append({
                        "pool_address": pool_address,
                        "token_mint": token_mint,
                        "token_symbol": base_symbol,
                        "quote_mint": "So11111111111111111111111111111111111111112",
                        "is_canonical": True,
                        "graduation_slot": 300_000_000,
                        "graduation_ts": created_dt.isoformat(),
                        "minutes_post_graduation": round(age_minutes, 1),
                        "current_sol_reserve": round(sol_reserve, 2),
                        "initial_sol_reserve": round(sol_reserve, 2),
                        "initial_token_reserve": 1_000_000_000.0,
                        "current_token_reserve": 1_000_000_000.0,
                        "market_cap_sol": round(mc_sol, 1),
                        "state": state,
                        "price_sol": price_sol,
                        "price_usd": price_usd,
                        "price_impact_20usd_pct": round(impact_pct, 2),
                        "modeled_friction_pct": round(modeled_friction, 2),
                        "volume_24h_usd": volume_usd,
                        "dex_name": attrs.get("dex_id", "Raydium/PumpSwap"),
                        "last_updated_ts": now.isoformat(),
                    })
                except Exception:
                    continue

        if not parsed_pools:
            # Fall back directly to DexScreener
            parsed_pools = self.fetch_dexscreener_solana_pools()

        if parsed_pools:
            self._cached_pools = parsed_pools
            logger.info("Successfully loaded %d real Solana pools", len(parsed_pools))
            return parsed_pools

        # If both failed in this instant, return cached real pools
        if self._cached_pools:
            logger.info("Returning %d cached real Solana pools", len(self._cached_pools))
            return self._cached_pools

        return []

    def get_live_token_quote(self, token_mint: str) -> Optional[Dict]:
        """
        Fetches live price and liquidity quote for a specific token mint from DexScreener.
        Normalizes price strictly to SOL using canonical SOL/USD rate to prevent quote-token distortion.
        """
        url = self.DEXSCREENER_TOKEN_URL.format(mint=token_mint)
        raw = self._http_get(url)
        if not raw or "pairs" not in raw:
            return None

        pairs = [p for p in (raw.get("pairs") or []) if p and p.get("chainId") == "solana"]
        if not pairs:
            return None

        # Filter for pairs with verified liquidity > $500
        liquid_pairs = [p for p in pairs if float(p.get("liquidity", {}).get("usd", 0.0) or 0.0) > 500.0]
        candidate_pairs = liquid_pairs if liquid_pairs else pairs

        # Select pair with highest liquidity
        best_pair = max(candidate_pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0.0) or 0.0))
        try:
            price_usd = float(best_pair.get("priceUsd", 0.0) or 0.0)
            if price_usd <= 0.0:
                return None

            # Compute normalized price in SOL via USD rate to eliminate non-SOL quote distortions
            price_sol = price_usd / self.sol_price_usd
            liq_usd = float(best_pair.get("liquidity", {}).get("usd", 0.0) or 0.0)
            vol_5m = float(best_pair.get("volume", {}).get("m5", 0.0) or 0.0)
            txns_5m = best_pair.get("txns", {}).get("m5", {})
            buys_5m = int(txns_5m.get("buys", 0))
            sells_5m = int(txns_5m.get("sells", 0))
            total_txns_5m = buys_5m + sells_5m
            price_change_5m = float(best_pair.get("priceChange", {}).get("m5", 0.0) or 0.0)
            quote_sol_reserve = float(best_pair.get("liquidity", {}).get("quote", 0.0) or (liq_usd / self.sol_price_usd / 2.0))

            return {
                "token_mint": token_mint,
                "price_sol": price_sol,
                "price_usd": price_usd,
                "liquidity_usd": liq_usd,
                "quote_sol_reserve": quote_sol_reserve,
                "volume_5m_usd": vol_5m,
                "txns_5m": txns_5m,
                "buys_5m": buys_5m,
                "sells_5m": sells_5m,
                "total_txns_5m": total_txns_5m,
                "price_change_5m": price_change_5m,
                "dex_id": best_pair.get("dexId", "PumpSwap"),
                "pair_address": best_pair.get("pairAddress"),
                "symbol": best_pair.get("baseToken", {}).get("symbol", "TOKEN"),
            }
        except Exception as e:
            logger.error("Error parsing DexScreener quote for %s: %s", token_mint, e)
            return None
