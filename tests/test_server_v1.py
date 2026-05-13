import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.operation.models import PatchOperation
from core.server.client import VoyagerServerClient
from core.server.server import VoyagerServer


class FakeProjectSession:
    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.started = False
        self.closed = False
        self.scan_calls = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def scan(self) -> dict:
        self.scan_calls += 1
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


def _start_fake_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[VoyagerServer, threading.Thread, list[BaseException]]:
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
    return server, thread, errors


def test_server_client_start_reuses_server_without_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server, thread, errors = _start_fake_server(monkeypatch, tmp_path)

    client = VoyagerServerClient(tmp_path, auto_start=False)
    result = client.start()

    assert result["running"] is True
    assert server.session.started is True
    assert server.session.scan_calls == 0
    assert client.shutdown()["ok"] is True

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors


def test_server_client_roundtrip_without_lsp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    server, thread, errors = _start_fake_server(monkeypatch, tmp_path)

    client = VoyagerServerClient(tmp_path, auto_start=False)
    assert client.ping()["ok"] is True
    assert client.scan()["symbols_count"] == 1
    assert server.session.scan_calls == 1
    status = client.status()
    assert status["capabilities"]["snapshot_diagnostics"] is False
    assert status["progress"]["stage"] == "scan"
    assert status["progress"]["status"] == "succeeded"
    assert client.progress()["status"] == "succeeded"
    assert client.cancel()["accepted"] is False

    operation = PatchOperation(
        patch="""--- a/UserDTO.java
+++ b/UserDTO.java
@@ -1,1 +1,1 @@
-class UserDTO {}
+class CustomerDTO {}
"""
    )
    assert client.plan(operation)["affected_files"] == ["UserDTO.java"]
    assert client.apply(operation)["modified_files"] == ["UserDTO.java"]
    assert client.progress()["stage"] == "apply"
    assert client.shutdown()["ok"] is True

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert not (tmp_path / ".voyager" / "cache" / "server.json").exists()


def test_distinct_projects_use_distinct_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    root_a.mkdir()
    root_b.mkdir()

    server_a, thread_a, errors_a = _start_fake_server(monkeypatch, root_a)
    server_b, thread_b, errors_b = _start_fake_server(monkeypatch, root_b)

    client_a = VoyagerServerClient(root_a, auto_start=False)
    client_b = VoyagerServerClient(root_b, auto_start=False)

    status_a = client_a.start()
    status_b = client_b.start()

    assert status_a["project_path"] == str(root_a.resolve())
    assert status_b["project_path"] == str(root_b.resolve())
    state_a = json.loads((root_a / ".voyager" / "cache" / "server.json").read_text())
    state_b = json.loads((root_b / ".voyager" / "cache" / "server.json").read_text())
    assert state_a["project_path"] == str(root_a.resolve())
    assert state_b["project_path"] == str(root_b.resolve())
    assert state_a["port"] != state_b["port"]
    assert state_a["token"] != state_b["token"]
    assert client_a.scan()["symbols_count"] == 1
    assert client_b.scan()["symbols_count"] == 1
    assert server_a.session.scan_calls == 1
    assert server_b.session.scan_calls == 1

    assert (root_a / ".voyager" / "cache" / "server.json").exists()
    assert (root_b / ".voyager" / "cache" / "server.json").exists()

    assert client_a.shutdown()["ok"] is True
    thread_a.join(timeout=5)
    assert not thread_a.is_alive()
    assert not (root_a / ".voyager" / "cache" / "server.json").exists()
    assert (root_b / ".voyager" / "cache" / "server.json").exists()
    assert server_a.session.closed is True
    assert server_b.session.closed is False
    assert not errors_a

    assert client_b.shutdown()["ok"] is True
    thread_b.join(timeout=5)
    assert not thread_b.is_alive()
    assert not errors_b
    assert not (root_b / ".voyager" / "cache" / "server.json").exists()
