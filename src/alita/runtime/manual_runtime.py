"""Manual patch runtime used for local tests and adapter smoke flows."""

from __future__ import annotations

from alita.runtime.base import AlitaRuntimeRequest, AlitaRuntimeResult
from alita.runtime.events import event


class ManualPatchRuntime:
    """
    Runtime that treats a provided patch as the model proposal.

    This keeps the agent coordinator testable before real model calls are
    enabled and mirrors the future runtime contract.
    """

    name = "manual"

    def run(self, request: AlitaRuntimeRequest) -> AlitaRuntimeResult:
        events = [
            event(
                "alita.runtime.started",
                request.run_id,
                message="Manual patch runtime started.",
                data={"runtime": self.name},
            )
        ]
        if not request.patch_text or not request.patch_text.strip():
            return AlitaRuntimeResult(
                runtime_name=self.name,
                run_id=request.run_id,
                success=False,
                events=[
                    *events,
                    event(
                        "alita.runtime.failed",
                        request.run_id,
                        message="Manual runtime requires patch text.",
                    ),
                ],
                errors=[
                    {
                        "type": "missing_patch",
                        "message": "Manual runtime requires --patch.",
                        "action": "provide_patch",
                    }
                ],
            )

        return AlitaRuntimeResult(
            runtime_name=self.name,
            run_id=request.run_id,
            success=True,
            patch_text=request.patch_text,
            raw_response=request.patch_text,
            events=[
                *events,
                event(
                    "alita.patch.generated",
                    request.run_id,
                    message="Manual patch proposal accepted.",
                    data={"patch_bytes": len(request.patch_text.encode("utf-8"))},
                ),
                event(
                    "alita.runtime.completed",
                    request.run_id,
                    message="Manual patch runtime completed.",
                ),
            ],
        )
