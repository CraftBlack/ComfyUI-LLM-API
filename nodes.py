"""Universal multi-provider LLM node for ComfyUI.

One node, several OpenAI-compatible gateways (CometAPI, Grokified, or any
self-hosted server), chosen with the ``provider`` dropdown.

The outputs deliberately match ``ComfyUI-LLM-text-processor`` and
``ComfyUI-Grokified-API`` (RESPONSE / REASONING / PERF / USAGE_JSON) so an
existing workflow keeps working after you swap the node.
"""

from __future__ import annotations

import json
import logging
import time

from .images import image_to_parts
from .llm_api_client import (
    LLMAPIError,
    LLMAPIKeyError,
    chat_completion,
    list_models,
    resolve_api_key,
)
from .prompts import NO_SYSTEM_PROMPT, build_system_prompt, system_prompt_options
from .providers import (
    DEFAULT_PROVIDER,
    MAX_TOKENS_PARAM,
    MAX_TOKENS_PARAM_CHOICES,
    PROVIDER_LABELS,
    all_efforts,
    all_models,
    get_provider,
)

LOGGER = logging.getLogger("LLMAPI")

IMAGE_DETAILS = ["auto", "low", "high"]


# --------------------------------------------------------------------------- #
# usage / perf formatting
# --------------------------------------------------------------------------- #
def _usage_numbers(usage: dict) -> dict:
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    grokified = usage.get("grokified") or {}
    numbers = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "cached_tokens": prompt_details.get("cached_tokens"),
        "image_tokens": prompt_details.get("image_tokens"),
        "cost_usd": usage.get("cost_usd"),
        "charged_usd": grokified.get("charged_usd"),
        "list_usd": grokified.get("list_usd"),
        "balance_usd": grokified.get("balance_usd"),
    }
    # drop the nulls so USAGE_JSON stays readable
    return {k: v for k, v in numbers.items() if v is not None}


def _format_perf(provider_label: str, model: str, numbers: dict, elapsed: float, streamed: bool) -> str:
    parts = [model or "?"]
    prompt_tokens = numbers.get("prompt_tokens")
    completion_tokens = numbers.get("completion_tokens")
    reasoning_tokens = numbers.get("reasoning_tokens")
    if prompt_tokens is not None or completion_tokens is not None:
        token_text = f"{prompt_tokens or 0} in / {completion_tokens or 0} out"
        if reasoning_tokens:
            token_text += f" (reasoning {reasoning_tokens})"
        parts.append(token_text)
    parts.append(f"{elapsed:.2f}s" + (" stream" if streamed else ""))
    cost = numbers.get("charged_usd")
    if cost is None:
        cost = numbers.get("cost_usd")
    if cost is not None:
        try:
            parts.append(f"${float(cost):.6f}")
        except (TypeError, ValueError):
            pass
    if numbers.get("balance_usd") is not None:
        parts.append(f"balance ${float(numbers['balance_usd']):.2f}")
    return f"{provider_label.split(' (')[0]} | " + " | ".join(parts)


