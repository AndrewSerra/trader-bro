"""
Supplementary market signal fetchers for enriching agent context.

All functions are best-effort — they log on failure and return None
so the agent can still run without these signals.
"""
import logging

import requests


def fetch_fear_greed_index() -> dict | None:
    """Fetch current Crypto Fear & Greed Index from Alternative.me (free, no auth)."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        resp.raise_for_status()
        entry = resp.json()["data"][0]
        return {
            "value": int(entry["value"]),
            "classification": entry["value_classification"],
        }
    except Exception:
        logging.warning("Failed to fetch Fear & Greed Index", exc_info=True)
        return None


_FUNDING_RATE_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "DOGE-USD": "DOGEUSDT",
    "ADA-USD": "ADAUSDT",
    "AVAX-USD": "AVAXUSDT",
}


def fetch_funding_rate(product_id: str) -> dict | None:
    """
    Fetch current perpetual funding rate from Binance (public API, no auth).

    Positive rate = longs paying shorts = market is long-biased (overheated longs).
    Negative rate = shorts paying longs = market is short-biased (potential short squeeze).
    """
    symbol = _FUNDING_RATE_SYMBOL_MAP.get(product_id)
    if not symbol:
        return None
    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data["lastFundingRate"])
        annualized_pct = rate * 3 * 365 * 100  # 3 settlements/day → annual %
        if rate > 0.0001:
            sentiment = "long_heavy"
        elif rate < -0.0001:
            sentiment = "short_heavy"
        else:
            sentiment = "neutral"
        return {
            "rate": rate,
            "annualized_pct": round(annualized_pct, 1),
            "sentiment": sentiment,
        }
    except Exception:
        logging.warning("Failed to fetch funding rate for %s", product_id, exc_info=True)
        return None


def fetch_all_funding_rates(product_ids: list[str]) -> dict[str, dict | None]:
    """Fetch funding rates for multiple products. Returns {product_id: result}."""
    return {pid: fetch_funding_rate(pid) for pid in product_ids}
