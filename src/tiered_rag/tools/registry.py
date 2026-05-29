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


# The planner keys item lookups inconsistently (item_id / sku / item_id_or_sku),
# nudged by the "item_id or sku" wording. Accept any of them so a correct call
# isn't misread as a failure.
_ITEM_KEYS = ("item_id", "sku", "item_id_or_sku", "item", "id")


def _item_arg(args: dict) -> str:
    for k in _ITEM_KEYS:
        if k in args:
            return str(args[k])
    raise KeyError(f"expected one of {_ITEM_KEYS}")


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
    args: str          # JSON-shaped hint of the call args, advertised to the planner
    description: str
    run: Callable[[dict, dict], dict]


TOOLS: dict[str, Tool] = {
    "check_order_status": Tool(
        "check_order_status", '{"order_id": "<id>"}',
        "Look up the live status of an order by order_id.",
        lambda args, catalog: check_order_status(str(args["order_id"]))),
    "check_item_price": Tool(
        "check_item_price", '{"item_id": "<id or sku>"}',
        "Get the current price of an item by item_id (a SKU is also accepted).",
        lambda args, catalog: check_item_price(_item_arg(args), catalog)),
    "check_account_tier": Tool(
        "check_account_tier", '{"account_id": "<id>"}',
        "Get the membership tier for an account_id.",
        lambda args, catalog: check_account_tier(str(args["account_id"]))),
    "get_item_details_from_xlsx": Tool(
        "get_item_details_from_xlsx", '{"item_id": "<id or sku>"}',
        "Get the full catalog record for an item_id (a SKU is also accepted).",
        lambda args, catalog: get_item_details_from_xlsx(_item_arg(args), catalog)),
}


def run_tool(name: str, args: dict, catalog: dict) -> dict:
    return TOOLS[name].run(args, catalog)