# --------------------------------------------------------------------------- #
# main node
# --------------------------------------------------------------------------- #
class LLMAPITextProcessor:
    """Text/vision LLM call against a choice of OpenAI-compatible gateways."""

    CATEGORY = "LLM/API"
    FUNCTION = "generate"
    TITLE = "LLM Text Processor (Multi-Provider API)"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("RESPONSE", "REASONING", "PERF", "USAGE_JSON")
    OUTPUT_TOOLTIPS = (
        "Final model response (reasoning removed).",
        "Reasoning text returned by the model, when it exposes any.",
        "Provider, model, token counts, latency and cost of the last call.",
        "Raw usage metadata as JSON, for logging or cost tracking.",
    )
    DESCRIPTION = (
        "Calls an OpenAI-compatible /chat/completions endpoint and returns the "
        "answer plus its reasoning trace. Pick the gateway with the provider "
        "dropdown; the model list and the reasoning levels follow it."
    )

    @classmethod
    def INPUT_TYPES(cls):
        models = all_models()
        default_model = DEFAULT_PROVIDER.default_model
        if default_model in models:
            models = [default_model] + [m for m in models if m != default_model]

        return {
            "required": {
                "provider": (PROVIDER_LABELS, {
                    "default": DEFAULT_PROVIDER.label,
                    "tooltip": "Which OpenAI-compatible gateway to call. Switching it reloads the model list.",
                }),
                "model": (models, {
                    "default": default_model,
                    "tooltip": "Model id for the selected provider. Refreshed live from the provider's /models endpoint.",
                }),
                "system_prompt": (system_prompt_options(), {
                    "default": NO_SYSTEM_PROMPT,
                    "tooltip": "System prompt preset from ComfyUI/models/LLM/prompts (same folder as the local LLM node).",
                }),
                "prompt": ("STRING", {
                    "default": "Describe this image in detail.",
                    "multiline": True,
                    "dynamicPrompts": True,
                    "tooltip": "User prompt sent to the model.",
                }),
                "max_tokens": ("INT", {
                    "default": 4096,
                    "min": 1,
                    "max": 131072,
                    "tooltip": "Maximum output tokens (sent as max_completion_tokens).",
                }),
                "temperature": ("FLOAT", {
                    "default": 0.7,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "tooltip": "Sampling temperature. Reasoning models may ignore it.",
                }),
                "top_p": ("FLOAT", {
                    "default": 0.95,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "tooltip": "Nucleus sampling threshold.",
                }),
                "reasoning_effort": (all_efforts(), {
                    "default": "high",
                    "tooltip": (
                        "'default' omits the field entirely. CometAPI accepts minimal..max; "
                        "Grokified accepts low/medium/high/xhigh."
                    ),
                }),
                "seed": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": 2**31 - 1,
                    "control_after_generate": True,
                    "tooltip": "Sent only when > 0. Also serves as ComfyUI's cache key, so bump it to force a new call.",
                }),
                "timeout_seconds": ("INT", {
                    "default": 300,
                    "min": 10,
                    "max": 3600,
                    "tooltip": "Maximum time to wait for the API response.",
                }),
            },
            "optional": {
                "image": ("IMAGE", {
                    "tooltip": "Optional image input. A single image or ComfyUI batch is sent to the vision model.",
                }),
                "system_prompt_text": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "Inline system prompt. Overrides the preset above when not empty.",
                }),
                "model_override": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Type any model id here to bypass the dropdown (needed for custom endpoints).",
                    "advanced": True,
                }),
                "api_key": ("STRING", {
                    "default": "",
                    "tooltip": "API key. Empty = read the provider's environment variable (COMETAPI_API_KEY / GROKIFIED_API_KEY).",
                    "advanced": True,
                }),
                "base_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Empty = the provider's own URL. The frontend fills this in when you switch provider.",
                    "advanced": True,
                }),
                "image_detail": (IMAGE_DETAILS, {
                    "default": "auto",
                    "tooltip": "Vision detail hint sent with every image (ignored by providers without the field).",
                    "advanced": True,
                }),
                "max_image_side": ("INT", {
                    "default": 2048,
                    "min": 0,
                    "max": 8192,
                    "step": 64,
                    "tooltip": "Downscale images whose longest side exceeds this. 0 disables resizing.",
                    "advanced": True,
                }),
                "max_images": ("INT", {
                    "default": 3,
                    "min": 1,
                    "max": 16,
                    "tooltip": "Maximum number of batch frames to send.",
                    "advanced": True,
                }),
                "stream": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Stream tokens: shows progress and avoids long-request timeouts.",
                    "advanced": True,
                }),
                "max_tokens_param": (list(MAX_TOKENS_PARAM_CHOICES), {
                    "default": MAX_TOKENS_PARAM,
                    "tooltip": "Wire name for the output cap. CometAPI ignores max_tokens, so leave this alone unless using a custom server.",
                    "advanced": True,
                }),
                "extra_body_json": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": 'Optional JSON merged into the request body, e.g. {"stop": ["\\n\\n"]}.',
                    "advanced": True,
                }),
                "enable_processing": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "When disabled, the input prompt is forwarded to RESPONSE without calling the API.",
                    "advanced": True,
                }),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        # The model/effort dropdowns are narrowed client-side per provider, so
        # the server must not reject values outside the static union list.
        return True

    def generate(
        self,
        provider: str,
        model: str,
        system_prompt: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        reasoning_effort: str,
        seed: int,
        timeout_seconds: int,
        image=None,
        system_prompt_text: str = "",
        model_override: str = "",
        api_key: str = "",
        base_url: str = "",
        image_detail: str = "auto",
        max_image_side: int = 2048,
        max_images: int = 3,
        stream: bool = True,
        max_tokens_param: str = MAX_TOKENS_PARAM,
        extra_body_json: str = "",
        enable_processing: bool = True,
    ):
        selected = get_provider(provider)

        if not enable_processing:
            return (prompt, "", "skipped (enable_processing = false)", "{}")

        chosen_model = (model_override or "").strip() or (model or "").strip()

        key = resolve_api_key(selected, api_key)
        if not key:
            raise LLMAPIKeyError(
                f"No API key for {selected.label}. Paste it into the api_key field or set "
                f"the {selected.key_env} environment variable, then retry."
            )

        system_text, warnings = build_system_prompt(system_prompt, system_prompt_text)
        for warning in warnings:
            LOGGER.warning("[LLMAPI] %s", warning)

        parts, image_warnings = image_to_parts(
            image,
            detail=image_detail,
            max_side=max_image_side,
            max_images=max_images,
            supports_detail=selected.supports_detail,
        )
        for warning in image_warnings:
            LOGGER.warning("[LLMAPI] %s", warning)

        messages: list[dict] = []
        if system_text:
            messages.append({"role": "system", "content": [{"type": "text", "text": system_text}]})
        user_content: list[dict] = [{"type": "text", "text": prompt}]
        user_content.extend(parts)
        messages.append({"role": "user", "content": user_content})

        extra_body = None
        if (extra_body_json or "").strip():
            try:
                extra_body = json.loads(extra_body_json)
            except json.JSONDecodeError as exc:
                raise LLMAPIError(f"extra_body_json is not valid JSON: {exc}") from exc
            if not isinstance(extra_body, dict):
                raise LLMAPIError("extra_body_json must be a JSON object")

        progress = None
        progress_step = 0
        if stream:
            try:
                import comfy.utils

                progress = comfy.utils.ProgressBar(int(max_tokens))
            except Exception:  # noqa: BLE001 - progress is cosmetic
                progress = None

        def on_delta(_kind: str) -> None:
            nonlocal progress_step
            if progress is None:
                return
            progress_step += 1
            if progress_step % 4 == 0:
                progress.update(4)  # update() is relative; ProgressBar clamps to total

        effective_base = (base_url or "").strip() or selected.base_url
        LOGGER.info(
            "[LLMAPI] %s | %s -> %s | %d image(s) | max_tokens=%s (%s) effort=%s stream=%s",
            selected.id,
            chosen_model,
            effective_base,
            len(parts),
            max_tokens,
            max_tokens_param,
            reasoning_effort,
            bool(stream),
        )

        started = time.perf_counter()
        result = chat_completion(
            messages=messages,
            model=chosen_model,
            provider=selected,
            base_url=effective_base,
            api_key=key,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            reasoning_effort=reasoning_effort,
            timeout=float(timeout_seconds),
            stream=bool(stream),
            max_tokens_param=max_tokens_param,
            extra_body=extra_body,
            on_delta=on_delta if progress is not None else None,
        )
        elapsed = time.perf_counter() - started

        for note in result.get("notes") or []:
            LOGGER.warning("[LLMAPI] %s", note)

        response_text = result.get("content") or ""
        reasoning_text = result.get("reasoning") or ""
        if not response_text and result.get("refusal"):
            response_text = str(result["refusal"])
            LOGGER.warning("[LLMAPI] model refused the request; returned the refusal text")

        if result.get("finish_reason") == "length":
            LOGGER.warning(
                "[LLMAPI] output hit max_tokens=%s; raise it if the response looks cut off",
                max_tokens,
            )

        numbers = _usage_numbers(result.get("usage") or {})
        notes = list(result.get("notes") or [])

        # CometAPI's grok-4.7 bills reasoning tokens but exposes the trace only
        # intermittently (measured: 2919 reasoning tokens with 0 characters of
        # reasoning_content, on both streaming and non-streaming calls). Say so
        # instead of leaving an unexplained empty REASONING output.
        if not reasoning_text and numbers.get("reasoning_tokens"):
            note = (
                f"the gateway reported {numbers['reasoning_tokens']} reasoning tokens but "
                "exposed no reasoning text for this call"
            )
            notes.append(note)
            LOGGER.warning("[LLMAPI] %s", note)

        reported_model = result.get("model") or chosen_model
        usage_json = json.dumps(
            {
                "provider": selected.id,
                "provider_label": selected.label,
                "base_url": result.get("base_url") or effective_base,
                "model": reported_model,
                "requested_model": chosen_model,
                "reasoning_effort": reasoning_effort,
                "finish_reason": result.get("finish_reason"),
                "streamed": result.get("streamed"),
                "elapsed_seconds": round(elapsed, 3),
                "images_sent": len(parts),
                "reasoning_chars": len(reasoning_text),
                "notes": notes,
                **numbers,
            },
            ensure_ascii=False,
        )
        perf = _format_perf(
            result.get("provider_label") or selected.label,
            reported_model,
            numbers,
            elapsed,
            bool(result.get("streamed")),
        )

        if response_text:
            LOGGER.info(
                "[LLMAPI] done in %.2fs | %s chars out | %s chars reasoning",
                elapsed,
                len(response_text),
                len(reasoning_text),
            )
        else:
            LOGGER.warning("[LLMAPI] the model returned an empty response (%s)", perf)

        return (response_text, reasoning_text, perf, usage_json)


