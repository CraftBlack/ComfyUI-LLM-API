"""HTTP routes used by ``web/js/llm_api.js``.

Every path is namespaced under ``/comfyui_llm_api/`` because ComfyUI aborts the
whole pack when two custom nodes register the same route, and several packs
already claim generic names such as ``/models``.
"""

from __future__ import annotations

import logging

from .llm_api_client import list_models
from .providers import DEFAULT_PROVIDER, get_provider, registry_json

LOGGER = logging.getLogger("LLMAPI")

ROUTE_PREFIX = "/comfyui_llm_api"
ROUTE_PROVIDERS = f"{ROUTE_PREFIX}/providers"
ROUTE_MODELS = f"{ROUTE_PREFIX}/models"


def _register() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception as exc:  # noqa: BLE001
        # "No module named 'server'" only means we are not running inside
        # ComfyUI (unit tests, linters). Anything else is worth reporting.
        if "server" not in str(exc):
            LOGGER.warning("[LLMAPI] HTTP routes not registered: %s", exc)
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        LOGGER.warning("[LLMAPI] PromptServer.instance is missing; routes not registered")
        return False

    routes = instance.routes

    @routes.get(ROUTE_PROVIDERS)
    async def llm_api_providers(request):  # noqa: ANN001, ARG001 - aiohttp handler
        return web.json_response(registry_json())

    @routes.get(ROUTE_MODELS)
    async def llm_api_models(request):  # noqa: ANN001 - aiohttp handler
        query = request.rel_url.query
        provider = get_provider(query.get("provider") or DEFAULT_PROVIDER.id)
        refresh = str(query.get("refresh") or "").lower() in ("1", "true", "yes")
        models, error = list_models(
            provider,
            base_url=query.get("base_url") or None,
            api_key=None,  # never accept a key over HTTP; env var only
            force=refresh,
        )
        return web.json_response(
            {
                "provider": provider.id,
                "providerLabel": provider.label,
                "models": models,
                "error": error,
                "source": "api" if not error else "fallback",
            }
        )

    LOGGER.info("[LLMAPI] routes ready: %s, %s", ROUTE_PROVIDERS, ROUTE_MODELS)
    return True


REGISTERED = _register()
