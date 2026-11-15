"""Provider registry for the universal ComfyUI LLM API nodes.

Every provider is an OpenAI-compatible ``/chat/completions`` gateway, so the
only things that differ are: where it lives, which environment variable holds
the key, which models it exposes, and which optional request fields it accepts.

Everything in this module was verified against the live endpoints - see
``tests/probe_*.py`` and the "Verified behaviour" section of README.md.
"""

from __future__ import annotations

import os
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
    #: local servers and proxies often run without auth; when False an empty
    #: key is sent as no Authorization header instead of raising.
    requires_key: bool = True
    #: attempts for transient failures; slow/flaky gateways can lower this.
    max_attempts: int = 4
    #: the gateway only reports ``usage`` over SSE when ``stream_options`` asks
    #: for it (verified on Coarena and Grok Build; CometAPI and Grokified
    #: always include it).
    stream_usage: bool = False

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
            "requiresKey": self.requires_key,
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

COARENA = Provider(
    id="coarena",
    label="Coarena (coarena.ai)",
    base_url="https://coarena.ai/api/v1",
    key_env="COARENA_API_KEY",
    key_hint="coa_...",
    default_model="grok-4-7",
    fallback_models=(
        "grok-4-7",
        "claude-fable-5",
        "claude-fable-5-1",
        "claude-fable-5-2",
        "claude-haiku-5-5",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "deepseek-v4-1-flash-1017",
        "gemini-3-8-flash",
        "gemini-4-argon",
        "gemini-4-carbon",
        "glm-5-3-flash",
        "hy4",
        "kimi-k3",
        "kimi-k3-1-pro",
        "mimo-v2-6-pro",
        "minimax-m3-1-flash",
        "qwen3-8-2-4t-a95b",
    ),
    # /models advertises low..xhigh, but the live endpoint also accepts
    # minimal and max (verified 2026-11-14). The bare word "default" is
    # rejected with HTTP 400, so it is stripped before the call like CometAPI.
    reasoning_efforts=("default", "minimal", "low", "medium", "high", "xhigh", "max"),
    docs_url="https://coarena.ai/docs",
    notes=(
        "Free tier: $5 of model use per day, 30 requests/min, 2 concurrent. "
        "max_completion_tokens caps the visible answer only; reasoning tokens "
        "are added on top. GET /models needs no key."
    ),
    stream_usage=True,
)

BAZA_AI = Provider(
    id="bazaai",
    label="Baza AI (baza-ai.org)",
    base_url="https://api.baza-ai.org/v1",
    key_env="BAZAAI_API_KEY",
    key_hint="ks_live_...",
    default_model="grok-4.7",
    fallback_models=(
        "grok-4.7",
        "gpt-6.1-terra",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
        "claude-sonnet-5-5",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
        "k3.1-pro",
        "k3",
        "k2.7-code",
        "mistral-medium-3.5",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "minimax-m2.7-highspeed",
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.2",
        "kimi-k2.6",
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "qwen3.7-plus",
        "qwen3.6-plus",
        "glm-5.1",
        "hy3",
        "grok-4.5",
        "grok-4.6",
        "qwen3.8-max",
        "gemini-3.1-pro",
        "gemini-3-flash",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gpt-6.1-sol",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
    ),
    # Live calls accepted minimal..max (verified 2026-11-15); "none" made the
    # gateway answer 502, and the node strips both "none" and "default".
    reasoning_efforts=("default", "minimal", "low", "medium", "high", "xhigh", "max"),
    notes=(
        "/models carries no capability metadata and the response model id is "
        "namespaced (e.g. gcli/grok-4.7). Reasoning text is streamed only; "
        "non-stream replies expose none."
    ),
)

def _grok_build_base() -> str:
    """The real Funnel URL comes from GROK_BUILD_URL; the default is redacted."""
    base = (os.environ.get("GROK_BUILD_URL") or "https://redacted.tail341768.ts.net:8443").rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


