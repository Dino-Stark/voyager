import json
import sys
from pathlib import Path

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alita.agent import run_agent_once
from alita.run import run_plan_mvp
from alita.policy import (
    PolicyAction,
    WriteIntent,
    WritePolicyConfig,
    WritePolicyMode,
    evaluate_write_intent,
)
from alita.tools import AlitaToolRegistry
from alita.tool_commands import build_write_policy
from alita.runtime.providers import default_provider_profiles, resolve_provider_profile
from core.operation.models import ApplyResult, PatchOperation, PlanResult
from storage.manager import StorageManager
from voyager_cmd.main import cli


PATCH_TEXT = """--- /dev/null
+++ b/src/main/java/com/acme/NewDTO.java
@@ -0,0 +1,3 @@
+package com.acme;
+public class NewDTO {}
+
"""


class FakeVoyagerServerClient:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path

    def plan(self, operation):
        return PlanResult(
            operation=operation,
            affected_files=["src/main/java/com/acme/NewDTO.java"],
            violations=[],
            is_valid=True,
        ).model_dump(mode="json")


class RecordingVoyagerClient:
    def __init__(self, plan_result: PlanResult, apply_result: ApplyResult | None = None) -> None:
        self.plan_result = plan_result
        self.apply_result = apply_result
        self.planned_operations = []
        self.applied_operations = []

    def plan(self, operation):
        self.planned_operations.append(operation)
        return self.plan_result.model_dump(mode="json")

    def apply(self, operation):
        self.applied_operations.append(operation)
        if self.apply_result is None:
            raise AssertionError("apply should not have been called")
        return self.apply_result.model_dump(mode="json")


class ApplyingVoyagerServerClient:
    applied_operations = []

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path

    def plan(self, operation):
        return PlanResult(
            operation=operation,
            affected_files=["src/main/java/com/acme/NewDTO.java"],
            violations=[],
            is_valid=True,
        ).model_dump(mode="json")

    def apply(self, operation):
        self.__class__.applied_operations.append(operation)
        return ApplyResult(
            success=True,
            operation=operation,
            modified_files=["src/main/java/com/acme/NewDTO.java"],
            errors=[],
        ).model_dump(mode="json")


