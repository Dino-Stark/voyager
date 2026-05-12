"""Local Voyager server protocol primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.operation.models import (
    Operation,
    PatchOperation,
)


DEFAULT_SERVER_HOST = "127.0.0.1"
SERVER_STATE_FILE = "server.json"
SERVER_LOG_FILE = "server.log"


@dataclass(frozen=True)
class VoyagerServerInfo:
    """
    Connection information for a running project-scoped Voyager server.
    """

    pid: int
    host: str
    port: int
    token: str
    project_path: str
    protocol: str = "voyager-jsonrpc-v1"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoyagerServerInfo":
        return cls(
            pid=int(data["pid"]),
            host=str(data.get("host") or DEFAULT_SERVER_HOST),
            port=int(data["port"]),
            token=str(data["token"]),
            project_path=str(data["project_path"]),
            protocol=str(data.get("protocol") or "voyager-jsonrpc-v1"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "project_path": self.project_path,
            "protocol": self.protocol,
        }

    @property
    def project_root(self) -> Path:
        return Path(self.project_path)


def deserialize_operation(data: dict[str, Any]) -> Operation:
    """
    Restore an operation model from a JSON-compatible payload.
    """
    op_type = data.get("op", "")
    if op_type == "patch":
        return PatchOperation(**data)
    raise ValueError(f"Unknown operation type: {op_type}. Voyager editing is patch-only.")
