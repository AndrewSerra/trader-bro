import json
import os

import anthropic
import requests

from bot.coinbase_client import (
    fetch_account_balances,
    fetch_best_bid_ask,
    fetch_candles,
    place_market_buy,
    place_market_sell,
)
from bot.database import save_decision
from bot.notifications import notify_credit_error

MODEL = "claude-haiku-4-5-20251001"

TOOLS: list[anthropic.types.ToolParam] = [
    {
        "name": "get_market_data",
        "description": (
            "Fetch current market data for a trading pair: best bid/ask prices and "
            "the last 24 hourly candles (OHLCV). Call this first before making any decision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "Coinbase product ID, e.g. BTC-USD",
                }
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "get_account_balance",
        "description": (
            "Fetch current account balances for all non-zero holdings. "
            "Call this after getting market data to know available funds before deciding."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "execute_trade",
        "description": (
            "Execute a trading decision. Must be called exactly once per cycle. "
            "For HOLD, no order is placed but the decision is recorded. "
            "For BUY/SELL, amount_usd must be > 0 and <= the configured max trade limit. "
            "Choose amount_usd based on conviction: low conviction = small %, high conviction = up to the max."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["BUY", "SELL", "HOLD"],
                    "description": "The trading action to take.",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Detailed reasoning referencing specific prices and percentages. "
                        "This is persisted to the database for auditability."
                    ),
                },
                "product_id": {
                    "type": "string",
                    "description": "The Coinbase product ID being traded, e.g. BTC-USD",
                },
                "amount_usd": {
                    "type": "number",
                    "description": (
                        "USD notional for the trade. Must be 0.0 for HOLD. "
                        "For BUY/SELL, choose based on conviction level, never exceeding the max trade limit."
                    ),
                },
            },
            "required": ["decision", "reason", "product_id", "amount_usd"],
        },
    },
]

SYSTEM_PROMPT_TEMPLATE = """You are a disciplined crypto trading agent. Your job is to analyze market data and make a single trading decision per cycle.

Follow this exact sequence:
1. Call get_market_data(product_id) to get current prices and 24h candle history
2. Call get_account_balance() to check available funds
3. Call execute_trade(...) with your decision

Rules:
- Your maximum trade limit is ${max_trade_usd:.2f} USD per decision
- Never exceed the configured max trade limit in amount_usd
- For HOLD decisions, set amount_usd to 0.0
- Scale amount_usd by conviction: low conviction = 10-25% of max, medium = 25-75%, high = 75-100%
- Your reason must reference specific prices, percentage changes, and volume trends
- Account for your USD balance before placing a BUY (must have sufficient funds)
- Account for your coin balance before placing a SELL (must hold the asset)
- You must call execute_trade exactly once — it records your decision permanently
"""


def _build_system_prompt(max_trade_usd: float) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(max_trade_usd=max_trade_usd)


