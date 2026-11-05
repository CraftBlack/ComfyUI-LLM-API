"""Follow-up probe: stream delta shape + /models entry shape + effort edge cases."""

from __future__ import annotations

import json
import os
import sys

import requests

KEY = (os.environ.get("COMETAPI_API_KEY") or "").strip()
BASE = os.environ.get("COMETAPI_BASE_URL", "https://api.cometapi.com/v1")
MODEL = os.environ.get("COMETAPI_MODEL", "grok-4.7")
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
if not KEY:
    sys.exit("COMETAPI_API_KEY is not set")


def brief(text: str, limit: int = 300) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + f"... (+{len(text) - limit})"


print("=== /models entry shape ===")
r = requests.get(f"{BASE}/models", headers=H, timeout=30)
data = r.json()
entries = [m for m in (data.get("data") or []) if isinstance(m, dict)]
print("top-level keys:", sorted(data.keys()))
grok = [m for m in entries if m.get("id") == MODEL]
print("grok-4.7 entry:", json.dumps(grok[0], indent=None)[:600] if grok else "MISSING")
print("extra entry keys seen:", sorted({k for m in entries for k in m}))
print("total:", len(entries))
chat = [m["id"] for m in entries if "openai-chat" in (m.get("supported_endpoint_types") or [])]
print("openai-chat capable:", len(chat))
print("chat sample:", chat[:30])
print("grok chat:", [i for i in chat if i.startswith("grok")])

print("\n=== stream delta keys (raw first 12 data lines) ===")
payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Think step by step: what is 12*12? Then answer."}],
    "stream": True,
    "max_tokens": 2048,
    "reasoning_effort": "high",
}
r = requests.post(f"{BASE}/chat/completions", headers=H, json=payload, timeout=(20, 180), stream=True)
print("status", r.status_code)
shown = 0
keys_seen: set[str] = set()
delta_keys: set[str] = set()
reason_chars = 0
content_chars = 0
final_usage: dict = {}
for raw in r.iter_lines(decode_unicode=False):
    if not raw:
        continue
    line = raw.decode("utf-8", "replace").strip()
    if not line.startswith("data:"):
        if shown < 3:
            print("non-data line:", brief(line, 160))
        continue
    body = line[5:].strip()
    if body == "[DONE]":
        print("... [DONE]")
        break
    try:
        chunk = json.loads(body)
    except json.JSONDecodeError:
        continue
    keys_seen |= set(chunk)
    if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
        final_usage = chunk["usage"]
    for ch in chunk.get("choices") or []:
        delta_keys |= set(ch.get("delta") or {})
        d = ch.get("delta") or {}
        reason_chars += len(str(d.get("reasoning_content") or ""))
        content_chars += len(str(d.get("content") or ""))
    if shown < 12:
        print(json.dumps(chunk)[:420])
        shown += 1
r.close()
print("chunk keys:", sorted(keys_seen))
print("delta keys:", sorted(delta_keys))
print("reasoning chars:", reason_chars, "| content chars:", content_chars)
print("final usage:", json.dumps(final_usage))

print("\n=== effort edge cases ===")
for effort in ("default", "none", "minimal", "disabled", "max"):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Say OK"}],
        "stream": False,
        "max_tokens": 512,
        "reasoning_effort": effort,
    }
    rr = requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=120)
    print(f"{effort:10s} -> {rr.status_code} {brief(rr.text if rr.status_code != 200 else 'OK', 200)}")

print("\n=== max_tokens vs max_completion_tokens semantics ===")
for key in ("max_tokens", "max_completion_tokens"):
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write a 200 word essay about rain."}],
        "stream": False,
        key: 40,
    }
    rr = requests.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=180)
    if rr.status_code == 200:
        d = rr.json()
        ch = (d.get("choices") or [{}])[0]
        u = d.get("usage") or {}
        print(f"{key:22s} -> finish={ch.get('finish_reason')} completion_tokens={u.get('completion_tokens')}")
    else:
        print(f"{key:22s} -> {rr.status_code} {brief(rr.text, 200)}")

print("\n=== bad model id error shape ===")
rr = requests.post(
    f"{BASE}/chat/completions",
    headers=H,
    json={"model": "definitely-not-a-model", "messages": [{"role": "user", "content": "hi"}], "stream": False},
    timeout=60,
)
print(rr.status_code, brief(rr.text, 400))

print("\n=== bad key error shape ===")
rr = requests.post(
    f"{BASE}/chat/completions",
    headers={"Authorization": "Bearer sk-invalid", "Content-Type": "application/json"},
    json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": False},
    timeout=60,
)
print(rr.status_code, brief(rr.text, 300))
