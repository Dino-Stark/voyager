"""Alita agent run coordinator."""

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
    PolicyAction,
    PolicyDecision,
    WriteIntent,
    WritePolicyConfig,
    evaluate_write_intent,
)
from alita.runtime.adk_runtime import AdkRuntimeAdapter
from alita.runtime.base import AlitaRuntime, AlitaRuntimeRequest, AlitaRuntimeResult
from alita.runtime.events import AlitaEvent, event
from alita.runtime.manual_runtime import ManualPatchRuntime
from alita.runtime.providers import ProviderProfile, resolve_provider_profile
from alita.tools import AlitaToolRegistry, ToolCallResult, write_intent_from_plan
from core.operation.models import PatchOperation, PlanResult
from storage.manager import StorageManager


AGENT_RUNS_DIR = "alita/runs"


@dataclass(frozen=True)
class AlitaAgentRunResult:
    """
    Result of a runtime-backed Alita agent run.
    """

    run_id: str
    run_dir: Path
    context_pack: ContextPack
    runtime_result: AlitaRuntimeResult
    plan_result: PlanResult | None = None
    plan_tool_call: ToolCallResult | None = None
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None

    @property
    def success(self) -> bool:
        return (
            self.runtime_result.success
            and self.plan_result is not None
            and self.plan_result.is_valid
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "success": self.success,
            "context_pack": self.context_pack.to_dict(),
            "runtime_result": self.runtime_result.to_dict(),
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
            "write_intent": (
                self.write_intent.to_dict() if self.write_intent is not None else None
            ),
            "policy_decision": (
                self.policy_decision.to_dict()
                if self.policy_decision is not None
                else None
            ),
        }


def run_agent_once(
    project_path: Path,
    task: str,
    *,
    runtime_name: str = "manual",
    provider_name: str = "gemini",
    model: str | None = None,
    provider_base_url: str | None = None,
    provider_api_key_env: str | None = None,
    patch_source: str | None = None,
    active_file: Path | None = None,
    write_policy: WritePolicyConfig | None = None,
    runtime: AlitaRuntime | None = None,
) -> AlitaAgentRunResult:
    """
    Run one Alita runtime turn, plan the produced patch, and stop before apply.
    """
    project_path = project_path.resolve()
    policy = write_policy or WritePolicyConfig()
    provider = resolve_provider_profile(
        provider_name,
        model=model,
        base_url=provider_base_url,
        api_key_env=provider_api_key_env,
    )
    run_id = _new_run_id(runtime_name)
    run_dir = _run_dir(project_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)

    context_pack = build_context_pack(
        project_path,
        task,
        mode="agent",
        active_file=active_file,
    )
    _write_json(run_dir / "context-pack.json", context_pack.to_dict())

    patch_text = _read_patch_text(patch_source) if patch_source is not None else None
    runtime_adapter = runtime or create_runtime(runtime_name)
    runtime_request = AlitaRuntimeRequest(
        project_path=project_path,
        run_id=run_id,
        task=task,
        context_pack=context_pack,
        provider=provider,
        model=model,
        patch_text=patch_text,
    )
    _write_json(
        run_dir / "run.json",
        _run_record(
            run_id,
            project_path,
            task,
            runtime_name,
            provider,
            policy,
            context_pack,
        ),
    )

    runtime_result = runtime_adapter.run(runtime_request)
    _write_json(run_dir / "runtime-result.json", runtime_result.to_dict())
    _write_events(run_dir / "events.jsonl", runtime_result.events)

    plan_result: PlanResult | None = None
    plan_tool_call: ToolCallResult | None = None
    write_intent: WriteIntent | None = None
    policy_decision: PolicyDecision | None = None
    status = "runtime_failed"
    if runtime_result.patch_text:
        patch_attempt = run_dir / "patch-attempt-1.diff"
        patch_attempt.write_text(runtime_result.patch_text, encoding="utf-8")
        operation = PatchOperation(
            patch=runtime_result.patch_text,
            description=f"alita-agent:{run_id}:{runtime_name}",
        )
        registry = AlitaToolRegistry(
            project_path,
            run_id=run_id,
            write_policy=policy,
        )
        plan_tool_call = registry.plan_patch(operation)
        _write_json(run_dir / "tool-call-plan-patch-1.json", plan_tool_call.to_dict())
        if plan_tool_call.payload is not None:
            plan_result = PlanResult.model_validate(plan_tool_call.payload)
        else:
            plan_result = PlanResult(
                operation=operation,
                affected_files=[],
                violations=plan_tool_call.errors,
                is_valid=False,
            )
        _write_json(run_dir / "plan-result-1.json", plan_result.model_dump(mode="json"))
        if plan_result.is_valid:
            status = "plan_valid"
            write_intent = write_intent_from_plan(
                run_id,
                plan_result,
                runtime_result.patch_text,
            )
            policy_decision = evaluate_write_intent(policy, write_intent)
            _write_json(run_dir / "write-intent.json", write_intent.to_dict())
            _write_json(run_dir / "policy-decision.json", policy_decision.to_dict())
            if policy_decision.action != PolicyAction.DENY:
                StorageManager(project_path).save_pending_plan(operation)
        else:
            status = "plan_rejected"

    _write_text(run_dir / "summary.md", _summary(run_id, task, runtime_result, plan_result))
    _write_json(
        run_dir / "run.json",
        _run_record(
            run_id,
            project_path,
            task,
            runtime_name,
            provider,
            policy,
            context_pack,
            status=status,
            runtime_result="runtime-result.json",
            write_intent=write_intent,
            policy_decision=policy_decision,
        ),
    )

    return AlitaAgentRunResult(
        run_id=run_id,
        run_dir=run_dir,
        context_pack=context_pack,
        runtime_result=runtime_result,
        plan_result=plan_result,
        plan_tool_call=plan_tool_call,
        write_intent=write_intent,
        policy_decision=policy_decision,
    )


