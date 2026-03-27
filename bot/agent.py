import json
import logging
import os

import anthropic
import requests

from bot.coinbase_client import (
    fetch_account_balances,
    fetch_base_precision,
    fetch_best_bid_ask,
    fetch_candles,
    place_market_buy,
    place_market_sell,
)
from bot.database import (
    get_last_successful_trade,
    get_latest_price_target,
    get_latest_price_targets,
    insert_price_target,
    save_decision,
)
from bot.notifications import notify_credit_error

MODEL = "claude-haiku-4-5-20251001"

TOOLS: list[anthropic.types.ToolParam] = [
    {
        "name": "get_account_balance",
        "description": (
            "Fetch current account balances for all non-zero holdings. "
            "Call this once at the start to know available funds across all triggered products."
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
            "Execute a trading decision for one product. Call once per triggered product. "
            "For HOLD, no order is placed but the decision is recorded. "
            "For BUY/SELL, amount_usd must be > 0 and <= the configured max trade limit."
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
                        "Detailed reasoning referencing specific prices, percentages, and volume. "
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
    {
        "name": "set_price_targets",
        "description": (
            "Record new low and high price targets for a product. "
            "REQUIRED — call this for EVERY triggered product, including HOLDs. "
            "This is how the bot knows when to wake up and run again: if price drops below low_target "
            "or rises above high_target, the agent is triggered. "
            "If you skip this call, the product has no targets and will trigger the agent on EVERY tick — "
            "wasting money on API calls and preventing the bot from functioning correctly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "string",
                    "description": "The Coinbase product ID, e.g. BTC-USD",
                },
                "low_target": {
                    "type": "number",
                    "description": "Price level below which the agent should be triggered (buy zone / support).",
                },
                "high_target": {
                    "type": "number",
                    "description": "Price level above which the agent should be triggered (sell zone / resistance).",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Explanation of why these levels were chosen. "
                        "If additional data or tools would improve target accuracy, describe them here."
                    ),
                },
            },
            "required": ["product_id", "low_target", "high_target", "reasoning"],
        },
    },
    # get_market_data kept for use by run_agent_cycle (manual/legacy trigger)
    {
        "name": "get_market_data",
        "description": (
            "Fetch current market data for a trading pair: best bid/ask prices and "
            "recent candles (OHLCV). Use only when market data was not pre-loaded."
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
]

_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "..", "prompts", "system_prompt.txt")

_FALLBACK_SYSTEM_PROMPT = """You are an aggressive crypto trading agent focused on capturing breakout moves and momentum.

You will receive a batch of triggered products with their market data already included.

STRATEGY:
- Price broke BELOW low_target: strong BUY signal
- Price broke ABOVE high_target: strong SELL signal
- Default to high conviction (75-100%% of max) on confirmed breakouts
- Only HOLD if there is a genuinely neutral signal

SEQUENCE:
1. Call get_account_balance() once
2. For each product: call execute_trade(...)
3. For each product: call set_price_targets(...) — REQUIRED

LIMITS:
- Max trade: ${max_trade_usd:.2f} USD per decision
- Never exceed max trade limit
- Must have sufficient balance before BUY/SELL
- Do not initiate new trades if net return after fees is negative
"""


def _build_system_prompt(max_trade_usd: float) -> str:
    prompt_path = os.environ.get("SYSTEM_PROMPT_FILE", _PROMPT_FILE)
    try:
        with open(prompt_path) as f:
            template = f.read()
    except OSError:
        template = _FALLBACK_SYSTEM_PROMPT
    return template.format(max_trade_usd=max_trade_usd)


