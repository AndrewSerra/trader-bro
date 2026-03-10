#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$(dirname "$0")/../data"

docker run \
  --rm \
  -d \
  --env-file .env \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  trader-bro
