"""Operation models.

Defines the structured operation specifications for semantic code modifications.
Each operation targets a semantic entity (not raw text) and is verifiable + reversible.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OperationType(str, Enum):
    """
    All operation types understood by Voyager.

    Each value corresponds to a structured modification that Voyager can plan and apply.
    Only ``rename_field`` is fully implemented in V1.
    """

    ADD_FIELD = "add_field"
    REMOVE_FIELD = "remove_field"
    RENAME_FIELD = "rename_field"
    UPDATE_API = "update_api"
    ADD_FUNCTION = "add_function"
    UPDATE_FUNCTION_SIGNATURE = "update_function_signature"

    # Add more types.
    # ADD_FILE
    # REMOVE_FILE
    # RENAME_FILE


class RenameFieldOperation(BaseModel):
    """
    Rename a field in a DTO class.

    The operation is atomically applied across all files using LSP rename, then
    re-validated before committing to disk.

    Attributes:
        op: Fixed to ``OperationType.RENAME_FIELD``.
        target: Fully qualified field spec in ``ClassName.fieldName`` format.
        to: New field name (must be a valid Java identifier).
    """

    op: Literal[OperationType.RENAME_FIELD] = OperationType.RENAME_FIELD
    target: str = Field(
        description="Target field in format 'ClassName.fieldName', e.g. 'OrderDTO.userId'"
    )
    to: str = Field(description="New field name")

    @model_validator(mode="after")
    def validate_target_format(self) -> "RenameFieldOperation":
        parts = self.target.split(".", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Target must be in format 'ClassName.fieldName', got: '{self.target}'"
            )
        return self

    @model_validator(mode="after")
    def validate_new_name(self) -> "RenameFieldOperation":
        if not self.to.isidentifier():
            raise ValueError(f"New field name must be a valid identifier, got: '{self.to}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target.split(".", 1)[0]

    @property
    def field_name(self) -> str:
        return self.target.split(".", 1)[1]

    def reverse(self) -> "RenameFieldOperation":
        """
        Return the inverse operation for rollback.
        """
        return RenameFieldOperation(target=f"{self.class_name}.{self.to}", to=self.field_name)


class AddFieldOperation(BaseModel):
    """
    Add a new field to a DTO class.

    V1: declared but not implemented in the execution engine.
    The ``reverse()`` method loses type information.

    Attributes:
        op: Fixed to ``OperationType.ADD_FIELD``.
        target: Target class name.
        field_name: Name of the new field.
        field_type: Java type (default ``"String"``).
        default_value: Optional default value expression.
    """

    op: Literal[OperationType.ADD_FIELD] = OperationType.ADD_FIELD
    target: str = Field(description="Target class name")
    field_name: str
    field_type: str = "String"
    default_value: str | None = None

    @model_validator(mode="after")
    def validate_field_name(self) -> "AddFieldOperation":
        if not self.field_name.isidentifier():
            raise ValueError(f"Field name must be a valid identifier, got: '{self.field_name}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target

    def reverse(self) -> "RemoveFieldOperation":
        """
        Return the inverse operation for rollback.
        """
        return RemoveFieldOperation(target=self.target, field_name=self.field_name)


class RemoveFieldOperation(BaseModel):
    """
    Remove a field from a DTO class.

    V1: declared but not implemented in the execution engine.

    Attributes:
        op: Fixed to ``OperationType.REMOVE_FIELD``.
        target: Target class name.
        field_name: Name of the field to remove.
    """

    op: Literal[OperationType.REMOVE_FIELD] = OperationType.REMOVE_FIELD
    target: str = Field(description="Target class name")
    field_name: str

    @model_validator(mode="after")
    def validate_field_name(self) -> "RemoveFieldOperation":
        if not self.field_name.isidentifier():
            raise ValueError(f"Field name must be a valid identifier, got: '{self.field_name}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target

    def reverse(self) -> AddFieldOperation:
        """
        Return the inverse operation for rollback (simplified, loses type info).
        """
        return AddFieldOperation(target=self.target, field_name=self.field_name)


# Union type for all supported operations
Operation = RenameFieldOperation | AddFieldOperation | RemoveFieldOperation


class PlanResult(BaseModel):
    """
    Outcome of the plan phase.

    Describes whether an operation is safe to apply and which files it would affect.

    Attributes:
        operation: The operation that was planned.
        affected_files: List of files that would be modified if applied.
        violations: Rule violations that blocked the plan (empty if valid).
        is_valid: ``True`` if the plan passed all pre-condition checks.
    """

    operation: Operation
    affected_files: list[str]
    violations: list[dict] = Field(default_factory=list)
    is_valid: bool = True


class ApplyResult(BaseModel):
    """
    Outcome of the apply phase.

    Describes whether the operation succeeded and which files were modified.

    Attributes:
        success: ``True`` if the operation committed all changes successfully.
        operation: The operation that was (or was not) applied.
        modified_files: Files that were written to disk.
        errors: Structured error details for each failure.
    """

    success: bool
    operation: Operation
    modified_files: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
