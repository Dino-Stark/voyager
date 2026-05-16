"""CLI-first Alita tool command helpers."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from alita.policy import (
    PolicyAction,
    PolicyDecision,
    WriteIntent,
    WritePolicyConfig,
    WritePolicyMode,
    evaluate_write_intent,
)
from alita.tools import AlitaToolRegistry, ToolCallResult, write_intent_from_plan
from core.operation.models import PatchOperation, PlanResult
from storage.manager import StorageManager


ApprovalCallback = Callable[[PolicyDecision, WriteIntent], bool]


@dataclass(frozen=True)
class AlitaToolCommandResult:
    """
    Machine-readable result for an Alita CLI tool command.
    """

    success: bool
    run_id: str | None = None
    plan_result: PlanResult | None = None
    plan_tool_call: ToolCallResult | None = None
    apply_tool_call: ToolCallResult | None = None
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None
    pending_plan_saved: bool = False
    pending_plan_cleared: bool = False
    errors: list[dict[str, Any]] | None = None
    status: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "run_id": self.run_id,
            "plan_result": (
                self.plan_result.model_dump(mode="json")
                if self.plan_result is not None
                else None
            ),
            "plan_tool_call": (
                self.plan_tool_call.to_dict()
                if self.plan_tool_call is not None
                else None
            ),
            "apply_tool_call": (
                self.apply_tool_call.to_dict()
                if self.apply_tool_call is not None
                else None
            ),
            "write_intent": (
                self.write_intent.to_dict() if self.write_intent is not None else None
            ),
            "policy_decision": (
                self.policy_decision.to_dict()
                if self.policy_decision is not None
                else None
            ),
            "pending_plan_saved": self.pending_plan_saved,
            "pending_plan_cleared": self.pending_plan_cleared,
            "errors": self.errors or [],
            "status": self.status,
        }


def build_write_policy(
    *,
    mode: WritePolicyMode = WritePolicyMode.MANUAL_CONFIRM,
    allow: tuple[str, ...] | list[str] | None = None,
    deny: tuple[str, ...] | list[str] | None = None,
    require_confirm: tuple[str, ...] | list[str] | None = None,
    fallback_action: PolicyAction = PolicyAction.ASK_USER,
) -> WritePolicyConfig:
    """
    Build a policy from CLI options while preserving defaults unless overridden.
    """
    default = WritePolicyConfig(mode=mode, fallback_action=fallback_action)
    return WritePolicyConfig(
        mode=mode,
        allow=list(allow or default.allow),
        deny=_merge_rules(default.deny, list(deny or [])),
        require_confirm=_merge_rules(
            default.require_confirm,
            list(require_confirm or []),
        ),
        max_files_changed=default.max_files_changed,
        max_patch_bytes=default.max_patch_bytes,
        fallback_action=fallback_action,
    )


def plan_patch_tool(
    project_path: Path,
    patch_sources: list[str],
    *,
    write_policy: WritePolicyConfig | None = None,
    run_id: str | None = None,
) -> AlitaToolCommandResult:
    """
    Plan a patch through the Alita tool registry and save a pending plan if usable.
    """
    project_path = project_path.resolve()
    policy = write_policy or WritePolicyConfig()
    current_run_id = run_id or _new_tool_run_id("tool-plan")
    try:
        operation = build_patch_operation(patch_sources, description_prefix="alita-tool")
    except Exception as exc:
        return AlitaToolCommandResult(
            success=False,
            run_id=current_run_id,
            errors=[_error("invalid_operation", str(exc))],
        )

    registry = AlitaToolRegistry(project_path, run_id=current_run_id, write_policy=policy)
    plan_call = registry.plan_patch(operation)
    plan_result = _plan_result_from_tool_call(plan_call, operation)

    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None
    pending_saved = False
    if plan_result.is_valid:
        write_intent = write_intent_from_plan(
            current_run_id,
            plan_result,
            patch_text_from_operation(operation),
        )
        policy_decision = evaluate_write_intent(policy, write_intent)
        if policy_decision.action != PolicyAction.DENY:
            StorageManager(project_path).save_pending_plan(operation)
            pending_saved = True

    return AlitaToolCommandResult(
        success=plan_result.is_valid,
        run_id=current_run_id,
        plan_result=plan_result,
        plan_tool_call=plan_call,
        write_intent=write_intent,
        policy_decision=policy_decision,
        pending_plan_saved=pending_saved,
        errors=plan_call.errors,
    )


def apply_patch_tool(
    project_path: Path,
    *,
    write_policy: WritePolicyConfig | None = None,
    run_id: str | None = None,
    user_approved: bool = False,
    approval_callback: ApprovalCallback | None = None,
) -> AlitaToolCommandResult:
    """
    Apply the current pending patch through the Alita policy wrapper.
    """
    project_path = project_path.resolve()
    policy = write_policy or WritePolicyConfig()
    current_run_id = run_id or _new_tool_run_id("tool-apply")
    storage = StorageManager(project_path)
    data = storage.load_pending_plan()
    if data is None:
        return AlitaToolCommandResult(
            success=False,
            run_id=current_run_id,
            errors=[_error("no_pending_plan", "No pending plan found.")],
        )

    try:
        operation = PatchOperation.model_validate(data)
    except Exception as exc:
        return AlitaToolCommandResult(
            success=False,
            run_id=current_run_id,
            errors=[_error("invalid_pending_plan", str(exc))],
        )

    registry = AlitaToolRegistry(project_path, run_id=current_run_id, write_policy=policy)
    plan_call = registry.plan_patch(operation)
    plan_result = _plan_result_from_tool_call(plan_call, operation)
    apply_call = registry.apply_patch(
        operation,
        plan_result=plan_result,
        patch_text=patch_text_from_operation(operation),
        user_approved=user_approved,
    )

    if (
        apply_call.policy_decision is not None
        and apply_call.policy_decision.action == PolicyAction.ASK_USER
        and approval_callback is not None
        and apply_call.write_intent is not None
        and approval_callback(apply_call.policy_decision, apply_call.write_intent)
    ):
        apply_call = registry.apply_patch(
            operation,
            plan_result=plan_result,
            patch_text=patch_text_from_operation(operation),
            user_approved=True,
        )

    success = (
        apply_call.executed
        and apply_call.payload is not None
        and apply_call.payload.get("success") is True
    )
    pending_cleared = False
    if success:
        storage.clear_pending_plan()
        pending_cleared = True

    return AlitaToolCommandResult(
        success=success,
        run_id=current_run_id,
        plan_result=plan_result,
        plan_tool_call=plan_call,
        apply_tool_call=apply_call,
        write_intent=apply_call.write_intent,
        policy_decision=apply_call.policy_decision,
        pending_plan_cleared=pending_cleared,
        errors=apply_call.errors,
    )


def status_tool(project_path: Path) -> AlitaToolCommandResult:
    """
    Return local Alita tool status without starting the Voyager server.
    """
    project_path = project_path.resolve()
    storage = StorageManager(project_path)
    pending = storage.load_pending_plan()
    status = {
        "project_path": str(project_path),
        "pending_plan": _pending_plan_status(pending),
        "latest_run": _latest_run_status(project_path),
    }
    return AlitaToolCommandResult(success=True, status=status)


def build_patch_operation(
    patch_sources: list[str],
    *,
    description_prefix: str | None = None,
) -> PatchOperation:
    """
    Build a patch operation from one or more files or a single stdin source.
    """
    if not patch_sources:
        raise ValueError("At least one patch source is required.")
    if patch_sources.count("-") > 1:
        raise ValueError("'patch' accepts stdin '-' at most once.")
    patch_texts = [
        sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
        for source in patch_sources
    ]
    description = ", ".join(patch_sources)
    if description_prefix:
        description = f"{description_prefix}:{description}"
    return PatchOperation(
        patch=patch_texts[0],
        patches=patch_texts[1:],
        description=description,
    )


def patch_text_from_operation(operation: PatchOperation) -> str:
    """
    Return the full patch text represented by an operation.
    """
    return "\n".join(operation.patch_texts())


def _plan_result_from_tool_call(
    tool_call: ToolCallResult,
    operation: PatchOperation,
) -> PlanResult:
    if tool_call.payload is not None:
        return PlanResult.model_validate(tool_call.payload)
    return PlanResult(
        operation=operation,
        affected_files=[],
        violations=tool_call.errors,
        is_valid=False,
    )


def _pending_plan_status(data: dict[str, Any] | None) -> dict[str, Any]:
    if data is None:
        return {"exists": False}
    try:
        operation = PatchOperation.model_validate(data)
    except Exception as exc:
        return {"exists": True, "valid": False, "error": str(exc)}
    return {
        "exists": True,
        "valid": True,
        "operation": operation.op.value,
        "description": operation.description,
        "patch_count": len(operation.patch_texts()),
    }


def _latest_run_status(project_path: Path) -> dict[str, Any] | None:
    runs_dir = project_path / ".voyager" / "alita" / "runs"
    if not runs_dir.exists():
        return None
    run_records = [
        path / "run.json"
        for path in runs_dir.iterdir()
        if path.is_dir() and (path / "run.json").exists()
    ]
    if not run_records:
        return None
    latest = max(run_records, key=lambda path: path.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"run_dir": str(latest.parent), "valid": False, "error": str(exc)}
    return {
        "run_id": data.get("run_id"),
        "status": data.get("status"),
        "task": data.get("task"),
        "run_dir": str(latest.parent),
    }


def _new_tool_run_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{prefix}-{uuid.uuid4().hex[:8]}"


def _error(error_type: str, message: str) -> dict[str, Any]:
    return {"type": error_type, "message": message, "action": "error"}


def _merge_rules(default_rules: list[str], extra_rules: list[str]) -> list[str]:
    rules: list[str] = []
    for rule in [*default_rules, *extra_rules]:
        if rule not in rules:
            rules.append(rule)
    return rules
