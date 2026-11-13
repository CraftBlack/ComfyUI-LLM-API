"""Live probe of the Coarena gateway (https://coarena.ai/api/v1).

Run:  set COARENA_API_KEY=coa_...  &&  python tests\\probe_coarena.py

Prints only capability facts - never the key itself.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

import requests

KEY = (os.environ.get("COARENA_API_KEY") or "").strip()
BASE = os.environ.get("COARENA_BASE_URL", "https://coarena.ai/api/v1")
MODEL = os.environ.get("COARENA_MODEL", "grok-4-7")

if not KEY:
    sys.exit("COARENA_API_KEY is not set")

H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


def post(body: dict, timeout: int = 180) -> requests.Response:
    return requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=timeout)


# --------------------------------------------------------------------------- #
show("GET /models")
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
print("status", r.status_code)
if r.status_code == 200:
    entries = [m for m in (r.json().get("data") or []) if isinstance(m, dict)]
    ids = [m.get("id") for m in entries]
    print("model count:", len(ids))
    print("ids:", ids)
    grok = next((m for m in entries if m.get("id") == MODEL), None)
    print("grok entry:", json.dumps(grok)[:600] if grok else "MISSING")
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
    print("usage:", json.dumps(data.get("usage") or {}))
else:
    print(brief(r.text, 600))

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance")
for effort in ("default", "minimal", "low", "medium", "high", "xhigh", "max"):
    started = time.perf_counter()
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "What is 17*23? Answer with the number only."}],
            "stream": False,
            "max_completion_tokens": 2048,
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
            f"  {effort:8s} OK  {time.perf_counter() - started:5.1f}s out={usage.get('completion_tokens')} "
            f"reasoning_tokens={details.get('reasoning_tokens')} reasoning_chars={len(str(trace))}"
        )
    else:
        print(f"  {effort:8s} FAIL {r.status_code} {brief(r.text, 220)}")

# --------------------------------------------------------------------------- #
show("max_completion_tokens caps the visible answer only")
for cap in (8, 40):
    r = post(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Count from 1 to 100."}],
            "stream": False,
            "max_completion_tokens": cap,
        }
    )
    if r.status_code == 200:
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        message = choice.get("message") or {}
        print(
            f"  cap={cap:3d} finish={choice.get('finish_reason')} "
            f"completion={usage.get('completion_tokens')} reasoning={details.get('reasoning_tokens')} "
            f"content={brief(str(message.get('content')), 50)!r}"
        )
    else:
        print(f"  cap={cap:3d} FAIL {r.status_code} {brief(r.text, 200)}")

# --------------------------------------------------------------------------- #
show("streaming: usage arrives only when requested")
for include_usage in (False, True):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say OK"}],
        "stream": True,
        "max_completion_tokens": 64,
    }
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    r = post(body, timeout=120)
    usage: dict = {}
    chunk_keys: set[str] = set()
    if r.status_code == 200:
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
        r.close()
    print(
        f"  include_usage={include_usage!s:5s} -> {r.status_code} "
        f"usage_keys={sorted(usage)} chunk_keys={sorted(chunk_keys)}"
    )

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
r = requests.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": "Bearer coa-invalid", "Content-Type": "application/json"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": False},
    timeout=60,
)
print("bad key   ->", r.status_code, brief(r.text, 300))

show("done")
