"""Alita run records and MVP plan workflow."""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alita.context import ContextPack, build_context_pack
from alita.policy import (
    PolicyDecision,
    PolicyAction,
    WriteIntent,
    WritePolicyConfig,
    evaluate_write_intent,
)
from alita.tools import AlitaToolRegistry, write_intent_from_plan
from core.operation.models import PatchOperation, PlanResult
from storage.manager import StorageManager


RUNS_DIR = "alita/runs"


@dataclass(frozen=True)
class AlitaRunResult:
    """
    Result of an Alita MVP run.
    """

    run_id: str
    run_dir: Path
    context_pack: ContextPack
    plan_result: PlanResult | None
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None

    @property
    def success(self) -> bool:
        return self.plan_result is not None and self.plan_result.is_valid

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "success": self.success,
            "context_pack": self.context_pack.to_dict(),
            "plan_result": (
                self.plan_result.model_dump(mode="json") if self.plan_result is not None else None
            ),
            "write_intent": self.write_intent.to_dict() if self.write_intent is not None else None,
            "policy_decision": (
                self.policy_decision.to_dict() if self.policy_decision is not None else None
            ),
        }


def run_plan_mvp(
    project_path: Path,
    task: str,
    *,
    patch_source: str,
    active_file: Path | None = None,
    write_policy: WritePolicyConfig | None = None,
) -> AlitaRunResult:
    """
    Create an Alita run record, build context, and plan a supplied patch.

    V0 deliberately stops at Voyager plan. It does not call a model and does not
    apply source changes.
    """
    project_path = project_path.resolve()
    policy = write_policy or WritePolicyConfig()
    run_id = _new_run_id()
    run_dir = _run_dir(project_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)

    context_pack = build_context_pack(
        project_path,
        task,
        mode="agent",
        active_file=active_file,
    )
    _write_json(run_dir / "context-pack.json", context_pack.to_dict())

    patch_text = _read_patch_text(patch_source)
    patch_path = run_dir / "patch-attempt-1.diff"
    patch_path.write_text(patch_text, encoding="utf-8")

    operation = PatchOperation(
        patch=patch_text,
        description=f"alita:{run_id}:{patch_source}",
    )
    registry = AlitaToolRegistry(
        project_path,
        run_id=run_id,
        write_policy=policy,
    )
    _write_json(
        run_dir / "run.json",
        _run_record(run_id, project_path, task, patch_source, context_pack, policy),
    )

    plan_tool_result = registry.plan_patch(operation)
    _write_json(run_dir / "tool-call-plan-patch-1.json", plan_tool_result.to_dict())
    if plan_tool_result.payload is not None:
        result = PlanResult.model_validate(plan_tool_result.payload)
    else:
        result = PlanResult(
            operation=operation,
            affected_files=[],
            violations=plan_tool_result.errors,
            is_valid=False,
        )

    _write_json(run_dir / "plan-result-1.json", result.model_dump(mode="json"))
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None
    if result.is_valid:
        write_intent = write_intent_from_plan(run_id, result, patch_text)
        policy_decision = evaluate_write_intent(policy, write_intent)
        _write_json(run_dir / "write-intent.json", write_intent.to_dict())
        _write_json(run_dir / "policy-decision.json", policy_decision.to_dict())

    _write_text(run_dir / "summary.md", _summary(run_id, task, result))
    if result.is_valid and (
        policy_decision is None or policy_decision.action != PolicyAction.DENY
    ):
        StorageManager(project_path).save_pending_plan(operation)
    _write_json(
        run_dir / "run.json",
        _run_record(
            run_id,
            project_path,
            task,
            patch_source,
            context_pack,
            policy,
            status="plan_valid" if result.is_valid else "plan_rejected",
            write_intent=write_intent,
            policy_decision=policy_decision,
        ),
    )

    return AlitaRunResult(
        run_id=run_id,
        run_dir=run_dir,
        context_pack=context_pack,
        plan_result=result,
        write_intent=write_intent,
        policy_decision=policy_decision,
    )


def _run_dir(project_path: Path, run_id: str) -> Path:
    return project_path / ".voyager" / RUNS_DIR / run_id


def _new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _read_patch_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _run_record(
    run_id: str,
    project_path: Path,
    task: str,
    patch_source: str,
    context_pack: ContextPack,
    policy: WritePolicyConfig,
    status: str = "planning",
    write_intent: WriteIntent | None = None,
    policy_decision: PolicyDecision | None = None,
) -> dict[str, Any]:
    record = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_path": str(project_path),
        "task": task,
        "mode": "agent",
        "status": status,
        "model": None,
        "runtime": {"name": "manual-patch-mvp", "adk_enabled": False},
        "policy": {"write_policy": policy.to_dict()},
        "patch_source": patch_source,
        "context_pack": "context-pack.json",
        "context_graph": context_pack.graph,
    }
    if write_intent is not None:
        record["write_intent"] = "write-intent.json"
    if policy_decision is not None:
        record["policy_decision"] = "policy-decision.json"
    return record


def _summary(run_id: str, task: str, result: PlanResult) -> str:
    status = "valid" if result.is_valid else "rejected"
    lines = [
        f"# Alita Run {run_id}",
        "",
        f"Task: {task}",
        f"Plan status: {status}",
        "",
    ]
    if result.is_valid:
        lines.append("Affected files:")
        lines.extend(f"- {file_path}" for file_path in result.affected_files)
    else:
        lines.append("Violations:")
        lines.extend(f"- {item.get('type', 'error')}: {item.get('message', item)}" for item in result.violations)
    lines.append("")
    return "\n".join(lines)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
