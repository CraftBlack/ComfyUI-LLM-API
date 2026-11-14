"""Live probe of the local Grok Build proxy (Get API Grok Build/api_server.py).

Run:  python tests\\probe_grokbuild.py
      GROK_BUILD_URL=http://127.0.0.1:8000 python tests\\probe_grokbuild.py

The proxy needs no API key on loopback by default; set GROK_API_KEY only if it
was started with one. Prints capability facts, never the key.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

import requests

ROOT = (os.environ.get("GROK_BUILD_URL") or "http://127.0.0.1:8000").rstrip("/")
BASE = f"{ROOT}/v1"
KEY = (os.environ.get("GROK_API_KEY") or "").strip()
MODEL = os.environ.get("GROK_BUILD_MODEL", "grok-4.7")

H = {"Content-Type": "application/json"}
if KEY:
    H["Authorization"] = f"Bearer {KEY}"


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


def post(body: dict, timeout: int = 180) -> requests.Response:
    return requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=timeout)


try:
    health = requests.get(f"{ROOT}/health", timeout=5)
except requests.RequestException as exc:
    sys.exit(f"Grok Build proxy is not reachable at {ROOT}: {exc}")

show("GET /health")
print("status", health.status_code, brief(health.text, 200))
if health.status_code != 200:
    sys.exit("Grok Build proxy is not healthy; start it with `python api_server.py`.")

# --------------------------------------------------------------------------- #
show("GET /models")
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
print("status", r.status_code)
if r.status_code == 200:
    entries = [m for m in (r.json().get("data") or []) if isinstance(m, dict)]
    print("model count:", len(entries))
    for m in entries:
        print(
            f"  {m.get('id')} | {m.get('name')} | ctx={m.get('context_window')} "
            f"| default_effort={m.get('reasoning_effort')} "
            f"| efforts={[e.get('id') for e in (m.get('reasoning_efforts') or [])]}"
        )
else:
    print(brief(r.text, 600))

# --------------------------------------------------------------------------- #
show("POST /chat/completions (plain, non-stream)")
started = time.perf_counter()
r = post({"model": MODEL, "messages": [{"role": "user", "content": "Reply with exactly: PONG"}], "stream": False})
print("status", r.status_code, f"({time.perf_counter() - started:.2f}s)")
if r.status_code == 200:
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    print("model:", data.get("model"), "| finish_reason:", choice.get("finish_reason"))
    print("content:", brief(str(message.get("content")), 120))
    print("message keys:", sorted(message))
    print("usage:", json.dumps(data.get("usage") or {})[:500])
else:
    print(brief(r.text, 600))

# --------------------------------------------------------------------------- #
show("token param semantics")
for key in ("max_tokens", "max_completion_tokens"):
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Write a 200 word essay about rain."}],
            "stream": False,
            key: 40,
        }
    )
    if r.status_code == 200:
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        print(
            f"  {key:22s} finish={choice.get('finish_reason')} "
            f"completion={usage.get('completion_tokens')} reasoning={details.get('reasoning_tokens')}"
        )
    else:
        print(f"  {key:22s} -> {r.status_code} {brief(r.text, 220)}")

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance")
for effort in ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
    started = time.perf_counter()
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "What is 17*23? Answer with the number only."}],
            "stream": False,
            "max_completion_tokens": 1024,
            "reasoning_effort": effort,
        }
    )
    if r.status_code == 200:
        data = r.json()
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        trace = message.get("reasoning_content") or message.get("reasoning") or ""
        print(
            f"  {effort:9s} OK  {time.perf_counter() - started:5.1f}s out={usage.get('completion_tokens')} "
            f"reasoning_tokens={details.get('reasoning_tokens')} reasoning_chars={len(str(trace))}"
        )
    else:
        print(f"  {effort:9s} FAIL {r.status_code} {brief(r.text, 220)}")

# --------------------------------------------------------------------------- #
show("streaming: delta keys + usage")
for include_usage in (False, True):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Think step by step: what is 12*12? Then answer."}],
        "stream": True,
        "max_completion_tokens": 1024,
        "reasoning_effort": "high",
    }
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    r = post(body)
    print("include_usage:", include_usage, "| status", r.status_code)
    if r.status_code != 200:
        print(" ", brief(r.text, 220))
        continue
    chunk_keys: set[str] = set()
    delta_keys: set[str] = set()
    reasoning: list[str] = []
    content: list[str] = []
    usage: dict = {}
    for raw in r.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        chunk_keys |= set(chunk)
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            delta_keys |= set(delta)
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning.append(str(delta.get("reasoning_content") or delta.get("reasoning")))
            if delta.get("content"):
                content.append(str(delta["content"]))
    r.close()
    print("  chunk keys:", sorted(chunk_keys))
    print("  delta keys:", sorted(delta_keys))
    print("  reasoning chars:", len("".join(reasoning)), "| content:", brief("".join(content), 70))
    print("  usage keys:", sorted(usage))

# --------------------------------------------------------------------------- #
show("sampling params")
for label, extra in (
    ("seed", {"seed": 12345}),
    ("temperature+top_p", {"temperature": 0.7, "top_p": 0.95}),
    ("stop", {"stop": ["\n\n"]}),
):
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Say OK"}],
            "stream": False,
            "max_completion_tokens": 64,
            **extra,
        },
        timeout=120,
    )
    print(f"  {label:18s} -> {r.status_code} {brief(r.text if r.status_code != 200 else 'OK', 180)}")

# --------------------------------------------------------------------------- #
show("vision (data-URI image_url)")
try:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    r = post(
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What colour is this image? One word."},
                        {"type": "image_url", "image_url": {"url": uri, "detail": "auto"}},
                    ],
                }
            ],
            "stream": False,
            "max_completion_tokens": 512,
        }
    )
    print("status", r.status_code)
    if r.status_code == 200:
        message = ((r.json().get("choices") or [{}])[0].get("message")) or {}
        print("content:", brief(str(message.get("content")), 120))
    else:
        print(brief(r.text, 400))
except Exception as exc:  # noqa: BLE001
    print("vision probe skipped:", type(exc).__name__, exc)

# --------------------------------------------------------------------------- #
show("error shapes")
r = post({"model": "definitely-not-a-model", "messages": [{"role": "user", "content": "hi"}], "stream": False}, timeout=60)
print("bad model ->", r.status_code, brief(r.text, 300))
r = post(
    {
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "max_completion_tokens": 32,
        "reasoning_effort": "max",
    },
    timeout=60,
)
print("bad effort ->", r.status_code, brief(r.text, 300))

show("done")
