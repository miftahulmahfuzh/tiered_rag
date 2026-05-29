from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import httpx

logger = logging.getLogger("tiered_rag.alerts")


@dataclass
class GapAlert:
    """A knowledge-gap signal worth a human's attention."""
    kind: str                       # "abstain" (no sources) | "unverified" (answer not supported)
    query: str
    answer: str                     # the abstained / rejected reply (for human context)
    evidence: str = ""              # final_input_context the answer was (not) grounded in
    reason: str = ""                # verifier reason, when kind == "unverified"


class Alerter:
    """In-memory collector + structured logger for knowledge-gap alerts.

    Optionally POSTs each alert to a webhook (best-effort; failures are swallowed so
    alerting can never break a user request). Designed to be dispatched asynchronously
    (FastAPI BackgroundTasks) from the gateway.
    """

    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = webhook_url
        self.alerts: list[GapAlert] = []

    def alert(self, gap: GapAlert) -> None:
        self.alerts.append(gap)
        logger.warning("knowledge_gap %s", json.dumps(asdict(gap)))
        if self.webhook_url:
            try:
                httpx.post(self.webhook_url, json=asdict(gap), timeout=2.0)
            except Exception:  # best-effort; alerting must never raise into the request path
                logger.exception("knowledge-gap webhook failed")