# --------------------------------------------------------------------------- #
# helper node: browse a gateway's catalogue
# --------------------------------------------------------------------------- #
class LLMAPIModelList:
    """Print the chat models a gateway currently advertises."""

    CATEGORY = "LLM/API"
    FUNCTION = "fetch"
    TITLE = "LLM API Model List"
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("MODELS", "COUNT")
    OUTPUT_TOOLTIPS = (
        "Newline-separated model ids, one per line.",
        "How many models were returned.",
    )
    DESCRIPTION = (
        "Calls GET /models on the selected provider and returns the chat-capable "
        "model ids. Handy for discovering what a gateway offers right now."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "provider": (PROVIDER_LABELS, {
                    "default": DEFAULT_PROVIDER.label,
                    "tooltip": "Gateway to query.",
                }),
                "refresh": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Bypass the 5-minute cache and hit the endpoint again.",
                }),
            },
            "optional": {
                "api_key": ("STRING", {
                    "default": "",
                    "tooltip": "API key. Empty = the provider's environment variable.",
                    "advanced": True,
                }),
                "base_url": ("STRING", {
                    "default": "",
                    "tooltip": "Empty = the provider's own URL.",
                    "advanced": True,
                }),
                "filter": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Case-insensitive substring filter, e.g. 'grok'.",
                }),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    def fetch(
        self,
        provider: str,
        refresh: bool = True,
        api_key: str = "",
        base_url: str = "",
        filter: str = "",
    ):
        selected = get_provider(provider)
        models, error = list_models(
            selected,
            base_url=(base_url or "").strip() or selected.base_url,
            api_key=api_key,
            force=bool(refresh),
        )
        if error:
            LOGGER.warning("[LLMAPI] model list for %s: %s", selected.id, error)

        needle = (filter or "").strip().lower()
        if needle:
            models = [m for m in models if needle in m.lower()]

        return ("\n".join(models), len(models))


NODE_CLASS_MAPPINGS = {
    "LLMAPITextProcessor": LLMAPITextProcessor,
    "LLMAPIModelList": LLMAPIModelList,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLMAPITextProcessor": "LLM Text Processor (Multi-Provider API)",
    "LLMAPIModelList": "LLM API Model List",
}
