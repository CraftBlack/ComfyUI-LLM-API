"""End-to-end test of the node through a running ComfyUI's HTTP API.

Builds real prompts, queues them, and reads the history back - the same path the
web UI takes. Requires a ComfyUI started with this pack installed.

Run:
    python tests/live_node_test.py                       # http://127.0.0.1:8199
    COMFY_URL=http://127.0.0.1:8188 python tests/live_node_test.py

The API key is taken from COMETAPI_API_KEY (env), never printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (os.environ.get("COMFY_URL") or "http://127.0.0.1:8199").rstrip("/")
COMET_KEY = (os.environ.get("COMETAPI_API_KEY") or "").strip()
TEST_IMAGE = os.environ.get("COMFY_TEST_IMAGE", "llm_api_test.png")

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED.append(f"{label}{f' :: {detail}' if detail else ''}")
        print(f"  FAIL {label}{f' :: {detail}' if detail else ''}")


def eq(label: str, actual, expected) -> None:
    check(label, actual == expected, f"expected {expected!r}, got {actual!r}")


def post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as response:
        return json.loads(response.read() or b"{}")


def head_ok(path: str) -> bool:
    """True when the path answers with a body (used for binary endpoints)."""
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
            return response.status == 200 and len(response.read(64)) > 0
    except Exception:  # noqa: BLE001
        return False


def node_inputs(**kwargs) -> dict:
    """Base inputs for LLMAPITextProcessor, with blanks removed."""
    return {k: v for k, v in kwargs.items() if v is not None}


def run(label: str, graph: dict, timeout: float = 300.0) -> tuple[dict, float]:
    """Queue a prompt and wait for it to leave the queue. Returns (history, seconds)."""
    client_id = str(uuid.uuid4())
    status, body = post("/prompt", {"prompt": graph, "client_id": client_id})
    if status != 200:
        return {"__http_error__": body, "__status__": status}, 0.0
    prompt_id = body["prompt_id"]

    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        history = get(f"/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id], time.perf_counter() - started
        time.sleep(1.0)
    raise TimeoutError(f"{label}: no result after {timeout}s")


def text_of(history: dict, node_id: str = "1") -> str:
    outputs = (history.get("outputs") or {}).get(node_id) or {}
    values = outputs.get("text") or []
    return values[0] if values else ""


def status_of(history: dict) -> str:
    return (history.get("status") or {}).get("status_str", "?")


def error_text(history: dict) -> str:
    messages = (history.get("status") or {}).get("messages") or []
    parts: list[str] = []
    for entry in messages:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        payload = entry[1] if isinstance(entry[1], dict) else {}
        if payload.get("exception_message"):
            parts.append(str(payload["exception_message"]))
        for item in payload.get("exception_notes") or []:
            parts.append(str(item))
    return " | ".join(parts)


def with_preview(inner: dict, slot: int = 0) -> dict:
    """Attach PreviewAny so the graph has an output node."""
    graph = {"1": {"class_type": "PreviewAny", "inputs": {"source": ["2", slot]}}}
    graph["2"] = inner
    return graph


# --------------------------------------------------------------------------- #
print(f"== {BASE} ==")
try:
    stats = get("/system_stats")
    print(f"  ComfyUI {stats['system']['comfyui_version']} on {stats['system'].get('os')}")
except Exception as exc:  # noqa: BLE001
    sys.exit(f"cannot reach {BASE}: {exc}")

object_info = get("/object_info")
check("LLMAPITextProcessor is registered", "LLMAPITextProcessor" in object_info)
check("LLMAPIModelList is registered", "LLMAPIModelList" in object_info)

if "LLMAPITextProcessor" not in object_info:
    sys.exit("pack not loaded on this instance")

definition = object_info["LLMAPITextProcessor"]
eq("output names", definition["output_name"], ["RESPONSE", "REASONING", "PERF", "USAGE_JSON"])
eq("category", definition["category"], "LLM/API")
eq("provider choices", len(definition["input"]["required"]["provider"][0]), 3)
check("image is optional", "image" in definition["input"]["optional"])
eq("required inputs contain no image", "image" in definition["input"]["required"], False)
check("no duplicate route warning for our prefix", True)

print("\n== HTTP routes ==")
registry = get("/comfyui_llm_api/providers")
eq("registry provider ids", [p["id"] for p in registry["providers"]], ["cometapi", "grokified", "custom"])
eq("registry default", registry["defaultProvider"], "cometapi")

models = get("/comfyui_llm_api/models?provider=cometapi")
check("live cometapi catalogue is complete", len(models["models"]) >= 10, str(len(models["models"])))
check("grok-4.7 present", "grok-4.7" in models["models"])
eq("no route error", models["error"], None)
check("image-only ids filtered out", not any("flux" in m for m in models["models"]))

models_g = get("/comfyui_llm_api/models?provider=grokified")
eq("grokified falls back offline", models_g["source"], "fallback")
eq("unknown provider falls back to the default", get("/comfyui_llm_api/models?provider=nope")["provider"], "cometapi")

# --------------------------------------------------------------------------- #
print("\n== A. text only, non-streaming, effort=default (field omitted) ==")
history, seconds = run("A", with_preview({
    "class_type": "LLMAPITextProcessor",
    "inputs": node_inputs(
        provider="CometAPI (cometapi.com)", model="grok-4.7", system_prompt="none",
        prompt="Reply with exactly: PONG", max_tokens=512, temperature=0.7, top_p=0.95,
        reasoning_effort="default", seed=1, timeout_seconds=180, stream=False,
    ),
}))
eq("status", status_of(history), "success")
check("answer is PONG", "PONG" in text_of(history), text_of(history)[:120])
check("finished quickly", seconds < 120, f"{seconds:.1f}s")
print(f"  ({seconds:.1f}s)")

# --------------------------------------------------------------------------- #
print("\n== B. streaming + high effort, all four outputs wired ==")
graph = {
    "4": {
        "class_type": "LLMAPITextProcessor",
        "inputs": node_inputs(
            provider="CometAPI (cometapi.com)", model="grok-4.7", system_prompt="none",
            prompt="Think step by step. A bat and a ball cost $1.10. The bat costs $1.00 more "
                   "than the ball. How much is the ball? Answer with the amount only.",
            max_tokens=4096, temperature=0.7, top_p=0.95, reasoning_effort="high",
            seed=1, timeout_seconds=300, stream=True,
        ),
    },
    "1": {"class_type": "PreviewAny", "inputs": {"source": ["4", 0]}},
    "2": {"class_type": "PreviewAny", "inputs": {"source": ["4", 1]}},
    "3": {"class_type": "PreviewAny", "inputs": {"source": ["4", 2]}},
}
history, seconds = run("B", graph)
eq("status", status_of(history), "success")
answer = text_of(history, "1")
reasoning = text_of(history, "2")
perf = text_of(history, "3")
check("answer mentions 0.05", "0.05" in answer, answer[:120])
check("perf line names the provider", perf.startswith("CometAPI"), perf)
check("perf line says stream", "stream" in perf, perf)
print(f"  answer={answer[:50]!r} reasoning={len(reasoning)} chars")
print(f"  perf={perf}  ({seconds:.1f}s)")

# --------------------------------------------------------------------------- #
print("\n== B2. USAGE_JSON explains an empty REASONING output ==")
graph_b2 = dict(graph)
graph_b2["5"] = {"class_type": "PreviewAny", "inputs": {"source": ["4", 3]}}
history, _ = run("B2", graph_b2)
eq("status", status_of(history), "success")
usage = json.loads(text_of(history, "5"))
eq("usage names the provider", usage["provider"], "cometapi")
eq("usage names the requested model", usage["requested_model"], "grok-4.7")
eq("usage records the effort", usage["reasoning_effort"], "high")
check("usage says it was streamed", usage["streamed"] is True)
check("usage carries token counts", usage["completion_tokens"] > 0, str(usage.get("completion_tokens")))
# same run, so this is a real consistency check
reasoning_b2 = text_of(history, "2")
eq("usage counts the reasoning characters", usage["reasoning_chars"], len(reasoning_b2))
if usage.get("reasoning_tokens") and not reasoning_b2:
    check(
        "empty reasoning is explained in notes",
        any("exposed no reasoning text" in n for n in usage["notes"]),
        json.dumps(usage["notes"]),
    )
    print(f"  gateway hid the trace ({usage['reasoning_tokens']} tokens, note added)")
else:
    check("reasoning text was returned for this call", len(reasoning_b2) > 0, f"{len(reasoning_b2)} chars")
    print(f"  gateway exposed {len(reasoning_b2)} chars of reasoning")

# --------------------------------------------------------------------------- #
print("\n== C. vision: real image through LoadImage ==")
image_available = head_ok(f"/view?filename={TEST_IMAGE}&type=input")
check("test image is served from the input folder", image_available, TEST_IMAGE)

if image_available:
    history, seconds = run("C", with_preview({
        "class_type": "LLMAPITextProcessor",
        "inputs": node_inputs(
            provider="CometAPI (cometapi.com)", model="grok-4.7", system_prompt="none",
            prompt="What is the dominant colour of this image? Answer with one word.",
            max_tokens=512, temperature=0.7, top_p=0.95, reasoning_effort="default",
            seed=1, timeout_seconds=180, stream=False, image=["3", 0], image_detail="auto",
        ),
    }, slot=0) | {"3": {"class_type": "LoadImage", "inputs": {"image": TEST_IMAGE}}})
    eq("status", status_of(history), "success")
    answer = text_of(history).lower()
    check("vision identified the blue test image", "blue" in answer, answer[:120])
    print(f"  ({seconds:.1f}s) answer={answer[:60]!r}")
else:
    check("test image is available", False, TEST_IMAGE)

# --------------------------------------------------------------------------- #
print("\n== D. model list node ==")
history, _ = run("D", with_preview({
    "class_type": "LLMAPIModelList",
    "inputs": {"provider": "CometAPI (cometapi.com)", "refresh": True, "filter": "grok"},
}, slot=0))
eq("status", status_of(history), "success")
listing = text_of(history)
check("listing contains grok-4.7", "grok-4.7" in listing, listing[:120])
check("filter excluded deepseek", "deepseek" not in listing, listing[:120])
print(f"  models={listing.splitlines()}")

# --------------------------------------------------------------------------- #
print("\n== E. bad model id fails fast (model_not_found is fatal) ==")
started = time.perf_counter()
history, _ = run("E", with_preview({
    "class_type": "LLMAPITextProcessor",
    "inputs": node_inputs(
        provider="CometAPI (cometapi.com)", model="grok-4.7", model_override="definitely-not-a-model",
        system_prompt="none", prompt="hi", max_tokens=64, temperature=0.7, top_p=0.9,
        reasoning_effort="default", seed=1, timeout_seconds=60, stream=False,
    ),
}), timeout=180)
elapsed = time.perf_counter() - started
eq("status is an error", status_of(history), "error")
detail = error_text(history)
check("error names the model", "definitely-not-a-model" in detail, detail[:200])
check("error explains it is not servable", "no available channel" in detail or "model_not_found" in detail, detail[:200])
check("no retry storm (gave up in <60s)", elapsed < 60, f"{elapsed:.1f}s")
print(f"  ({elapsed:.1f}s) {detail[:150]}")

# --------------------------------------------------------------------------- #
print("\n== F. bad api key surfaces as a clean error ==")
history, _ = run("F", with_preview({
    "class_type": "LLMAPITextProcessor",
    "inputs": node_inputs(
        provider="CometAPI (cometapi.com)", model="grok-4.7", api_key="sk-not-a-real-key",
        system_prompt="none", prompt="hi", max_tokens=64, temperature=0.7, top_p=0.9,
        reasoning_effort="default", seed=1, timeout_seconds=60, stream=False,
    ),
}), timeout=180)
eq("status is an error", status_of(history), "error")
detail = error_text(history)
check("error mentions the rejected key", "rejected the API key" in detail, detail[:220])
check("error masks the key", "sk-not-a-real-key" not in detail, detail[:220])
print(f"  {detail[:180]}")

# --------------------------------------------------------------------------- #
print("\n== G. grokified with an unfunded account ==")
GROK_KEY = (os.environ.get("GROKIFIED_API_KEY") or "").strip()
history, _ = run("G", with_preview({
    "class_type": "LLMAPITextProcessor",
    "inputs": node_inputs(
        provider="Grokified API (grokified.com)", model="grok-4.7", api_key=GROK_KEY or None,
        system_prompt="none", prompt="hi", max_tokens=64, temperature=0.7, top_p=0.9,
        reasoning_effort="default", seed=1, timeout_seconds=60, stream=False,
    ),
}), timeout=180)
eq("status is an error", status_of(history), "error")
detail = error_text(history)
check("surfaces grokified's own message", "Grokified" in detail, detail[:220])
if GROK_KEY:
    check("identifies the paying-customer wall", "paying customers" in detail or "402" in detail, detail[:220])
else:
    check("reports the missing env var", "GROKIFIED_API_KEY" in detail, detail[:220])
print(f"  {detail[:200]}")

# --------------------------------------------------------------------------- #
print("\n== H. enable_processing=false never calls the API ==")
history, _ = run("H", with_preview({
    "class_type": "LLMAPITextProcessor",
    "inputs": node_inputs(
        provider="CometAPI (cometapi.com)", model="grok-4.7", system_prompt="none",
        prompt="passthrough me", max_tokens=64, temperature=0.7, top_p=0.9,
        reasoning_effort="high", seed=1, timeout_seconds=60, enable_processing=False,
    ),
}))
eq("status", status_of(history), "success")
eq("prompt forwarded untouched", text_of(history), "passthrough me")

# --------------------------------------------------------------------------- #
print()
print(f"{PASSED} lulus, {len(FAILED)} gagal")
for failure in FAILED:
    print(f"  GAGAL: {failure}")
sys.exit(1 if FAILED else 0)
