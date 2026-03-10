# trader-bro

A Claude-powered crypto trading bot that uses AI as the decision-making brain. Claude analyzes live Coinbase market data, decides BUY/SELL/HOLD, executes trades, and logs every decision to SQLite with full reasoning.

## Overview

- **AI Brain:** Claude (`claude-sonnet-4-6`) uses tool use to gather market data and account balances before making trade decisions
- **Exchange:** Coinbase Advanced Trade API
- **Persistence:** Every decision (including HOLDs) saved to SQLite with Claude's reasoning
- **API:** FastAPI endpoints to trigger cycles and inspect history

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Coinbase Advanced Trade API key (CDP API key)
- Anthropic API key

## Setup

1. **Install dependencies:**
   ```bash
   uv sync
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your actual API keys
   ```

3. **Environment variables:**

   | Variable | Description |
   |---|---|
   | `COINBASE_API_KEY` | Coinbase CDP API key name |
   | `COINBASE_API_SECRET` | Coinbase CDP API private key (PEM format) |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `PRODUCT_IDS` | Comma-separated trading pairs (default: BTC-USD,ETH-USD,SOL-USD,DOGE-USD,ADA-USD,AVAX-USD) |
   | `MAX_TRADE_AMOUNT_USD` | Maximum USD per single trade decision (default: 100.00) |

## Running Locally

```bash
uv run uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

## Docker

```bash
# Build
docker build -t trader-bro .

# Run (requires .env file)
docker run --env-file .env -p 8000:8000 trader-bro
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/run` | Run trading cycle for all configured assets |
| `POST` | `/run/{product_id}` | Run cycle for a single asset (e.g. `/run/ETH-USD`) |
| `GET` | `/decisions` | All decisions, newest first. Optional `?product_id=BTC-USD` filter |
| `GET` | `/decisions/{id}` | Single decision by ID |

### Example Usage

```bash
# Trigger all assets
curl -X POST http://localhost:8000/run

# Trigger single asset
curl -X POST http://localhost:8000/run/ETH-USD

# View decision history
curl "http://localhost:8000/decisions?product_id=BTC-USD"
```

## Supported Assets

| Asset | Product ID |
|---|---|
| Bitcoin | BTC-USD |
| Ethereum | ETH-USD |
| Solana | SOL-USD |
| Dogecoin | DOGE-USD |
| Cardano | ADA-USD |
| Avalanche | AVAX-USD |

## How It Works

1. A `POST /run` triggers Claude for each configured trading pair
2. Claude calls tools in sequence: market data → account balance → execute trade
3. Claude chooses BUY/SELL/HOLD with conviction-scaled `amount_usd` (never exceeds `MAX_TRADE_AMOUNT_USD`)
4. Every decision is persisted to SQLite (`trader_bro.db`) with Claude's full reasoning
5. Orders are placed via Coinbase Advanced Trade API
