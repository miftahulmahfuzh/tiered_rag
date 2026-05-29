from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

_STATUSES = ["processing", "shipped", "delivered", "cancelled"]
_TIERS = ["Bronze", "Silver", "Gold", "Platinum"]


def _bucket(key: str, n: int) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n


def check_order_status(order_id: str) -> dict:
    return {
        "order_id": order_id,
        "status": _STATUSES[_bucket("order:" + order_id, len(_STATUSES))],
        "tracking_number": f"TRK-{_bucket(order_id, 1_000_000):06d}",
    }


def check_account_tier(account_id: str) -> dict:
    return {"account_id": account_id,
            "account_tier": _TIERS[_bucket("acct:" + account_id, len(_TIERS))]}


def _lookup(item_id: str, catalog: dict) -> dict | None:
    return catalog.get(str(item_id).upper()) or catalog.get(str(item_id))


def check_item_price(item_id: str, catalog: dict) -> dict:
    row = _lookup(item_id, catalog)
    if not row:
        return {"error": "unknown item", "item_id": item_id}
    return {"item_id": item_id, "name": row["name"], "price_usd": row["price_usd"]}


def get_item_details_from_xlsx(item_id: str, catalog: dict) -> dict:
    row = _lookup(item_id, catalog)
    return dict(row) if row else {"error": "item not found", "item_id": item_id}


@dataclass
class Tool:
    name: str
    description: str
    run: Callable[[dict, dict], dict]


TOOLS: dict[str, Tool] = {
    "check_order_status": Tool(
        "check_order_status", "Look up the live status of an order by order_id.",
        lambda args, catalog: check_order_status(str(args["order_id"]))),
    "check_item_price": Tool(
        "check_item_price", "Get the current price of an item by item_id or sku.",
        lambda args, catalog: check_item_price(str(args["item_id"]), catalog)),
    "check_account_tier": Tool(
        "check_account_tier", "Get the membership tier for an account_id.",
        lambda args, catalog: check_account_tier(str(args["account_id"]))),
    "get_item_details_from_xlsx": Tool(
        "get_item_details_from_xlsx", "Get the full catalog record for an item_id or sku.",
        lambda args, catalog: get_item_details_from_xlsx(str(args["item_id"]), catalog)),
}


def run_tool(name: str, args: dict, catalog: dict) -> dict:
    return TOOLS[name].run(args, catalog)