def create_runtime(runtime_name: str) -> AlitaRuntime:
    """
    Create a runtime adapter by name.
    """
    if runtime_name == "manual":
        return ManualPatchRuntime()
    if runtime_name == "adk":
        return AdkRuntimeAdapter()
    raise ValueError(f"Unknown Alita runtime: {runtime_name}")


def _run_dir(project_path: Path, run_id: str) -> Path:
    return project_path / ".voyager" / AGENT_RUNS_DIR / run_id


def _new_run_id(runtime_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{runtime_name}-{uuid.uuid4().hex[:8]}"


def _read_patch_text(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _run_record(
    run_id: str,
    project_path: Path,
    task: str,
    runtime_name: str,
    provider: ProviderProfile,
    policy: WritePolicyConfig,
    context_pack: ContextPack,
    status: str = "runtime_running",
    runtime_result: str | None = None,
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
        "runtime": {"name": runtime_name, "adk_enabled": runtime_name == "adk"},
        "provider": provider.to_dict(),
        "policy": {"write_policy": policy.to_dict()},
        "context_pack": "context-pack.json",
        "context_graph": context_pack.graph,
        "events": "events.jsonl",
    }
    if runtime_result is not None:
        record["runtime_result"] = runtime_result
    if write_intent is not None:
        record["write_intent"] = "write-intent.json"
    if policy_decision is not None:
        record["policy_decision"] = "policy-decision.json"
    return record


def _summary(
    run_id: str,
    task: str,
    runtime_result: AlitaRuntimeResult,
    plan_result: PlanResult | None,
) -> str:
    lines = [
        f"# Alita Agent Run {run_id}",
        "",
        f"Task: {task}",
        f"Runtime: {runtime_result.runtime_name}",
        f"Runtime status: {'success' if runtime_result.success else 'failed'}",
        "",
    ]
    if runtime_result.errors:
        lines.append("Runtime errors:")
        lines.extend(
            f"- {item.get('type', 'error')}: {item.get('message', item)}"
            for item in runtime_result.errors
        )
        lines.append("")
    if plan_result is not None:
        lines.append(f"Plan status: {'valid' if plan_result.is_valid else 'rejected'}")
        if plan_result.is_valid:
            lines.append("Affected files:")
            lines.extend(f"- {file_path}" for file_path in plan_result.affected_files)
        else:
            lines.append("Violations:")
            lines.extend(
                f"- {item.get('type', 'error')}: {item.get('message', item)}"
                for item in plan_result.violations
            )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_events(path: Path, events: list[AlitaEvent]) -> None:
    lines = [json.dumps(item.to_dict(), ensure_ascii=False) for item in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
