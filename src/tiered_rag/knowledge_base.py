import pandas as pd


def load_knowledge_base(path: str) -> list[dict]:
    df = pd.read_excel(path)
    return df.to_dict(orient="records")


def load_item_details(path: str) -> list[dict]:
    df = pd.read_excel(path)
    return df.to_dict(orient="records")


def catalog_index(rows: list[dict]) -> dict[str, dict]:
    """Lookup keyed by str(item_id) and by sku (both upper- and lower-cased)."""
    idx: dict[str, dict] = {}
    for r in rows:
        idx[str(r["item_id"])] = r
        sku = str(r["sku"])
        idx[sku.upper()] = r
        idx[sku.lower()] = r
    return idx
