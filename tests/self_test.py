"""Offline test suite for ComfyUI-LLM-API. No network, no ComfyUI required.

Run:  python tests/self_test.py
Exit code 0 = every check passed.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# load the pack exactly like ComfyUI does (folder name has dashes, so it is
# registered under an arbitrary sys.modules key with relative imports intact)
# --------------------------------------------------------------------------- #
PACKAGE = "ComfyUI-LLM-API"
spec = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
)
pack = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = pack
spec.loader.exec_module(pack)

from importlib import import_module  # noqa: E402

providers = import_module(f"{PACKAGE}.providers")
client = import_module(f"{PACKAGE}.llm_api_client")
fallback = import_module(f"{PACKAGE}.fallback")
images = import_module(f"{PACKAGE}.images")
nodes = import_module(f"{PACKAGE}.nodes")
prompts = import_module(f"{PACKAGE}.prompts")
routes = import_module(f"{PACKAGE}.routes")

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{label}{f' :: {detail}' if detail else ''}")


def eq(label: str, actual, expected) -> None:
    check(label, actual == expected, f"expected {expected!r}, got {actual!r}")


# --------------------------------------------------------------------------- #
print("== providers ==")
labels = [p.label for p in providers.PROVIDERS]
eq("labels are unique", len(labels), len(set(labels)))
eq("provider ids are unique", len(labels), len({p.id for p in providers.PROVIDERS}))
check("cometapi is registered", "cometapi" in providers.BY_ID)
check("grokified is registered", "grokified" in providers.BY_ID)
check("coarena is registered", "coarena" in providers.BY_ID)
eq("cometapi base url", providers.COMETAPI.base_url, "https://api.cometapi.com/v1")
eq("cometapi key env", providers.COMETAPI.key_env, "COMETAPI_API_KEY")
eq("grokified key env", providers.GROKIFIED.key_env, "GROKIFIED_API_KEY")
eq("coarena base url", providers.COARENA.base_url, "https://coarena.ai/api/v1")
eq("coarena key env", providers.COARENA.key_env, "COARENA_API_KEY")
eq("coarena default model", providers.COARENA.default_model, "grok-4-7")
check("grok-4-7 in coarena fallback list", "grok-4-7" in providers.COARENA.fallback_models)
check("coarena accepts minimal/max effort", {"minimal", "max"} <= set(providers.COARENA.reasoning_efforts))
check("coarena asks for usage on stream", providers.COARENA.stream_usage)
check("bazaai is registered", "bazaai" in providers.BY_ID)
eq("bazaai base url", providers.BAZA_AI.base_url, "https://api.baza-ai.org/v1")
eq("bazaai key env", providers.BAZA_AI.key_env, "BAZAAI_API_KEY")
eq("bazaai default model", providers.BAZA_AI.default_model, "grok-4.7")
check("grok-4.7 in bazaai fallback list", "grok-4.7" in providers.BAZA_AI.fallback_models)
check("bazaai accepts minimal/max effort", {"minimal", "max"} <= set(providers.BAZA_AI.reasoning_efforts))
check("bazaai keeps the default stream usage", not providers.BAZA_AI.stream_usage)
check("grokbuild is registered", "grokbuild" in providers.BY_ID)
expected_grok_base = (os.environ.get("GROK_BUILD_URL") or "https://redacted.tail341768.ts.net:8443").rstrip("/")
if not expected_grok_base.endswith("/v1"):
    expected_grok_base += "/v1"
eq("grokbuild base url", providers.GROK_BUILD.base_url, expected_grok_base)
saved_grok_url = os.environ.pop("GROK_BUILD_URL", None)
try:
    eq(
        "grokbuild default is redacted",
        providers._grok_build_base(),
        "https://redacted.tail341768.ts.net:8443/v1",
    )
finally:
    if saved_grok_url is not None:
        os.environ["GROK_BUILD_URL"] = saved_grok_url
eq("grokbuild key env", providers.GROK_BUILD.key_env, "GROK_API_KEY")
eq("grokbuild default model", providers.GROK_BUILD.default_model, "grok-4.7")
check("grokbuild fallback list", providers.GROK_BUILD.fallback_models == ("grok-4.7",))
check("grokbuild has no max effort", "max" not in providers.GROK_BUILD.reasoning_efforts)
check("grokbuild requires a key", providers.GROK_BUILD.requires_key)
check("grokbuild key hint names the file", "kunci_api_grok.txt" in providers.GROK_BUILD.key_hint)
check("grokbuild asks for usage on stream", providers.GROK_BUILD.stream_usage)
check("custom works without a key", not providers.CUSTOM.requires_key)
check("howtofix is registered", "howtofix" in providers.BY_ID)
eq("howtofix base url", providers.HOWTOFIX.base_url, "https://api.howtofix.id/v1")
eq("howtofix key env", providers.HOWTOFIX.key_env, "HOWTOFIX_API_KEY")
eq("howtofix default model", providers.HOWTOFIX.default_model, "grok-4.7")
check("grok-4.7 in howtofix fallback list", "grok-4.7" in providers.HOWTOFIX.fallback_models)
check("howtofix requires a key", providers.HOWTOFIX.requires_key)
check("howtofix does not send stream_options", not providers.HOWTOFIX.stream_usage)
eq("howtofix retries twice", providers.HOWTOFIX.max_attempts, 2)
check("howtofix has no max effort", "max" not in providers.HOWTOFIX.reasoning_efforts)
check("cloudflare is registered", "cloudflare" in providers.BY_ID)
expected_cf_base = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{os.environ.get('CLOUDFLARE_ACCOUNT_ID') or 'redacted'}/ai/v1"
)
eq("cloudflare base url", providers.CLOUDFLARE.base_url, expected_cf_base)
eq("cloudflare key env", providers.CLOUDFLARE.key_env, "CLOUDFLARE_API_TOKEN")
eq("cloudflare default model", providers.CLOUDFLARE.default_model, "@cf/google/gemma-4-26b-a4b-it")
check("cloudflare fallback list", providers.CLOUDFLARE.fallback_models == ("@cf/google/gemma-4-26b-a4b-it",))
check("cloudflare requires a key", providers.CLOUDFLARE.requires_key)
check("cloudflare does not send stream_options", not providers.CLOUDFLARE.stream_usage)
check("cloudflare offers high effort", "high" in providers.CLOUDFLARE.reasoning_efforts)
saved_cf_account = os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
try:
    eq(
        "cloudflare default is redacted",
        providers._cloudflare_base(),
        "https://api.cloudflare.com/client/v4/accounts/redacted/ai/v1",
    )
finally:
    if saved_cf_account is not None:
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = saved_cf_account
check("crusoe is registered", "crusoe" in providers.BY_ID)
eq("crusoe base url", providers.CRUSOE.base_url, "https://api.inference.crusoecloud.com/v1")
eq("crusoe key env", providers.CRUSOE.key_env, "CRUSOE_API_KEY")
eq("crusoe default model", providers.CRUSOE.default_model, "google/gemma-4-31b-it")
check("crusoe fallback has its default", "google/gemma-4-31b-it" in providers.CRUSOE.fallback_models)
check("crusoe requires a key", providers.CRUSOE.requires_key)
check("crusoe does not send stream_options", not providers.CRUSOE.stream_usage)
check("fastrouter is registered", "fastrouter" in providers.BY_ID)
eq("fastrouter base url", providers.FASTROUTER.base_url, "https://api.fastrouter.ai/api/v1")
eq("fastrouter key env", providers.FASTROUTER.key_env, "FASTROUTER_API_KEY")
eq("fastrouter default model", providers.FASTROUTER.default_model, "google/gemma-4-31b-it-ultra")
check("fastrouter fallback has its default", "google/gemma-4-31b-it-ultra" in providers.FASTROUTER.fallback_models)
check("fastrouter requires a key", providers.FASTROUTER.requires_key)
check("fastrouter does not send stream_options", not providers.FASTROUTER.stream_usage)
eq("fastrouter model filter key", providers.FASTROUTER.model_filter_key, "architecture.modality")
eq("fastrouter model filter value", providers.FASTROUTER.model_filter_value, "->text")
eq("fastrouter model filter mode", providers.FASTROUTER.model_filter_mode, "contains")
check("cometapi keeps the default stream usage", not providers.COMETAPI.stream_usage)
eq("cometapi default model", providers.COMETAPI.default_model, "grok-4.7")
check("grok-4.7 in cometapi fallback list", "grok-4.7" in providers.COMETAPI.fallback_models)
check(
    "cometapi filters /models to openai-chat",
    providers.COMETAPI.model_filter_key == "supported_endpoint_types"
    and providers.COMETAPI.model_filter_value == "openai-chat",
)
check("grokified has no min/max effort", "max" not in providers.GROKIFIED.reasoning_efforts)
check("cometapi has min/max effort", {"minimal", "max"} <= set(providers.COMETAPI.reasoning_efforts))

for provider in providers.PROVIDERS:
    check(f"{provider.id}: base_url looks like a url", provider.base_url.startswith("http"))
    check(f"{provider.id}: key_env is set", bool(provider.key_env))
    check(f"{provider.id}: default model is in its own list", provider.default_model in provider.fallback_models)
    check(f"{provider.id}: efforts start with default", provider.reasoning_efforts[0] == "default")

eq("get_provider by id", providers.get_provider("cometapi").id, "cometapi")
eq("get_provider by label", providers.get_provider(providers.COMETAPI.label).id, "cometapi")
eq("get_provider case-insensitive", providers.get_provider("COMETAPI").id, "cometapi")
eq("get_provider unknown -> default", providers.get_provider("nope").id, providers.DEFAULT_PROVIDER_ID)
eq("get_provider empty -> default", providers.get_provider("  ").id, providers.DEFAULT_PROVIDER_ID)

models = providers.all_models()
eq("all_models has no duplicates", len(models), len(set(models)))
check("all_models starts with a default", models[0] in {p.default_model for p in providers.PROVIDERS})
check("all_models contains cometapi-only model", "deepseek-v4.1-flash" in models)
check("all_models contains grokified-only model", "claude-opus-5" in models)
check("all_models contains bazaai-only model", "gpt-5.6-sol" in models)
check("all_models contains howtofix-only model", "big-pickle" in models)
check("all_models contains the cloudflare model", "@cf/google/gemma-4-26b-a4b-it" in models)
check("all_models contains the crusoe model", "google/gemma-4-31b-it" in models)
check("all_models contains the fastrouter model", "google/gemma-4-31b-it-ultra" in models)

efforts = providers.all_efforts()
check("all_efforts is the union", {"default", "minimal", "low", "medium", "high", "xhigh", "max"} <= set(efforts))
eq("all_efforts keeps a stable order", efforts, [e for e in providers.ALL_EFFORTS if e in set(efforts)])

registry = providers.registry_json()
eq("registry_json provider count", len(registry["providers"]), len(providers.PROVIDERS))
check("registry_json exposes effort lists", all(p["efforts"] for p in registry["providers"]))
check("registry_json is serialisable", json.dumps(registry) is not None)
check(
    "registry marks grokbuild as keyed",
    any(p["id"] == "grokbuild" and p["requiresKey"] is True for p in registry["providers"]),
)
check(
    "registry marks custom as optional-key",
    any(p["id"] == "custom" and p["requiresKey"] is False for p in registry["providers"]),
)

# --------------------------------------------------------------------------- #
print("== client: url + key helpers ==")
eq("normalize host", client.normalize_base_url("api.cometapi.com"), "https://api.cometapi.com/v1")
eq("normalize trailing slash", client.normalize_base_url("https://api.cometapi.com/"), "https://api.cometapi.com/v1")
eq("normalize explicit path", client.normalize_base_url("https://x.test/v1/"), "https://x.test/v1")
eq("normalize keeps sub path", client.normalize_base_url("https://x.test/openai/v1"), "https://x.test/openai/v1")
eq("normalize empty -> provider default", client.normalize_base_url("", "cometapi"), "https://api.cometapi.com/v1")
eq("normalize empty -> grokified default", client.normalize_base_url(None, "grokified"), "https://api.grokified.com/v1")
eq("normalize accepts a Provider object", client.normalize_base_url("", providers.GROKIFIED), providers.GROKIFIED.base_url)

eq("mask_key short", client.mask_key("abc"), "abc...")
eq("mask_key long", client.mask_key("sk-" + "x" * 48), "sk-xxxxx...xxxx".replace("xxxxx", "xxxxx"))
check("mask_key never leaks the middle", "x" * 30 not in client.mask_key("sk-" + "x" * 40))
eq("mask_key empty", client.mask_key(""), "(none)")
eq("headers carry a key", client._headers("abc")["Authorization"], "Bearer abc")
check("headers omit auth when the key is empty", "Authorization" not in client._headers(""))

os.environ["__LLM_TEST_KEY__"] = "from-env"
fake = providers.Provider(
    id="fake", label="Fake", base_url="https://fake.test/v1", key_env="__LLM_TEST_KEY__",
    default_model="m", fallback_models=("m",),
)
eq("resolve_api_key prefers the widget", client.resolve_api_key(fake, "from-widget"), "from-widget")
eq("resolve_api_key falls back to env", client.resolve_api_key(fake, ""), "from-env")
eq("resolve_api_key with no env", client.resolve_api_key(providers.CUSTOM, ""), "")
del os.environ["__LLM_TEST_KEY__"]

# --------------------------------------------------------------------------- #
print("== client: request body ==")
body = client.build_body(
    provider=providers.COMETAPI, model="grok-4.7", messages=[{"role": "user", "content": "hi"}],
    max_tokens=512, temperature=0.7, top_p=0.9, seed=7, reasoning_effort="high", stream=True,
)
eq("uses max_completion_tokens", body.get("max_completion_tokens"), 512)
check("does not also send max_tokens", "max_tokens" not in body)
eq("stream flag", body["stream"], True)
eq("effort forwarded", body["reasoning_effort"], "high")
eq("seed forwarded", body["seed"], 7)

for stop_word in ("default", "", "none", "off", "auto", "disabled"):
    b = client.build_body(
        provider=providers.COMETAPI, model="m", messages=[], reasoning_effort=stop_word, stream=False
    )
    check(f"effort '{stop_word}' is omitted", "reasoning_effort" not in b)

b = client.build_body(provider=providers.COMETAPI, model="m", messages=[], seed=0, stream=False)
check("seed 0 is omitted", "seed" not in b)

b = client.build_body(
    provider=providers.CUSTOM, model="m", messages=[], max_tokens_param="max_tokens", max_tokens=99, stream=False
)
eq("max_tokens_param override", b.get("max_tokens"), 99)

b = client.build_body(
    provider=providers.CUSTOM, model="m", messages=[], extra_body={"stop": ["\n\n"]}, stream=False
)
eq("extra_body merged", b.get("stop"), ["\n\n"])

b = client.build_body(provider=providers.COARENA, model="grok-4-7", messages=[], stream=True)
eq("coarena asks the stream for usage", b.get("stream_options"), {"include_usage": True})
b = client.build_body(provider=providers.COARENA, model="grok-4-7", messages=[], stream=False)
check("stream_options only rides on a stream", "stream_options" not in b)
b = client.build_body(provider=providers.COMETAPI, model="m", messages=[], stream=True)
check("cometapi never sends stream_options", "stream_options" not in b)

b = client.build_body(
    provider=providers.BAZA_AI, model="grok-4.7", messages=[], max_tokens=99, stream=True
)
eq("bazaai uses max_completion_tokens", b.get("max_completion_tokens"), 99)
check("bazaai does not send stream_options", "stream_options" not in b)

b = client.build_body(
    provider=providers.GROK_BUILD, model="grok-4.7", messages=[], max_tokens=99, stream=True
)
eq("grokbuild uses max_completion_tokens", b.get("max_completion_tokens"), 99)
eq("grokbuild asks the stream for usage", b.get("stream_options"), {"include_usage": True})

b = client.build_body(
    provider=providers.HOWTOFIX, model="grok-4.7", messages=[], max_tokens=99, stream=True
)
eq("howtofix uses max_completion_tokens", b.get("max_completion_tokens"), 99)
check("howtofix does not send stream_options", "stream_options" not in b)

b = client.build_body(
    provider=providers.CLOUDFLARE,
    model="@cf/google/gemma-4-26b-a4b-it",
    messages=[],
    max_tokens=99,
    stream=True,
)
eq("cloudflare uses max_completion_tokens", b.get("max_completion_tokens"), 99)
check("cloudflare does not send stream_options", "stream_options" not in b)

for provider, model_id in (
    (providers.CRUSOE, "google/gemma-4-31b-it"),
    (providers.FASTROUTER, "google/gemma-4-31b-it-ultra"),
):
    b = client.build_body(provider=provider, model=model_id, messages=[], max_tokens=99, stream=True)
    eq(f"{provider.id} uses max_completion_tokens", b.get("max_completion_tokens"), 99)
    check(f"{provider.id} does not send stream_options", "stream_options" not in b)

# --------------------------------------------------------------------------- #
print("== client: parameter downgrade ==")
b = {"max_completion_tokens": 10, "stream": True, "messages": [], "model": "m"}
note = client._downgrade(b, "Unsupported parameter: 'max_completion_tokens' is not supported with this model.")
eq("downgrade moves the token field", b.get("max_tokens"), 10)
check("downgrade pops the old field", "max_completion_tokens" not in b)
check("downgrade reports a note", bool(note))

b = {"reasoning_effort": "high"}
check("downgrade matches reasoning_effort", bool(client._downgrade(b, "Invalid `reasoning_effort` value")))
check("downgrade removes it", "reasoning_effort" not in b)

b = {"reasoning_effort": "none"}
check(
    "downgrade matches 'reasoning effort' with a space",
    bool(client._downgrade(b, "This model does not support reasoning effort value none.")),
)

b = {"model": "m", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x", "detail": "auto"}}]}]}
check("downgrade strips image detail", bool(client._downgrade(b, "unknown field detail")))
check("detail removed", "detail" not in b["messages"][0]["content"][0]["image_url"])

b = {"stream": True, "model": "m", "messages": []}
check("downgrade turns stream off", bool(client._downgrade(b, "stream is not supported")))
eq("stream now false", b["stream"], False)

b = {"model": "m", "messages": []}
eq("downgrade returns None when nothing matches", client._downgrade(b, "totally unrelated problem"), None)

# --------------------------------------------------------------------------- #
print("== client: error parsing (real captured payloads) ==")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", lines=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self._lines = lines or []

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line if isinstance(line, bytes) else line.encode()

    def close(self):
        pass


comet_503 = {
    "error": {
        "code": "model_not_found",
        "message": "no available channel for group default and model definitely-not-a-model (distributor) (request id: 20261106044332574404986kgfqgQtG)",
        "type": "cometapi_error",
    }
}
code, message = client._error_details(FakeResponse(503, comet_503))
eq("cometapi 503 code", code, "model_not_found")
check("cometapi 503 message preserved", "no available channel" in message)
check("503 model_not_found is fatal (no retry)", client._is_fatal(503, code, message))

comet_400_nested = {
    "error": {
        "message": '{"code":"invalid-argument","error":"Invalid reasoning effort."}\uff08traceid: e04203b5\uff09',
        "type": "comet_api_error",
        "param": "",
        "code": None,
    }
}
code, message = client._error_details(FakeResponse(400, comet_400_nested))
eq("nested cometapi code unwrapped", code, "invalid-argument")
eq("nested cometapi message unwrapped", message, "Invalid reasoning effort.")
check("invalid-argument is not fatal", not client._is_fatal(400, code, message))

comet_401 = {"error": {"code": "", "message": "invalid token (request id: abc)", "type": "cometapi_error"}}
code, message = client._error_details(FakeResponse(401, comet_401))
check("401 message preserved", "invalid token" in message)

code, message = client._error_details(FakeResponse(500, None, text="<html>gateway blew up</html>"))
eq("non-json body falls back to text", message, "<html>gateway blew up</html>")

baza_400 = {
    "error": {
        "message": "Only Web models are allowed. The model ID must begin with gpt-.",
        "type": "model_not_allowed",
        "code": "model_not_allowed",
    }
}
code, message = client._error_details(FakeResponse(400, baza_400))
eq("baza 400 code", code, "model_not_allowed")
check("baza 400 is fatal (no retry storm)", client._is_fatal(400, code, message))

grokbuild_404 = {
    "code": "not-found",
    "error": "The model definitely-not-a-model does not exist or your team does not have access to it.",
}
code, message = client._error_details(FakeResponse(404, grokbuild_404))
eq("grokbuild 404 code", code, "not-found")
check("grokbuild 404 is fatal", client._is_fatal(404, code, message))

cf_400 = {
    "errors": [{"message": "AiError: No such model: @cf/definitely-not-a-model", "code": 5007}],
    "success": False,
    "result": {},
    "messages": [],
}
code, message = client._error_details(FakeResponse(400, cf_400))
eq("cloudflare 400 code", code, "5007")
check("cloudflare 400 message preserved", "No such model" in message)
check("cloudflare 400 is fatal", client._is_fatal(400, code, message))

cf_401 = {
    "result": None,
    "success": False,
    "errors": [{"code": 10000, "message": "Authentication error"}],
    "messages": [],
}
code, message = client._error_details(FakeResponse(401, cf_401))
eq("cloudflare 401 code", code, "10000")
eq("cloudflare 401 message", message, "Authentication error")

crusoe_404 = {"code": "model_not_found", "message": "Model not found: nope/nope", "error_id": "abc"}
code, message = client._error_details(FakeResponse(404, crusoe_404))
eq("crusoe 404 code", code, "model_not_found")
check("crusoe 404 is fatal", client._is_fatal(404, code, message))

crusoe_401 = {"errors": ["Authentication failed"], "id": "abc"}
code, message = client._error_details(FakeResponse(401, crusoe_401))
eq("crusoe 401 message", message, "Authentication failed")

fastrouter_400 = {
    "error": {
        "code": None,
        "message": "nope/nope is not a valid model ID or no providers found ",
        "param": None,
        "type": "invalid_request_error",
    }
}
code, message = client._error_details(FakeResponse(400, fastrouter_400))
check("fastrouter 400 is fatal", client._is_fatal(400, code, message))

fastrouter_401 = {
    "error": {
        "code": "invalid_api_key",
        "message": "Invalid API key provided: *****",
        "param": None,
        "type": "invalid_request_error",
    }
}
code, message = client._error_details(FakeResponse(401, fastrouter_401))
eq("fastrouter 401 code", code, "invalid_api_key")

check("429 is retryable", 429 in client.RETRY_STATUSES)
check("503 is retryable", 503 in client.RETRY_STATUSES)
check("402 is not retryable", 402 not in client.RETRY_STATUSES)
check(
    "model_unavailable stays retryable (not fatal)",
    not client._is_fatal(503, "model_unavailable", "This model is not available right now."),
)

# --------------------------------------------------------------------------- #
print("== client: model catalogue ==")
comet_models = [
    {"id": "grok-4.7", "object": "model", "supported_endpoint_types": ["openai", "openai-chat", "openai-response"]},
    {"id": "black-forest-labs/flux-1.1-pro", "supported_endpoint_types": ["image-generation", "openai"]},
    {"id": "act_two", "supported_endpoint_types": ["openai", "primary"]},
    {"id": "deepseek-v4.1-flash", "supported_endpoint_types": ["openai-chat"]},
    {"id": "grok-4.7", "supported_endpoint_types": ["openai-chat"]},
]
kept = client._filter_models(providers.COMETAPI, comet_models)
eq("cometapi filter keeps only chat models, deduped", kept, ["grok-4.7", "deepseek-v4.1-flash"])
eq(
    "a provider without a filter keeps everything",
    client._filter_models(providers.GROKIFIED, comet_models),
    ["grok-4.7", "black-forest-labs/flux-1.1-pro", "act_two", "deepseek-v4.1-flash"],
)
eq("plain string entries are accepted", client._filter_models(providers.GROKIFIED, ["a", "b", "a"]), ["a", "b"])

fastrouter_models = [
    {"id": "google/gemma-4-31b-it-ultra", "architecture": {"modality": "text+image->text"}},
    {"id": "google/gemma-4-31b-it", "architecture": {"modality": "text+image->text"}},
    {"id": "flux", "architecture": {"modality": "text->image"}},
    {"id": "veo", "architecture": {"modality": "text+image->video"}},
    {"id": "sarvam", "architecture": {"modality": "text->audio"}},
]
eq(
    "fastrouter nested contains filter keeps only text-output models",
    client._filter_models(providers.FASTROUTER, fastrouter_models),
    ["google/gemma-4-31b-it-ultra", "google/gemma-4-31b-it"],
)

client.clear_model_cache()
static_models, error = client.list_models(fake)
eq("no key -> static fallback", static_models, ["m"])
check("no key -> error explains why", "no API key" in (error or ""))
eq("cached_models works offline", client.cached_models(fake), ["m"])

# --------------------------------------------------------------------------- #
print("== client: response parsing ==")
parsed = client._parse_completion(
    {
        "model": "grok-4.7",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": "PONG", "reasoning_content": "thinking hard", "role": "assistant"},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
)
eq("completion content", parsed["content"], "PONG")
eq("completion reasoning_content", parsed["reasoning"], "thinking hard")
eq("completion finish_reason", parsed["finish_reason"], "stop")
eq("completion usage", parsed["usage"]["total_tokens"], 12)

parsed = client._parse_completion(
    {"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "reasoning": "r"}}]}
)
eq("list content is joined", parsed["content"], "ab")
eq("'reasoning' alias is understood", parsed["reasoning"], "r")

sse = [
    b'data: {"model":"grok-4.7","choices":[{"delta":{"reasoning_content":"I am calculating ","role":"assistant"}}]}',
    b"",
    b'data: {"choices":[{"delta":{"reasoning_content":"12 x 12."}}]}',
    b'data: {"choices":[{"delta":{"content":"12"}}]}',
    b'data: {"choices":[{"delta":{"content":" x 12 = 144."}}]}',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    b'data: {"choices":[],"usage":{"prompt_tokens":1257,"completion_tokens":156,"total_tokens":1413}}',
    b"data: [DONE]",
]
deltas: list[str] = []
streamed = client._parse_stream(FakeResponse(200, lines=sse), deltas.append)
eq("stream content", streamed["content"], "12 x 12 = 144.")
eq("stream reasoning", streamed["reasoning"], "I am calculating 12 x 12.")
eq("stream finish_reason", streamed["finish_reason"], "stop")
eq("stream model", streamed["model"], "grok-4.7")
eq("stream usage captured", streamed["usage"]["completion_tokens"], 156)
eq("stream marks itself streamed", streamed["streamed"], True)
check("on_delta fired for content and reasoning", deltas.count("content") == 2 and deltas.count("reasoning") == 2)

broken = [b"data: {not json}", b": keep-alive comment", b"data: [DONE]"]
out = client._parse_stream(FakeResponse(200, lines=broken), None)
eq("malformed chunks are skipped", out["content"], "")

sse_fastrouter = [
    b'data: {"provider":"deepinfra","choices":[{"delta":{"content":"OK"}}]}',
    b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2}}',
    b"data: [DONE]",
]
out = client._parse_stream(FakeResponse(200, lines=sse_fastrouter), None)
eq("stream captures the upstream provider", out["usage"].get("provider"), "deepinfra")

# --------------------------------------------------------------------------- #
print("== images ==")
import base64  # noqa: E402

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

eq("None image is ignored", images.image_to_parts(None), ([], []))
lenient, _ = images.image_to_parts(np.zeros((4, 4)))
eq("2-D array is tolerated as one grayscale frame", len(lenient), 1)

tensor = torch.rand(1, 64, 64, 3)
parts, warns = images.image_to_parts(tensor)
eq("one image -> one part", len(parts), 1)
eq("part type", parts[0]["type"], "image_url")
check("data uri emitted", parts[0]["image_url"]["url"].startswith("data:image/"))
eq("detail forwarded", parts[0]["image_url"]["detail"], "auto")
eq("no warnings for a single image", warns, [])

parts, _ = images.image_to_parts(tensor, supports_detail=False)
check("detail omitted when unsupported", "detail" not in parts[0]["image_url"])

batch = torch.rand(5, 32, 32, 3)
parts, warns = images.image_to_parts(batch, max_images=2)
eq("batch is capped", len(parts), 2)
check("batch cap warns", any("only the first 2" in w for w in warns))

big = torch.rand(1, 512, 256, 3)
parts, _ = images.image_to_parts(big, max_side=128)
raw = base64.b64decode(parts[0]["image_url"]["url"].split(",", 1)[1])
with Image.open(io.BytesIO(raw)) as decoded:
    eq("downscale respects max_side", max(decoded.size), 128)

gray = torch.rand(1, 32, 32, 1)
parts, _ = images.image_to_parts(gray)
check("single-channel tensor is accepted", len(parts) == 1)

# --------------------------------------------------------------------------- #
print("== prompts ==")
eq("NO_SYSTEM_PROMPT sentinel", prompts.NO_SYSTEM_PROMPT, "none")
check("options always offer 'none'", prompts.system_prompt_options()[0] == "none")
text, warns = prompts.build_system_prompt("none", "")
eq("'none' preset -> empty prompt", text, "")
eq("'none' preset -> no warning", warns, [])
text, _ = prompts.build_system_prompt("none", "  inline wins  ")
eq("inline text overrides the preset", text, "inline wins")
text, warns = prompts.build_system_prompt("does-not-exist-preset", "")
eq("missing preset -> empty text", text, "")
check("missing preset warns", len(warns) == 1 and "was not found" in warns[0])

# --------------------------------------------------------------------------- #
print("== fallback chain ==")
entries = fallback.parse_chain("crusoe | google/gemma-4-31b-it\nfastrouter | x")
eq("line syntax parses two entries", [e.provider.id for e in entries], ["crusoe", "fastrouter"])
eq("line syntax keeps the model", entries[0].model, "google/gemma-4-31b-it")
eq("empty model means provider default", fallback.parse_chain("fastrouter")[0].model_id, providers.FASTROUTER.default_model)
eq("empty text -> empty chain", fallback.parse_chain("  \n "), [])
eq("comment-only text -> empty chain", fallback.parse_chain("# nothing here"), [])
eq("comments are skipped inside line syntax", len(fallback.parse_chain("# skip\ncrusoe")), 1)
eq("dash placeholders mean default", fallback.parse_chain("crusoe | - | ~ | none")[0].model, "")

os.environ["LLM_TEST_CHAIN_KEY"] = "chain-secret"
try:
    entry = fallback.parse_chain("custom | m | https://example.test/v1 | $LLM_TEST_CHAIN_KEY")[0]
    eq("$VAR api key expanded", entry.api_key, "chain-secret")
    entry = fallback.parse_chain("custom | m | https://example.test/v1 | ${LLM_TEST_CHAIN_KEY}")[0]
    eq("${VAR} api key expanded", entry.api_key, "chain-secret")
    entry = fallback.parse_chain("custom | m | https://example.test/v1 | env:LLM_TEST_CHAIN_KEY")[0]
    eq("env:VAR api key expanded", entry.api_key, "chain-secret")
    entry = fallback.parse_chain("custom | m | $LLM_TEST_CHAIN_MISSING")[0]
    eq("missing env var expands to empty", entry.api_key, "")
finally:
    os.environ.pop("LLM_TEST_CHAIN_KEY", None)

eq("comma shorthand", [e.provider.id for e in fallback.parse_chain("crusoe, fastrouter; coarena")],
   ["crusoe", "fastrouter", "coarena"])
eq("comma shorthand uses default models",
   [e.model_id for e in fallback.parse_chain("crusoe, fastrouter")],
   [providers.CRUSOE.default_model, providers.FASTROUTER.default_model])

json_chain = fallback.parse_chain(json.dumps([
    {"provider": "custom", "model": "local", "base_url": "http://127.0.0.1:1234/v1", "api_key": "$NOPE"},
    "crusoe | google/gemma-4-31b-it",
]))
eq("json syntax parses objects and strings", [e.provider.id for e in json_chain], ["custom", "crusoe"])
eq("json base_url kept", json_chain[0].base_url, "http://127.0.0.1:1234/v1")
eq("json missing env key -> empty", json_chain[0].api_key, "")
wrapped = fallback.parse_chain(json.dumps({"chain": [{"provider": "crusoe"}]}))
eq("json object wrapper accepted", [e.provider.id for e in wrapped], ["crusoe"])
int_chain = fallback.parse_chain(json.dumps([{"provider": "crusoe", "max_tokens": 128, "timeout": 12.5,
                                             "stream": "false", "seed": "7"}]))
eq("json per-entry max_tokens", int_chain[0].max_tokens, 128)
eq("json per-entry timeout", int_chain[0].timeout, 12.5)
eq("json per-entry stream", int_chain[0].stream, False)
eq("json per-entry seed", int_chain[0].seed, 7)
eq("json per-entry reasoning effort", fallback.parse_chain(
    json.dumps([{"provider": "crusoe", "reasoning_effort": "low"}])
)[0].reasoning_effort, "low")

try:
    fallback.parse_chain("nope | m")
    check("unknown provider is rejected", False, "no exception raised")
except fallback.ChainConfigError as exc:
    check("unknown provider is rejected", "unknown provider" in str(exc))

try:
    fallback.parse_chain("a | b | c | d | e")
    check("too many line fields rejected", False, "no exception raised")
except fallback.ChainConfigError as exc:
    check("too many line fields rejected", "at most 4 fields" in str(exc))

try:
    fallback.parse_chain("[{not json")
    check("bad json rejected", False, "no exception raised")
except fallback.ChainConfigError as exc:
    check("bad json rejected", "not valid JSON" in str(exc))

try:
    fallback.parse_chain(json.dumps([{"provider": "crusoe", "temperature": "hot"}]))
    check("bad number rejected", False, "no exception raised")
except fallback.ChainConfigError as exc:
    check("bad number rejected", "must be a number" in str(exc))

primary = providers.get_provider(providers.COMETAPI.label)
attempts = fallback.build_attempts(
    primary, model="grok-4.7", api_key="k",
    fallback_chain="cometapi | grok-4.7 | - | k\ncrusoe | google/gemma-4-31b-it",
)
eq("primary comes first", attempts[0].provider.id, "cometapi")
eq("duplicate primary is dropped", [e.provider.id for e in attempts], ["cometapi", "crusoe"])
eq("primary keeps the widget model", attempts[0].model, "grok-4.7")
eq("primary widget key carried", attempts[0].api_key, "k")
rotated = fallback.build_attempts(
    primary, model="grok-4.7", api_key="k",
    fallback_chain="cometapi | grok-4.7 | - | k2",
)
eq("same endpoint with another key stays", len(rotated), 2)
eq("the rotated key is the one written", rotated[1].api_key, "k2")
same_env = fallback.build_attempts(
    primary, model="grok-4.7",
    fallback_chain="cometapi | grok-4.7\ncometapi | grok-4.7",
)
eq("repeated no-key lines collapse to one", len(same_env), 1)
eq("no chain -> one attempt", len(fallback.build_attempts(primary, model="m")), 1)

# --------------------------------------------------------------------------- #
print("== nodes ==")
node = nodes.LLMAPITextProcessor()
types = node.INPUT_TYPES()

required = types["required"]
optional = types["optional"]
for key in ("provider", "model", "system_prompt", "prompt", "max_tokens", "temperature", "top_p",
            "reasoning_effort", "seed", "timeout_seconds"):
    check(f"required input '{key}'", key in required)
for key in ("image", "system_prompt_text", "api_key", "base_url", "model_override", "stream",
            "max_tokens_param", "extra_body_json", "enable_processing", "fallback_chain"):
    check(f"optional input '{key}'", key in optional)

eq("provider default", required["provider"][1]["default"], providers.DEFAULT_PROVIDER.label)
eq("provider choices", list(required["provider"][0]), providers.PROVIDER_LABELS)
eq("model default is the cometapi one", required["model"][1]["default"], providers.COMETAPI.default_model)
check("model list is the union", "claude-opus-5" in required["model"][0] and "gpt-6-astra" in required["model"][0])
eq("effort default", required["reasoning_effort"][1]["default"], "high")
eq("max_tokens_param default", optional["max_tokens_param"][1]["default"], "max_completion_tokens")
eq("base_url defaults to empty (= provider url)", optional["base_url"][1]["default"], "")
check("image is optional", "image" in optional)

eq("RETURN_NAMES match the Grokified node", nodes.LLMAPITextProcessor.RETURN_NAMES,
   ("RESPONSE", "REASONING", "PERF", "USAGE_JSON"))
eq("RETURN_TYPES length matches", len(nodes.LLMAPITextProcessor.RETURN_TYPES), 4)
eq("category", nodes.LLMAPITextProcessor.CATEGORY, "LLM/API")
eq("validate_inputs accepts anything", nodes.LLMAPITextProcessor.VALIDATE_INPUTS(model="whatever"), True)
eq("model list node validates too", nodes.LLMAPIModelList.VALIDATE_INPUTS(provider="x"), True)

node_mapping = pack.NODE_CLASS_MAPPINGS
check("both nodes are exported", set(node_mapping) == {"LLMAPITextProcessor", "LLMAPIModelList"})
check("display names are exported", set(pack.NODE_DISPLAY_NAME_MAPPINGS) == set(node_mapping))
eq("WEB_DIRECTORY is declared", pack.WEB_DIRECTORY, "./web")

# generate() short-circuits when processing is disabled - no key, no network
result = node.generate(
    provider=providers.COMETAPI.label, model="grok-4.7", system_prompt="none", prompt="hello world",
    max_tokens=16, temperature=0.7, top_p=0.9, reasoning_effort="high", seed=1, timeout_seconds=30,
    enable_processing=False,
)
eq("disabled processing returns the prompt", result[0], "hello world")
eq("disabled processing reports itself", result[2], "skipped (enable_processing = false)")
eq("disabled processing returns empty usage", result[3], "{}")

# an optional-key provider must reach the client without an API key
original_chat_completion = nodes.chat_completion
captured: dict = {}


def fake_chat_completion(**kwargs):
    captured.update(kwargs)
    return {
        "content": "ok",
        "reasoning": "",
        "usage": {},
        "notes": [],
        "provider": "custom",
        "provider_label": providers.CUSTOM.label,
        "base_url": providers.CUSTOM.base_url,
        "model": "local-model",
        "streamed": False,
        "finish_reason": "stop",
    }


nodes.chat_completion = fake_chat_completion
saved_llm_key = os.environ.pop("LLM_API_KEY", None)
try:
    result = node.generate(
        provider=providers.CUSTOM.label, model="local-model", system_prompt="none", prompt="hi",
        max_tokens=16, temperature=0.7, top_p=0.9, reasoning_effort="high", seed=1,
        timeout_seconds=30, api_key="", stream=False,
    )
    eq("custom runs without a key", result[0], "ok")
    eq("node forwards provider max_attempts", captured.get("max_attempts"), providers.CUSTOM.max_attempts)
finally:
    nodes.chat_completion = original_chat_completion
    if saved_llm_key is not None:
        os.environ["LLM_API_KEY"] = saved_llm_key

# missing key must raise before any HTTP call
saved = {name: os.environ.pop(name, None) for name in ("COMETAPI_API_KEY", "GROKIFIED_API_KEY", "LLM_API_KEY")}
try:
    node.generate(
        provider=providers.COMETAPI.label, model="grok-4.7", system_prompt="none", prompt="hi",
        max_tokens=16, temperature=0.7, top_p=0.9, reasoning_effort="high", seed=1, timeout_seconds=30,
    )
    check("missing key raises", False, "no exception raised")
except client.LLMAPIKeyError as exc:
    check("missing key raises LLMAPIKeyError", True)
    check("key error names the env var", "COMETAPI_API_KEY" in str(exc))

try:
    node.generate(
        provider=providers.COMETAPI.label, model="grok-4.7", system_prompt="none", prompt="hi",
        max_tokens=16, temperature=0.7, top_p=0.9, reasoning_effort="high", seed=1, timeout_seconds=30,
        api_key="sk-test", extra_body_json="{not json",
    )
    check("bad extra_body_json raises", False, "no exception raised")
except client.LLMAPIError as exc:
    check("bad extra_body_json raises", "not valid JSON" in str(exc))
finally:
    for name, value in saved.items():
        if value is not None:
            os.environ[name] = value

# provider label -> provider resolution inside the node
eq("node resolves the grokified label", providers.get_provider(providers.GROKIFIED.label).key_env, "GROKIFIED_API_KEY")

# --------------------------------------------------------------------------- #
print("== nodes: fallback chain ==")


def _stub_result(provider, model):
    return {
        "content": "from " + provider.id,
        "reasoning": "",
        "usage": {"total_tokens": 1},
        "notes": [],
        "provider": provider.id,
        "provider_label": provider.label,
        "base_url": "http://stub",
        "model": model,
        "streamed": False,
        "finish_reason": "stop",
    }


common = dict(
    provider=providers.COMETAPI.label, model="grok-4.7", system_prompt="none", prompt="hi",
    max_tokens=16, temperature=0.7, top_p=0.9, reasoning_effort="high", seed=1,
    timeout_seconds=30, stream=False,
)
custom_chain = "custom | local-model | http://127.0.0.1:9/v1 | dummy"
original_chat_completion = nodes.chat_completion
saved_comet = os.environ.pop("COMETAPI_API_KEY", None)
saved_llm = os.environ.pop("LLM_API_KEY", None)
try:
    seen: list[str] = []

    def fail_primary(**kwargs):
        seen.append(kwargs["provider"].id)
        if kwargs["provider"].id == "cometapi":
            raise client.LLMAPIError("primary down")
        return _stub_result(kwargs["provider"], kwargs["model"])

    nodes.chat_completion = fail_primary
    result = node.generate(**common, api_key="dummy", fallback_chain=custom_chain)
    eq("fallback answers after the primary fails", result[0], "from custom")
    eq("both endpoints were called in order", seen, ["cometapi", "custom"])
    payload = json.loads(result[3])
    eq("usage reports the fallback provider", payload["provider"], "custom")
    eq("usage flags the failover", payload["fallback_used"], True)
    eq("usage lists both attempts", [a["ok"] for a in payload["attempts"]], [False, True])
    eq("failed attempt keeps its error", payload["attempts"][0]["error"], "primary down")
    check("perf mentions the failover", "failover after 1 failed attempt(s)" in result[2], result[2])

    def empty_primary(**kwargs):
        if kwargs["provider"].id == "cometapi":
            return {**_stub_result(kwargs["provider"], kwargs["model"]), "content": ""}
        return _stub_result(kwargs["provider"], kwargs["model"])

    nodes.chat_completion = empty_primary
    result = node.generate(**common, api_key="dummy", fallback_chain=custom_chain)
    eq("empty response falls over to the next endpoint", result[0], "from custom")
    eq(
        "empty response recorded as a failed attempt",
        [a.get("error") for a in json.loads(result[3])["attempts"]],
        ["the gateway returned an empty response", None],
    )

    def fail_all(**kwargs):
        raise client.LLMAPIError(f"{kwargs['provider'].id} down")

    nodes.chat_completion = fail_all
    try:
        node.generate(**common, api_key="dummy", fallback_chain=custom_chain)
        check("all endpoints failing raises", False, "no exception raised")
    except client.LLMAPIChainError as exc:
        check("all endpoints failing raises LLMAPIChainError", True)
        check("chain error names both endpoints", "cometapi" in str(exc) and "custom" in str(exc), str(exc))
        eq("chain error keeps structured failures", len(exc.failures), 2)

    try:
        node.generate(**common, api_key="dummy")
        check("single endpoint failure raises", False, "no exception raised")
    except client.LLMAPIChainError:
        check("single endpoint failure is not wrapped in a chain error", False, "got LLMAPIChainError")
    except client.LLMAPIError as exc:
        check("single endpoint failure raises the original error", "down" in str(exc), str(exc))

    # a missing primary key is just another failure, so the chain still runs
    def never_fails(**kwargs):
        return _stub_result(kwargs["provider"], kwargs["model"])

    nodes.chat_completion = never_fails
    result = node.generate(**common, api_key="", fallback_chain=custom_chain)
    eq("missing primary key falls over", result[0], "from custom")

    # no chain configured -> the old usage shape is unchanged
    result = node.generate(**common, api_key="dummy")
    check("no chain -> usage has no attempts key", "attempts" not in json.loads(result[3]))

    try:
        node.generate(**common, api_key="dummy", fallback_chain="not-a-provider")
        check("bad chain rejected", False, "no exception raised")
    except client.LLMAPIError as exc:
        check("bad chain rejected", "fallback_chain is invalid" in str(exc), str(exc))

    captured_override: dict = {}

    def capture_fallback(**kwargs):
        if kwargs["provider"].id == "cometapi":
            raise client.LLMAPIError("primary down")
        captured_override.update(kwargs)
        return _stub_result(kwargs["provider"], kwargs["model"])

    nodes.chat_completion = capture_fallback
    node.generate(
        **common,
        api_key="dummy",
        fallback_chain=json.dumps([{
            "provider": "custom", "model": "m2", "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "k2", "max_tokens": 7, "stream": False,
        }]),
    )
    eq("entry model override used", captured_override.get("model"), "m2")
    eq("entry base_url override used", captured_override.get("base_url"), "http://127.0.0.1:1234/v1")
    eq("entry max_tokens override used", captured_override.get("max_tokens"), 7)
    eq("entry key used", captured_override.get("api_key"), "k2")
    eq("entry stream override used", captured_override.get("stream"), False)
finally:
    nodes.chat_completion = original_chat_completion
    if saved_comet is not None:
        os.environ["COMETAPI_API_KEY"] = saved_comet
    if saved_llm is not None:
        os.environ["LLM_API_KEY"] = saved_llm

perf = nodes._format_perf(providers.COMETAPI.label, "grok-4.7",
                          {"prompt_tokens": 1248, "completion_tokens": 58, "reasoning_tokens": 56}, 2.1, True)
check("perf mentions the provider", perf.startswith("CometAPI |"))
check("perf mentions tokens", "1248 in / 58 out" in perf)
check("perf mentions reasoning", "reasoning 56" in perf)
check("perf mentions streaming", "2.10s stream" in perf)

numbers = nodes._usage_numbers(
    {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
     "completion_tokens_details": {"reasoning_tokens": 4},
     "prompt_tokens_details": {"cached_tokens": 5, "image_tokens": 0}}
)
eq("usage drops nulls", set(numbers), {"prompt_tokens", "completion_tokens", "total_tokens",
                                       "reasoning_tokens", "cached_tokens", "image_tokens"})
eq("usage reads reasoning_tokens", numbers["reasoning_tokens"], 4)

cf_numbers = nodes._usage_numbers(
    {"prompt_tokens": 18, "completion_tokens": 78, "total_tokens": 96, "neurons": 2.5}
)
eq("usage reads neurons", cf_numbers["neurons"], 2.5)
cf_perf = nodes._format_perf(providers.CLOUDFLARE.label, "@cf/google/gemma-4-26b-a4b-it", cf_numbers, 1.0, False)
check("perf shows neurons", "2.50 neurons" in cf_perf, cf_perf)

crusoe_numbers = nodes._usage_numbers(
    {"prompt_tokens": 31, "completion_tokens": 126, "reasoning_tokens": 120}
)
eq("usage reads top-level reasoning_tokens", crusoe_numbers["reasoning_tokens"], 120)

fastrouter_numbers = nodes._usage_numbers(
    {"prompt_tokens": 18, "completion_tokens": 55, "cost": 0.0000466, "provider": "deepinfra"}
)
eq("usage reads cost", fastrouter_numbers["cost_usd"], 0.0000466)
eq("usage reads upstream provider", fastrouter_numbers["upstream_provider"], "deepinfra")
fastrouter_perf = nodes._format_perf(
    providers.FASTROUTER.label, "google/gemma-4-31b-it-ultra", fastrouter_numbers, 1.0, False
)
check("perf shows fastrouter cost", "$0.000047" in fastrouter_perf, fastrouter_perf)

# model-list node with a stubbed catalogue
original_list_models = nodes.list_models
nodes.list_models = lambda *a, **k: (["grok-4.7", "grok-build-0.1", "deepseek-v4.1-flash"], None)
try:
    text, count = nodes.LLMAPIModelList().fetch(providers.COMETAPI.label, True)
    eq("model list node count", count, 3)
    eq("model list node text", text, "grok-4.7\ngrok-build-0.1\ndeepseek-v4.1-flash")
    text, count = nodes.LLMAPIModelList().fetch(providers.COMETAPI.label, True, filter="grok")
    eq("model list filter", text, "grok-4.7\ngrok-build-0.1")
    eq("model list filter count", count, 2)
finally:
    nodes.list_models = original_list_models

# --------------------------------------------------------------------------- #
print("== routes ==")
eq("route prefix is namespaced", routes.ROUTE_PREFIX, "/comfyui_llm_api")
check("providers route", routes.ROUTE_PROVIDERS == "/comfyui_llm_api/providers")
check("models route", routes.ROUTE_MODELS == "/comfyui_llm_api/models")
check("routes module imports outside ComfyUI", isinstance(routes.REGISTERED, bool))

# --------------------------------------------------------------------------- #
print("== web/js/llm_api.js ==")
js = (ROOT / "web" / "js" / "llm_api.js").read_text(encoding="utf-8")
check("js imports app.js at the right depth", '../../../scripts/app.js' in js)
check("js imports api.js", "../../../scripts/api.js" in js)
check("js registers an extension", "app.registerExtension" in js)
check("js uses loadedGraphNode", "loadedGraphNode" in js)
check("js exposes a test hook", "window.__LLM_API__" in js)
check("js hits the providers route", "/comfyui_llm_api/providers" in js)
check("js hits the models route", "/comfyui_llm_api/models" in js)
check("js guards on comfyClass", 'node.comfyClass !== NODE_CLASS' in js)
check("js narrows the model combo", "setComboValues" in js)
check("js narrows the effort combo", 'findWidget(node, "reasoning_effort")' in js)
check("js has a refresh button", "Refresh model list" in js)
check("js explains the fallback box", "decorateFallbackWidget" in js and "provider | model | base_url | api_key" in js)
check("js has no unbalanced brace count", js.count("{") == js.count("}"))

# --------------------------------------------------------------------------- #
print("== repository hygiene ==")
for name in ("README.md", "LICENSE", "pyproject.toml", "requirements.txt", ".gitignore", "providers.py",
             "llm_api_client.py", "fallback.py", "nodes.py", "images.py", "prompts.py", "routes.py",
             "__init__.py"):
    check(f"{name} exists", (ROOT / name).is_file())

text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
check("pyproject publisher", 'PublisherId = "CraftBlack"' in text)
check("pyproject repository url", "github.com/CraftBlack/ComfyUI-LLM-API" in text)

# no secret may be committed anywhere in the pack
import re  # noqa: E402

secret_re = re.compile(
    r"sk-[A-Za-z0-9]{20,}|sk-v1-[A-Za-z0-9]{20,}|coa_[A-Za-z0-9]{20,}"
    r"|ks_live_[A-Za-z0-9_-]{20,}|cfut_[A-Za-z0-9]{20,}"
)
for path in ROOT.rglob("*"):
    if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
        continue
    if path.suffix.lower() not in (".py", ".js", ".md", ".toml", ".txt", ".json", ".yml", ".yaml"):
        continue
    found = secret_re.findall(path.read_text(encoding="utf-8", errors="replace"))
    check(f"no hardcoded api key in {path.relative_to(ROOT)}", not found, f"found {len(found)} match(es)")

# --------------------------------------------------------------------------- #
print()
print(f"{PASSED} lulus, {len(FAILED)} gagal")
for failure in FAILED:
    print(f"  GAGAL: {failure}")
sys.exit(1 if FAILED else 0)