def test_alita_run_mvp_writes_run_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alita.tools.VoyagerServerClient", FakeVoyagerServerClient)
    patch_path = tmp_path / "agent.patch"
    patch_path.write_text(PATCH_TEXT, encoding="utf-8")
    active_file = tmp_path / "src/main/java/com/acme/NewDTO.java"

    result = run_plan_mvp(
        tmp_path,
        "create NewDTO",
        patch_source=str(patch_path),
        active_file=active_file,
    )

    assert result.success
    assert result.run_dir.exists()
    assert (result.run_dir / "run.json").exists()
    assert (result.run_dir / "context-pack.json").exists()
    assert (result.run_dir / "patch-attempt-1.diff").read_text(encoding="utf-8") == PATCH_TEXT
    assert (result.run_dir / "tool-call-plan-patch-1.json").exists()
    assert (result.run_dir / "plan-result-1.json").exists()
    assert (result.run_dir / "write-intent.json").exists()
    assert (result.run_dir / "policy-decision.json").exists()
    assert (result.run_dir / "summary.md").exists()
    assert (tmp_path / ".voyager/pending_plan.json").exists()
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.ASK_USER

    context = json.loads((result.run_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert context["task"] == "create NewDTO"
    assert context["anchors"][0]["path"] == "src/main/java/com/acme/NewDTO.java"
    assert context["graph"]["exists"] is False
    run_record = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["status"] == "plan_valid"
    assert run_record["write_intent"] == "write-intent.json"
    assert run_record["policy_decision"] == "policy-decision.json"
    tool_call = json.loads(
        (result.run_dir / "tool-call-plan-patch-1.json").read_text(encoding="utf-8")
    )
    assert tool_call["tool_name"] == "voyager_plan_patch"
    assert tool_call["executed"] is True


def test_alita_run_cli_json(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alita.tools.VoyagerServerClient", FakeVoyagerServerClient)
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".voyager").mkdir()
        Path("agent.patch").write_text(PATCH_TEXT, encoding="utf-8")
        result = runner.invoke(
            cli,
            ["alita", "run", "create NewDTO", "--patch", "agent.patch", "--json"],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["plan_result"]["is_valid"] is True
    assert payload["plan_result"]["affected_files"] == ["src/main/java/com/acme/NewDTO.java"]
    assert payload["policy_decision"]["action"] == "ask_user"


def test_provider_profiles_cover_target_providers() -> None:
    profiles = default_provider_profiles()

    assert set(profiles) == {
        "openai",
        "gemini",
        "anthropic",
        "qwen",
        "doubao",
        "kimi",
        "glm",
    }
    assert profiles["gemini"].adk_backend == "native"
    assert profiles["openai"].adk_backend == "litellm"
    assert profiles["qwen"].base_url is not None


def test_resolve_provider_profile_applies_overrides() -> None:
    profile = resolve_provider_profile(
        "openai",
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key_env="TEST_KEY",
    )

    assert profile.model == "gpt-test"
    assert profile.base_url == "https://example.test/v1"
    assert profile.api_key_env == "TEST_KEY"


def test_alita_agent_manual_runtime_writes_artifacts_and_plans(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alita.tools.VoyagerServerClient", FakeVoyagerServerClient)
    patch_path = tmp_path / "agent.patch"
    patch_path.write_text(PATCH_TEXT, encoding="utf-8")

    result = run_agent_once(
        tmp_path,
        "create NewDTO",
        runtime_name="manual",
        provider_name="openai",
        model="manual-model",
        patch_source=str(patch_path),
    )

    assert result.success
    assert result.runtime_result.success
    assert result.plan_result is not None
    assert result.plan_result.is_valid
    assert (result.run_dir / "runtime-result.json").exists()
    assert (result.run_dir / "events.jsonl").exists()
    assert (result.run_dir / "patch-attempt-1.diff").read_text(encoding="utf-8") == PATCH_TEXT
    assert (result.run_dir / "tool-call-plan-patch-1.json").exists()
    assert (result.run_dir / "plan-result-1.json").exists()
    assert (result.run_dir / "write-intent.json").exists()
    assert (result.run_dir / "policy-decision.json").exists()
    assert (tmp_path / ".voyager/pending_plan.json").exists()

    runtime_result = json.loads((result.run_dir / "runtime-result.json").read_text(encoding="utf-8"))
    assert runtime_result["runtime_name"] == "manual"
    assert runtime_result["success"] is True
    run_record = json.loads((result.run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_record["runtime"]["name"] == "manual"
    assert run_record["provider"]["name"] == "openai"
    assert run_record["status"] == "plan_valid"


def test_alita_agent_manual_runtime_cli_json(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alita.tools.VoyagerServerClient", FakeVoyagerServerClient)
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".voyager").mkdir()
        Path("agent.patch").write_text(PATCH_TEXT, encoding="utf-8")
        result = runner.invoke(
            cli,
            [
                "alita",
                "agent",
                "run",
                "create NewDTO",
                "--runtime",
                "manual",
                "--provider",
                "openai",
                "--model",
                "manual-model",
                "--patch",
                "agent.patch",
                "--json",
            ],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["runtime_result"]["runtime_name"] == "manual"
    assert payload["runtime_result"]["success"] is True
    assert payload["plan_result"]["is_valid"] is True
    assert payload["policy_decision"]["action"] == "ask_user"


def test_policy_auto_execute_allows_safe_write() -> None:
    intent = WriteIntent(
        tool_name="voyager_apply_patch",
        operation_type="patch_apply",
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        patch_summary="1 file affected",
        risk_level="medium",
        validation_result="valid",
        requested_by="agent",
        run_id="run-1",
        patch_bytes=100,
    )
    decision = evaluate_write_intent(
        WritePolicyConfig(mode=WritePolicyMode.AUTO_EXECUTE),
        intent,
    )

    assert decision.action == PolicyAction.ALLOW


def test_cli_policy_builder_preserves_default_safety_rules() -> None:
    policy = build_write_policy(
        mode=WritePolicyMode.AUTO_EXECUTE,
        deny=("secrets/**",),
        require_confirm=("build.gradle",),
    )

    assert ".env" in policy.deny
    assert "secrets/**" in policy.deny
    assert "pom.xml" in policy.require_confirm
    assert "build.gradle" in policy.require_confirm


def test_policy_denylist_blocks_write_and_pending_plan(
    monkeypatch, tmp_path: Path
) -> None:
    class DeniedVoyagerServerClient:
        def __init__(self, project_path: Path) -> None:
            self.project_path = project_path

        def plan(self, operation):
            return PlanResult(
                operation=operation,
                affected_files=[".env"],
                violations=[],
                is_valid=True,
            ).model_dump(mode="json")

    monkeypatch.setattr("alita.tools.VoyagerServerClient", DeniedVoyagerServerClient)
    patch_path = tmp_path / "agent.patch"
    patch_path.write_text(PATCH_TEXT, encoding="utf-8")

    result = run_plan_mvp(
        tmp_path,
        "try secret write",
        patch_source=str(patch_path),
    )

    assert result.success
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.DENY
    assert not (tmp_path / ".voyager/pending_plan.json").exists()


def test_tool_registry_plans_patch_through_voyager(tmp_path: Path) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test plan")
    plan_result = PlanResult(
        operation=operation,
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        violations=[],
        is_valid=True,
    )
    client = RecordingVoyagerClient(plan_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        client=client,
    ).plan_patch(operation)

    assert result.tool_name == "voyager_plan_patch"
    assert result.executed is True
    assert result.ok is True
    assert result.payload is not None
    assert result.payload["is_valid"] is True
    assert client.planned_operations == [operation]


def test_tool_registry_apply_patch_requires_manual_confirmation(tmp_path: Path) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test apply")
    plan_result = PlanResult(
        operation=operation,
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        violations=[],
        is_valid=True,
    )
    client = RecordingVoyagerClient(plan_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        client=client,
    ).apply_patch(operation, plan_result=plan_result, patch_text=PATCH_TEXT)

    assert result.tool_name == "voyager_apply_patch"
    assert result.executed is False
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.ASK_USER
    assert client.applied_operations == []


def test_tool_registry_apply_patch_executes_after_user_approval(tmp_path: Path) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test apply")
    plan_result = PlanResult(
        operation=operation,
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        violations=[],
        is_valid=True,
    )
    apply_result = ApplyResult(
        success=True,
        operation=operation,
        modified_files=["src/main/java/com/acme/NewDTO.java"],
        errors=[],
    )
    client = RecordingVoyagerClient(plan_result, apply_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        client=client,
    ).apply_patch(
        operation,
        plan_result=plan_result,
        patch_text=PATCH_TEXT,
        user_approved=True,
    )

    assert result.executed is True
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.ALLOW
    assert "human_approved" in result.policy_decision.matched_rules
    assert client.applied_operations == [operation]


def test_tool_registry_apply_patch_auto_executes_when_allowed(tmp_path: Path) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test apply")
    plan_result = PlanResult(
        operation=operation,
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        violations=[],
        is_valid=True,
    )
    apply_result = ApplyResult(
        success=True,
        operation=operation,
        modified_files=["src/main/java/com/acme/NewDTO.java"],
        errors=[],
    )
    client = RecordingVoyagerClient(plan_result, apply_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        write_policy=WritePolicyConfig(mode=WritePolicyMode.AUTO_EXECUTE),
        client=client,
    ).apply_patch(operation, plan_result=plan_result, patch_text=PATCH_TEXT)

    assert result.tool_name == "voyager_apply_patch"
    assert result.executed is True
    assert result.ok is True
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.ALLOW
    assert result.payload is not None
    assert result.payload["success"] is True
    assert client.applied_operations == [operation]


def test_tool_registry_apply_patch_blocks_denylisted_write(tmp_path: Path) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test apply")
    plan_result = PlanResult(
        operation=operation,
        affected_files=[".env"],
        violations=[],
        is_valid=True,
    )
    client = RecordingVoyagerClient(plan_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        write_policy=WritePolicyConfig(mode=WritePolicyMode.AUTO_EXECUTE),
        client=client,
    ).apply_patch(operation, plan_result=plan_result, patch_text=PATCH_TEXT)

    assert result.tool_name == "voyager_apply_patch"
    assert result.executed is False
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.DENY
    assert result.errors[0]["type"] == "policy_blocked"
    assert client.applied_operations == []


def test_tool_registry_apply_patch_blocks_invalid_plan_even_with_auto_execute(
    tmp_path: Path,
) -> None:
    operation = PatchOperation(patch=PATCH_TEXT, description="test apply")
    plan_result = PlanResult(
        operation=operation,
        affected_files=["src/main/java/com/acme/NewDTO.java"],
        violations=[{"type": "invalid_patch", "message": "invalid"}],
        is_valid=False,
    )
    client = RecordingVoyagerClient(plan_result)

    result = AlitaToolRegistry(
        tmp_path,
        run_id="run-1",
        write_policy=WritePolicyConfig(mode=WritePolicyMode.AUTO_EXECUTE),
        client=client,
    ).apply_patch(operation, plan_result=plan_result, patch_text=PATCH_TEXT)

    assert result.tool_name == "voyager_apply_patch"
    assert result.executed is False
    assert result.policy_decision is not None
    assert result.policy_decision.action == PolicyAction.DENY
    assert result.policy_decision.matched_rules == ["validation_result:invalid"]
    assert client.applied_operations == []


def test_alita_tool_plan_patch_cli_json_saves_pending_plan(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("alita.tools.VoyagerServerClient", FakeVoyagerServerClient)
    runner = CliRunner()

    project_dir = None
    with runner.isolated_filesystem(temp_dir=tmp_path):
        project_dir = Path.cwd()
        Path(".voyager").mkdir()
        Path("agent.patch").write_text(PATCH_TEXT, encoding="utf-8")
        result = runner.invoke(
            cli,
            ["alita", "tool", "plan-patch", "--patch", "agent.patch", "--json"],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["plan_result"]["is_valid"] is True
    assert payload["policy_decision"]["action"] == "ask_user"
    assert payload["pending_plan_saved"] is True
    assert project_dir is not None
    assert (project_dir / ".voyager/pending_plan.json").exists()


def test_alita_tool_apply_patch_cli_json_returns_ask_user_without_yes(
    monkeypatch, tmp_path: Path
) -> None:
    ApplyingVoyagerServerClient.applied_operations = []
    monkeypatch.setattr("alita.tools.VoyagerServerClient", ApplyingVoyagerServerClient)
    runner = CliRunner()

    project_dir = None
    with runner.isolated_filesystem(temp_dir=tmp_path):
        project_dir = Path.cwd()
        Path(".voyager").mkdir()
        StorageManager(Path.cwd()).save_pending_plan(
            PatchOperation(patch=PATCH_TEXT, description="pending")
        )
        result = runner.invoke(
            cli,
            ["alita", "tool", "apply-patch", "--json"],
        )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["success"] is False
    assert payload["apply_tool_call"]["executed"] is False
    assert payload["policy_decision"]["action"] == "ask_user"
    assert project_dir is not None
    assert (project_dir / ".voyager/pending_plan.json").exists()
    assert ApplyingVoyagerServerClient.applied_operations == []


def test_alita_tool_apply_patch_cli_json_yes_executes_and_clears_pending_plan(
    monkeypatch, tmp_path: Path
) -> None:
    ApplyingVoyagerServerClient.applied_operations = []
    monkeypatch.setattr("alita.tools.VoyagerServerClient", ApplyingVoyagerServerClient)
    runner = CliRunner()

    project_dir = None
    with runner.isolated_filesystem(temp_dir=tmp_path):
        project_dir = Path.cwd()
        Path(".voyager").mkdir()
        StorageManager(Path.cwd()).save_pending_plan(
            PatchOperation(patch=PATCH_TEXT, description="pending")
        )
        result = runner.invoke(
            cli,
            ["alita", "tool", "apply-patch", "--yes", "--json"],
        )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["apply_tool_call"]["executed"] is True
    assert payload["policy_decision"]["action"] == "allow"
    assert "human_approved" in payload["policy_decision"]["matched_rules"]
    assert payload["pending_plan_cleared"] is True
    assert project_dir is not None
    assert not (project_dir / ".voyager/pending_plan.json").exists()
    assert len(ApplyingVoyagerServerClient.applied_operations) == 1


def test_alita_tool_apply_patch_cli_prompts_for_manual_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    ApplyingVoyagerServerClient.applied_operations = []
    monkeypatch.setattr("alita.tools.VoyagerServerClient", ApplyingVoyagerServerClient)
    runner = CliRunner()

    project_dir = None
    with runner.isolated_filesystem(temp_dir=tmp_path):
        project_dir = Path.cwd()
        Path(".voyager").mkdir()
        StorageManager(Path.cwd()).save_pending_plan(
            PatchOperation(patch=PATCH_TEXT, description="pending")
        )
        result = runner.invoke(
            cli,
            ["alita", "tool", "apply-patch"],
            input="y\n",
        )

    assert result.exit_code == 0
    assert "Approve this Alita write?" in result.output
    assert project_dir is not None
    assert not (project_dir / ".voyager/pending_plan.json").exists()
    assert len(ApplyingVoyagerServerClient.applied_operations) == 1


def test_alita_tool_status_cli_json_reports_pending_plan(tmp_path: Path) -> None:
    runner = CliRunner()

    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path(".voyager").mkdir()
        StorageManager(Path.cwd()).save_pending_plan(
            PatchOperation(patch=PATCH_TEXT, description="pending")
        )
        result = runner.invoke(cli, ["alita", "tool", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["success"] is True
    assert payload["status"]["pending_plan"]["exists"] is True
    assert payload["status"]["pending_plan"]["patch_count"] == 1
