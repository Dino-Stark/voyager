"""Optional Google ADK runtime adapter for Alita."""

from __future__ import annotations

import asyncio
import inspect
import os
import re
from pathlib import Path
from typing import Any

from alita.tools import AlitaToolRegistry
from alita.runtime.base import AlitaRuntimeRequest, AlitaRuntimeResult
from alita.runtime.events import AlitaEvent, event
from alita.runtime.providers import ProviderProfile
from core.operation.models import PatchOperation


class AdkRuntimeAdapter:
    """
    Thin optional adapter around Google ADK.

    The adapter is intentionally isolated so the rest of Alita remains usable
    without the optional `google-adk` dependency installed.
    """

    name = "adk"

    def run(self, request: AlitaRuntimeRequest) -> AlitaRuntimeResult:
        return asyncio.run(self._run_async(request))

    async def _run_async(self, request: AlitaRuntimeRequest) -> AlitaRuntimeResult:
        events = [
            event(
                "alita.runtime.started",
                request.run_id,
                message="ADK runtime started.",
                data={
                    "runtime": self.name,
                    "provider": request.provider.to_dict(),
                    "model": request.model or request.provider.model,
                },
            )
        ]
        try:
            adk = _load_adk_symbols()
            model = _adk_model(request.provider, request.model, adk)
            agent = adk["Agent"](
                name="alita_patch_agent",
                model=model,
                description="Alita patch proposal agent.",
                instruction=_agent_instruction(),
                tools=_adk_tools(request),
            )
            session_service = adk["InMemorySessionService"]()
            runner = adk["Runner"](
                agent=agent,
                app_name="alita",
                session_service=session_service,
            )
            await _maybe_await(
                session_service.create_session(
                    app_name="alita",
                    user_id="local-user",
                    session_id=request.run_id,
                )
            )
            message = adk["Content"](
                role="user",
                parts=[adk["Part"](text=_prompt(request))],
            )

            raw_response = ""
            async for adk_event in runner.run_async(
                user_id="local-user",
                session_id=request.run_id,
                new_message=message,
            ):
                translated = _translate_adk_event(request.run_id, adk_event)
                if translated is not None:
                    events.append(translated)
                if _is_final_response(adk_event):
                    raw_response = _event_text(adk_event) or raw_response

            patch_text = _extract_patch(raw_response)
            if not patch_text:
                return AlitaRuntimeResult(
                    runtime_name=self.name,
                    run_id=request.run_id,
                    success=False,
                    raw_response=raw_response,
                    events=[
                        *events,
                        event(
                            "alita.runtime.failed",
                            request.run_id,
                            message="ADK response did not contain a unified diff patch.",
                        ),
                    ],
                    errors=[
                        {
                            "type": "missing_patch",
                            "message": "ADK response did not contain a unified diff patch.",
                            "action": "revise_prompt",
                        }
                    ],
                )

            return AlitaRuntimeResult(
                runtime_name=self.name,
                run_id=request.run_id,
                success=True,
                patch_text=patch_text,
                raw_response=raw_response,
                events=[
                    *events,
                    event(
                        "alita.patch.generated",
                        request.run_id,
                        message="ADK generated a patch proposal.",
                        data={"patch_bytes": len(patch_text.encode("utf-8"))},
                    ),
                    event(
                        "alita.runtime.completed",
                        request.run_id,
                        message="ADK runtime completed.",
                    ),
                ],
            )
        except Exception as exc:
            return AlitaRuntimeResult(
                runtime_name=self.name,
                run_id=request.run_id,
                success=False,
                events=[
                    *events,
                    event(
                        "alita.runtime.failed",
                        request.run_id,
                        message=str(exc),
                        data={"error_type": exc.__class__.__name__},
                    ),
                ],
                errors=[
                    {
                        "type": "adk_runtime_failed",
                        "message": str(exc),
                        "action": "install_or_configure_adk",
                    }
                ],
            )


def _load_adk_symbols() -> dict[str, Any]:
    try:
        try:
            from google.adk.agents import Agent
        except ImportError:
            from google.adk.agents.llm_agent import Agent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part
    except ImportError as exc:
        raise RuntimeError(
            "Google ADK is not installed. Install the optional dependency with "
            "`pip install -e .[adk]`."
        ) from exc
    return {
        "Agent": Agent,
        "Content": Content,
        "InMemorySessionService": InMemorySessionService,
        "LiteLlm": LiteLlm,
        "Part": Part,
        "Runner": Runner,
    }


