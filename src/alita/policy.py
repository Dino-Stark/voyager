"""Policy and HITL decision models for Alita write operations."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any


class WritePolicyMode(str, Enum):
    """
    Supported write policy modes for Alita.
    """

    MANUAL_CONFIRM = "manual_confirm"
    ALLOWLIST = "allowlist"
    DENYLIST = "denylist"
    AUTO_EXECUTE = "auto_execute"


class PolicyAction(str, Enum):
    """
    Decision returned by a policy evaluation.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK_USER = "ask_user"


@dataclass(frozen=True)
class WritePolicyConfig:
    """
    Rules used to decide whether a write intent can proceed.
    """

    mode: WritePolicyMode = WritePolicyMode.MANUAL_CONFIRM
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(
        default_factory=lambda: [".git/**", ".env", "**/*secret*", "**/credentials/**"]
    )
    require_confirm: list[str] = field(
        default_factory=lambda: ["pyproject.toml", "pom.xml", "package.json"]
    )
    max_files_changed: int = 20
    max_patch_bytes: int = 200_000
    fallback_action: PolicyAction = PolicyAction.ASK_USER

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "allow": self.allow,
            "deny": self.deny,
            "require_confirm": self.require_confirm,
            "max_files_changed": self.max_files_changed,
            "max_patch_bytes": self.max_patch_bytes,
            "fallback_action": self.fallback_action.value,
        }


@dataclass(frozen=True)
class WriteIntent:
    """
    A requested source-writing operation before policy approval.
    """

    tool_name: str
    operation_type: str
    affected_files: list[str]
    patch_summary: str
    risk_level: str
    validation_result: str
    requested_by: str
    run_id: str
    patch_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "operation_type": self.operation_type,
            "affected_files": self.affected_files,
            "patch_summary": self.patch_summary,
            "risk_level": self.risk_level,
            "validation_result": self.validation_result,
            "requested_by": self.requested_by,
            "run_id": self.run_id,
            "patch_bytes": self.patch_bytes,
        }


@dataclass(frozen=True)
class PolicyDecision:
    """
    Result of evaluating a write intent against a policy.
    """

    action: PolicyAction
    reason: str
    policy_mode: WritePolicyMode
    matched_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "reason": self.reason,
            "policy_mode": self.policy_mode.value,
            "matched_rules": self.matched_rules,
        }


def evaluate_write_intent(
    policy: WritePolicyConfig,
    intent: WriteIntent,
) -> PolicyDecision:
    """
    Evaluate a write intent against HITL policy rules.
    """
    affected_files = [_normalize_path(path) for path in intent.affected_files]

    if intent.validation_result != "valid":
        return PolicyDecision(
            action=PolicyAction.DENY,
            reason="Write intent was not validated successfully.",
            policy_mode=policy.mode,
            matched_rules=[f"validation_result:{intent.validation_result}"],
        )

    if len(affected_files) > policy.max_files_changed:
        return PolicyDecision(
            action=PolicyAction.DENY,
            reason="Write affects too many files.",
            policy_mode=policy.mode,
            matched_rules=[f"max_files_changed:{policy.max_files_changed}"],
        )

    if intent.patch_bytes > policy.max_patch_bytes:
        return PolicyDecision(
            action=PolicyAction.DENY,
            reason="Patch is larger than the configured maximum.",
            policy_mode=policy.mode,
            matched_rules=[f"max_patch_bytes:{policy.max_patch_bytes}"],
        )

    denied = _matching_patterns(affected_files, policy.deny)
    if denied:
        return PolicyDecision(
            action=PolicyAction.DENY,
            reason="Write matched a deny rule.",
            policy_mode=policy.mode,
            matched_rules=denied,
        )

    confirm = _matching_patterns(affected_files, policy.require_confirm)
    if confirm:
        return PolicyDecision(
            action=PolicyAction.ASK_USER,
            reason="Write matched a require-confirm rule.",
            policy_mode=policy.mode,
            matched_rules=confirm,
        )

    if policy.mode == WritePolicyMode.MANUAL_CONFIRM:
        return PolicyDecision(
            action=PolicyAction.ASK_USER,
            reason="Manual confirmation is required for all writes.",
            policy_mode=policy.mode,
        )

    if policy.mode == WritePolicyMode.AUTO_EXECUTE:
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason="Auto-execute policy allows this write.",
            policy_mode=policy.mode,
        )

    if policy.mode == WritePolicyMode.ALLOWLIST:
        if not policy.allow:
            return PolicyDecision(
                action=PolicyAction.ASK_USER,
                reason="Allowlist policy has no allow rules.",
                policy_mode=policy.mode,
            )
        unmatched = [
            path for path in affected_files if not _matches_any(path, policy.allow)
        ]
        if unmatched:
            return PolicyDecision(
                action=PolicyAction.ASK_USER,
                reason="Write includes files outside the allowlist.",
                policy_mode=policy.mode,
                matched_rules=unmatched,
            )
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason="Write matched the allowlist.",
            policy_mode=policy.mode,
            matched_rules=_matching_patterns(affected_files, policy.allow),
        )

    if policy.mode == WritePolicyMode.DENYLIST:
        return PolicyDecision(
            action=policy.fallback_action,
            reason="Write did not match deny rules; using fallback action.",
            policy_mode=policy.mode,
        )

    return PolicyDecision(
        action=PolicyAction.ASK_USER,
        reason="Unknown policy mode; asking user.",
        policy_mode=policy.mode,
    )


def _matching_patterns(paths: list[str], patterns: list[str]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        if any(_matches_pattern(path, pattern) for path in paths):
            matches.append(pattern)
    return matches


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_matches_pattern(path, pattern) for pattern in patterns)


def _matches_pattern(path: str, pattern: str) -> bool:
    normalized_pattern = _normalize_path(pattern)
    if fnmatch.fnmatchcase(path, normalized_pattern):
        return True
    if "/" not in normalized_pattern and fnmatch.fnmatchcase(PurePosixPath(path).name, normalized_pattern):
        return True
    return False


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")
