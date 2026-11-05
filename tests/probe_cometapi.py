"""Live probe of the CometAPI gateway (https://api.cometapi.com/v1).

Run:  set COMETAPI_API_KEY=sk-...  &&  python tests\\probe_cometapi.py

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

KEY = (os.environ.get("COMETAPI_API_KEY") or "").strip()
BASE = os.environ.get("COMETAPI_BASE_URL", "https://api.cometapi.com/v1")
MODEL = os.environ.get("COMETAPI_MODEL", "grok-4.7")

if not KEY:
    sys.exit("COMETAPI_API_KEY is not set")

H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 400) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


# --------------------------------------------------------------------------- #
show("GET /models")
started = time.perf_counter()
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
print("status", r.status_code, f"({time.perf_counter() - started:.2f}s)")
ids: list[str] = []
if r.status_code == 200:
    payload = r.json()
    ids = [m.get("id") for m in (payload.get("data") or []) if isinstance(m, dict)]
    print("model count:", len(ids))
    grok = [i for i in ids if "grok" in (i or "").lower()]
    print("grok*:", grok)
    print("exact grok-4.7 present:", MODEL in ids)
    print("first 25 ids:", ids[:25])
else:
    print(brief(r.text))

# --------------------------------------------------------------------------- #
show("POST /chat/completions (plain, non-stream)")
body = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
    "stream": False,
}
started = time.perf_counter()
r = requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=180)
elapsed = time.perf_counter() - started
print("status", r.status_code, f"({elapsed:.2f}s)")
if r.status_code == 200:
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    print("model:", data.get("model"))
    print("finish_reason:", choice.get("finish_reason"))
    print("content:", brief(str(msg.get("content"))))
    print("reasoning keys:", [k for k in msg if "reason" in k.lower()])
    print("usage:", json.dumps(data.get("usage") or {}))
    print("top-level keys:", sorted(data.keys()))
else:
    print(brief(r.text, 800))

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance")
for effort in ("low", "medium", "high", "xhigh"):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "What is 17*23? Answer with the number only."}],
        "stream": False,
        "max_tokens": 2048,
        "reasoning_effort": effort,
    }
    started = time.perf_counter()
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=180)
    dt = time.perf_counter() - started
    if r.status_code == 200:
        data = r.json()
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        usage = data.get("usage") or {}
        det = usage.get("completion_tokens_details") or {}
        print(
            f"{effort:7s} OK  {dt:6.2f}s out={usage.get('completion_tokens')} "
            f"reasoning={det.get('reasoning_tokens')} content={brief(str(msg.get('content')), 60)!r}"
        )
    else:
        print(f"{effort:7s} FAIL {r.status_code} {brief(r.text, 200)}")

# --------------------------------------------------------------------------- #
show("vision (data-URI image_url)")
try:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    payload = {
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
        "max_tokens": 512,
    }
    started = time.perf_counter()
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=180)
    print("status", r.status_code, f"({time.perf_counter() - started:.2f}s)")
    if r.status_code == 200:
        data = r.json()
        msg = ((data.get("choices") or [{}])[0].get("message")) or {}
        print("content:", brief(str(msg.get("content")), 120))
    else:
        print(brief(r.text, 400))
except Exception as exc:  # noqa: BLE001
    print("vision probe skipped:", type(exc).__name__, exc)

# --------------------------------------------------------------------------- #
show("streaming (SSE)")
payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Count from 1 to 5, comma separated."}],
    "stream": True,
    "max_tokens": 1024,
}
started = time.perf_counter()
try:
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=(20, 180), stream=True)
    print("status", r.status_code)
    chunks = 0
    text: list[str] = []
    reason: list[str] = []
    usage = {}
    saw_done = False
    for raw in r.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            saw_done = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        chunks += 1
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for ch in chunk.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                text.append(str(delta["content"]))
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reason.append(str(delta.get("reasoning_content") or delta.get("reasoning")))
    print(f"chunks={chunks} done={saw_done} ({time.perf_counter() - started:.2f}s)")
    print("content:", brief("".join(text), 120))
    print("reasoning chars:", len("".join(reason)))
    print("usage:", json.dumps(usage))
    r.close()
except Exception as exc:  # noqa: BLE001
    print("stream failed:", type(exc).__name__, exc)

# --------------------------------------------------------------------------- #
show("optional-parameter tolerance")
for label, extra in (
    ("seed", {"seed": 12345}),
    ("temperature+top_p", {"temperature": 0.7, "top_p": 0.95}),
    ("max_completion_tokens", {"max_completion_tokens": 512}),
):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say OK"}],
        "stream": False,
        **extra,
    }
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=120)
    print(f"{label:22s} -> {r.status_code} {brief(r.text if r.status_code != 200 else 'OK', 200)}")

show("done")