def _adk_model(
    provider: ProviderProfile,
    model_override: str | None,
    adk: dict[str, Any],
) -> object:
    model = model_override or provider.model
    if not model:
        raise RuntimeError(
            f"Provider '{provider.name}' requires an explicit --model for ADK runtime."
        )

    if provider.api_key_env and provider.api_key_env not in os.environ:
        raise RuntimeError(
            f"Provider '{provider.name}' requires ${provider.api_key_env} for ADK runtime."
        )

    if provider.adk_backend == "native":
        return model

    if provider.adk_backend == "litellm":
        model_id = model
        if provider.litellm_model_prefix and "/" not in model_id:
            model_id = f"{provider.litellm_model_prefix}/{model_id}"
        kwargs: dict[str, Any] = {"model": model_id}
        if provider.base_url:
            kwargs["api_base"] = provider.base_url
        if provider.api_key_env and os.environ.get(provider.api_key_env):
            kwargs["api_key"] = os.environ[provider.api_key_env]
        return adk["LiteLlm"](**kwargs)

    raise RuntimeError(f"Unsupported ADK backend: {provider.adk_backend}")


def _agent_instruction() -> str:
    return (
        "You are Alita, a coding agent. Produce one Git-style unified diff patch "
        "for the user's task. Do not write files directly. Do not explain unless "
        "the task cannot be solved. Return only the unified diff, preferably in a "
        "diff code fence."
    )


def _adk_tools(request: AlitaRuntimeRequest) -> list[object]:
    def read_context_pack() -> dict[str, Any]:
        """
        Return the deterministic context pack for this Alita run.
        """
        return request.context_pack.to_dict()

    def voyager_plan_patch(patch: str) -> dict[str, Any]:
        """
        Safely validate a Git-style unified diff without writing source files.
        """
        try:
            operation = PatchOperation(
                patch=patch,
                description=f"adk-tool:{request.run_id}",
            )
            result = AlitaToolRegistry(
                Path(request.project_path),
                run_id=request.run_id,
            ).plan_patch(operation)
            return result.to_dict()
        except Exception as exc:
            return {
                "tool_name": "voyager_plan_patch",
                "executed": False,
                "ok": False,
                "errors": [
                    {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "action": "revise_patch",
                    }
                ],
            }

    return [read_context_pack, voyager_plan_patch]


def _prompt(request: AlitaRuntimeRequest) -> str:
    context = request.context_pack.to_dict()
    return (
        f"Task:\n{request.task}\n\n"
        f"Context pack:\n{context}\n\n"
        "Generate a Git-style unified diff patch. Voyager will validate and apply it."
    )


def _translate_adk_event(run_id: str, adk_event: object) -> AlitaEvent | None:
    author = getattr(adk_event, "author", None)
    text = _event_text(adk_event)
    data: dict[str, Any] = {}
    if author is not None:
        data["author"] = author
    if text:
        data["text_preview"] = text[:500]
    if not data:
        return None
    return event(
        "alita.model.stream.delta",
        run_id,
        message="ADK event received.",
        data=data,
    )


def _is_final_response(adk_event: object) -> bool:
    marker = getattr(adk_event, "is_final_response", None)
    if callable(marker):
        try:
            return bool(marker())
        except Exception:
            return False
    return False


def _event_text(adk_event: object) -> str:
    content = getattr(adk_event, "content", None)
    parts = getattr(content, "parts", None) or []
    texts = [getattr(part, "text", None) for part in parts]
    return "".join(text for text in texts if text)


def _extract_patch(raw_response: str) -> str | None:
    if not raw_response:
        return None
    fence = re.search(r"```(?:diff|patch)?\s*(.*?)```", raw_response, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
        if _looks_like_patch(candidate):
            return candidate + "\n"
    candidate = raw_response.strip()
    if _looks_like_patch(candidate):
        return candidate + "\n"
    return None


def _looks_like_patch(text: str) -> bool:
    return (
        "--- " in text
        and "+++ " in text
        and "@@ " in text
    ) or ("diff --git " in text and "@@ " in text)


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value
