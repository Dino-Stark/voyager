"""Engine error types.

Structured error definitions for the execution engine.
All errors are recoverable and carry sufficient context for debugging.
"""

from __future__ import annotations

from enum import Enum


class ErrorType(str, Enum):
    SYMBOL_NOT_FOUND = "symbol_not_found"
    SYMBOL_ALREADY_EXISTS = "symbol_already_exists"
    VALIDATION_FAILED = "validation_failed"
    RULE_VIOLATION = "rule_violation"
    PARSE_ERROR = "parse_error"
    WRITE_ERROR = "write_error"
    LSP_UNAVAILABLE = "lsp_unavailable"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    INTERNAL_ERROR = "internal_error"


class EngineError(Exception):
    """Base error for the execution engine."""

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        target: str | None = None,
        file_path: str | None = None,
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.target = target
        self.file_path = file_path
        super().__init__(message)

    def to_dict(self) -> dict:
        result: dict = {
            "type": self.error_type.value,
            "message": self.message,
        }
        if self.target:
            result["target"] = self.target
        if self.file_path:
            result["file"] = self.file_path
        return result


class SymbolNotFoundError(EngineError):
    """Raised when a target symbol cannot be found in the graph."""

    def __init__(self, target: str, file_path: str | None = None) -> None:
        super().__init__(
            ErrorType.SYMBOL_NOT_FOUND,
            f"Symbol not found: {target}",
            target=target,
            file_path=file_path,
        )


class SymbolAlreadyExistsError(EngineError):
    """Raised when trying to create a symbol that already exists."""

    def __init__(self, target: str, file_path: str | None = None) -> None:
        super().__init__(
            ErrorType.SYMBOL_ALREADY_EXISTS,
            f"Symbol already exists: {target}",
            target=target,
            file_path=file_path,
        )


class RuleViolationError(EngineError):
    """Raised when an operation violates a rule."""

    def __init__(self, rule_id: str, message: str, target: str | None = None) -> None:
        super().__init__(
            ErrorType.RULE_VIOLATION,
            f"[{rule_id}] {message}",
            target=target,
        )
        self.rule_id = rule_id


class ValidationError(EngineError):
    """Raised when post-apply validation fails."""

    def __init__(self, message: str, target: str | None = None) -> None:
        super().__init__(
            ErrorType.VALIDATION_FAILED,
            message,
            target=target,
        )


class LspUnavailableError(EngineError):
    """Raised when a semantic operation requires LSP but no server is available."""

    def __init__(self, message: str, target: str | None = None) -> None:
        super().__init__(
            ErrorType.LSP_UNAVAILABLE,
            message,
            target=target,
        )


class UnsupportedOperationError(EngineError):
    """Raised when an operation exists in the model but is not supported in V1."""

    def __init__(self, message: str, target: str | None = None) -> None:
        super().__init__(
            ErrorType.UNSUPPORTED_OPERATION,
            message,
            target=target,
        )
