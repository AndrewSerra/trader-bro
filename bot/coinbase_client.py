import os
import secrets
import time
import uuid

import jwt
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

HOST = "api.coinbase.com"
BASE_URL = f"https://{HOST}"

_product_precision_cache: dict[str, int] = {}


def _load_key():
    key_name = os.environ["COINBASE_API_KEY"]
    # Secret is a PEM EC private key; .env may encode newlines as literal \n
    pem = os.environ["COINBASE_API_SECRET"].replace("\\n", "\n").encode()
    private_key = load_pem_private_key(pem, password=None)
    return key_name, private_key


def _build_jwt(method: str, path: str) -> str:
    key_name, private_key = _load_key()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": key_name,
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method.upper()} {HOST}{path}",
        },
        private_key,
        algorithm="ES256",
        headers={"kid": key_name, "nonce": secrets.token_hex(16)},
    )
    return token


def _get(path: str, params: dict | None = None) -> dict:
    token = _build_jwt("GET", path)
    resp = requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict) -> dict:
    token = _build_jwt("POST", path)
    resp = requests.post(
        f"{BASE_URL}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_best_bid_ask(product_id: str) -> dict:
    data = _get("/api/v3/brokerage/best_bid_ask", params={"product_ids": product_id})
    pricebook = data["pricebooks"][0]
    bid = float(pricebook["bids"][0]["price"])
    ask = float(pricebook["asks"][0]["price"])
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}


def fetch_candles(product_id: str, granularity: str = "FIVE_MINUTE", hours: int = 1) -> list[dict]:
    end = int(time.time())
    start = end - hours * 3600
    data = _get(
        f"/api/v3/brokerage/products/{product_id}/candles",
        params={"start": str(start), "end": str(end), "granularity": granularity},
    )
    return [
        {
            "start": c["start"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]),
        }
        for c in data.get("candles", [])
    ]


def fetch_base_precision(product_id: str) -> int:
    """Return the number of decimal places allowed for base_size on this product."""
    if product_id in _product_precision_cache:
        return _product_precision_cache[product_id]
    data = _get(f"/api/v3/brokerage/products/{product_id}")
    increment = data.get("base_increment", "0.00000001")
    if "." in increment:
        decimals = len(increment.rstrip("0").split(".")[1])
    else:
        decimals = 0
    _product_precision_cache[product_id] = decimals
    return decimals


def fetch_order_book_depth(product_id: str, limit: int = 10) -> dict | None:
    """
    Fetch top-of-book depth from Coinbase product_book endpoint.

    Returns bid/ask walls within 1% of mid-price (in USD notional) and
    the top 3 price levels on each side for the agent to reason about.
    """
    try:
        data = _get("/api/v3/brokerage/product_book", params={"product_id": product_id, "limit": limit})
        pricebook = data["pricebook"]
        bids = [(float(b["price"]), float(b["size"])) for b in pricebook.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in pricebook.get("asks", [])]
        if not bids or not asks:
            return None
        mid = (bids[0][0] + asks[0][0]) / 2
        bid_wall = sum(price * size for price, size in bids if price >= mid * 0.99)
        ask_wall = sum(price * size for price, size in asks if price <= mid * 1.01)
        depth_ratio = round(bid_wall / ask_wall, 2) if ask_wall > 0 else None
        return {
            "bid_wall_usd": round(bid_wall),
            "ask_wall_usd": round(ask_wall),
            "depth_ratio": depth_ratio,  # >1 = more buy pressure, <1 = more sell pressure
            "top_bids": bids[:3],
            "top_asks": asks[:3],
        }
    except Exception:
        import logging
        logging.warning("Failed to fetch order book depth for %s", product_id, exc_info=True)
        return None


def fetch_account_balances() -> list[dict]:
    data = _get("/api/v3/brokerage/accounts")
    balances = []
    for account in data.get("accounts", []):
        available = float(account.get("available_balance", {}).get("value", 0))
        if available > 0:
            balances.append({"currency": account["currency"], "available": available})
    return balances


def _extract_order_result(data: dict) -> dict:
    import logging
    success = data.get("success", False)
    order_id = data.get("order_id") or data.get("success_response", {}).get("order_id")
    error = None
    if not success:
        failure_reason = data.get("failure_reason", "")
        error_response = data.get("error_response", {})
        error_detail = error_response.get("message") or error_response.get("error") or ""
        error = f"{failure_reason}: {error_detail}".strip(": ") or "unknown"
        logging.error("Coinbase order failed — %s", error)
    return {"order_id": order_id, "status": "filled" if success else "failed", "error": error}


def place_market_buy(product_id: str, quote_size_usd: str) -> dict:
    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {"market_market_ioc": {"quote_size": quote_size_usd}},
    }
    data = _post("/api/v3/brokerage/orders", body)
    return _extract_order_result(data)


def place_market_sell(product_id: str, base_size: str) -> dict:
    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {"market_market_ioc": {"base_size": base_size}},
    }
    data = _post("/api/v3/brokerage/orders", body)
    return _extract_order_result(data)
