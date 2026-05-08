import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.operation.models import RenameFieldOperation
from core.server.client import VoyagerServerClient
from core.server.server import VoyagerServer


class FakeProjectSession:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def scan(self) -> dict:
        return {
            "symbols_count": 1,
            "references_count": 0,
            "classes": [{"name": "UserDTO", "fields": 1, "methods": 0, "references": 0}],
        }

    async def plan(self, operation):
        from core.operation.models import PlanResult

        return PlanResult(operation=operation, affected_files=["UserDTO.java"])

    async def apply(self, operation):
        from core.operation.models import ApplyResult

        return ApplyResult(success=True, operation=operation, modified_files=["UserDTO.java"])


def test_server_client_roundtrip_without_lsp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("core.server.server.ProjectSession", FakeProjectSession)

    server = VoyagerServer(tmp_path)
    ready = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(server.run())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    server_info_path = tmp_path / ".voyager" / "cache" / "server.json"
    deadline = time.time() + 5
    while time.time() < deadline:
        if server_info_path.exists():
            ready.set()
            break
        time.sleep(0.05)

    assert ready.is_set()

    client = VoyagerServerClient(tmp_path, auto_start=False)
    assert client.ping()["ok"] is True
    assert client.scan()["symbols_count"] == 1

    operation = RenameFieldOperation(target="UserDTO.userName", to="customerName")
    assert client.plan(operation)["affected_files"] == ["UserDTO.java"]
    assert client.apply(operation)["modified_files"] == ["UserDTO.java"]
    assert client.shutdown()["ok"] is True

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert not server_info_path.exists()
