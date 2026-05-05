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
    """
    Supported programming languages for LSP analysis.

    Each value maps to a language server that Voyager knows how to launch and
    communicate with.  See :class:`LanguageConfig` for server details.

    Currently only Java (Eclipse JDT Language Server) is implemented.
    """

    JAVA = "java"
    # Planned language support:
    # Python = "python"      # pyright-langserver --stdio
    # TYPESCRIPT = "typescript"  # typescript-language-server --stdio
    # CSHARP = "csharp"      # OmniSharp
    # GO = "go"              # gopls
    # CPP = "cpp"            # clangd


@dataclass
class LanguageConfig:
    """
    Configuration for launching and initialising a language server.

    Describes how to find the server binary, which file extensions it handles,
    and any server-specific initialisation options sent during the ``initialize``
    handshake.
    """

    language: Language
    file_extensions: list[str]
    command: list[str]
    initialization_options: dict = field(default_factory=dict)

    def find_server_command(self) -> list[str] | None:
        """
        Check if the LSP server binary is available on PATH.
        """
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
    """
    Get the LSP server configuration for a given language.
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
    }

    if language not in configs:
        raise NotImplementedError(
            f"Language '{language.value}' is not yet supported. "
            f"Supported languages: {[item.value for item in Language]}"
        )
    return configs[language]


def detect_language(file_path: Path) -> Language | None:
    """
    Detect the programming language of a file based on its extension.
    """
    ext = file_path.suffix.lower()
    lang_map: dict[str, Language] = {
        ".java": Language.JAVA,
    }
    return lang_map.get(ext)
