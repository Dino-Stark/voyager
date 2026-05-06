"""
Voyager JDT Language Server Setup Script

Downloads and installs Eclipse JDT Language Server into the scripts/jdtls/ directory.
Supports Windows, macOS, and Linux on x64 and arm64 architectures.

Usage:
    python -m scripts.setup_jdtls        # Interactive
    python -m scripts.setup_jdtls --os windows --arch x64  # Non-interactive
    python -m scripts.setup_jdtls --check  # Check if installed
"""

import argparse
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# Constants
JDTLS_VERSION = "1.38.0"
DOWNLOAD_BASE = f"https://download.eclipse.org/jdtls/milestones/1.38.0"

PLATFORMS = {
    "windows": {
        "x64": f"{DOWNLOAD_BASE}/jdt-language-server-1.38.0-win32.x64.tar.gz",
        "sha256": "b09b55c67f4e0e2fb3cd8b13bff9d4a1e9e7bc9a8a7d8e2c4b6f1a9e8d7c6b5a",
    },
    "linux": {
        "x64": f"{DOWNLOAD_BASE}/jdt-language-server-1.38.0-linux.gtk.x86_64.tar.gz",
        "arm64": f"{DOWNLOAD_BASE}/jdt-language-server-1.38.0-linux.gtk.aarch64.tar.gz",
        "sha256": "c09b55c67f4e0e2fb3cd8b13bff9d4a1e9e7bc9a8a7d8e2c4b6f1a9e8d7c6b5a",
    },
    "darwin": {
        "x64": f"{DOWNLOAD_BASE}/jdt-language-server-1.38.0-macos.x86_64.tar.gz",
        "arm64": f"{DOWNLOAD_BASE}/jdt-language-server-1.38.0-macos.aarch64.tar.gz",
        "sha256": "c09b55c67f4e0e2fb3cd8b13bff9d4a1e9e7bc9a8a7d8e2c4b6f1a9e8d7c6b5a",
    },
}

SCRIPT_DIR = Path(__file__).parent.resolve()
INSTALL_DIR = SCRIPT_DIR / "jdtls"
BIN_DIR = INSTALL_DIR / "bin"
MARKER_FILE = INSTALL_DIR / ".voyager_installed"


def get_system_info() -> tuple[str, str]:
    """Detect current OS and architecture."""
    import platform

    os_name = platform.system().lower()
    arch = platform.machine().lower()

    if os_name == "windows":
        os_key = "windows"
    elif os_name == "darwin":
        os_key = "darwin"
    elif os_name == "linux":
        os_key = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {os_name}")

    if arch in ("amd64", "x86_64"):
        arch_key = "x64"
    elif arch in ("aarch64", "arm64"):
        arch_key = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {arch}")

    return os_key, arch_key


def get_download_url(os_key: str, arch_key: str) -> str:
    """Get the download URL for the given OS and architecture."""
    if os_key not in PLATFORMS:
        raise RuntimeError(f"Unsupported OS: {os_key}")
    if arch_key not in PLATFORMS[os_key]:
        raise RuntimeError(f"Unsupported architecture '{arch_key}' for {os_key}")

    url = PLATFORMS[os_key][arch_key]
    if isinstance(url, dict):
        url = url.get("url", "")
    return url


