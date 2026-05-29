from .router import Router


def evaluate(router: Router, dataset: list[dict]) -> dict:
    records = []
    correct = 0
    per_cat: dict[str, list[int]] = {}  # category -> [correct, total]
    confusion: dict[tuple[int, int], int] = {}
    for item in dataset:
        sel = router.route(item["q"])
        ok = sel.tier == item["expected_tier"]
        correct += int(ok)
        cat = item.get("category", "uncategorized")
        per_cat.setdefault(cat, [0, 0])
        per_cat[cat][0] += int(ok)
        per_cat[cat][1] += 1
        key = (item["expected_tier"], sel.tier)
        confusion[key] = confusion.get(key, 0) + 1
        records.append({
            "q": item["q"],
            "category": cat,
            "expected_tier": item["expected_tier"],
            "predicted_tier": sel.tier,
            "correct": ok,
            "reason": sel.reason,
        })
    total = len(dataset)
    return {
        "accuracy": (correct / total) if total else 0.0,
        "per_category": {c: (v[0] / v[1] if v[1] else 0.0) for c, v in per_cat.items()},
        "confusion": confusion,
        "records": records,
    }
