"""Base runtime contract for Alita agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from alita.context import ContextPack
from alita.runtime.events import AlitaEvent
from alita.runtime.providers import ProviderProfile


@dataclass(frozen=True)
class AlitaRuntimeRequest:
    """
    Input passed from the Run Coordinator to a runtime backend.
    """

    project_path: Path
    run_id: str
    task: str
    context_pack: ContextPack
    provider: ProviderProfile
    model: str | None = None
    patch_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlitaRuntimeResult:
    """
    Runtime output before Voyager validation.
    """

    runtime_name: str
    run_id: str
    success: bool
    patch_text: str | None = None
    raw_response: str | None = None
    events: list[AlitaEvent] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_name": self.runtime_name,
            "run_id": self.run_id,
            "success": self.success,
            "patch_text": self.patch_text,
            "raw_response": self.raw_response,
            "events": [item.to_dict() for item in self.events],
            "errors": self.errors,
        }


class AlitaRuntime(Protocol):
    """
    Runtime backend contract. ADK is one implementation, not Alita's core.
    """

    name: str

    def run(self, request: AlitaRuntimeRequest) -> AlitaRuntimeResult:
        """
        Produce a patch proposal or a structured runtime failure.
        """
