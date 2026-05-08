"""Compatibility aliases for the project-scoped Voyager server."""

from __future__ import annotations

from pathlib import Path

from core.server.client import VoyagerServerClient
from core.server.protocol import VoyagerServerInfo as DaemonSessionInfo
from core.server.server import VoyagerServer
from core.server.server import run_server as _run_server


VoyagerDaemonClient = VoyagerServerClient
VoyagerDaemonServer = VoyagerServer


def run_daemon(project_path: Path) -> None:
    """
    Legacy daemon entrypoint kept for old imports.
    """
    _run_server(project_path)
