#!/usr/bin/env bash
# Serve ollama so the docker-compose containers can reach it, and ensure the
# embedding model is present.
#
# Why: ollama defaults to binding 127.0.0.1, which is NOT reachable from inside a
# container (the gateway talks to the host at host.docker.internal -> 172.17.0.1).
# Binding 0.0.0.0 fixes the gateway's "httpx.ConnectError: Connection refused"
# on every FAQ / cache / retrieve query.
#
# Idempotent: re-run anytime. If ollama is already serving on 0.0.0.0 it is left
# alone; if it's bound to localhost it is restarted on 0.0.0.0; the model is
# pulled only if missing.
#
# Usage:
#   ./deploy_ollama.sh
set -euo pipefail

cd "$(dirname "$0")"

PORT="11434"
LOG="/tmp/ollama.log"

# Embedding model: prefer EMBED_MODEL from .env, else the project default.
EMBED_MODEL="$(grep -E '^EMBED_MODEL=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text:v1.5}"

command -v ollama >/dev/null || { echo "❌ ollama not found on PATH"; exit 1; }

listener() { ss -ltnH "sport = :$PORT" 2>/dev/null; }

# --- 1. ensure ollama is serving on 0.0.0.0 ----------------------------------
line="$(listener || true)"
if [ -n "$line" ] && echo "$line" | grep -qE '(\*|0\.0\.0\.0):'"$PORT"; then
    echo "✅ ollama already serving on all interfaces (:$PORT)"
else
    if [ -n "$line" ]; then
        echo "🔁 ollama is bound to localhost only — restarting on 0.0.0.0"
        pids="$(echo "$line" | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)"
        for pid in $pids; do kill "$pid" 2>/dev/null || true; done
        sleep 1
    else
        echo "▶️  starting ollama on 0.0.0.0:$PORT"
    fi
    OLLAMA_HOST="0.0.0.0:$PORT" nohup ollama serve > "$LOG" 2>&1 &
    disown || true

    # wait for the API to come up
    for i in $(seq 1 30); do
        if curl -fsS "http://localhost:$PORT/api/tags" >/dev/null 2>&1; then break; fi
        [ "$i" = 30 ] && { echo "❌ ollama did not become ready — see $LOG"; exit 1; }
        sleep 1
    done
    echo "✅ ollama serving on 0.0.0.0:$PORT (logs: $LOG)"
fi

# --- 2. ensure the embedding model is present --------------------------------
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$EMBED_MODEL"; then
    echo "✅ embedding model present: $EMBED_MODEL"
else
    echo "⬇️  pulling embedding model: $EMBED_MODEL"
    OLLAMA_HOST="0.0.0.0:$PORT" ollama pull "$EMBED_MODEL"
fi

echo ""
echo "✨ Ready. Verify from a container with:"
echo "   docker compose exec gateway curl -s http://host.docker.internal:$PORT/api/tags"
