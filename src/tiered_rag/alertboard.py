"""Knowledge-gap operator dashboard — a standalone, stateless webhook receiver.

The gateway already POSTs every GapAlert to ``alert_webhook_url`` (see
``alerting.Alerter``). Point that URL at this app and it captures each alert in an
in-memory ring buffer and renders it for a human operator — proving the
"asynchronous alert for human operators" requirement end-to-end, with zero
changes to the chatbot. State lives only in memory (lost on restart), so the
app is effectively stateless: no DB, no files.

Run it: ``uvicorn tiered_rag.alertboard:app --host 0.0.0.0 --port 9000``.
"""
from __future__ import annotations

import html
from collections import deque
from typing import Deque

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

MAX_ALERTS = 50            # ring-buffer depth; old gaps fall off the end
REFRESH_SECONDS = 2        # browser auto-refresh cadence

# The five fields the Alerter sends (asdict(GapAlert)); we display them in this order.
_FIELDS = ("query", "answer", "reason", "evidence")
_BADGE = {"unverified": "#c0392b", "abstain": "#d68910"}   # red / amber


def create_app() -> FastAPI:
    app = FastAPI(title="tiered_rag knowledge-gap board")
    alerts: Deque[dict] = deque(maxlen=MAX_ALERTS)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/alert")
    async def receive(request: Request):
        # Best-effort: never reject a stray payload with a 5xx — the gateway
        # fires this from a background task and must not see errors.
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            alerts.appendleft(dict(payload))   # newest first
        return {"ok": True}

    @app.get("/alerts")
    def list_alerts():
        return {"alerts": list(alerts)}

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return _render(list(alerts))

    return app


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _card(a: dict) -> str:
    kind = str(a.get("kind", "unknown"))
    color = _BADGE.get(kind, "#566573")
    rows = "".join(
        f'<tr><th>{f}</th><td>{_esc(a.get(f))}</td></tr>'
        for f in _FIELDS if a.get(f)
    )
    return (
        f'<div class="card">'
        f'<span class="badge" style="background:{color}">{_esc(kind)}</span>'
        f'<table>{rows}</table></div>'
    )


def _render(alerts: list[dict]) -> str:
    if alerts:
        body = "".join(_card(a) for a in alerts)
    else:
        body = '<p class="empty">No gaps yet — the bot has answered everything confidently.</p>'
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{REFRESH_SECONDS}">
<title>Knowledge-Gap Board</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 820px; color: #1c2833; }}
  h1 {{ font-size: 1.3rem; }}
  .meta {{ color: #7f8c8d; font-size: .85rem; margin-bottom: 1.5rem; }}
  .card {{ border: 1px solid #d5d8dc; border-radius: 8px; padding: 1rem 1.2rem; margin-bottom: 1rem; }}
  .badge {{ color: #fff; padding: .15rem .6rem; border-radius: 999px; font-size: .75rem;
            text-transform: uppercase; letter-spacing: .04em; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: .8rem; }}
  th {{ text-align: left; width: 90px; vertical-align: top; color: #566573; font-weight: 600;
        padding: .25rem .5rem .25rem 0; text-transform: capitalize; }}
  td {{ padding: .25rem 0; white-space: pre-wrap; }}
  .empty {{ color: #7f8c8d; }}
</style></head>
<body>
  <h1>🚩 Knowledge-Gap Operator Board</h1>
  <p class="meta">Live feed of answers the bot could not ground. Auto-refreshes every {REFRESH_SECONDS}s · showing {len(alerts)} (max {MAX_ALERTS}).</p>
  {body}
</body></html>"""


app = create_app()