def _dispatch_tool(
    tool_name: str,
    tool_input: dict,
    max_trade_usd: float,
    decisions: dict,
    input_tokens: int = 0,
    output_tokens: int = 0,
    targets_set: set | None = None,
) -> tuple[str, dict]:
    """Dispatch a tool call and return (result_json, updated_decisions).

    decisions is keyed by product_id for multi-product cycles.
    targets_set is updated in-place when set_price_targets is called.
    """
    if tool_name == "get_market_data":
        product_id = tool_input["product_id"]
        bid_ask = fetch_best_bid_ask(product_id)
        candles = fetch_candles(product_id)
        result = {"bid_ask": bid_ask, "candles": candles}
        return json.dumps(result), decisions

    if tool_name == "get_account_balance":
        balances = fetch_account_balances()
        return json.dumps({"balances": balances}), decisions

    if tool_name == "set_price_targets":
        product_id = tool_input["product_id"]
        low_target = float(tool_input["low_target"])
        high_target = float(tool_input["high_target"])
        reasoning = tool_input.get("reasoning", "")
        decision_id = decisions.get(product_id, {}).get("id")
        target_id = insert_price_target(
            product_id=product_id,
            low_target=low_target,
            high_target=high_target,
            decision_id=decision_id,
        )
        if targets_set is not None:
            targets_set.add(product_id)
        logging.info(
            "Price targets set for %s: low=%.4f high=%.4f (target_id=%d)",
            product_id, low_target, high_target, target_id,
        )
        return json.dumps({
            "recorded": True,
            "product_id": product_id,
            "low_target": low_target,
            "high_target": high_target,
            "reasoning": reasoning,
        }), decisions

    if tool_name == "execute_trade":
        decision = tool_input["decision"]
        reason = tool_input["reason"]
        product_id = tool_input["product_id"]
        amount_usd = float(tool_input["amount_usd"])

        if amount_usd > max_trade_usd:
            amount_usd = max_trade_usd

        order_id = None
        status = "skipped"
        order_error = None

        if decision == "BUY":
            try:
                balances = fetch_account_balances()
                usd_available = next((b["available"] for b in balances if b["currency"] == "USD"), 0.0)
                if amount_usd > usd_available:
                    status = "failed"
                    order_error = f"Insufficient USD balance: need {amount_usd:.2f}, have {usd_available:.2f}"
                    logging.error("BUY %s %.2f USD rejected — %s", product_id, amount_usd, order_error)
                else:
                    order_result = place_market_buy(product_id, str(round(amount_usd, 2)))
                    order_id = order_result["order_id"]
                    status = order_result["status"]
                    order_error = order_result.get("error")
            except Exception as exc:
                logging.exception("BUY order exception for %s", product_id)
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    if exc.response.status_code in (401, 402, 429):
                        notify_credit_error("Coinbase", str(exc))
                status = "failed"
                order_error = str(exc)

        elif decision == "SELL":
            try:
                bid_ask = fetch_best_bid_ask(product_id)
                mid_price = bid_ask["mid"]
                precision = fetch_base_precision(product_id)
                base_size_float = round(amount_usd / mid_price, precision)
                base_currency = product_id.split("-")[0]
                balances = fetch_account_balances()
                base_available = next((b["available"] for b in balances if b["currency"] == base_currency), 0.0)
                if base_size_float > base_available:
                    status = "failed"
                    order_error = f"Insufficient {base_currency} balance: need {base_size_float}, have {base_available}"
                    logging.error("SELL %s %.8f %s rejected — %s", product_id, base_size_float, base_currency, order_error)
                else:
                    order_result = place_market_sell(product_id, str(base_size_float))
                    order_id = order_result["order_id"]
                    status = order_result["status"]
                    order_error = order_result.get("error")
            except Exception as exc:
                logging.exception("SELL order exception for %s", product_id)
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    if exc.response.status_code in (401, 402, 429):
                        notify_credit_error("Coinbase", str(exc))
                status = "failed"
                order_error = str(exc)

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
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=order_error,
        )

        decisions[product_id] = {
            "id": record_id,
            "product_id": product_id,
            "decision": decision,
            "reason": reason,
            "price": price,
            "amount_usd": amount_usd,
            "max_trade_limit_usd": max_trade_usd,
            "order_id": order_id,
            "status": status,
            "error": order_error,
        }

        return json.dumps({
            "recorded": True,
            "decision_id": record_id,
            "order_id": order_id,
            "status": status,
            **({"error": order_error} if order_error else {}),
        }), decisions

    return json.dumps({"error": f"Unknown tool: {tool_name}"}), decisions


