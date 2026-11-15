"""Live probe of the HowToFix ID gateway (https://api.howtofix.id/v1).

Run:  set HOWTOFIX_API_KEY=sk-...  &&  python tests\\probe_howtofix.py

The gateway can be slow or bursty; every request uses a 90s read timeout and
the probe sleeps between calls. Prints capability facts, never the key.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time

import requests

KEY = (os.environ.get("HOWTOFIX_API_KEY") or "").strip()
BASE = os.environ.get("HOWTOFIX_BASE_URL", "https://api.howtofix.id/v1")
MODEL = os.environ.get("HOWTOFIX_MODEL", "grok-4.7")

if not KEY:
    sys.exit("HOWTOFIX_API_KEY is not set")

H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def show(title: str) -> None:
    print(f"\n=== {title} ===")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


def post(body: dict, timeout: int = 90) -> requests.Response:
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
    print("grok ids:", [i for i in ids if "grok" in str(i).lower()])
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
show("max_completion_tokens caps the visible answer")
started = time.perf_counter()
r = post(
    {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write the numbers 1 to 100 separated by commas."}],
        "stream": False,
        "max_completion_tokens": 16,
        "seed": 1,
        "temperature": 0.7,
        "top_p": 0.95,
    }
)
print("status", r.status_code, f"({time.perf_counter() - started:.2f}s)")
if r.status_code == 200:
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = data.get("usage") or {}
    content = str(message.get("content") or "")
    trace = message.get("reasoning_content") or message.get("reasoning") or ""
    print(
        f"finish={choice.get('finish_reason')} completion={usage.get('completion_tokens')} "
        f"content_chars={len(content)} reasoning_chars={len(str(trace))}"
    )
    print("content:", brief(content, 120))
else:
    print(brief(r.text, 400))
time.sleep(2)

# --------------------------------------------------------------------------- #
show("reasoning_effort acceptance (slow gateway; 90s read timeout)")
for effort in ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
    started = time.perf_counter()
    try:
        r = post(
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": "What is 17*23? Answer with the number only."}],
                "stream": False,
                "max_completion_tokens": 256,
                "reasoning_effort": effort,
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  {effort:9s} EXC {type(exc).__name__} ({time.perf_counter() - started:.1f}s)")
        time.sleep(2)
        continue
    if r.status_code == 200:
        data = r.json()
        usage = data.get("usage") or {}
        message = ((data.get("choices") or [{}])[0].get("message")) or {}
        trace = message.get("reasoning_content") or message.get("reasoning") or ""
        print(
            f"  {effort:9s} OK  {time.perf_counter() - started:5.1f}s out={usage.get('completion_tokens')} "
            f"reasoning_chars={len(str(trace))}"
        )
    else:
        print(f"  {effort:9s} FAIL {r.status_code} {brief(r.text, 220)}")
    time.sleep(2)

# --------------------------------------------------------------------------- #
show("streaming (usage included by default)")
body = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Say OK"}],
    "stream": True,
    "max_completion_tokens": 128,
}
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
    print("content:", brief("".join(content), 80), "| reasoning chars:", len("".join(reasoning)))
    print("usage:", json.dumps(usage)[:400])
else:
    print(brief(r.text, 400))
time.sleep(2)

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
            "max_completion_tokens": 128,
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
try:
    r = post({"model": "definitely-not-a-model", "messages": [{"role": "user", "content": "hi"}], "stream": False}, timeout=60)
    print("bad model ->", r.status_code, brief(r.text, 400))
except Exception as exc:  # noqa: BLE001
    print("bad model EXC:", type(exc).__name__, str(exc)[:200])
time.sleep(1)
try:
    r = requests.post(
        f"{BASE}/chat/completions",
        headers={"Authorization": "Bearer sk-invalid", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": False},
        timeout=(10, 30),
    )
    print("bad key   ->", r.status_code, brief(r.text, 300))
except Exception as exc:  # noqa: BLE001
    print("bad key EXC:", type(exc).__name__, str(exc)[:200])

show("done")
