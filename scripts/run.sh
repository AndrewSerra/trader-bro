#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$(dirname "$0")/../data"
mkdir -p "$(dirname "$0")/../prompts"

docker run \
  --rm \
  -d \
  --env-file .env \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/prompts:/app/prompts" \
  --name trader-bro \
  trader-bro
