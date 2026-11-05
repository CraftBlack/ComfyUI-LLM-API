"""Calibration: effort scaling on CometAPI + Grokified sanity + token param semantics."""

from __future__ import annotations

import json
import os
import sys
import time

import requests

COMET_KEY = (os.environ.get("COMETAPI_API_KEY") or "").strip()
GROK_KEY = (os.environ.get("GROKIFIED_API_KEY") or "").strip()
PROMPT = "Think carefully. A bat and a ball cost $1.10 total. The bat costs $1.00 more than the ball. How much is the ball? Answer with the amount only."


def call(base: str, key: str, model: str, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}], "stream": False, **extra}
    t0 = time.perf_counter()
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body,
        timeout=240,
    )
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {(r.text or '')[:200]}"
    d = r.json()
    u = d.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    return (
        f"{dt:6.2f}s finish={ch.get('finish_reason'):8s} out={u.get('completion_tokens'):5} "
        f"reason={det.get('reasoning_tokens')} ans={str(msg.get('content'))[:24]!r}"
    )


print("=== CometAPI grok-4.7 effort scaling (max_completion_tokens=4096) ===")
for effort in ("minimal", "low", "medium", "high", "xhigh", "max"):
    print(
        f"  {effort:8s}",
        call("https://api.cometapi.com/v1", COMET_KEY, "grok-4.7", max_completion_tokens=4096, reasoning_effort=effort),
    )

print("\n=== CometAPI grok-4.7 without reasoning_effort ===")
print("  (none)  ", call("https://api.cometapi.com/v1", COMET_KEY, "grok-4.7", max_completion_tokens=4096))

print("\n=== CometAPI tool/other models smoke ===")
for model in ("deepseek-v4.1-flash", "gpt-6-astra", "claude-sonnet-5-5"):
    print(f"  {model:22s}", call("https://api.cometapi.com/v1", COMET_KEY, model, max_completion_tokens=2048))

if GROK_KEY:
    print("\n=== Grokified grok-4.7 token param semantics ===")
    print("  max_completion_tokens=80 ", call("https://api.grokified.com/v1", GROK_KEY, "grok-4.7", max_completion_tokens=80))
    print("  max_tokens=80            ", call("https://api.grokified.com/v1", GROK_KEY, "grok-4.7", max_tokens=80))
    print("\n=== Grokified effort scaling ===")
    for effort in ("low", "medium", "high", "xhigh"):
        print(
            f"  {effort:8s}",
            call("https://api.grokified.com/v1", GROK_KEY, "grok-4.7", max_completion_tokens=4096, reasoning_effort=effort),
        )
    print("\n=== Grokified /models ===")
    r = requests.get(
        "https://api.grokified.com/v1/models",
        headers={"Authorization": f"Bearer {GROK_KEY}"},
        timeout=30,
    )
    print("  status", r.status_code)
    if r.status_code == 200:
        ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
        print("  count:", len(ids))
        print("  ids:", ids)
else:
    print("\nGROKIFIED_API_KEY not set - skipping Grokified side")
