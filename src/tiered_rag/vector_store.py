from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams


@dataclass
class Hit:
    id: int | str
    score: float
    payload: dict


class QdrantStore:
    def __init__(self, client: QdrantClient, collection: str):
        self.client, self.collection = client, collection

    def recreate(self, dim: int):
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    def upsert(self, points: list[dict]):
        self.client.upsert(
            self.collection,
            points=[
                PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
                for p in points
            ],
        )

    def search(self, vector: list[float], limit: int = 3) -> list[Hit]:
        res = self.client.query_points(
            self.collection, query=vector, limit=limit
        ).points
        return [Hit(id=r.id, score=r.score, payload=r.payload) for r in res]
