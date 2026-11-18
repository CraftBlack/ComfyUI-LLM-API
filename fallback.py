"""Fallback chain ("batch API") for the multi-provider LLM node.

The primary endpoint is the node's own ``provider`` / ``model`` / ``base_url`` /
``api_key`` widgets. ``fallback_chain`` adds more endpoints that are tried, in
order, whenever an earlier one fails, so a dead or unfunded gateway never stops
a workflow.

Two syntaxes are accepted.

Line syntax - one endpoint per line, ``#`` starts a comment::

    crusoe | google/gemma-4-31b-it
    fastrouter | google/gemma-4-31b-it-ultra
    custom | my-model | https://my-host/v1 | $MY_API_KEY

Only the provider is required; the other slots may be left empty or written as
``-``. ``api_key`` and ``base_url`` accept ``$ENV_VAR`` / ``env:ENV_VAR`` so the
secret does not have to live in the workflow JSON.

JSON syntax::

    [
      {"provider": "crusoe", "model": "google/gemma-4-31b-it"},
      {"provider": "custom", "base_url": "http://127.0.0.1:1234/v1",
       "model": "local-model", "api_key": "$LOCAL_LLM_KEY"}
    ]

Shorthand: when the text contains no ``|``, it is split on commas, semicolons
and newlines, so ``crusoe, fastrouter`` is a valid two-entry chain.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .providers import BY_ID, BY_LABEL, PROVIDERS, Provider

#: Slots written like this mean "use the provider's own default".
_EMPTY = {"", "-", "--", "—", "none", "null", "nil", "~"}


class ChainConfigError(ValueError):
    """``fallback_chain`` could not be parsed; the message names the problem."""


@dataclass(frozen=True)
class ChainEntry:
    """One endpoint in the chain. Empty fields inherit the provider defaults."""

    provider: Provider
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    stream: bool | None = None
    timeout: float | None = None
    source: str = ""

    @property
    def model_id(self) -> str:
        return self.model or self.provider.default_model

    def describe(self) -> str:
        return f"{self.provider.id}:{self.model_id}"


# --------------------------------------------------------------------------- #
# scalar helpers
# --------------------------------------------------------------------------- #
def _clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in _EMPTY else text


def _expand_env(value: str) -> str:
    """``$NAME`` / ``${NAME}`` / ``env:NAME`` -> the environment variable."""
    text = _clean(value)
    if text.startswith("${") and text.endswith("}"):
        return (os.environ.get(text[2:-1].strip()) or "").strip()
    if text.startswith("$"):
        return (os.environ.get(text[1:].strip()) or "").strip()
    if text.lower().startswith("env:"):
        return (os.environ.get(text[4:].strip()) or "").strip()
    return text


def _as_int(value: Any, field: str) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ChainConfigError(f"{field} must be a number, got {text!r}") from exc


def _as_float(value: Any, field: str) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ChainConfigError(f"{field} must be a number, got {text!r}") from exc


def _as_bool(value: Any, field: str) -> bool | None:
    text = _clean(value).lower()
    if not text:
        return None
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ChainConfigError(f"{field} must be true/false, got {text!r}")


def _resolve_provider(value: Any) -> Provider:
    text = _clean(value)
    if not text:
        raise ChainConfigError("every fallback entry needs a provider id or label")
    found = BY_ID.get(text.lower()) or BY_LABEL.get(text)
    if found is None:
        for provider in PROVIDERS:
            if provider.label.lower() == text.lower():
                found = provider
                break
    if found is None:
        known = ", ".join(p.id for p in PROVIDERS)
        raise ChainConfigError(f"unknown provider {text!r}; use one of: {known}")
    return found


# --------------------------------------------------------------------------- #
# entry construction
# --------------------------------------------------------------------------- #
def _from_parts(parts: list[str], source: str) -> ChainEntry:
    """Build an entry from ``provider | model | api_key``.

    The line syntax deliberately has no base_url slot: a URL typed in the key
    position would be an easy mistake. Per-entry ``base_url`` is only available
    through the JSON form.
    """
    if len(parts) > 3:
        raise ChainConfigError(
            "a fallback line has at most 3 fields: provider | model | api_key"
        )
    return ChainEntry(
        provider=_resolve_provider(parts[0]),
        model=_clean(parts[1]) if len(parts) > 1 else "",
        api_key=_expand_env(_clean(parts[2])) if len(parts) > 2 else "",
        source=source,
    )


def _first(mapping: dict, *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _from_mapping(item: dict) -> ChainEntry:
    provider = _resolve_provider(_first(item, "provider", "provider_id", "id", "name"))
    return ChainEntry(
        provider=provider,
        model=_clean(_first(item, "model", "model_override")),
        base_url=_expand_env(_first(item, "base_url", "baseUrl", "url", "endpoint") or ""),
        api_key=_expand_env(_first(item, "api_key", "apiKey", "key") or ""),
        reasoning_effort=_clean(_first(item, "reasoning_effort", "reasoningEffort", "effort")) or None,
        max_tokens=_as_int(_first(item, "max_tokens", "maxTokens"), "max_tokens"),
        temperature=_as_float(item.get("temperature"), "temperature"),
        top_p=_as_float(_first(item, "top_p", "topP"), "top_p"),
        seed=_as_int(item.get("seed"), "seed"),
        stream=_as_bool(item.get("stream"), "stream"),
        timeout=_as_float(_first(item, "timeout", "timeout_seconds", "timeoutSeconds"), "timeout"),
        source=json.dumps(item, ensure_ascii=False),
    )


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def parse_chain(text: str | None) -> list[ChainEntry]:
    """Parse ``fallback_chain`` text into endpoint entries (may be empty)."""
    raw = (text or "").strip()
    if not raw:
        return []

    if raw[0] in "[{":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChainConfigError(f"fallback_chain is not valid JSON: {exc}") from exc
        if isinstance(payload, dict):
            nested = _first(payload, "chain", "fallbacks", "providers", "entries")
            payload = nested if isinstance(nested, list) else [payload]
        if not isinstance(payload, list):
            raise ChainConfigError("fallback_chain JSON must be a list of endpoints")
        entries: list[ChainEntry] = []
        for item in payload:
            if isinstance(item, str):
                entries.append(_from_parts([part.strip() for part in item.split("|")], item))
            elif isinstance(item, dict):
                entries.append(_from_mapping(item))
            else:
                raise ChainConfigError(
                    "each fallback_chain JSON item must be a string or an object"
                )
        return entries

    if "|" not in raw:
        tokens = []
        for chunk in re.split(r"[,;\n\r]+", raw):
            chunk = chunk.split("#", 1)[0].strip()
            if chunk:
                tokens.append(chunk)
        return [_from_parts([token], token) for token in tokens]

    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        entries.append(_from_parts(parts, line))
    return entries


def _split_keys(value: str | None) -> list[str]:
    """Split the api_key widget into one key per attempt.

    ``key1,key2`` (commas and/or newlines) rotates keys on the provider that
    is selected in the dropdown. A single key behaves exactly as before.
    """
    raw = _clean(value)
    if not raw:
        return [""]
    keys = [_expand_env(part) for part in re.split(r"[,\n\r]+", raw) if part.strip()]
    return keys or [""]


def build_attempts(
    primary: Provider,
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    fallback_chain: str | None = None,
) -> list[ChainEntry]:
    """The primary endpoint followed by every configured fallback.

    A comma-separated ``api_key`` becomes several attempts on the same
    provider and model (key rotation) before any fallback entry is reached.
    For that reason the selected provider may not be listed in the chain
    again; it raises instead of being silently dropped. ``custom`` is the one
    exception: another custom entry is allowed as long as its base URL differs,
    because there the URL identifies the endpoint.

    Repeating the exact same request (provider, model, base URL and effective
    key) is dropped, so duplicates inside the chain do not send it twice.
    """
    primary_base = _expand_env(base_url) or primary.base_url
    keys = _split_keys(api_key)
    attempts = [
        ChainEntry(
            provider=primary,
            model=_clean(model),
            base_url=_expand_env(base_url),
            api_key=key,
            source="primary" if index == 0 else f"primary#{index + 1}",
        )
        for index, key in enumerate(keys)
    ]
    chain = parse_chain(fallback_chain)
    for entry in chain:
        if entry.provider.id != primary.id:
            continue
        same_url = (entry.base_url or entry.provider.base_url) == primary_base
        if entry.provider.id != "custom" or same_url:
            raise ChainConfigError(
                f"{entry.provider.label} is already the selected provider; put its "
                "extra keys in the api_key widget as key1,key2,... and list only "
                "other providers in fallback_chain"
            )
    attempts.extend(chain)

    seen: set[tuple] = set()
    unique: list[ChainEntry] = []
    for entry in attempts:
        # Same gateway + model + URL + key is the same request. A different key
        # is a different credential, so it stays as its own attempt.
        identity = (
            entry.provider.id,
            entry.model_id,
            entry.base_url or entry.provider.base_url,
            entry.api_key or (os.environ.get(entry.provider.key_env) or "").strip(),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(entry)
    return unique