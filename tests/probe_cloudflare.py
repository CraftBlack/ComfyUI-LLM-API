"""Live probe of Cloudflare Workers AI (OpenAI-compatible endpoint).

Run:
  set CLOUDFLARE_ACCOUNT_ID=...  &&  set CLOUDFLARE_API_TOKEN=cfut_...  &&  python tests\\probe_cloudflare.py

The account id and token are read from the environment; the committed default
is redacted. Prints capability facts, never the credentials.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

import requests

ACCOUNT = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
KEY = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/google/gemma-4-26b-a4b-it")

if not ACCOUNT or not KEY:
    sys.exit("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required")

BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


def post(body: dict, timeout: int = 90) -> requests.Response:
    return requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=(10, timeout))


# --------------------------------------------------------------------------- #
show("GET /v1/models")
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
print("status", r.status_code, brief(r.text, 220))

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
show("token caps (reasoning counts toward the cap)")
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
        content = str((choice.get("message") or {}).get("content") or "")
        print(
            f"  {key:22s} finish={choice.get('finish_reason')} "
            f"completion={usage.get('completion_tokens')} content={brief(content, 40)!r}"
        )
    else:
        print(f"  {key:22s} -> {r.status_code} {brief(r.text, 220)}")
    time.sleep(1)

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance")
for effort in ("default", "none", "low", "medium", "high", "max"):
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
        usage = data.get("usage") or {}
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        trace = message.get("reasoning_content") or message.get("reasoning") or ""
        print(
            f"  {effort:9s} OK  {time.perf_counter() - started:5.1f}s out={usage.get('completion_tokens')} "
            f"reasoning_chars={len(str(trace))} content={brief(str(message.get('content')), 30)!r}"
        )
    else:
        print(f"  {effort:9s} FAIL {r.status_code} {brief(r.text, 220)}")
    time.sleep(1)

# --------------------------------------------------------------------------- #
show("streaming: delta keys + usage")
body = {"model": MODEL, "messages": [{"role": "user", "content": "Say OK"}], "stream": True, "max_tokens": 256}
r = post(body)
print("status", r.status_code)
if r.status_code == 200:
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
            if delta.get("content"):
                content.append(str(delta["content"]))
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning.append(str(delta.get("reasoning_content") or delta.get("reasoning")))
    r.close()
    print("chunk keys:", sorted(chunk_keys))
    print("delta keys:", sorted(delta_keys))
    print("content:", brief("".join(content), 60), "| reasoning chars:", len("".join(reasoning)))
    print("usage:", json.dumps(usage)[:400])
else:
    print(brief(r.text, 400))
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
time.sleep(1)

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
            "max_tokens": 256,
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
time.sleep(1)

# --------------------------------------------------------------------------- #
show("error shapes")
r = post({"model": "@cf/definitely-not-a-model", "messages": [{"role": "user", "content": "hi"}], "stream": False})
print("bad model ->", r.status_code, brief(r.text, 300))
r = requests.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": "Bearer cfut_invalid", "Content-Type": "application/json"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": False},
    timeout=(10, 30),
)
print("bad key   ->", r.status_code, brief(r.text, 300))

show("done")
