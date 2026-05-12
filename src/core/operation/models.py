"""Operation models for Voyager's patch-first editing API."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OperationType(str, Enum):
    """
    Operation types understood by Voyager's public edit API.
    """

    PATCH = "patch"


class PatchOperation(BaseModel):
    """
    Apply one or more unified diff patches to files inside the project.

    Attributes:
        op: Fixed to ``OperationType.PATCH``.
        patch: First unified diff text, kept for backward compatibility.
        patches: Additional unified diff texts applied after ``patch``.
        description: Optional human-readable note for operation logs.
    """

    op: Literal[OperationType.PATCH] = OperationType.PATCH
    patch: str | None = Field(default=None, description="First unified diff text to apply")
    patches: list[str] = Field(
        default_factory=list,
        description="Additional unified diff texts to apply in order",
    )
    description: str | None = None

    @model_validator(mode="after")
    def validate_patch_text(self) -> "PatchOperation":
        """
        Ensure the operation contains at least one non-empty patch text.
        """
        if not self.patch_texts():
            raise ValueError("Patch text must not be empty")
        if any(not item.strip() for item in self.patches):
            raise ValueError("Patch set entries must not be empty")
        return self

    def patch_texts(self) -> list[str]:
        """
        Return all patch texts in the order they should be applied.
        """
        texts: list[str] = []
        if self.patch is not None and self.patch.strip():
            texts.append(self.patch)
        texts.extend(item for item in self.patches if item.strip())
        return texts


Operation = PatchOperation


class PlanResult(BaseModel):
    """
    Outcome of the plan phase.

    Attributes:
        operation: The operation that was planned.
        affected_files: List of files that would be modified if applied.
        violations: Rule violations that blocked the plan.
        is_valid: Whether the plan passed validation.
    """

    operation: Operation
    affected_files: list[str]
    violations: list[dict] = Field(default_factory=list)
    is_valid: bool = True


class ApplyResult(BaseModel):
    """
    Outcome of the apply phase.

    Attributes:
        success: Whether the operation committed successfully.
        operation: The operation that was applied or rejected.
        modified_files: Files written to disk.
        errors: Structured error details for each failure.
    """

    success: bool
    operation: Operation
    modified_files: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
