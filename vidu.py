# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "numpy", "pillow"]
# ///
"""Small Vidu API client for title-screen experiments.

  uv run vidu.py balance
  uv run vidu.py ref-image                 # writes vidu/ref_title.png (1920x1080 from MENU_Title_00001)
  uv run vidu.py submit tests.json         # submits every entry not yet submitted, records task ids
  uv run vidu.py poll                      # waits for running tasks and downloads finished mp4s

The API key is read from .vidu_key (or VIDU_API_KEY) and never printed.
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "vidu"
STATE = OUT / "tasks.json"
API = "https://api.vidu.com/ent/v2"
ENDPOINTS = {"img2video": "img2video", "start-end": "start-end2video", "reference": "reference2video"}


def key():
    k = os.environ.get("VIDU_API_KEY") or (HERE / ".vidu_key").read_text().strip()
    return {"Authorization": f"Token {k}", "Content-Type": "application/json"}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    OUT.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False))


def data_uri(path):
    p = HERE / path
    mime = "image/png" if p.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def cmd_balance():
    r = requests.get(f"{API}/credits", headers=key(), timeout=30)
    r.raise_for_status()
    for rem in r.json().get("remains", []):
        print(f"{rem.get('type')}: {rem.get('credit_remain')} credits, concurrency {rem.get('current_concurrency')}/{rem.get('concurrency_limit')}")


def cmd_ref_image():
    from bbfmt import Tpf, dcx_read, deswizzle
    import bc1
    menu = Path(os.environ.get("BB_MENU") or HERE.parent / "dvdroot_ps4" / "menu")
    tpf = Tpf.parse(dcx_read((menu / "title.tpf.dcx").read_bytes()))
    t = next(x for x in tpf.textures if x.name == "MENU_Title_00001")
    img = bc1.decode(deswizzle(t.data, t.w, t.h, 8), t.w, t.h)[:, :1920]
    full = np.concatenate([img, img[-56:][::-1]])  # 1920x1080: mirror the dark cobblestones into the missing rows
    OUT.mkdir(exist_ok=True)
    Image.fromarray(full).save(OUT / "ref_title.png")
    print("wrote", OUT / "ref_title.png", full.shape)


def cmd_submit(spec_file):
    state = load_state()
    for t in json.loads((HERE / spec_file).read_text()):
        name = t.pop("name")
        if name in state:
            print(f"{name}: already submitted ({state[name]['task_id']})")
            continue
        kind = t.pop("kind")
        t["images"] = [data_uri(p) for p in t["images"]]
        r = requests.post(f"{API}/{ENDPOINTS[kind]}", headers=key(), json=t, timeout=120)
        if r.status_code != 200:
            print(f"{name}: HTTP {r.status_code} {r.text[:300]}")
            continue
        j = r.json()
        state[name] = {"task_id": j["task_id"], "kind": kind, "model": t["model"], "prompt": t.get("prompt", ""),
                       "duration": t.get("duration"), "resolution": t.get("resolution"), "credits": j.get("credits"),
                       "state": j.get("state")}
        print(f"{name}: task {j['task_id']} state={j.get('state')} credits={j.get('credits')}")
        save_state(state)


def cmd_poll(timeout_s=1800):
    state = load_state()
    t0 = time.time()
    while True:
        pending = [n for n, s in state.items() if s["state"] not in ("success", "failed", "downloaded")]
        for name in pending:
            s = state[name]
            r = requests.get(f"{API}/tasks/{s['task_id']}/creations", headers=key(), timeout=30)
            if r.status_code != 200:
                print(f"{name}: poll HTTP {r.status_code}")
                continue
            j = r.json()
            s["state"], s["credits"] = j["state"], j.get("credits", s.get("credits"))
            if j["state"] == "failed":
                s["err_code"] = j.get("err_code")
                print(f"{name}: FAILED {j.get('err_code')}")
            elif j["state"] == "success":
                url = j["creations"][0]["url"]
                mp4 = OUT / f"{name}.mp4"
                mp4.write_bytes(requests.get(url, timeout=300).content)
                s["state"], s["file"] = "downloaded", mp4.name
                print(f"{name}: downloaded {mp4.name} ({mp4.stat().st_size // 1024} KB, {s['credits']} credits)")
            save_state(state)
        pending = [n for n, s in state.items() if s["state"] not in ("failed", "downloaded")]
        if not pending or time.time() - t0 > timeout_s:
            print("pending:", pending or "none")
            return
        time.sleep(20)


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"balance": cmd_balance, "ref-image": cmd_ref_image, "poll": cmd_poll}.get(cmd, lambda: cmd_submit(sys.argv[2]))()
