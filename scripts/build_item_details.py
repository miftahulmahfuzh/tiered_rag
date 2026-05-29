"""Generate xlsx/item_details.xlsx — a small structured item catalog for Tier-2
structured extraction + price lookups. Run once to (re)produce the committed artifact:

    python scripts/build_item_details.py
"""
from pathlib import Path

import pandas as pd

ITEMS = [
    {"item_id": 1,  "sku": "SKU-01", "name": "Starter Sword",    "price_usd": 4.99,  "rarity": "Common",    "stock": 999, "category": "Weapon"},
    {"item_id": 2,  "sku": "SKU-02", "name": "Iron Shield",      "price_usd": 6.49,  "rarity": "Common",    "stock": 540, "category": "Armor"},
    {"item_id": 3,  "sku": "SKU-03", "name": "Healing Potion",   "price_usd": 1.99,  "rarity": "Common",    "stock": 999, "category": "Consumable"},
    {"item_id": 4,  "sku": "SKU-04", "name": "Steel Greaves",    "price_usd": 8.99,  "rarity": "Uncommon",  "stock": 320, "category": "Armor"},
    {"item_id": 5,  "sku": "SKU-05", "name": "Flame Bow",        "price_usd": 12.99, "rarity": "Rare",      "stock": 150, "category": "Weapon"},
    {"item_id": 6,  "sku": "SKU-06", "name": "Frost Staff",      "price_usd": 14.49, "rarity": "Rare",      "stock": 120, "category": "Weapon"},
    {"item_id": 7,  "sku": "SKU-07", "name": "Dragon Skin",      "price_usd": 19.99, "rarity": "Legendary", "stock": 42,  "category": "Cosmetic"},
    {"item_id": 8,  "sku": "SKU-08", "name": "Phoenix Wings",    "price_usd": 24.99, "rarity": "Legendary", "stock": 30,  "category": "Cosmetic"},
    {"item_id": 9,  "sku": "SKU-09", "name": "Shadow Cloak",     "price_usd": 17.49, "rarity": "Epic",      "stock": 65,  "category": "Cosmetic"},
    {"item_id": 10, "sku": "SKU-10", "name": "Mana Crystal",     "price_usd": 3.49,  "rarity": "Uncommon",  "stock": 800, "category": "Consumable"},
    {"item_id": 11, "sku": "SKU-11", "name": "Titan Gauntlets",  "price_usd": 21.99, "rarity": "Epic",      "stock": 55,  "category": "Armor"},
    {"item_id": 12, "sku": "SKU-12", "name": "Golden Compass",   "price_usd": 9.99,  "rarity": "Rare",      "stock": 200, "category": "Accessory"},
]


def main() -> None:
    out = Path("xlsx/item_details.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["item_id", "sku", "name", "price_usd", "rarity", "stock", "category"]
    pd.DataFrame(ITEMS, columns=cols).to_excel(out, index=False)
    print(f"wrote {len(ITEMS)} items to {out}")


if __name__ == "__main__":
    main()
