"""Does CometAPI expose the reasoning trace over the stream, or only non-streamed?"""

from __future__ import annotations

import json
import os
import sys
import time

import requests

KEY = (os.environ.get("COMETAPI_API_KEY") or "").strip()
BASE = os.environ.get("COMETAPI_BASE_URL", "https://api.cometapi.com/v1")
MODEL = os.environ.get("COMETAPI_MODEL", "grok-4.7")
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
if not KEY:
    sys.exit("COMETAPI_API_KEY is not set")

PROMPT = (
    "Think step by step. A bat and a ball cost $1.10. The bat costs $1.00 more "
    "than the ball. How much is the ball? Answer with the amount only."
)

# what the node actually sends: content is a list of typed parts
PARTS = [{"type": "text", "text": PROMPT}]


def non_stream(content, **extra):
    body = {"model": MODEL, "messages": [{"role": "user", "content": content}], "stream": False, **extra}
    t0 = time.perf_counter()
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=300)
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:160]}"
    d = r.json()
    msg = ((d.get("choices") or [{}])[0].get("message")) or {}
    det = (d.get("usage") or {}).get("completion_tokens_details") or {}
    reason = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return (f"{time.perf_counter() - t0:5.1f}s  reasoning_tokens={det.get('reasoning_tokens')!s:>5}  "
            f"reasoning_chars={len(reason):>5}  content_chars={len(str(msg.get('content') or '')):>4}")


def stream(content, **extra):
    body = {"model": MODEL, "messages": [{"role": "user", "content": content}], "stream": True, **extra}
    t0 = time.perf_counter()
    r = requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=(20, 300), stream=True)
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:160]}"
    reason: list[str] = []
    content_out: list[str] = []
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
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reason.append(str(delta.get("reasoning_content") or delta.get("reasoning")))
            if delta.get("content"):
                content_out.append(str(delta["content"]))
    r.close()
    det = (usage.get("completion_tokens_details") or {})
    return (f"{time.perf_counter() - t0:5.1f}s  reasoning_tokens={det.get('reasoning_tokens')!s:>5}  "
            f"reasoning_chars={len(''.join(reason)):>5}  content_chars={len(''.join(content_out)):>4}")


print("content shape = plain string")
for label, kwargs in (
    ("no options          ", {}),
    ("max_tokens=4096     ", {"max_tokens": 4096}),
    ("max_completion=4096 ", {"max_completion_tokens": 4096}),
    ("+effort=high        ", {"max_completion_tokens": 4096, "reasoning_effort": "high"}),
):
    print(f"  NONSTREAM {label} {non_stream(PROMPT, **kwargs)}")
    print(f"  STREAM    {label} {stream(PROMPT, **kwargs)}")

print("\ncontent shape = typed parts (exactly what the node sends)")
for label, kwargs in (
    ("max_completion=4096 ", {"max_completion_tokens": 4096}),
    ("+effort=high        ", {"max_completion_tokens": 4096, "reasoning_effort": "high"}),
    ("+effort=high+seed   ", {"max_completion_tokens": 4096, "reasoning_effort": "high", "seed": 1}),
):
    print(f"  NONSTREAM {label} {non_stream(PARTS, **kwargs)}")
    print(f"  STREAM    {label} {stream(PARTS, **kwargs)}")

print("\nrepeat: is it deterministic?")
for i in range(3):
    print(f"  run {i}: NONSTREAM {non_stream(PARTS, max_completion_tokens=4096, reasoning_effort='high')}")
    print(f"  run {i}: STREAM    {stream(PARTS, max_completion_tokens=4096, reasoning_effort='high')}")
