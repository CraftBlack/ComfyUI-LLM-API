"""Provider registry for the universal ComfyUI LLM API nodes.

Every provider is an OpenAI-compatible ``/chat/completions`` gateway, so the
only things that differ are: where it lives, which environment variable holds
the key, which models it exposes, and which optional request fields it accepts.

Everything in this module was verified against the live endpoints - see
``tests/probe_*.py`` and the "Verified behaviour" section of README.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# shared combo values
# --------------------------------------------------------------------------- #
#: ``default`` means "do not send reasoning_effort at all" - both gateways
#: reject the literal string ``default``, so it is stripped before the call.
NO_EFFORT = "default"

ALL_EFFORTS: tuple[str, ...] = ("default", "minimal", "low", "medium", "high", "xhigh", "max")

#: Both live gateways cap output with ``max_completion_tokens``.
#: CometAPI silently *ignores* ``max_tokens`` (measured: a value of 40 produced
#: 3426 completion tokens), so ``max_completion_tokens`` is the default and
#: ``max_tokens`` is only offered as a fallback for custom servers.
MAX_TOKENS_PARAM = "max_completion_tokens"
MAX_TOKENS_PARAM_CHOICES = ("max_completion_tokens", "max_tokens")


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible gateway."""

    id: str
    label: str
    base_url: str
    key_env: str
    default_model: str
    fallback_models: tuple[str, ...]
    reasoning_efforts: tuple[str, ...] = ALL_EFFORTS
    key_hint: str = ""
    docs_url: str = ""
    notes: str = ""

    # capability flags - drive both the node widgets and the request body
    supports_vision: bool = True
    supports_stream: bool = True
    supports_seed: bool = True
    supports_temperature: bool = True
    supports_detail: bool = True
    max_tokens_param: str = MAX_TOKENS_PARAM

    #: ``/models`` entries are kept only when this key exists on the entry
    #: (CometAPI ships 472 ids of which only 15 are chat models).
    model_filter_key: str = ""
    #: value that :attr:`model_filter_key` must contain.
    model_filter_value: str = ""

    def to_dict(self) -> dict:
        """JSON shape consumed by ``web/js/llm_api.js``."""
        return {
            "id": self.id,
            "label": self.label,
            "baseUrl": self.base_url,
            "keyEnv": self.key_env,
            "keyHint": self.key_hint,
            "defaultModel": self.default_model,
            "models": list(self.fallback_models),
            "efforts": list(self.reasoning_efforts),
            "supportsVision": self.supports_vision,
            "supportsSeed": self.supports_seed,
            "supportsTemperature": self.supports_temperature,
            "supportsDetail": self.supports_detail,
            "maxTokensParam": self.max_tokens_param,
            "docsUrl": self.docs_url,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
COMETAPI = Provider(
    id="cometapi",
    label="CometAPI (cometapi.com)",
    base_url="https://api.cometapi.com/v1",
    key_env="COMETAPI_API_KEY",
    key_hint="sk-...",
    default_model="grok-4.7",
    fallback_models=(
        "grok-4.7",
        "grok-build-0.1",
        "deepseek-v4.1-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash-vision-exp",
        "deepseek-flash",
        "gpt-6-astra",
        "claude-opus-4-8",
        "claude-sonnet-5-5",
        "gemini-4-argon",
        "glm-5.3-flashx",
        "kimi-k3.1-pro",
        "mimo-v2.6-pro-ultraspeed",
        "qwen3.8-omni-flash",
        "auto",
    ),
    # Verified 2026-11-06 on api.cometapi.com: minimal/low/medium/high/xhigh/max
    # all return HTTP 200 and all emit reasoning tokens.  "none"/"disabled"
    # return HTTP 400 and the bare word "default" returns
    # {"code":"invalid-argument","error":"Invalid reasoning effort."}
    reasoning_efforts=("default", "minimal", "low", "medium", "high", "xhigh", "max"),
    docs_url="https://apidoc.cometapi.com/",
    notes=(
        "472 model ids in /models but only the 15 tagged openai-chat accept "
        "/chat/completions; the node filters to those automatically."
    ),
    model_filter_key="supported_endpoint_types",
    model_filter_value="openai-chat",
)

GROKIFIED = Provider(
    id="grokified",
    label="Grokified API (grokified.com)",
    base_url="https://api.grokified.com/v1",
    key_env="GROKIFIED_API_KEY",
    default_model="grok-4.7",
    fallback_models=(
        "grok-4.7",
        "grok-4.6",
        "grok-4.5",
        "grok-4.3",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-4.20-multi-agent-0309",
        "grok-build-0.1",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-opus-4-8",
    ),
    # https://grokified.com/docs/reasoning - low/medium/high/xhigh only.
    reasoning_efforts=("default", "low", "medium", "high", "xhigh"),
    docs_url="https://grokified.com/docs",
    notes=(
        "Returns HTTP 402 with a top-up message when the account has no "
        "credit; the node surfaces that verbatim instead of retrying."
    ),
)

CUSTOM = Provider(
    id="custom",
    label="Custom / self-hosted (OpenAI-compatible)",
    base_url="http://127.0.0.1:1234/v1",
    key_env="LLM_API_KEY",
    key_hint="often empty for local servers",
    default_model="local-model",
    fallback_models=("local-model",),
    reasoning_efforts=ALL_EFFORTS,
    docs_url="",
    notes=(
        "Point base_url at any OpenAI-compatible server (LM Studio, Ollama "
        "/v1, vLLM, llama.cpp server, OpenRouter, ...). Type the model id into "
        "model_override; the dropdown only carries the built-in names."
    ),
    supports_seed=True,
    supports_detail=False,
)

PROVIDERS: tuple[Provider, ...] = (COMETAPI, GROKIFIED, CUSTOM)

BY_ID: dict[str, Provider] = {p.id: p for p in PROVIDERS}
BY_LABEL: dict[str, Provider] = {p.label: p for p in PROVIDERS}

DEFAULT_PROVIDER_ID = COMETAPI.id
DEFAULT_PROVIDER = COMETAPI

PROVIDER_LABELS: list[str] = [p.label for p in PROVIDERS]


def get_provider(value: str | None) -> Provider:
    """Resolve an id *or* a combo label; falls back to the default provider."""
    text = (value or "").strip()
    if not text:
        return DEFAULT_PROVIDER
    if text in BY_LABEL:
        return BY_LABEL[text]
    lowered = text.lower()
    for provider in PROVIDERS:
        if provider.id == lowered:
            return provider
    for provider in PROVIDERS:
        if provider.label.lower() == lowered:
            return provider
    return DEFAULT_PROVIDER


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def all_models() -> list[str]:
    """Union of every provider catalogue, defaults first.

    The combo widget is built from this list; ``web/js/llm_api.js`` narrows it
    to the selected provider at runtime and ``VALIDATE_INPUTS`` accepts
    anything, so the union never blocks a valid choice.
    """
    preferred = _dedupe(p.default_model for p in PROVIDERS)
    rest = sorted(
        _dedupe(m for p in PROVIDERS for m in p.fallback_models if m not in preferred)
    )
    return preferred + rest


def all_efforts() -> list[str]:
    """Union of every provider's reasoning levels, in a stable order."""
    seen = set(_dedupe(e for p in PROVIDERS for e in p.reasoning_efforts))
    return [e for e in ALL_EFFORTS if e in seen]


def registry_json() -> dict:
    """Payload for the ``/comfyui_llm_api/providers`` route."""
    return {
        "defaultProvider": DEFAULT_PROVIDER_ID,
        "providers": [p.to_dict() for p in PROVIDERS],
    }
