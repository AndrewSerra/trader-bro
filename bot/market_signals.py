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


_FUNDING_RATE_COINS = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
    "DOGE-USD": "DOGE",
    "ADA-USD": "ADA",
    "AVAX-USD": "AVAX",
}


def _classify_funding(rate_8h: float) -> dict:
    annualized_pct = rate_8h * 3 * 365 * 100
    if rate_8h > 0.0001:
        sentiment = "long_heavy"
    elif rate_8h < -0.0001:
        sentiment = "short_heavy"
    else:
        sentiment = "neutral"
    return {"rate": rate_8h, "annualized_pct": round(annualized_pct, 1), "sentiment": sentiment}


def _fetch_hyperliquid_meta() -> tuple[list, list]:
    resp = requests.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "metaAndAssetCtxs"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()  # [meta, asset_ctxs]


def fetch_funding_rate(product_id: str) -> dict | None:
    """
    Fetch current perpetual funding rate from Hyperliquid (public API, no auth).

    Positive rate = longs paying shorts = market is long-biased (overheated longs).
    Negative rate = shorts paying longs = market is short-biased (potential short squeeze).
    """
    coin = _FUNDING_RATE_COINS.get(product_id)
    if not coin:
        return None
    try:
        meta, asset_ctxs = _fetch_hyperliquid_meta()
        name_to_idx = {a["name"]: i for i, a in enumerate(meta["universe"])}
        if coin not in name_to_idx:
            return None
        hourly_rate = float(asset_ctxs[name_to_idx[coin]]["funding"])
        return _classify_funding(hourly_rate * 8)
    except Exception:
        logging.warning("Failed to fetch funding rate for %s", product_id, exc_info=True)
        return None


def fetch_all_funding_rates(product_ids: list[str]) -> dict[str, dict | None]:
    """Fetch funding rates for multiple products in a single API call."""
    try:
        meta, asset_ctxs = _fetch_hyperliquid_meta()
        name_to_idx = {a["name"]: i for i, a in enumerate(meta["universe"])}
        result = {}
        for pid in product_ids:
            coin = _FUNDING_RATE_COINS.get(pid)
            if coin and coin in name_to_idx:
                hourly_rate = float(asset_ctxs[name_to_idx[coin]]["funding"])
                result[pid] = _classify_funding(hourly_rate * 8)
            else:
                result[pid] = None
        return result
    except Exception:
        logging.warning("Failed to fetch funding rates from Hyperliquid", exc_info=True)
        return {pid: None for pid in product_ids}
