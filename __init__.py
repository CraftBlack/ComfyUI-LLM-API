"""ComfyUI LLM API - one node, several OpenAI-compatible gateways."""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

# Importing registers the /comfyui_llm_api/* routes. It is wrapped in its own
# module so a failure there can never stop the nodes from loading.
try:
    from . import routes  # noqa: F401
except Exception:  # noqa: BLE001 - nodes must still register
    import logging

    logging.getLogger("LLMAPI").exception("HTTP routes failed to register")

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
