"""Operation models.

Defines the structured operation specifications for semantic code modifications.
Each operation targets a semantic entity (not raw text) and is verifiable + reversible.
"""

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _is_qualified_java_name(value: str) -> bool:
    parts = value.split(".")
    return len(parts) >= 2 and all(part.isidentifier() for part in parts)


def _new_fqn(old_fqn: str, new_simple_name: str) -> str:
    package, _, _ = old_fqn.rpartition(".")
    return f"{package}.{new_simple_name}" if package else new_simple_name


_JAVA_TYPE_RE = re.compile(r"^[\w$.\[\]<>?, extends super&\s]+$")


def _is_safe_java_type(value: str) -> bool:
    return bool(value.strip()) and bool(_JAVA_TYPE_RE.match(value))


class OperationType(str, Enum):
    """
    All operation types understood by Voyager.

    Each value corresponds to a structured modification that Voyager can plan and apply.
    Rename operations are implemented through the same LSP-backed pipeline in V1.
    """

    ADD_FIELD = "add_field"
    REMOVE_FIELD = "remove_field"
    RENAME_FIELD = "rename_field"
    RENAME_METHOD = "rename_method"
    RENAME_CLASS = "rename_class"
    PATCH = "patch"
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
        target: Fully qualified field spec in ``package.ClassName.fieldName`` format.
        to: New field name (must be a valid Java identifier).
    """

    op: Literal[OperationType.RENAME_FIELD] = OperationType.RENAME_FIELD
    target: str = Field(
        description="Target field in format 'package.ClassName.fieldName', e.g. 'com.shop.UserDTO.userName'"
    )
    to: str = Field(description="New field name")

    @model_validator(mode="after")
    def validate_target_format(self) -> "RenameFieldOperation":
        parts = self.target.rsplit(".", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Target must be in format 'package.ClassName.fieldName', got: '{self.target}'"
            )
        if not _is_qualified_java_name(parts[0]) or not parts[1].isidentifier():
            raise ValueError(
                f"Target must use a fully qualified class name, got: '{self.target}'"
            )
        return self

    @model_validator(mode="after")
    def validate_new_name(self) -> "RenameFieldOperation":
        if not self.to.isidentifier():
            raise ValueError(f"New field name must be a valid identifier, got: '{self.to}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target.rsplit(".", 1)[0]

    @property
    def field_name(self) -> str:
        return self.target.rsplit(".", 1)[1]

    def reverse(self) -> "RenameFieldOperation":
        """
        Return the inverse operation for rollback.
        """
        return RenameFieldOperation(target=f"{self.class_name}.{self.to}", to=self.field_name)


class RenameMethodOperation(BaseModel):
    """
    Rename a Java method.

    V1 resolves methods by class plus simple method name. Overloaded methods are
    intentionally rejected because the current graph does not persist signatures.
    """

    op: Literal[OperationType.RENAME_METHOD] = OperationType.RENAME_METHOD
    target: str = Field(
        description="Target method in format 'package.ClassName.methodName', e.g. 'com.shop.UserService.register'"
    )
    to: str = Field(description="New method name")

    @model_validator(mode="after")
    def validate_target_format(self) -> "RenameMethodOperation":
        parts = self.target.rsplit(".", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"Target must be in format 'package.ClassName.methodName', got: '{self.target}'"
            )
        if not _is_qualified_java_name(parts[0]) or not parts[1].isidentifier():
            raise ValueError(
                f"Target must use a fully qualified class name, got: '{self.target}'"
            )
        return self

    @model_validator(mode="after")
    def validate_new_name(self) -> "RenameMethodOperation":
        if not self.to.isidentifier():
            raise ValueError(f"New method name must be a valid identifier, got: '{self.to}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target.rsplit(".", 1)[0]

    @property
    def method_name(self) -> str:
        return self.target.rsplit(".", 1)[1]

    def reverse(self) -> "RenameMethodOperation":
        """
        Return the inverse operation for rollback.
        """
        return RenameMethodOperation(target=f"{self.class_name}.{self.to}", to=self.method_name)


class RenameClassOperation(BaseModel):
    """
    Rename a Java class.

    The operation uses LSP rename for semantic references and, when the source
    file follows the Java public-class naming convention, moves the file to the
    new class name during commit.
    """

    op: Literal[OperationType.RENAME_CLASS] = OperationType.RENAME_CLASS
    target: str = Field(description="Target fully qualified class name")
    to: str = Field(description="New class name")

    @model_validator(mode="after")
    def validate_target_format(self) -> "RenameClassOperation":
        if not _is_qualified_java_name(self.target):
            raise ValueError(
                f"Target class must be a fully qualified name, got: '{self.target}'"
            )
        return self

    @model_validator(mode="after")
    def validate_new_name(self) -> "RenameClassOperation":
        if not self.to.isidentifier():
            raise ValueError(f"New class name must be a valid identifier, got: '{self.to}'")
        return self

    @property
    def class_name(self) -> str:
        return self.target

    @property
    def new_class_name(self) -> str:
        return _new_fqn(self.target, self.to)

    def reverse(self) -> "RenameClassOperation":
        """
        Return the inverse operation for rollback.
        """
        return RenameClassOperation(target=self.new_class_name, to=self.target.rsplit(".", 1)[-1])


class AddFieldOperation(BaseModel):
    """
    Add a new field to a DTO class.

    Attributes:
        op: Fixed to ``OperationType.ADD_FIELD``.
        target: Fully qualified target class name.
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
    def validate_target_format(self) -> "AddFieldOperation":
        if not _is_qualified_java_name(self.target):
            raise ValueError(
                f"Target class must be a fully qualified name, got: '{self.target}'"
            )
        return self

    @model_validator(mode="after")
    def validate_field_name(self) -> "AddFieldOperation":
        if not self.field_name.isidentifier():
            raise ValueError(f"Field name must be a valid identifier, got: '{self.field_name}'")
        return self

    @model_validator(mode="after")
    def validate_field_type(self) -> "AddFieldOperation":
        if not _is_safe_java_type(self.field_type):
            raise ValueError(f"Field type must be a Java type, got: '{self.field_type}'")
        return self

    @model_validator(mode="after")
    def validate_default_value(self) -> "AddFieldOperation":
        if self.default_value is None:
            return self
        if "\n" in self.default_value or "\r" in self.default_value or ";" in self.default_value:
            raise ValueError("Default value must be a single Java expression without semicolons")
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

    Attributes:
        op: Fixed to ``OperationType.REMOVE_FIELD``.
        target: Fully qualified target class name.
        field_name: Name of the field to remove.
    """

    op: Literal[OperationType.REMOVE_FIELD] = OperationType.REMOVE_FIELD
    target: str = Field(description="Target class name")
    field_name: str

    @model_validator(mode="after")
    def validate_target_format(self) -> "RemoveFieldOperation":
        if not _is_qualified_java_name(self.target):
            raise ValueError(
                f"Target class must be a fully qualified name, got: '{self.target}'"
            )
        return self

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


class PatchOperation(BaseModel):
    """
    Apply a unified diff patch to files inside the project.

    Attributes:
        op: Fixed to ``OperationType.PATCH``.
        patch: Unified diff text.
        description: Optional human-readable note for operation logs.
    """

    op: Literal[OperationType.PATCH] = OperationType.PATCH
    patch: str = Field(description="Unified diff text to apply")
    description: str | None = None

    @model_validator(mode="after")
    def validate_patch_text(self) -> "PatchOperation":
        if not self.patch.strip():
            raise ValueError("Patch text must not be empty")
        return self


# Union type for all supported operations
Operation = (
    RenameFieldOperation
    | RenameMethodOperation
    | RenameClassOperation
    | AddFieldOperation
    | RemoveFieldOperation
    | PatchOperation
)


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
