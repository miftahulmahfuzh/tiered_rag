from __future__ import annotations

from .client import LLMClient
from .usage import LLMResponse


class WorkerHealth:
    def __init__(self, n: int):
        self.failures = [0] * n

    def order(self) -> list[int]:
        # fewest failures first; stable so a fresh pool keeps declared order
        return sorted(range(len(self.failures)), key=lambda i: self.failures[i])

    def record_success(self, i: int) -> None:
        self.failures[i] = 0

    def record_failure(self, i: int) -> None:
        self.failures[i] += 1


class FailoverLLM:
    """Ordered worker pool: try the healthiest worker, fail over to the next on any error."""

    def __init__(self, workers: list[LLMClient]):
        if not workers:
            raise ValueError("FailoverLLM needs at least one worker")
        self.workers = workers
        self.health = WorkerHealth(len(workers))

    def complete(self, system: str, user: str, *, temperature: float = 0.0) -> LLMResponse:
        last_err: Exception | None = None
        for i in self.health.order():
            try:
                resp = self.workers[i].complete(system, user, temperature=temperature)
                self.health.record_success(i)
                return resp
            except Exception as e:  # transport/connection error -> try the next worker
                self.health.record_failure(i)
                last_err = e
        raise last_err  # all workers down