def check_and_collect_triggered(product_ids: list[str]) -> list[dict]:
    """Check all products against stored targets. Return those that need agent attention."""
    triggered = []
    for product_id in product_ids:
        try:
            bid_ask = fetch_best_bid_ask(product_id)
            current_price = bid_ask["mid"]
        except Exception:
            logging.exception("Failed to fetch price for %s", product_id)
            continue

        target = get_latest_price_target(product_id)

        if target is None:
            reason = "no_target"
        elif current_price <= target["low_target"]:
            reason = "below_low"
        elif current_price >= target["high_target"]:
            reason = "above_high"
        else:
            logging.debug(
                "%s price %.4f within targets [%.4f, %.4f] — skipping",
                product_id, current_price, target["low_target"], target["high_target"],
            )
            continue

        try:
            candles = fetch_candles(product_id)
        except Exception:
            logging.exception("Failed to fetch candles for %s", product_id)
            candles = []

        triggered.append({
            "product_id": product_id,
            "current_price": current_price,
            "bid": bid_ask["bid"],
            "ask": bid_ask["ask"],
            "low_target": target["low_target"] if target else None,
            "high_target": target["high_target"] if target else None,
            "trigger_reason": reason,
            "candles": candles,
            "last_trade": get_last_successful_trade(product_id),
        })
        logging.info("Triggered %s: price=%.4f reason=%s", product_id, current_price, reason)

    return triggered


def _build_triggered_user_message(triggered: list[dict], balances: list[dict]) -> str:
    """Build the user message that contains all triggered products' market data."""
    lines = [
        f"The following {len(triggered)} product(s) have triggered their price targets. "
        "Analyze each, make a trade decision, and update targets.\n"
    ]
    lines.append("## Account Balances")
    for b in balances:
        lines.append(f"- {b['currency']}: {b['available']:.8f}")
    lines.append("")
    for item in triggered:
        lines.append(f"## {item['product_id']}")
        lines.append(f"- Current price: {item['current_price']:.6f} (bid: {item['bid']:.6f}, ask: {item['ask']:.6f})")
        if item["low_target"] is not None:
            lines.append(f"- Targets: low={item['low_target']:.6f}, high={item['high_target']:.6f}")
            lines.append(f"- Trigger reason: {item['trigger_reason']}")
        else:
            lines.append("- No prior targets (bootstrap — set initial targets)")
        last = item.get("last_trade")
        if last:
            lines.append(
                f"- Last trade: {last['decision']} {last['amount_usd']:.2f} USD"
                f" @ {last['price']:.6f} on {last['timestamp'][:19].replace('T', ' ')} UTC"
            )
            lines.append(f"  Reason: {last['reason']}")
        lines.append(f"- Last-hour candles (FIVE_MINUTE, newest first):")
        for c in item["candles"][:12]:
            lines.append(
                f"  open={c['open']:.4f} high={c['high']:.4f} low={c['low']:.4f} "
                f"close={c['close']:.4f} vol={c['volume']:.2f}"
            )
        lines.append("")
    return "\n".join(lines)


