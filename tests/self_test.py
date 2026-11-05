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
eq("cometapi base url", providers.COMETAPI.base_url, "https://api.cometapi.com/v1")
eq("cometapi key env", providers.COMETAPI.key_env, "COMETAPI_API_KEY")
eq("grokified key env", providers.GROKIFIED.key_env, "GROKIFIED_API_KEY")
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

efforts = providers.all_efforts()
check("all_efforts is the union", {"default", "minimal", "low", "medium", "high", "xhigh", "max"} <= set(efforts))
eq("all_efforts keeps a stable order", efforts, [e for e in providers.ALL_EFFORTS if e in set(efforts)])

registry = providers.registry_json()
eq("registry_json provider count", len(registry["providers"]), len(providers.PROVIDERS))
check("registry_json exposes effort lists", all(p["efforts"] for p in registry["providers"]))
check("registry_json is serialisable", json.dumps(registry) is not None)

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

check("429 is retryable", 429 in client.RETRY_STATUSES)
check("402 is not retryable", 402 not in client.RETRY_STATUSES)

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

client.clear_model_cache()
fallback, error = client.list_models(fake)
eq("no key -> static fallback", fallback, ["m"])
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
print("== nodes ==")
node = nodes.LLMAPITextProcessor()
types = node.INPUT_TYPES()

required = types["required"]
optional = types["optional"]
for key in ("provider", "model", "system_prompt", "prompt", "max_tokens", "temperature", "top_p",
            "reasoning_effort", "seed", "timeout_seconds"):
    check(f"required input '{key}'", key in required)
for key in ("image", "system_prompt_text", "api_key", "base_url", "model_override", "stream",
            "max_tokens_param", "extra_body_json", "enable_processing"):
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
check("js has no unbalanced brace count", js.count("{") == js.count("}"))

# --------------------------------------------------------------------------- #
print("== repository hygiene ==")
for name in ("README.md", "LICENSE", "pyproject.toml", "requirements.txt", ".gitignore", "providers.py",
             "llm_api_client.py", "nodes.py", "images.py", "prompts.py", "routes.py", "__init__.py"):
    check(f"{name} exists", (ROOT / name).is_file())

text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
check("pyproject publisher", 'PublisherId = "CraftBlack"' in text)
check("pyproject repository url", "github.com/CraftBlack/ComfyUI-LLM-API" in text)

# no secret may be committed anywhere in the pack
import re  # noqa: E402

secret_re = re.compile(r"sk-[A-Za-z0-9]{20,}")
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
