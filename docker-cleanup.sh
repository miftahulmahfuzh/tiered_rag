#!/usr/bin/env bash
# Cleanup for the tiered_rag docker-compose stack.
#
# Tears down every container/network defined in docker-compose.yml via
# `docker compose down`, and (optionally) frees stray HOST processes that are
# holding the stack's published ports — the usual cause of:
#   "failed to bind host port for 0.0.0.0:8000 ... address already in use"
# (e.g. a `uvicorn tiered_rag.api:app` you started by hand in another terminal).
#
# Usage:
#   ./docker-cleanup.sh             # compose down (stop + remove containers & network)
#   ./docker-cleanup.sh --volumes   # also remove named volumes (does NOT touch ./qdrant_storage bind mount)
#   ./docker-cleanup.sh --ports     # also kill stray HOST processes occupying our published ports
#   ./docker-cleanup.sh --ports --volumes
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE_FILE="docker-compose.yml"

DOWN_FLAGS=(--remove-orphans)
FREE_PORTS=false
for arg in "$@"; do
    case "$arg" in
        --volumes) DOWN_FLAGS+=(--volumes) ;;
        --ports)   FREE_PORTS=true ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $arg (try --help)" >&2; exit 1 ;;
    esac
done

# --- 1. tear down the compose stack ------------------------------------------
echo "🧹 docker compose down ${DOWN_FLAGS[*]} ..."
docker compose -f "$COMPOSE_FILE" down "${DOWN_FLAGS[@]}"
echo "   ✅ stack down"
echo ""

# --- 2. (optional) free stray HOST processes on our published ports ----------
if [ "$FREE_PORTS" = true ]; then
    # Extract published host ports from the compose file, expanding "6333-6334" ranges.
    HOST_PORTS=$(
        grep -oE '"[0-9]+(-[0-9]+)?:[0-9]+(-[0-9]+)?"' "$COMPOSE_FILE" |
        tr -d '"' | cut -d: -f1 |
        while read -r spec; do
            if [[ "$spec" == *-* ]]; then seq "${spec%-*}" "${spec#*-}"; else echo "$spec"; fi
        done | sort -un
    )

    echo "🔌 Checking host ports: $(echo "$HOST_PORTS" | tr '\n' ' ')"
    for port in $HOST_PORTS; do
        # Only non-docker host listeners (docker-proxy is already gone after `down`).
        pids=$(ss -ltnpH "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
        for pid in $pids; do
            cmd=$(ps -o comm= -p "$pid" 2>/dev/null || echo "?")
            echo "   🔴 port $port held by host process $pid ($cmd) — killing"
            kill "$pid" 2>/dev/null || true
            sleep 0.3
            kill -9 "$pid" 2>/dev/null || true
        done
    done
    echo "   ✅ ports freed"
    echo ""
fi

echo "✨ Cleanup complete. Bring the stack back up with:"
echo "   docker compose up -d --build"