def run_agent_for_triggered_products(triggered: list[dict]) -> list[dict]:
    """Run a single Claude cycle for all triggered products. Returns list of decisions."""
    if not triggered:
        return []

    max_trade_usd = float(os.environ.get("MAX_TRADE_AMOUNT_USD", "100.00"))
    client = anthropic.Anthropic()

    try:
        balances = fetch_account_balances()
    except Exception:
        logging.exception("Failed to fetch account balances")
        balances = []

    user_message = _build_triggered_user_message(triggered, balances)
    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": user_message}
    ]

    decisions: dict = {}
    targets_set: set = set()
    total_input_tokens = 0
    total_output_tokens = 0

    # Balances are pre-loaded in the message; exclude both data-fetching tools
    active_tools = [t for t in TOOLS if t["name"] not in ("get_market_data", "get_account_balance")]

    while True:
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=_build_system_prompt(max_trade_usd),
                tools=active_tools,
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            notify_credit_error("Anthropic", str(exc))
            raise
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                notify_credit_error("Anthropic", str(exc))
            raise
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402:
                notify_credit_error("Anthropic", str(exc))
            raise

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results: list[anthropic.types.ToolResultBlockParam] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json, decisions = _dispatch_tool(
                        block.name,
                        block.input,
                        max_trade_usd,
                        decisions,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        targets_set=targets_set,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_json,
                    })
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn":
            missing_targets = [
                item["product_id"] for item in triggered
                if item["product_id"] not in targets_set
            ]
            if missing_targets:
                logging.warning("Claude ended turn without set_price_targets for: %s — prompting", missing_targets)
                messages.append({
                    "role": "user",
                    "content": (
                        f"You must call set_price_targets for the following products before finishing: "
                        f"{', '.join(missing_targets)}. This is required for every triggered product."
                    ),
                })
            else:
                break
        else:
            break

    # Defensive fallback: any triggered product that got no execute_trade call
    for item in triggered:
        pid = item["product_id"]
        if pid not in decisions:
            try:
                price = item["current_price"]
            except Exception:
                price = 0.0
            record_id = save_decision(
                product_id=pid,
                decision="HOLD",
                reason="Claude did not call execute_trade — defaulting to HOLD.",
                price=price,
                amount_usd=0.0,
                max_trade_limit_usd=max_trade_usd,
                order_id=None,
                status="failed",
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )
            decisions[pid] = {
                "id": record_id,
                "product_id": pid,
                "decision": "HOLD",
                "reason": "Claude did not call execute_trade — defaulting to HOLD.",
                "price": price,
                "amount_usd": 0.0,
                "max_trade_limit_usd": max_trade_usd,
                "order_id": None,
                "status": "failed",
            }

    # Defensive fallback: set ±3% targets for any product Claude skipped
    for item in triggered:
        pid = item["product_id"]
        if pid not in targets_set:
            price = item["current_price"]
            low = round(price * 0.97, 8)
            high = round(price * 1.03, 8)
            insert_price_target(product_id=pid, low_target=low, high_target=high)
            logging.warning(
                "Claude did not call set_price_targets for %s — defaulting to ±3%%: low=%.6f high=%.6f",
                pid, low, high,
            )

    return list(decisions.values())


def run_agent_cycle(product_id: str) -> dict:
    """Run a single Claude trading cycle for the given product_id (manual/legacy trigger)."""
    max_trade_usd = float(os.environ.get("MAX_TRADE_AMOUNT_USD", "100.00"))
    client = anthropic.Anthropic()

    messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": (
                f"Analyze {product_id} and make a trading decision. "
                f"Follow the tool sequence: get_market_data → get_account_balance → execute_trade → set_price_targets."
            ),
        }
    ]

    decisions: dict = {}
    total_input_tokens = 0
    total_output_tokens = 0

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
        except anthropic.BadRequestError as exc:
            if "credit balance" in str(exc).lower():
                notify_credit_error("Anthropic", str(exc))
            raise
        except anthropic.APIStatusError as exc:
            if exc.status_code == 402:
                notify_credit_error("Anthropic", str(exc))
            raise

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results: list[anthropic.types.ToolResultBlockParam] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_json, decisions = _dispatch_tool(
                        block.name,
                        block.input,
                        max_trade_usd,
                        decisions,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_json,
                    })
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn":
            break
        else:
            break

    if product_id not in decisions:
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
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )
        decisions[product_id] = {
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

    return decisions[product_id]


def run_all_cycles() -> list[dict]:
    """Run trading cycles for all configured product IDs (manual trigger)."""
    product_ids_raw = os.environ.get(
        "PRODUCT_IDS", "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,ADA-USD,AVAX-USD"
    )
    product_ids = [p.strip() for p in product_ids_raw.split(",") if p.strip()]

    results = []
    for product_id in product_ids:
        result = run_agent_cycle(product_id)
        results.append(result)
    return results
