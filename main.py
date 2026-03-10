import asyncio
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must be first, before bot imports

from fastapi import FastAPI, HTTPException

from bot.agent import run_agent_cycle, run_all_cycles
from bot.database import get_all_decisions, get_decision_by_id, init_db

logger = logging.getLogger("trader_bro")


async def _trading_loop():
    interval = int(os.environ.get("CYCLE_INTERVAL_SECONDS", "3600"))
    loop = asyncio.get_event_loop()
    logger.info("Trading loop started — interval %ds", interval)
    while True:
        try:
            results = await loop.run_in_executor(None, run_all_cycles)
            logger.info("Cycle complete: %s", [r["decision"] for r in results])
        except Exception:
            logger.exception("Cycle error")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_trading_loop())
    yield
    task.cancel()


app = FastAPI(title="trader-bro", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run_all():
    """Trigger trading cycles for all configured assets."""
    return run_all_cycles()


@app.post("/run/{product_id}")
def run_single(product_id: str):
    """Trigger a trading cycle for a single asset (e.g. /run/ETH-USD)."""
    return run_agent_cycle(product_id)


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
