"""Legacy Voyager daemon entry point.

Use ``python -m voyager_cmd.server`` or ``voyager serve`` for the explicit
server entrypoint.
"""

from pathlib import Path
import sys

from core.server.server import run_server


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m voyager_cmd.daemon <project_path>")
        raise SystemExit(2)
    run_server(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
