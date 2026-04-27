"""Language-specific LSP server configurations.

Defines how to launch and initialize each supported language server.
New languages can be added here with their server command and init params.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Language(str, Enum):
    """Supported programming languages."""

    JAVA = "java"
    # TODO: Python = "python"    # pyright-langserver --stdio
    # TODO: TYPESCRIPT = "typescript"  # typescript-language-server --stdio
    # TODO: CSHARP = "csharp"    # OmniSharp
    # TODO: GO = "go"            # gopls
    # TODO: CPP = "cpp"          # clangd


@dataclass
class LanguageConfig:
    """Configuration for a specific language's LSP server."""

    language: Language
    file_extensions: list[str]
    command: list[str]
    initialization_options: dict = field(default_factory=dict)

    def find_server_command(self) -> list[str] | None:
        """Check if the LSP server binary is available on PATH."""
        executable = self.command[0]
        rest = self.command[1:]

        if os.name == "nt" and Path(executable).suffix == "":
            for suffix in (".cmd", ".bat", ".exe"):
                resolved = shutil.which(executable + suffix)
                if resolved:
                    if suffix in {".cmd", ".bat"}:
                        return ["cmd.exe", "/c", resolved, *rest]
                    return [resolved, *rest]

        resolved = shutil.which(executable)
        if resolved:
            if os.name == "nt" and Path(resolved).suffix == "":
                return [sys.executable, resolved, *rest]
            return [resolved, *rest]
        return None


def get_language_config(language: Language) -> LanguageConfig:
    """Get the LSP server configuration for a given language.

    Args:
        language: The target programming language.

    Returns:
        LanguageConfig with server command and init options.

    Raises:
        NotImplementedError: If the language is not yet supported.
    """
    configs: dict[Language, LanguageConfig] = {
        Language.JAVA: LanguageConfig(
            language=Language.JAVA,
            file_extensions=[".java"],
            command=["jdtls"],
            initialization_options={
                "settings": {
                    "java": {
                        "maven": {"downloadSources": False},
                        "autobuild": {"enabled": False},
                        "format": {"enabled": False},
                    }
                }
            },
        ),
        # TODO: Add Python support with pyright-langserver
        # Language.PYTHON: LanguageConfig(
        #     language=Language.PYTHON,
        #     file_extensions=[".py"],
        #     command=["pyright-langserver", "--stdio"],
        #     initialization_options={},
        # ),
        # TODO: Add TypeScript/JavaScript support
        # Language.TYPESCRIPT: LanguageConfig(
        #     language=Language.TYPESCRIPT,
        #     file_extensions=[".ts", ".tsx", ".js", ".jsx"],
        #     command=["typescript-language-server", "--stdio"],
        #     initialization_options={},
        # ),
        # TODO: Add C# support with OmniSharp
        # Language.CSHARP: LanguageConfig(
        #     language=Language.CSHARP,
        #     file_extensions=[".cs"],
        #     command=["OmniSharp", "-lsp"],
        #     initialization_options={},
        # ),
        # TODO: Add Go support with gopls
        # Language.GO: LanguageConfig(
        #     language=Language.GO,
        #     file_extensions=[".go"],
        #     command=["gopls"],
        #     initialization_options={},
        # ),
        # TODO: Add C/C++ support with clangd
        # Language.CPP: LanguageConfig(
        #     language=Language.CPP,
        #     file_extensions=[".c", ".cpp", ".h", ".hpp"],
        #     command=["clangd"],
        #     initialization_options={},
        # ),
    }

    if language not in configs:
        raise NotImplementedError(
            f"Language '{language.value}' is not yet supported. "
            f"Supported languages: {[item.value for item in Language]}"
        )
    return configs[language]


def detect_language(file_path: Path) -> Language | None:
    """Detect the programming language of a file based on its extension.

    Args:
        file_path: Path to the source file.

    Returns:
        The detected Language, or None if unknown.
    """
    ext = file_path.suffix.lower()
    lang_map: dict[str, Language] = {
        ".java": Language.JAVA,
        # TODO: ".py": Language.PYTHON,
        # TODO: ".ts": Language.TYPESCRIPT,
        # TODO: ".tsx": Language.TYPESCRIPT,
        # TODO: ".js": Language.TYPESCRIPT,
        # TODO: ".jsx": Language.TYPESCRIPT,
        # TODO: ".cs": Language.CSHARP,
        # TODO: ".go": Language.GO,
        # TODO: ".c": Language.CPP,
        # TODO: ".cpp": Language.CPP,
        # TODO: ".h": Language.CPP,
        # TODO: ".hpp": Language.CPP,
    }
    return lang_map.get(ext)
