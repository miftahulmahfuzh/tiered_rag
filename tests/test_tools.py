from tiered_rag.tools.registry import TOOLS, run_tool

CATALOG = {
    "7": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
          "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
    "SKU-07": {"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
               "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"},
}


def test_registry_has_the_four_brief_tools():
    assert {"check_order_status", "check_item_price",
            "check_account_tier", "get_item_details_from_xlsx"} <= set(TOOLS)


def test_check_order_status_is_deterministic():
    a = run_tool("check_order_status", {"order_id": "12345"}, CATALOG)
    b = run_tool("check_order_status", {"order_id": "12345"}, CATALOG)
    assert a == b and a["status"] in {"processing", "shipped", "delivered", "cancelled"}


def test_get_item_details_hit_and_miss():
    hit = run_tool("get_item_details_from_xlsx", {"item_id": "SKU-07"}, CATALOG)
    assert hit["name"] == "Dragon Skin" and hit["rarity"] == "Legendary"
    miss = run_tool("get_item_details_from_xlsx", {"item_id": "SKU-42"}, CATALOG)
    assert "error" in miss


def test_check_item_price_reads_catalog():
    assert run_tool("check_item_price", {"item_id": "7"}, CATALOG)["price_usd"] == 19.99


def test_unknown_tool_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        run_tool("no_such_tool", {}, CATALOG)


def test_item_tools_accept_sku_and_synonym_keys():
    # The planner often keys item lookups as 'sku' or 'item_id_or_sku' (the menu
    # wording invites it); the tool must resolve them, not blow up on args["item_id"].
    for args in ({"sku": "SKU-07"}, {"item_id_or_sku": "SKU-07"}, {"item_id": "SKU-07"}):
        assert run_tool("get_item_details_from_xlsx", args, CATALOG)["name"] == "Dragon Skin"
    assert run_tool("check_item_price", {"sku": "7"}, CATALOG)["price_usd"] == 19.99


def test_item_tool_with_no_recognizable_id_key_raises():
    import pytest
    with pytest.raises(KeyError):
        run_tool("get_item_details_from_xlsx", {"foo": "bar"}, CATALOG)
