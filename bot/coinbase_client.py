import base64
import os
import secrets
import time
import uuid

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HOST = "api.coinbase.com"
BASE_URL = f"https://{HOST}"


def _load_key() -> tuple[str, Ed25519PrivateKey]:
    key_id = os.environ["COINBASE_API_KEY"]
    raw = base64.b64decode(os.environ["COINBASE_API_SECRET"])
    # CDP exports 64 bytes (seed + public key) or 32 bytes (seed only)
    private_key = Ed25519PrivateKey.from_private_bytes(raw[:32])
    return key_id, private_key


def _build_jwt(method: str, path: str) -> str:
    key_id, private_key = _load_key()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": key_id,
            "iss": "cdp",
            "aud": ["cdp_service"],
            "nbf": now,
            "exp": now + 120,
            "uri": f"{method.upper()} {HOST}{path}",
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": key_id, "nonce": secrets.token_hex(16)},
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


def fetch_candles(product_id: str, granularity: str = "ONE_HOUR") -> list[dict]:
    end = int(time.time())
    start = end - 24 * 3600
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


def fetch_account_balances() -> list[dict]:
    data = _get("/api/v3/brokerage/accounts")
    balances = []
    for account in data.get("accounts", []):
        available = float(account.get("available_balance", {}).get("value", 0))
        if available > 0:
            balances.append({"currency": account["currency"], "available": available})
    return balances


def place_market_buy(product_id: str, quote_size_usd: str) -> dict:
    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {"market_market_ioc": {"quote_size": quote_size_usd}},
    }
    data = _post("/api/v3/brokerage/orders", body)
    success = data.get("success", False)
    order_id = data.get("order_id") or data.get("success_response", {}).get("order_id")
    return {"order_id": order_id, "status": "filled" if success else "failed"}


def place_market_sell(product_id: str, base_size: str) -> dict:
    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {"market_market_ioc": {"base_size": base_size}},
    }
    data = _post("/api/v3/brokerage/orders", body)
    success = data.get("success", False)
    order_id = data.get("order_id") or data.get("success_response", {}).get("order_id")
    return {"order_id": order_id, "status": "filled" if success else "failed"}
