"""Alita runtime event primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class AlitaEvent:
    """
    Domain event emitted by Alita runtimes and translated from runtime backends.
    """

    event_type: str
    run_id: str
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "message": self.message,
            "data": self.data,
        }


def event(
    event_type: str,
    run_id: str,
    *,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> AlitaEvent:
    """
    Convenience factory that keeps event creation terse at call sites.
    """
    return AlitaEvent(
        event_type=event_type,
        run_id=run_id,
        message=message,
        data=data or {},
    )
