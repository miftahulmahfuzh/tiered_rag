from pydantic import BaseModel, Field


class TierSelection(BaseModel):
    tier: int = Field(ge=1, le=3)
    reason: str = ""
    plan: str | None = None
