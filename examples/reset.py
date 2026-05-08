"""Reset example projects by copying from _sources/ to their runtime directories.

Usage:
    python examples/reset.py [project_name]

    If project_name is given, only reset that project.
    Otherwise, reset all projects found in _sources/.
"""

import shutil
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
SOURCES_DIR = EXAMPLES_DIR / "_sources"
REPO_ROOT = EXAMPLES_DIR.parent


def stop_voyager_server(project_path: Path) -> None:
    """
    Stop a running Voyager Server before deleting the runtime example.

    Reset removes the target `.voyager/` directory. If a Server is still running,
    deleting that state file first would leave the Server/JDT LS process harder
    to discover and stop later.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    try:
        from core.server.client import VoyagerServerClient

        VoyagerServerClient(project_path, auto_start=False).shutdown()
        print(f"Stopped Voyager server: {project_path}")
    except Exception:
        # No server is the common case when reset is used before a test run.
        return


def reset_project(name: str) -> None:
    source = SOURCES_DIR / name
    target = EXAMPLES_DIR / name

    if not source.exists():
        print(f"Source not found: {source}")
        sys.exit(1)

    if target.exists():
        stop_voyager_server(target.resolve())

    # Clean target content (don't rmtree the directory itself to avoid Windows locks)
    if target.exists():
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    # Copy source content into target
    shutil.copytree(source, target, dirs_exist_ok=True)
    print(f"Reset: {name}  ({source} -> {target})")


def main() -> None:
    if len(sys.argv) > 1:
        names = [sys.argv[1]]
    else:
        names = sorted(d.name for d in SOURCES_DIR.iterdir() if d.is_dir())

    if not names:
        print("No projects found in _sources/")
        sys.exit(1)

    for name in names:
        reset_project(name)

    print(f"\nDone. Reset {len(names)} project(s).")


if __name__ == "__main__":
    main()
