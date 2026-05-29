from tiered_rag.knowledge_base import load_knowledge_base


def test_loads_twenty_qa_pairs():
    rows = load_knowledge_base("xlsx/knowledge_base.xlsx")
    assert len(rows) == 20
    first = rows[0]
    assert {"id", "question", "answer", "category"} <= first.keys()
    assert all(r["question"] and r["answer"] for r in rows)
    assert len({r["id"] for r in rows}) == 20  # unique ids
