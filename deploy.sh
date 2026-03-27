#!/usr/bin/env bash
set -euo pipefail

CONTAINER="trader-bro"
IMAGE="trader-bro:latest"

if docker ps -q --filter "name=^${CONTAINER}$" | grep -q .; then
    echo "Stopping running container..."
    docker stop "$CONTAINER"
fi

echo "Building image..."
docker build -t "$IMAGE" .

echo "Starting container..."
docker run --rm -d \
    --name "$CONTAINER" \
    --env-file .env \
    -p 8000:8000 \
    -v "$(pwd)/data:/app/data" \
    "$IMAGE"


