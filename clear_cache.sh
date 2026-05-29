#!/usr/bin/env bash
# Flush the semantic cache (all Redis content) for the tiered_rag stack.
#
# Why: the gateway caches served answers in Redis. After changing answer logic
# (e.g. the mock echo cap, a prompt, the KB), stale entries are served until the
# cache is cleared. Run this to force fresh answers.
#
# Usage:
#   ./clear_cache.sh
set -euo pipefail

cd "$(dirname "$0")"

if ! docker compose ps --services --filter status=running | grep -qx redis; then
    echo "❌ redis service is not running (start it with: docker compose up -d redis)"
    exit 1
fi

echo "🧹 flushing all Redis content ..."
docker compose exec -T redis redis-cli FLUSHALL
echo "✨ cache cleared — next queries will be answered fresh."
