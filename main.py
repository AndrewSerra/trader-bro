import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must be first, before bot imports

from fastapi import FastAPI, HTTPException

from bot.agent import (
    check_and_collect_triggered,
    run_agent_cycle,
    run_agent_for_triggered_products,
    run_all_cycles,
)
from bot.database import (
    get_all_decisions,
    get_decision_by_id,
    get_latest_price_targets,
    init_db,
)
from bot.notifications import create_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trader_bro")


def _get_product_ids() -> list[str]:
    raw = os.environ.get("PRODUCT_IDS", "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,ADA-USD,AVAX-USD")
    return [p.strip() for p in raw.split(",") if p.strip()]


async def _price_check_loop():
    interval = int(os.environ.get("CYCLE_INTERVAL_SECONDS", "600"))
    product_ids = _get_product_ids()
    loop = asyncio.get_event_loop()
    logger.info("Price-check loop started — interval %ds, products: %s", interval, product_ids)
    while True:
        try:
            triggered = await loop.run_in_executor(None, check_and_collect_triggered, product_ids)
            if triggered:
                logger.info("Triggered products: %s", [t["product_id"] for t in triggered])
                results = await loop.run_in_executor(None, run_agent_for_triggered_products, triggered)
                logger.info("Agent cycle complete: %s", {r["product_id"]: r["decision"] for r in results if r})
            else:
                logger.info("No products triggered this tick")
        except Exception:
            logger.exception("Price-check cycle error")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Create Todoist task for implementing additional data/tools for target price decisions
    create_task(
        "Implement additional data/tools for target price decisions in trader-bro",
        description=(
            "The trading agent has requested better data sources for setting price targets. "
            "Consider: order book depth, funding rates, fear/greed index, on-chain flows. "
            "Check recent set_price_targets reasoning fields in the DB for specific agent requests."
        ),
        priority=2,
    )
    task = asyncio.create_task(_price_check_loop())
    yield
    task.cancel()


app = FastAPI(title="trader-bro", lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run_all():
    """Force-trigger agent for all configured products (bypasses target check)."""
    product_ids = _get_product_ids()
    triggered = check_and_collect_triggered(product_ids)
    if not triggered:
        # Force all products even if within target range
        return run_all_cycles()
    return run_agent_for_triggered_products(triggered)


@app.post("/run/{product_id}")
def run_single(product_id: str):
    """Trigger a trading cycle for a single asset (e.g. /run/ETH-USD)."""
    return run_agent_cycle(product_id)


@app.get("/targets")
def list_targets():
    """Return the latest price targets for all products."""
    return get_latest_price_targets()


@app.get("/decisions")
def list_decisions(product_id: str | None = None):
    """Return all decisions, newest first. Optional ?product_id= filter."""
    return get_all_decisions(product_id=product_id)


@app.get("/decisions/{decision_id}")
def get_decision(decision_id: int):
    """Return a single decision by ID."""
    decision = get_decision_by_id(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision
