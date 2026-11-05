"""Provider-agnostic OpenAI-compatible client for the ComfyUI LLM API nodes.

Deliberately dependency-light: only ``requests``, which ComfyUI already ships,
so nothing extra has to be installed.

Endpoints used (both gateways implement them identically):

    GET  {base_url}/models            -> catalogue, used to fill the combo widget
    POST {base_url}/chat/completions  -> chat completion (streaming or not)

Verified gateway quirks this module compensates for:

* ``grok-4.7`` on CometAPI ignores ``max_tokens`` (a value of 40 produced 3426
  completion tokens) but honours ``max_completion_tokens`` exactly.
* CometAPI answers an unknown model with HTTP **503** ``model_not_found`` -
  retrying that is pointless, so it is classified before the retry table.
* CometAPI rejects the literal effort ``"default"``/``"none"`` with HTTP 400,
  so unsupported values are stripped and the call is retried once.
* Grokified answers an unfunded account with HTTP 402 and a human-readable
  top-up message; that is surfaced verbatim rather than retried.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests

from .providers import (
    MAX_TOKENS_PARAM,
    NO_EFFORT,
    Provider,
    get_provider,
)

#: HTTP statuses worth a retry: transient gateway/rate-limit problems.
RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 504})

#: Error codes that mean "this request will never succeed" regardless of status.
FATAL_CODES = frozenset(
    {
        "model_not_found",
        "invalid_request_error",
        "invalid_api_key",
        "insufficient_quota",
        "context_length_exceeded",
    }
)

_STOP_WORDS = {"", NO_EFFORT, "none", "off", "auto", "disabled", "false"}


class LLMAPIError(RuntimeError):
    """Non-recoverable gateway problem (bad request, server error, ...)."""


class LLMAPIKeyError(LLMAPIError):
    """Missing or rejected API key."""


class LLMAPIQuotaError(LLMAPIError):
    """The account has no credit / no active plan (HTTP 402)."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def resolve_api_key(provider: Provider | str | None, value: str | None = None) -> str:
    """Widget value first, then the provider's environment variable."""
    key = (value or "").strip()
    if key:
        return key
    provider = get_provider(provider) if not isinstance(provider, Provider) else provider
    return (os.environ.get(provider.key_env) or "").strip()


def normalize_base_url(value: str | None, provider: Provider | str | None = None) -> str:
    """Turn ``host``, ``host/`` or ``host/v1`` into a usable base URL."""
    provider = get_provider(provider) if not isinstance(provider, Provider) else provider
    url = (value or "").strip() or provider.base_url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.rstrip("/")
    parts = urlsplit(url)
    if parts.path in ("", "/"):
        url = urlunsplit((parts.scheme, parts.netloc, "/v1", "", ""))
    return url


def mask_key(key: str) -> str:
    if not key:
        return "(none)"
    if len(key) <= 12:
        return key[:4] + "..."
    return f"{key[:8]}...{key[-4:]}"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _as_text(value: Any) -> str:
    """Message content may be a string or a list of ``{type, text}`` parts."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out: list[str] = []
        for part in value:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                out.append(_as_text(part.get("text") or part.get("content")))
        return "".join(out)
    return str(value)


def _clean_effort(value: str | None) -> str:
    return str(value or "").strip().lower()


# --------------------------------------------------------------------------- #
# error introspection
# --------------------------------------------------------------------------- #
def _error_payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_details(response: requests.Response) -> tuple[str, str]:
    """Return ``(code, message)`` for a failed response, defensively.

    CometAPI wraps a JSON string inside ``error.message``; the inner ``code``
    is more useful than the outer HTTP status, so it is unwrapped.
    """
    payload = _error_payload(response)
    if payload is None:
        return "", (response.text or "").strip()[:800]

    if not isinstance(payload, dict):
        return "", str(payload)[:800]

    error = payload.get("error")
    if isinstance(error, str):
        return "", error[:800]
    if not isinstance(error, dict):
        return "", str(payload.get("message") or payload)[:800]

    code = str(error.get("code") or "")
    message = error.get("message")

    # {"error":{"message":"{\"code\":\"invalid-argument\",...}（traceid...)","type":"comet_api_error"}}
    # The JSON is followed by a trace id in full-width parentheses, so only the
    # leading object is decoded (raw_decode stops at the matching brace).
    if isinstance(message, str) and message.lstrip().startswith("{"):
        try:
            inner, _end = json.JSONDecoder().raw_decode(message.lstrip())
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            code = code or str(inner.get("code") or "")
            message = inner.get("error") or inner.get("message") or message

    if not message:
        message = error.get("type") or json.dumps(error)
    return code, str(message)[:800]


def _is_fatal(status: int, code: str, message: str) -> bool:
    if code and code in FATAL_CODES:
        return True
    text = (message or "").lower()
    return "model_not_found" in text or "no available channel" in text


# --------------------------------------------------------------------------- #
# model catalogue
# --------------------------------------------------------------------------- #
_MODEL_CACHE: dict[str, Any] = {"entries": {}, "errors": {}}
MODEL_CACHE_TTL = 300.0
MODEL_CACHE_FAIL_TTL = 45.0


def clear_model_cache() -> None:
    _MODEL_CACHE["entries"].clear()
    _MODEL_CACHE["errors"].clear()


def _filter_models(provider: Provider, items: list[Any]) -> list[str]:
    models: list[str] = []
    for item in items:
        if isinstance(item, dict):
            model_id = item.get("id")
            if provider.model_filter_key and provider.model_filter_value:
                tags = item.get(provider.model_filter_key) or []
                if isinstance(tags, str):
                    tags = [tags]
                if provider.model_filter_value not in tags:
                    continue
        else:
            model_id = item
        if isinstance(model_id, str) and model_id and model_id not in models:
            models.append(model_id)
    return models


def list_models(
    provider: Provider | str | None = None,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
    force: bool = False,
) -> tuple[list[str], str | None]:
    """Model ids from ``GET /models``, cached per provider.

    Returns ``(models, error)``. On failure the provider's static fallback list
    is returned together with a human-readable error, so node registration and
    the combo widget never break.
    """
    provider = get_provider(provider) if not isinstance(provider, Provider) else provider
    base = normalize_base_url(base_url, provider)
    key = resolve_api_key(provider, api_key)
    cache_key = f"{provider.id}|{base}|{key}"

    now = time.time()
    entry = _MODEL_CACHE["entries"].get(cache_key)
    if entry and not force:
        ttl = MODEL_CACHE_FAIL_TTL if entry["error"] else MODEL_CACHE_TTL
        if now - entry["ts"] < ttl:
            return list(entry["models"]), entry["error"]

    models: list[str] = []
    error: str | None = None

    if not key:
        error = f"no API key (widget empty and {provider.key_env} unset)"
    else:
        try:
            response = requests.get(f"{base}/models", headers=_headers(key), timeout=timeout)
            if response.status_code == 200:
                payload = response.json()
                items = payload.get("data") if isinstance(payload, dict) else payload
                models = _filter_models(provider, list(items or []))
                if not models:
                    error = "the API returned no chat-capable models"
            else:
                code, message = _error_details(response)
                error = f"HTTP {response.status_code}: {message}"
                if code:
                    error = f"HTTP {response.status_code} [{code}]: {message}"
        except Exception as exc:  # noqa: BLE001 - never break node registration
            error = f"{type(exc).__name__}: {exc}"

    if not models:
        models = list(provider.fallback_models)

    _MODEL_CACHE["entries"][cache_key] = {"ts": now, "models": list(models), "error": error}
    return models, error


def cached_models(provider: Provider | str | None = None) -> list[str]:
    """Last known catalogue for ``provider`` without touching the network."""
    provider = get_provider(provider) if not isinstance(provider, Provider) else provider
    for cache_key, entry in _MODEL_CACHE["entries"].items():
        if cache_key.startswith(f"{provider.id}|"):
            return list(entry["models"])
    return list(provider.fallback_models)


# --------------------------------------------------------------------------- #
# request body
# --------------------------------------------------------------------------- #
def build_body(
    *,
    provider: Provider,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    seed: int | None = None,
    reasoning_effort: str | None = None,
    stream: bool = True,
    max_tokens_param: str = "",
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the JSON body, skipping what this provider does not accept."""
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": bool(stream)}

    token_field = (max_tokens_param or provider.max_tokens_param or MAX_TOKENS_PARAM).strip()
    if max_tokens:
        body[token_field] = int(max_tokens)
    if temperature is not None and provider.supports_temperature:
        body["temperature"] = float(temperature)
    if top_p is not None and provider.supports_temperature:
        body["top_p"] = float(top_p)
    if seed and provider.supports_seed:
        body["seed"] = int(seed)

    effort = _clean_effort(reasoning_effort)
    if effort and effort not in _STOP_WORDS:
        body["reasoning_effort"] = effort

    if extra_body:
        body.update(extra_body)
    return body


def _downgrade(body: dict[str, Any], message: str) -> str | None:
    """Drop/convert the single parameter the gateway complained about.

    Both gateways mix model families that accept different optional fields, so
    a rejected parameter is removed and the request retried rather than failing
    the whole node.
    """
    # normalise "reasoning effort" / "`reasoning_effort`" / "REASONING_EFFORT"
    text = (message or "").lower().replace("`", "").replace("-", "_")
    compact = text.replace(" ", "").replace("_", "")

    def mentions(name: str) -> bool:
        return name in text or name.replace("_", "") in compact

    for field in ("max_completion_tokens", "max_tokens"):
        if mentions(field) and field in body:
            target = "max_tokens" if field == "max_completion_tokens" else "max_completion_tokens"
            body[target] = body.pop(field)
            return f"server rejected {field} -> resent as {target}"

    if mentions("reasoning_effort") and "reasoning_effort" in body:
        body.pop("reasoning_effort")
        return "server rejected reasoning_effort -> omitted"

    for field in ("seed", "temperature", "top_p", "stop", "stream_options", "frequency_penalty", "presence_penalty"):
        if mentions(field) and field in body:
            body.pop(field)
            return f"server rejected {field} -> omitted"

    if "detail" in text:
        removed = False
        for msg in body.get("messages") or []:
            for part in msg.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("image_url"), dict):
                    if part["image_url"].pop("detail", None) is not None:
                        removed = True
        if removed:
            return "server rejected image detail -> omitted"

    if "stream" in text and body.get("stream"):
        body["stream"] = False
        return "server rejected stream -> retried without streaming"

    return None


# --------------------------------------------------------------------------- #
# response parsing
# --------------------------------------------------------------------------- #
def _parse_completion(data: dict[str, Any]) -> dict[str, Any]:
    choice = (data.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    return {
        "content": _as_text(message.get("content")),
        # CometAPI + Grokified both use reasoning_content; other gateways use
        # "reasoning" (OpenRouter) or "thinking" (some self-hosted servers).
        "reasoning": _as_text(
            message.get("reasoning_content")
            or message.get("reasoning")
            or message.get("thinking")
        ),
        "refusal": message.get("refusal"),
        "usage": data.get("usage") or {},
        "finish_reason": choice.get("finish_reason"),
        "model": data.get("model"),
        "streamed": False,
    }


def _parse_stream(
    response: requests.Response, on_delta: Callable[[str], None] | None
) -> dict[str, Any]:
    content: list[str] = []
    reasoning: list[str] = []
    refusal: str | None = None
    usage: dict[str, Any] = {}
    finish_reason = None
    model = None

    for raw in response.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        if not payload:
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(chunk, dict):
            continue

        if isinstance(chunk.get("model"), str):
            model = chunk["model"]
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        # some servers attach the error inside the stream instead of a status
        if chunk.get("error"):
            err = chunk["error"]
            raise LLMAPIError(
                f"stream error: {err.get('message') if isinstance(err, dict) else err}"
            )

        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or choice.get("message") or {}

            piece = _as_text(delta.get("content"))
            if piece:
                content.append(piece)
                if on_delta:
                    on_delta("content")

            piece = _as_text(
                delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
            )
            if piece:
                reasoning.append(piece)
                if on_delta:
                    on_delta("reasoning")

            if delta.get("refusal"):
                refusal = (refusal or "") + _as_text(delta["refusal"])
                if on_delta:
                    on_delta("refusal")

    return {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "refusal": refusal,
        "usage": usage,
        "finish_reason": finish_reason,
        "model": model,
        "streamed": True,
    }


# --------------------------------------------------------------------------- #
# the call
# --------------------------------------------------------------------------- #
def chat_completion(
    *,
    messages: list[dict[str, Any]],
    model: str,
    provider: Provider | str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    seed: int | None = None,
    reasoning_effort: str | None = None,
    timeout: float = 300.0,
    stream: bool = True,
    max_tokens_param: str = "",
    extra_body: dict[str, Any] | None = None,
    max_attempts: int = 4,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """POST ``/chat/completions`` with retries and parameter auto-downgrade."""
    provider = get_provider(provider) if not isinstance(provider, Provider) else provider
    base = normalize_base_url(base_url, provider)
    key = resolve_api_key(provider, api_key)
    if not key:
        raise LLMAPIKeyError(
            f"No API key for {provider.label}. Paste it into the node's api_key field "
            f"or set the {provider.key_env} environment variable."
        )
    if not (model or "").strip():
        raise LLMAPIError(
            f"No model selected for {provider.label}. Pick one from the model dropdown "
            "or type an id into model_override."
        )

    body = build_body(
        provider=provider,
        model=model.strip(),
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        reasoning_effort=reasoning_effort,
        stream=stream,
        max_tokens_param=max_tokens_param,
        extra_body=extra_body,
    )

    notes: list[str] = []
    started = time.perf_counter()
    last_error: str | None = None

    for attempt in range(max_attempts):
        streaming = bool(body.get("stream"))
        request_started = time.perf_counter()
        try:
            response = requests.post(
                f"{base}/chat/completions",
                headers=_headers(key),
                json=body,
                timeout=(20.0, float(timeout)),
                stream=streaming,
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts - 1:
                time.sleep(min(2.0**attempt, 8.0))
                continue
            raise LLMAPIError(f"Could not reach {base}: {exc}") from exc

        try:
            if response.status_code == 200:
                try:
                    result = _parse_stream(response, on_delta) if streaming else _parse_completion(response.json())
                except json.JSONDecodeError as exc:
                    raise LLMAPIError(
                        f"{provider.label} returned HTTP 200 with a body that is not JSON: {exc}"
                    ) from exc
                result["elapsed"] = time.perf_counter() - started
                result["request_seconds"] = time.perf_counter() - request_started
                result["notes"] = notes
                result["provider"] = provider.id
                result["provider_label"] = provider.label
                result["base_url"] = base
                result["request"] = {
                    "model": body.get("model"),
                    "base_url": base,
                    "provider": provider.id,
                    "stream": streaming,
                    "params": sorted(k for k in body if k not in ("messages", "model")),
                }
                return result

            code, detail = _error_details(response)

            if response.status_code in (401, 403):
                raise LLMAPIKeyError(
                    f"{provider.label} rejected the API key ({mask_key(key)}): HTTP "
                    f"{response.status_code} {detail}"
                )

            if response.status_code == 402:
                raise LLMAPIQuotaError(
                    f"{provider.label} says the account cannot pay for this request "
                    f"(HTTP 402): {detail}"
                )

            if _is_fatal(response.status_code, code, detail):
                raise LLMAPIError(
                    f"{provider.label} cannot serve model '{body.get('model')}' "
                    f"(HTTP {response.status_code}{f' [{code}]' if code else ''}): {detail}"
                )

            if response.status_code == 400:
                note = _downgrade(body, detail)
                if note:
                    notes.append(note)
                    continue
                raise LLMAPIError(
                    f"HTTP 400 from {provider.label} for model '{body.get('model')}': {detail}"
                )

            if response.status_code in RETRY_STATUSES and attempt < max_attempts - 1:
                last_error = f"HTTP {response.status_code}: {detail}"
                time.sleep(min(2.0**attempt, 8.0))
                continue

            raise LLMAPIError(
                f"HTTP {response.status_code} from {provider.label}"
                f"{f' [{code}]' if code else ''}: {detail}"
            )
        finally:
            response.close()

    raise LLMAPIError(
        f"{provider.label} request failed after {max_attempts} attempts"
        + (f": {last_error}" if last_error else "")
    )
