"""Live probe of Crusoe Cloud inference (https://api.inference.crusoecloud.com/v1).

Run:  set CRUSOE_API_KEY=...  &&  python tests\\probe_crusoe.py

Prints capability facts, never the key.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

import requests

KEY = (os.environ.get("CRUSOE_API_KEY") or "").strip()
BASE = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1")
MODEL = os.environ.get("CRUSOE_MODEL", "google/gemma-4-31b-it")

if not KEY:
    sys.exit("CRUSOE_API_KEY is not set")

H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


def post(body: dict, timeout: int = 60) -> requests.Response:
    return requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=(10, timeout))


# --------------------------------------------------------------------------- #
show("GET /models")
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
print("status", r.status_code)
if r.status_code == 200:
    entries = [m for m in (r.json().get("data") or []) if isinstance(m, dict)]
    ids = [m.get("id") for m in entries]
    print("model count:", len(ids))
    print("ids:", ids)
    gemma = next((m for m in entries if m.get("id") == MODEL), None)
    print("gemma entry:", json.dumps(gemma)[:700] if gemma else "MISSING")
else:
    print(brief(r.text, 600))

# --------------------------------------------------------------------------- #
show("POST /chat/completions (plain, non-stream)")
started = time.perf_counter()
r = post({"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}], "stream": False})
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
time.sleep(1)

# --------------------------------------------------------------------------- #
show("token caps")
for key in ("max_tokens", "max_completion_tokens"):
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Count from 1 to 50, comma separated."}],
            "stream": False,
            key: 16,
        }
    )
    if r.status_code == 200:
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        print(
            f"  {key:22s} finish={choice.get('finish_reason')} "
            f"completion={usage.get('completion_tokens')} "
            f"content={brief(str((choice.get('message') or {}).get('content')), 50)!r}"
        )
    else:
        print(f"  {key:22s} -> {r.status_code} {brief(r.text, 220)}")
    time.sleep(1)

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance")
for effort in ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
    started = time.perf_counter()
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "What is 17*23? Answer with the number only."}],
            "stream": False,
            "max_tokens": 256,
            "reasoning_effort": effort,
        }
    )
    if r.status_code == 200:
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        message = choice.get("message") or {}
        trace = message.get("reasoning_content") or message.get("reasoning") or ""
        print(
            f"  {effort:9s} OK  {time.perf_counter() - started:5.1f}s out={usage.get('completion_tokens')} "
            f"reasoning_tokens={usage.get('reasoning_tokens')} reasoning_chars={len(str(trace))}"
        )
    else:
        print(f"  {effort:9s} FAIL {r.status_code} {brief(r.text, 220)}")
    time.sleep(1)

# --------------------------------------------------------------------------- #
show("streaming")
for include_usage in (False, True):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}], "stream": True, "max_tokens": 256}
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    r = post(body)
    print("include_usage:", include_usage, "| status", r.status_code)
    if r.status_code != 200:
        print(" ", brief(r.text, 220))
        continue
    chunk_keys: set[str] = set()
    delta_keys: set[str] = set()
    content: list[str] = []
    reasoning: list[str] = []
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
            if delta.get("content"):
                content.append(str(delta["content"]))
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning.append(str(delta.get("reasoning_content") or delta.get("reasoning")))
    r.close()
    print("  chunk keys:", sorted(chunk_keys))
    print("  delta keys:", sorted(delta_keys))
    print("  content:", brief("".join(content), 60), "| reasoning chars:", len("".join(reasoning)))
    print("  usage:", json.dumps(usage)[:400])
    time.sleep(1)

# --------------------------------------------------------------------------- #
show("sampling params")
r = post(
    {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say OK"}],
        "stream": False,
        "max_tokens": 64,
        "seed": 1,
        "temperature": 0.7,
        "top_p": 0.95,
    }
)
print("seed+temperature+top_p ->", r.status_code, brief(r.text if r.status_code != 200 else "OK", 180))

# --------------------------------------------------------------------------- #
show("vision (data-URI image_url)")
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
        "max_tokens": 256,
    }
)
print("status", r.status_code)
if r.status_code == 200:
    message = ((r.json().get("choices") or [{}])[0].get("message")) or {}
    print("content:", brief(str(message.get("content")), 120))
else:
    print(brief(r.text, 400))

# --------------------------------------------------------------------------- #
show("error shapes")
r = post({"model": "nope/nope", "messages": [{"role": "user", "content": "hi"}], "stream": False})
print("bad model ->", r.status_code, brief(r.text, 300))
r = requests.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": "Bearer bad-key", "Content-Type": "application/json"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": False},
    timeout=(10, 30),
)
print("bad key   ->", r.status_code, brief(r.text, 300))

show("done")
