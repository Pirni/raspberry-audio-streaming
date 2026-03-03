#!/usr/bin/env python3
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
USER_CONFIG_DIR = ROOT_DIR / "user-config"
CONFIG_PATH = USER_CONFIG_DIR / "config.json"

def load_cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text())

def _run_capture(cmd: list[str], timeout: int = 3) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    return (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")

def detect_alsa_input() -> Optional[str]:
    """
    Best-effort ALSA input detection.
    Returns something like 'hw:0,0' or 'plughw:1,0'. Returns None if not found.
    """
    # 1) Prefer explicit hardware capture devices from 'arecord -l'
    try:
        out = _run_capture(["arecord", "-l"], timeout=3)
        # Example lines:
        # card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
        # card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
        cards = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("card ") and ", device " in line:
                # extract card and device numbers
                # card X: ... device Y: ...
                try:
                    left = line.split(":", 1)[0]  # "card X"
                    card_no = int(left.split()[1])
                    dev_part = line.split("device", 1)[1]  # " 0: ..."
                    dev_no = int(dev_part.strip().split(":", 1)[0])
                    cards.append((card_no, dev_no, line))
                except Exception:
                    pass

        if cards:
            # Heuristics: prefer non-bcm2835 and non-hdmi if possible
            def score(t):
                line = t[2].lower()
                s = 0
                if "usb" in line:
                    s += 30
                if "bcm2835" in line:
                    s -= 20
                if "hdmi" in line:
                    s -= 10
                # lower card/dev as slight preference
                s += max(0, 10 - t[0])
                return -s  # sort ascending later

            cards.sort(key=score)
            card_no, dev_no, _ = cards[0]
            return f"plughw:{card_no},{dev_no}"
    except Exception:
        pass

    # 2) Fallback: parse arecord -L for first hw/plughw
    try:
        out = _run_capture(["arecord", "-L"], timeout=3)
        candidates = []
        for line in out.splitlines():
            name = line.strip()
            if not name or name.startswith("#") or name.endswith(":"):
                continue
            if name.startswith("hw:") or name.startswith("plughw:"):
                candidates.append(name)
        if candidates:
            # Prefer plughw (more forgiving formats), else hw
            plug = [c for c in candidates if c.startswith("plughw:")]
            return plug[0] if plug else candidates[0]
    except Exception:
        pass

    return None

def build_cmd(cfg: dict) -> list[str]:
    st = (cfg.get("stream") or {})
    if not st.get("enabled"):
        return []
    url = (st.get("url") or "").strip()

    inp = (st.get("input") or "").strip()
    if not inp:
        inp = detect_alsa_input() or ""
        print(f"[stream-runner] detected input: {inp}", flush=True)
        if not inp:
            # No input device found -> can't stream
            return []

    args = (st.get("ffmpeg_args") or "").strip()
    if not url or not inp:
        return []
    cmd = ["ffmpeg", "-hide_banner", "-f", "alsa", "-i", inp]
    if args:
        cmd += shlex.split(args)  # handles quotes properly
    cmd += [url]
    return cmd

def main() -> int:
    while True:
        try:
            cfg = load_cfg()
        except Exception as e:
            print(f"[stream-runner] config read error: {e}", flush=True)
            time.sleep(5)
            continue

        cmd = build_cmd(cfg)
        if not cmd:
            print("[stream-runner] stream disabled or missing url/input; exiting 0", flush=True)
            return 0

        print("[stream-runner] starting:", " ".join(shlex.quote(x) for x in cmd), flush=True)

        # Run ffmpeg in foreground; return code triggers systemd restart
        p = subprocess.Popen(cmd)
        rc = p.wait()
        print(f"[stream-runner] ffmpeg exited rc={rc}", flush=True)

        # If ffmpeg exits, let systemd restart us
        return rc

if __name__ == "__main__":
    sys.exit(main())