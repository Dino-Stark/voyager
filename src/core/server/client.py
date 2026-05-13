"""Client for the local Voyager server."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.operation.models import Operation
from core.server.protocol import (
    METHOD_OPERATION_APPLY,
    METHOD_OPERATION_CANCEL,
    METHOD_OPERATION_PLAN,
    METHOD_PROJECT_SCAN,
    METHOD_SERVER_PING,
    METHOD_SERVER_PROGRESS,
    METHOD_SERVER_SHUTDOWN,
    METHOD_SERVER_STATUS,
    SERVER_LOG_FILE,
    VoyagerServerInfo,
)
from storage.manager import StorageManager

logger = logging.getLogger(__name__)


class VoyagerServerClient:
    """
    Local client that starts or reuses a project-scoped Voyager server.
    """

    def __init__(
        self,
        project_path: Path,
        *,
        startup_timeout: float = 30.0,
        request_timeout: float = 300.0,
        auto_start: bool = True,
    ) -> None:
        self.project_path = project_path.resolve()
        self.storage = StorageManager(self.project_path)
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.auto_start = auto_start
        self._server_info: VoyagerServerInfo | None = None

    def scan(self) -> dict[str, Any]:
        return self._request(METHOD_PROJECT_SCAN, {})

    def start(self) -> dict[str, Any]:
        """
        Explicitly start or reuse the project-scoped Voyager server.

        Unlike scan/plan/apply, this does not run a semantic project operation.
        It only ensures the background server is alive and returns its status.
        """
        server_info = self._ensure_server(allow_start=True)
        return self._request_with_server(server_info, METHOD_SERVER_STATUS, {})

    def plan(self, operation: Operation) -> dict[str, Any]:
        return self._request(METHOD_OPERATION_PLAN, {"operation": operation.model_dump(mode="json")})

    def apply(self, operation: Operation) -> dict[str, Any]:
        return self._request(METHOD_OPERATION_APPLY, {"operation": operation.model_dump(mode="json")})

    def status(self) -> dict[str, Any]:
        return self._request(METHOD_SERVER_STATUS, {})

    def ping(self) -> dict[str, Any]:
        return self._request(METHOD_SERVER_PING, {})

    def progress(self) -> dict[str, Any]:
        return self._request(METHOD_SERVER_PROGRESS, {})

    def cancel(self) -> dict[str, Any]:
        return self._request(METHOD_OPERATION_CANCEL, {})

    def shutdown(self) -> dict[str, Any]:
        server_info = self._load_server_info()
        if server_info is None:
            self.storage.clear_server_info()
            raise RuntimeError("No running Voyager server for this project")

        if not self._ping_server(server_info):
            if self._ping_legacy_daemon(server_info):
                try:
                    self._shutdown_legacy_daemon(server_info)
                    return {"ok": True}
                finally:
                    self.storage.clear_server_info()
            self.storage.clear_server_info()
            raise RuntimeError("No running Voyager server for this project")

        try:
            return self._request_with_server(server_info, METHOD_SERVER_SHUTDOWN, {})
        finally:
            self.storage.clear_server_info()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        server_info = self._ensure_server()
        return self._request_with_server(server_info, method, params)

    def _request_with_server(
        self,
        server_info: VoyagerServerInfo,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
            "token": server_info.token,
        }
        with socket.create_connection((server_info.host, server_info.port), timeout=10.0) as sock:
            sock.settimeout(self.request_timeout if timeout is None else timeout)
            with sock.makefile("rwb") as stream:
                stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                stream.flush()
                raw = stream.readline()
        if not raw:
            raise RuntimeError("Voyager server returned no response")
        response = json.loads(raw.decode("utf-8"))
        error = response.get("error")
        if error:
            raise RuntimeError(error.get("message", "Voyager server request failed"))
        return response.get("result", {})

    def _ensure_server(self, *, allow_start: bool | None = None) -> VoyagerServerInfo:
        should_start = self.auto_start if allow_start is None else allow_start
        server_info = self._load_server_info()
        if server_info is not None:
            if self._ping_server(server_info):
                self._server_info = server_info
                return server_info
            if self._ping_legacy_daemon(server_info):
                self._shutdown_legacy_daemon(server_info)

        self.storage.clear_server_info()
        if not should_start:
            raise RuntimeError("Voyager server is not running for this project")

        self._start_server()
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            server_info = self._load_server_info()
            if server_info is not None and self._ping_server(server_info):
                self._server_info = server_info
                return server_info
            time.sleep(0.2)

        raise RuntimeError("Voyager server did not start in time")

    def _ping_server(self, server_info: VoyagerServerInfo) -> bool:
        try:
            response = self._request_with_server(server_info, METHOD_SERVER_PING, {}, timeout=2.0)
            return response.get("ok") is True
        except Exception:
            return False

    def _ping_legacy_daemon(self, server_info: VoyagerServerInfo) -> bool:
        try:
            response = self._request_with_server(server_info, "ping", {}, timeout=2.0)
            return response.get("ok") is True
        except Exception:
            return False

    def _shutdown_legacy_daemon(self, server_info: VoyagerServerInfo) -> None:
        try:
            self._request_with_server(server_info, "shutdown", {}, timeout=10.0)
        except Exception:
            logger.debug("Failed to stop legacy Voyager daemon", exc_info=True)

    def _load_server_info(self) -> VoyagerServerInfo | None:
        data = self.storage.load_server_info()
        if not data:
            data = self.storage.load_session()
        if not data:
            return None
        try:
            return VoyagerServerInfo.from_dict(data)
        except Exception as exc:
            logger.warning("Invalid Voyager server state: %s", exc)
            return None

    def _start_server(self) -> None:
        log_path = self.storage.get_cache_dir() / SERVER_LOG_FILE
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        src_path = str(Path(__file__).resolve().parents[2])
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else os.pathsep.join([src_path, existing_pythonpath])
        )
        cmd = [sys.executable, "-m", "voyager_cmd.server", str(self.project_path)]
        with open(log_path, "a", encoding="utf-8") as log_file:
            kwargs: dict[str, Any] = {
                "cwd": str(self.project_path),
                "env": env,
                "stdout": log_file,
                "stderr": log_file,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(cmd, **kwargs)