GROK_BUILD = Provider(
    id="grokbuild",
    label="Grok Build (local proxy)",
    base_url=_grok_build_base(),
    key_env="GROK_API_KEY",
    key_hint="key from kunci_api_grok.txt",
    default_model="grok-4.7",
    fallback_models=("grok-4.7",),
    # Live calls accepted minimal..xhigh (verified 2026-11-15); "max", "none"
    # and the literal "default" are rejected by the backend.
    reasoning_efforts=("default", "minimal", "low", "medium", "high", "xhigh"),
    notes=(
        "Tailscale Funnel in front of Get API Grok Build/api_server_tailscale.py "
        "(local port 8010). The committed hostname is redacted: set GROK_BUILD_URL "
        "(or the node's base_url) to your real Funnel URL. The key is stored in "
        "kunci_api_grok.txt on the proxy machine and is required, even for /health. "
        "The backend rejects images below 512 px total."
    ),
    stream_usage=True,
)

HOWTOFIX = Provider(
    id="howtofix",
    label="HowToFix ID (howtofix.id)",
    base_url="https://api.howtofix.id/v1",
    key_env="HOWTOFIX_API_KEY",
    key_hint="sk-...",
    default_model="grok-4.7",
    fallback_models=(
        "grok-4.7",
        "grok-4.7-liberated-sg",
        "grok-4.6",
        "grok-4.6-liberated-sg",
        "grok-4.5",
        "grok-4.5-liberated-sg",
        "big-pickle",
        "deepseek-v4.1-flash",
        "deepseek-v4.1-flash-1017",
        "deepSeek-v4.1-flash-1017-sg",
        "glm-5.3-flash",
        "mimo-2.6-flash",
    ),
    # Verified live 2026-11-15: minimal/medium/xhigh answered quickly; low/high
    # timed out on a 45s read (gateway slowness, not a 400 rejection).
    reasoning_efforts=("default", "minimal", "low", "medium", "high", "xhigh"),
    docs_url="https://howtofix.id/docs",
    notes=(
        "Distributor gateway (group free); calls can be slow or bursty (a "
        "stream took >2 minutes to first byte). max_completion_tokens caps the "
        "visible answer only; reasoning is added on top and exposed as "
        "reasoning_content. Usage has no reasoning_tokens field."
    ),
    max_attempts=2,
)

def _cloudflare_base() -> str:
    """The real account id comes from CLOUDFLARE_ACCOUNT_ID; default redacted."""
    account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "redacted").strip()
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"


CLOUDFLARE = Provider(
    id="cloudflare",
    label="Cloudflare Workers AI (cloudflare.com)",
    base_url=_cloudflare_base(),
    key_env="CLOUDFLARE_API_TOKEN",
    key_hint="cfut_...",
    default_model="@cf/google/gemma-4-26b-a4b-it",
    fallback_models=("@cf/google/gemma-4-26b-a4b-it",),
    # Verified live 2026-11-15: low/medium/high/max answered; the model always
    # emits reasoning, so "none" does not turn the trace off.
    reasoning_efforts=("default", "low", "medium", "high", "max"),
    docs_url="https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/",
    notes=(
        "OpenAI-compatible Workers AI endpoint. Set CLOUDFLARE_ACCOUNT_ID "
        "(the committed default is redacted) and CLOUDFLARE_API_TOKEN. "
        "GET /v1/models is not supported (405), so the combo uses a static "
        "fallback; type other @cf/ ids into model_override. The default "
        "gemma-4-26b-a4b-it is text+vision+reasoning (256k context), and "
        "max_completion_tokens caps reasoning+content together."
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
    requires_key=False,
)

PROVIDERS: tuple[Provider, ...] = (
    COMETAPI,
    GROKIFIED,
    COARENA,
    BAZA_AI,
    GROK_BUILD,
    HOWTOFIX,
    CLOUDFLARE,
    CUSTOM,
)

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
