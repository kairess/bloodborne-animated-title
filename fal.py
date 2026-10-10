# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""Minimal fal.ai queue client for image-to-video jobs.

  uv run fal.py submit spec.json     # submits every entry not yet submitted (state in vidu/fal_tasks.json)
  uv run fal.py poll                 # waits for running jobs and downloads vidu/<name>.mp4

Spec entries: {"name", "endpoint", "image": local path, ...model inputs}. The key is read from .fal_key
(or FAL_KEY) and never printed.
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "vidu"
STATE = OUT / "fal_tasks.json"
QUEUE = "https://queue.fal.run"


def headers():
    k = os.environ.get("FAL_KEY") or (HERE / ".fal_key").read_text().strip()
    return {"Authorization": f"Key {k}", "Content-Type": "application/json"}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    OUT.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False))


def data_uri(path):
    p = HERE / path
    mime = "image/png" if p.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def cmd_submit(spec_file):
    state = load_state()
    for t in json.loads((HERE / spec_file).read_text()):
        name, endpoint = t.pop("name"), t.pop("endpoint")
        if name in state:
            print(f"{name}: already submitted ({state[name]['request_id']})")
            continue
        t["image_url"] = data_uri(t.pop("image"))
        r = requests.post(f"{QUEUE}/{endpoint}", headers=headers(), json=t, timeout=120)
        if r.status_code not in (200, 201, 202):
            print(f"{name}: HTTP {r.status_code} {r.text[:400]}")
            continue
        j = r.json()
        state[name] = {"endpoint": endpoint, "request_id": j["request_id"], "status_url": j["status_url"],
                       "response_url": j["response_url"], "prompt": t.get("prompt", ""), "duration": t.get("duration"),
                       "resolution": t.get("resolution"), "seed": t.get("seed"), "state": "IN_QUEUE"}
        print(f"{name}: request {j['request_id']}")
        save_state(state)


def cmd_poll(timeout_s=1800):
    state = load_state()
    t0 = time.time()
    while True:
        for name, s in state.items():
            if s["state"] in ("downloaded", "failed"):
                continue
            r = requests.get(s["status_url"], headers=headers(), timeout=30)
            if r.status_code not in (200, 202):
                print(f"{name}: status HTTP {r.status_code} {r.text[:300]}")
                continue
            st = r.json().get("status")
            if st != s["state"]:
                print(f"{name}: {st}")
            s["state"] = st
            if st == "COMPLETED":
                res = requests.get(s["response_url"], headers=headers(), timeout=60)
                if res.status_code != 200:
                    s["state"], s["error"] = "failed", res.text[:500]
                    print(f"{name}: FAILED {res.text[:500]}")
                else:
                    j = res.json()
                    mp4 = OUT / f"{name}.mp4"
                    mp4.write_bytes(requests.get(j["video"]["url"], timeout=300).content)
                    s["state"], s["file"], s["seed_used"] = "downloaded", mp4.name, j.get("seed")
                    print(f"{name}: downloaded {mp4.name} ({mp4.stat().st_size // 1024} KB, seed {j.get('seed')})")
            save_state(state)
        pending = [n for n, s in state.items() if s["state"] not in ("downloaded", "failed")]
        if not pending or time.time() - t0 > timeout_s:
            print("pending:", pending or "none")
            return
        time.sleep(15)


if __name__ == "__main__":
    {"poll": cmd_poll}.get(sys.argv[1], lambda: cmd_submit(sys.argv[2]))()
