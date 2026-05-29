from tiered_rag.knowledge_base import catalog_index, load_item_details


def test_loads_catalog_rows():
    rows = load_item_details("xlsx/item_details.xlsx")
    assert len(rows) >= 10
    first = rows[0]
    assert {"item_id", "sku", "name", "price_usd", "rarity", "stock", "category"} <= first.keys()
    assert len({r["item_id"] for r in rows}) == len(rows)  # unique ids


def test_catalog_index_keys_by_id_and_sku():
    rows = [{"item_id": 7, "sku": "SKU-07", "name": "Dragon Skin",
             "price_usd": 19.99, "rarity": "Legendary", "stock": 42, "category": "Cosmetic"}]
    idx = catalog_index(rows)
    assert idx["7"]["name"] == "Dragon Skin"
    assert idx["SKU-07"]["name"] == "Dragon Skin"   # sku lookup, case-normalized
    assert idx["sku-07"]["name"] == "Dragon Skin"
