import pandas as pd


def load_knowledge_base(path: str) -> list[dict]:
    df = pd.read_excel(path)
    return df.to_dict(orient="records")
