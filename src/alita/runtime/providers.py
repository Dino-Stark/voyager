"""Provider profile definitions for Alita runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class ProviderProfile:
    """
    Provider capability and routing metadata.

    Profiles are configuration hints, not hard-coded credentials. All values can
    be overridden from project or CLI configuration.
    """

    name: str
    model: str | None
    api_key_env: str | None
    base_url: str | None = None
    adk_backend: str = "native"
    litellm_model_prefix: str | None = None
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    supports_json_schema: bool = False
    supports_usage: bool = True
    supports_reasoning: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

    def with_overrides(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
    ) -> "ProviderProfile":
        return replace(
            self,
            model=model or self.model,
            base_url=base_url or self.base_url,
            api_key_env=api_key_env or self.api_key_env,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "base_url": self.base_url,
            "adk_backend": self.adk_backend,
            "litellm_model_prefix": self.litellm_model_prefix,
            "supports_streaming": self.supports_streaming,
            "supports_tool_calling": self.supports_tool_calling,
            "supports_json_schema": self.supports_json_schema,
            "supports_usage": self.supports_usage,
            "supports_reasoning": self.supports_reasoning,
            "extra_headers": self.extra_headers,
            "extra_body": self.extra_body,
        }


def default_provider_profiles() -> dict[str, ProviderProfile]:
    """
    Return built-in provider profiles for Alita's target provider set.
    """
    return {
        "gemini": ProviderProfile(
            name="gemini",
            model=None,
            api_key_env="GOOGLE_API_KEY",
            adk_backend="native",
            supports_json_schema=True,
            supports_reasoning=True,
        ),
        "openai": ProviderProfile(
            name="openai",
            model=None,
            api_key_env="OPENAI_API_KEY",
            adk_backend="litellm",
            litellm_model_prefix="openai",
            supports_json_schema=True,
            supports_reasoning=True,
        ),
        "anthropic": ProviderProfile(
            name="anthropic",
            model=None,
            api_key_env="ANTHROPIC_API_KEY",
            adk_backend="litellm",
            litellm_model_prefix="anthropic",
        ),
        "qwen": ProviderProfile(
            name="qwen",
            model=None,
            api_key_env="DASHSCOPE_API_KEY",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            adk_backend="litellm",
            litellm_model_prefix="openai",
            supports_json_schema=True,
        ),
        "doubao": ProviderProfile(
            name="doubao",
            model=None,
            api_key_env="ARK_API_KEY",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            adk_backend="litellm",
            litellm_model_prefix="openai",
            supports_json_schema=True,
        ),
        "kimi": ProviderProfile(
            name="kimi",
            model=None,
            api_key_env="MOONSHOT_API_KEY",
            base_url="https://api.moonshot.cn/v1",
            adk_backend="litellm",
            litellm_model_prefix="openai",
            supports_json_schema=True,
        ),
        "glm": ProviderProfile(
            name="glm",
            model=None,
            api_key_env="ZHIPUAI_API_KEY",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            adk_backend="litellm",
            litellm_model_prefix="openai",
            supports_json_schema=True,
        ),
    }


def resolve_provider_profile(
    name: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> ProviderProfile:
    """
    Resolve a built-in profile and apply CLI/project overrides.
    """
    key = name.lower()
    profiles = default_provider_profiles()
    if key not in profiles:
        supported = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown provider '{name}'. Supported providers: {supported}")
    return profiles[key].with_overrides(
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
    )