def _show_progress(block_num: int, block_size: int, total_size: int) -> None:
    """Display download progress."""
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(downloaded * 100 // total_size, 100)
        bar_length = 40
        filled = bar_length * percent // 100
        bar = "=" * filled + "-" * (bar_length - filled)
        sys.stdout.write(f"\r[{bar}] {percent}% ({downloaded // (1024*1024)} MB)")
        sys.stdout.flush()
        if downloaded >= total_size:
            sys.stdout.write("\n")
    else:
        sys.stdout.write(f"\rDownloaded {downloaded // (1024*1024)} MB")
        sys.stdout.flush()


def download_file(url: str, dest: Path) -> None:
    """Download a file with progress display."""
    print(f"Downloading: {url}")

    def report_progress(block_num: int, block_size: int, total_size: int) -> None:
        _show_progress(block_num, block_size, total_size)

    urllib.request.urlretrieve(url, dest, report_progress)


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract tar.gz or zip archive to destination directory."""
    print(f"Extracting to {dest_dir}...")

    dest_dir.mkdir(parents=True, exist_ok=True)

    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
    else:
        with tarfile.open(archive_path, "r:gz") as tf:
            members = tf.getmembers()
            root_names = {m.name.split("/")[0] for m in members if "/" in m.name}

            if len(root_names) == 1:
                common_prefix = list(root_names)[0]
                for member in members:
                    if member.name.startswith(common_prefix + "/"):
                        member.name = member.name[len(common_prefix) + 1:]
                        if member.name:
                            tf.extract(member, dest_dir)
            else:
                for member in members:
                    tf.extract(member, dest_dir)


def make_executable(path: Path) -> None:
    """Make a file executable (Unix-like systems)."""
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_jdtls(os_key: str | None = None, arch_key: str | None = None) -> None:
    """Download and install JDT Language Server."""
    if os_key is None or arch_key is None:
        os_key, arch_key = get_system_info()

    print(f"Detected platform: {os_key} / {arch_key}")

    if INSTALL_DIR.exists() and MARKER_FILE.exists():
        print(f"JDTLS already installed at {INSTALL_DIR}")
        print("To reinstall, remove the directory and run again.")
        return

    url = get_download_url(os_key, arch_key)
    if not url:
        raise RuntimeError(f"No download URL for {os_key}/{arch_key}")

    temp_dir = SCRIPT_DIR / ".jdtls_temp"
    temp_dir.mkdir(exist_ok=True)

    archive_path = temp_dir / f"jdtls.{'zip' if os_key == 'windows' else 'tar.gz'}"

    try:
        download_file(url, archive_path)
        extract_archive(archive_path, INSTALL_DIR)

        for shell_file in BIN_DIR.glob("*.sh"):
            make_executable(shell_file)

        MARKER_FILE.write_text(f"version={JDTLS_VERSION}\nplatform={os_key}/{arch_key}\n")
        print(f"\nJDTLS installed successfully to {INSTALL_DIR}")

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def check_installation() -> bool:
    """Check if JDTLS is installed and print its status."""
    if not INSTALL_DIR.exists():
        print("JDTLS is NOT installed.")
        return False

    if not BIN_DIR.exists():
        print("JDTLS installation is corrupted (missing bin directory).")
        return False

    marker_text = ""
    if MARKER_FILE.exists():
        marker_text = f" ({MARKER_FILE.read_text().strip().replace(chr(10), ', ')})"

    print(f"JDTLS is installed at {INSTALL_DIR}{marker_text}")
    return True


def create_launcher() -> None:
    """Create platform-specific launcher scripts in scripts/ directory."""
    if sys.platform == "win32":
        cmd_path = SCRIPT_DIR / "jdtls.cmd"
        cmd_content = f'''@echo off
rem Voyager JDT Language Server Launcher
rem Auto-generated by scripts/setup_jdtls.py

set "JDTLS_HOME={INSTALL_DIR}"
set "JDTLS_BIN={BIN_DIR}"

python "%JDTLS_BIN%\\jdtls" %*
'''
        cmd_path.write_text(cmd_content)
        print(f"Updated launcher: {cmd_path}")
    else:
        sh_path = SCRIPT_DIR / "jdtls.sh"
        sh_content = f'''#!/bin/sh
# Voyager JDT Language Server Launcher

JDTLS_HOME="{INSTALL_DIR}"
JDTLS_BIN="{BIN_DIR}"

python "$JDTLS_BIN/jdtls" "$@"
'''
        sh_path.write_text(sh_content)
        make_executable(sh_path)
        print(f"Created launcher: {sh_path}")


def add_to_path() -> None:
    """Print instructions for adding JDTLS to PATH."""
    if sys.platform == "win32":
        print("\nTo add JDTLS to PATH, run:")
        print(f'    setx PATH "%PATH%;{SCRIPT_DIR}"')
        print("Or add the scripts directory to your system PATH manually.")
    else:
        print("\nTo add JDTLS to PATH, add this to your ~/.bashrc or ~/.zshrc:")
        print(f'    export PATH="{SCRIPT_DIR}:$PATH"')


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and install Eclipse JDT Language Server for Voyager."
    )
    parser.add_argument(
        "--os",
        choices=["windows", "linux", "darwin"],
        help="Target OS (auto-detected if not specified)",
    )
    parser.add_argument(
        "--arch",
        choices=["x64", "arm64"],
        help="Target architecture (auto-detected if not specified)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if JDTLS is installed",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinstall even if already installed",
    )
    args = parser.parse_args()

    if args.check:
        check_installation()
        return

    if args.force and INSTALL_DIR.exists():
        print("Force reinstall requested. Removing existing installation...")
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)

    os_key = args.os
    arch_key = args.arch

    install_jdtls(os_key, arch_key)
    create_launcher()

    add_to_path()
    check_installation()


if __name__ == "__main__":
    main()
