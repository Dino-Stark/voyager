"""LSP (Language Server Protocol) client module.

Provides a unified LSP client driver that can connect to different
language servers (jdt.ls, pyright, etc.) for semantic code analysis.

Architecture: one generic LSP client + dynamic backend server switching.
"""

from core.lsp.client import LspClient
from core.lsp.config import LanguageConfig, get_language_config

__all__ = ["LspClient", "LanguageConfig", "get_language_config"]