def _dispatch_tool(
    tool_name: str,
    tool_input: dict,
    max_trade_usd: float,
    final_decision: dict,
) -> tuple[str, dict]:
    """Dispatch a tool call and return (result_json, updated_final_decision)."""
    if tool_name == "get_market_data":
        product_id = tool_input["product_id"]
        bid_ask = fetch_best_bid_ask(product_id)
        candles = fetch_candles(product_id)
        result = {"bid_ask": bid_ask, "candles": candles}
        return json.dumps(result), final_decision

    if tool_name == "get_account_balance":
        balances = fetch_account_balances()
        return json.dumps({"balances": balances}), final_decision

    if tool_name == "execute_trade":
        decision = tool_input["decision"]
        reason = tool_input["reason"]
        product_id = tool_input["product_id"]
        amount_usd = float(tool_input["amount_usd"])

        # Hard cap — never trust the prompt alone
        if amount_usd > max_trade_usd:
            amount_usd = max_trade_usd

        order_id = None
        status = "skipped"

        if decision == "BUY":
            try:
                order_result = place_market_buy(product_id, str(round(amount_usd, 2)))
                order_id = order_result["order_id"]
                status = order_result["status"]
            except Exception as exc:
                status = "failed"
                reason = f"{reason} [ORDER ERROR: {exc}]"
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    if exc.response.status_code in (401, 402, 429):
                        notify_credit_error("Coinbase", str(exc))

        elif decision == "SELL":
            try:
                # Get current price to convert USD amount to base size
                bid_ask = fetch_best_bid_ask(product_id)
                mid_price = bid_ask["mid"]
                base_size = str(round(amount_usd / mid_price, 8))
                order_result = place_market_sell(product_id, base_size)
                order_id = order_result["order_id"]
                status = order_result["status"]
            except Exception as exc:
                status = "failed"
                reason = f"{reason} [ORDER ERROR: {exc}]"
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    if exc.response.status_code in (401, 402, 429):
                        notify_credit_error("Coinbase", str(exc))

        # Fetch price for record (best effort)
        try:
            price = fetch_best_bid_ask(product_id)["mid"]
        except Exception:
            price = 0.0

        record_id = save_decision(
            product_id=product_id,
            decision=decision,
            reason=reason,
            price=price,
            amount_usd=amount_usd,
            max_trade_limit_usd=max_trade_usd,
            order_id=order_id,
            status=status,
        )

        final_decision.update({
            "id": record_id,
            "product_id": product_id,
            "decision": decision,
            "reason": reason,
            "price": price,
            "amount_usd": amount_usd,
            "max_trade_limit_usd": max_trade_usd,
            "order_id": order_id,
            "status": status,
        })

        result = {
            "recorded": True,
            "decision_id": record_id,
            "order_id": order_id,
            "status": status,
        }
        return json.dumps(result), final_decision

    return json.dumps({"error": f"Unknown tool: {tool_name}"}), final_decision


def run_agent_cycle(product_id: str) -> dict:
    """Run a single Claude trading cycle for the given product_id."""
    max_trade_usd = float(os.environ.get("MAX_TRADE_AMOUNT_USD", "100.00"))
    client = anthropic.Anthropic()

    messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": (
                f"Analyze {product_id} and make a trading decision. "
                f"Follow the tool sequence: get_market_data → get_account_balance → execute_trade."
            ),
        }
    ]

    final_decision: dict = {}

    while True:
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=_build_system_prompt(max_trade_usd),
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            notify_credit_error("Anthropic", str(exc))
            raise
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402:
                notify_credit_error("Anthropic", str(exc))
            raise

        # Append assistant response to message history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            # Collect all tool_use blocks and dispatch them
            tool_results: list[anthropic.types.ToolResultBlockParam] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json, final_decision = _dispatch_tool(
                        block.name,
                        block.input,
                        max_trade_usd,
                        final_decision,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_json,
                    })

            # All tool results go back in a single user message
            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "end_turn":
            break
        else:
            # Unexpected stop reason — break to avoid infinite loop
            break

    # Defensive fallback: if Claude never called execute_trade
    if not final_decision:
        try:
            price = fetch_best_bid_ask(product_id)["mid"]
        except Exception:
            price = 0.0

        record_id = save_decision(
            product_id=product_id,
            decision="HOLD",
            reason="Claude did not call execute_trade — defaulting to HOLD.",
            price=price,
            amount_usd=0.0,
            max_trade_limit_usd=max_trade_usd,
            order_id=None,
            status="failed",
        )
        final_decision = {
            "id": record_id,
            "product_id": product_id,
            "decision": "HOLD",
            "reason": "Claude did not call execute_trade — defaulting to HOLD.",
            "price": price,
            "amount_usd": 0.0,
            "max_trade_limit_usd": max_trade_usd,
            "order_id": None,
            "status": "failed",
        }

    return final_decision


def run_all_cycles() -> list[dict]:
    """Run trading cycles for all configured product IDs."""
    product_ids_raw = os.environ.get(
        "PRODUCT_IDS", "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,ADA-USD,AVAX-USD"
    )
    product_ids = [p.strip() for p in product_ids_raw.split(",") if p.strip()]

    results = []
    for product_id in product_ids:
        result = run_agent_cycle(product_id)
        results.append(result)
    return results
