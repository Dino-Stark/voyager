"""Alita tool registry and policy wrappers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alita.policy import (
    PolicyAction,
    PolicyDecision,
    WriteIntent,
    WritePolicyConfig,
    evaluate_write_intent,
)
from core.operation.models import ApplyResult, PatchOperation, PlanResult
from core.server.client import VoyagerServerClient


@dataclass(frozen=True)
class ToolCallResult:
    """
    Stable result envelope for Alita tool calls.
    """

    tool_name: str
    executed: bool
    payload: dict[str, Any] | None = None
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.executed and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "executed": self.executed,
            "ok": self.ok,
            "payload": self.payload,
            "write_intent": (
                self.write_intent.to_dict() if self.write_intent is not None else None
            ),
            "policy_decision": (
                self.policy_decision.to_dict() if self.policy_decision is not None else None
            ),
            "errors": self.errors,
        }


class AlitaToolRegistry:
    """
    Policy-aware tool facade for Alita and future ADK tool calls.
    """

    def __init__(
        self,
        project_path: Path,
        *,
        run_id: str,
        write_policy: WritePolicyConfig | None = None,
        client: VoyagerServerClient | None = None,
    ) -> None:
        self.project_path = project_path.resolve()
        self.run_id = run_id
        self.write_policy = write_policy or WritePolicyConfig()
        self.client = client or VoyagerServerClient(self.project_path)

    def plan_patch(self, operation: PatchOperation) -> ToolCallResult:
        """
        Plan a patch through Voyager. Planning does not write source files.
        """
        try:
            result = PlanResult.model_validate(self.client.plan(operation))
        except Exception as exc:
            return ToolCallResult(
                tool_name="voyager_plan_patch",
                executed=True,
                errors=[{"type": "plan_failed", "message": str(exc), "action": "error"}],
            )

        return ToolCallResult(
            tool_name="voyager_plan_patch",
            executed=True,
            payload=result.model_dump(mode="json"),
        )

    def apply_patch(
        self,
        operation: PatchOperation,
        *,
        plan_result: PlanResult,
        patch_text: str,
        user_approved: bool = False,
    ) -> ToolCallResult:
        """
        Apply a patch only when the write policy allows it.
        """
        intent = write_intent_from_plan(self.run_id, plan_result, patch_text)
        decision = evaluate_write_intent(self.write_policy, intent)
        if decision.action == PolicyAction.ASK_USER and user_approved:
            decision = PolicyDecision(
                action=PolicyAction.ALLOW,
                reason="User approved this write after policy requested confirmation.",
                policy_mode=decision.policy_mode,
                matched_rules=[*decision.matched_rules, "human_approved"],
            )
        if decision.action != PolicyAction.ALLOW:
            return ToolCallResult(
                tool_name="voyager_apply_patch",
                executed=False,
                write_intent=intent,
                policy_decision=decision,
                errors=[
                    {
                        "type": "policy_blocked",
                        "message": decision.reason,
                        "action": decision.action.value,
                    }
                ],
            )

        try:
            result = ApplyResult.model_validate(self.client.apply(operation))
        except Exception as exc:
            return ToolCallResult(
                tool_name="voyager_apply_patch",
                executed=True,
                write_intent=intent,
                policy_decision=decision,
                errors=[{"type": "apply_failed", "message": str(exc), "action": "error"}],
            )

        return ToolCallResult(
            tool_name="voyager_apply_patch",
            executed=True,
            payload=result.model_dump(mode="json"),
            write_intent=intent,
            policy_decision=decision,
        )


def write_intent_from_plan(
    run_id: str,
    result: PlanResult,
    patch_text: str,
) -> WriteIntent:
    """
    Create the write intent that would be needed to apply a valid plan.
    """
    return WriteIntent(
        tool_name="voyager_apply_patch",
        operation_type="patch_apply",
        affected_files=result.affected_files,
        patch_summary=f"{len(result.affected_files)} file(s) affected",
        risk_level="medium",
        validation_result="valid" if result.is_valid else "invalid",
        requested_by="agent",
        run_id=run_id,
        patch_bytes=len(patch_text.encode("utf-8")),
    )
