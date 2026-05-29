from .retrieval import Retriever


def evaluate(retriever: Retriever, dataset: list[dict]) -> dict:
    records, ood_total, ood_abstained, ans_total, ans_abstained = [], 0, 0, 0, 0
    for item in dataset:
        res = retriever.retrieve(item["q"])
        records.append({
            "q": item["q"],
            "should_answer": item["should_answer"],
            "abstained": res.abstain,
            "score": res.score,
        })
        if item["should_answer"]:
            ans_total += 1
            ans_abstained += int(res.abstain)
        else:
            ood_total += 1
            ood_abstained += int(res.abstain)
    return {
        "abstention_rate": (ood_abstained / ood_total) if ood_total else 0.0,
        "false_abstention_rate": (ans_abstained / ans_total) if ans_total else 0.0,
        "records": records,
    }
