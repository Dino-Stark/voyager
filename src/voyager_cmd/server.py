"""Voyager server entry point."""

from pathlib import Path
import sys

from core.server.server import run_server


def main() -> None:
    if len(sys.argv) not in {2, 4}:
        print("Usage: python -m voyager_cmd.server <project_path> [<host> <port>]")
        raise SystemExit(2)

    project_path = Path(sys.argv[1])
    host = sys.argv[2] if len(sys.argv) == 4 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) == 4 else 0
    run_server(project_path, host=host, port=port)


if __name__ == "__main__":
    main()
