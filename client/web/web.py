import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

EXEC_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXEC_DIR.parent
TOOL_CONFIG_DIR = ROOT_DIR / "tool-config"
USER_CONFIG_DIR = ROOT_DIR / "user-config"
STATIC_DIR = EXEC_DIR / "static"

APP_TITLE = "Radio Station Web"
CONFIG_PATH = USER_CONFIG_DIR / "config.json"
STATUS_PATH = USER_CONFIG_DIR / "status.json"

# ---- JSON config (stream + wireguard) --------------------------------------

DEFAULT_CONFIG = {
    "wireguard": {
        "enabled": False,
        "conf_path": str(USER_CONFIG_DIR / "wg0.conf"),
    },
    "stream": {
        "enabled": False,
        "url": "",
        "input": "",
        "ffmpeg_args": "",
    },
}

# ---- Wi-Fi device name ------------------------------------------------------
WIFI_DEVICE = "wlan0"

# ---- helpers ----------------------------------------------------------------

def ensure_root() -> None:
    if os.geteuid() != 0:
        raise HTTPException(
            status_code=403,
            detail="This service must run as root (needs nmcli and write access).",
        )

def run(cmd: List[str], timeout: int = 20) -> str:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run {cmd}: {e}")

    if p.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Command failed: {' '.join(cmd)} | {p.stderr.strip() or p.stdout.strip()}",
        )
    return p.stdout

def nmcli_available() -> None:
    if shutil.which("nmcli") is None:
        raise HTTPException(status_code=500, detail="nmcli not found. Install NetworkManager.")

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        os.chmod(CONFIG_PATH, 0o600)
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse {CONFIG_PATH}: {e}")

def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.parent / ".config.json.tmp"
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)

def safe_filename(filename: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", filename or "wg0.conf")

def validate_wireguard_conf(text: str) -> None:
    if "[Interface]" not in text:
        raise HTTPException(status_code=400, detail="WireGuard config missing [Interface] section.")
    if "PrivateKey" not in text:
        raise HTTPException(status_code=400, detail="WireGuard config missing PrivateKey.")

# ---- models -----------------------------------------------------------------

class WifiConnectRequest(BaseModel):
    ssid: str
    psk: Optional[str] = None

class WgEnableRequest(BaseModel):
    enabled: bool

class StreamConfigRequest(BaseModel):
    enabled: bool
    url: str
    input: Optional[str] = ""
    ffmpeg_args: Optional[str] = ""

# ---- app --------------------------------------------------------------------

app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- captive portal ---
@app.get("/generate_204")
@app.get("/gen_204")
def android_probe():
    return RedirectResponse(url="/", status_code=302)

@app.get("/hotspot-detect.html")
def apple_probe():
    return RedirectResponse(url="/", status_code=302)

@app.get("/connecttest.txt")
@app.get("/ncsi.txt")
def windows_probe():
    return RedirectResponse(url="/", status_code=302)


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/status")
def get_controller_status() -> Dict[str, Any]:
    if not STATUS_PATH.exists():
        return {"status": "unknown"}

    try:
        cfg = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse {STATUS_PATH}: {e}")

# ---- Config endpoint ---------------------------------------------------------

@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return load_config()

# ---- Wi-Fi endpoints ---------------------------------------------------------

@app.get("/api/wifi/status")
def wifi_status() -> Dict[str, Any]:
    nmcli_available()

    out = run([
        "nmcli", "-t",
        "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY",
        "device", "show", WIFI_DEVICE
    ])

    data: Dict[str, str] = {}
    for line in out.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            data[k] = v

    return {
        "device": WIFI_DEVICE,
        "state": data.get("GENERAL.STATE"),
        "connection": data.get("GENERAL.CONNECTION"),
        "ip4_address": data.get("IP4.ADDRESS[1]") or data.get("IP4.ADDRESS"),
        "ip4_gateway": data.get("IP4.GATEWAY"),
    }

@app.get("/api/wifi/saved")
def wifi_saved() -> Dict[str, Any]:
    nmcli_available()

    out = run(["nmcli", "-t", "-f", "NAME,TYPE,UUID,DEVICE", "connection", "show"])
    conns = []
    for line in out.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 4:
            conns.append({
                "name": parts[0],
                "type": parts[1],
                "uuid": parts[2],
                "device": parts[3]
            })

    conns.sort(key=lambda c: (c["type"] != "wifi", c["name"].lower()))
    return {"connections": conns}

@app.get("/api/wifi/scan")
def wifi_scan() -> Dict[str, Any]:
    nmcli_available()

    try:
        run(["nmcli", "device", "wifi", "rescan"], timeout=25)
    except HTTPException:
        pass

    out = run(["nmcli", "-t", "-f", "SSID,SECURITY,SIGNAL,BARS", "device", "wifi", "list"])
    nets = []
    for line in out.strip().splitlines():
        ssid, sec, sig, bars = (line.split(":") + ["", "", "", ""])[:4]
        ssid = ssid.strip()
        if not ssid:
            continue
        nets.append({"ssid": ssid, "security": sec, "signal": sig, "bars": bars})

    def sig_key(n):
        try:
            return -int(n["signal"])
        except Exception:
            return 0

    nets.sort(key=sig_key)
    return {"networks": nets}

@app.post("/api/wifi/connect")
def wifi_connect(req: WifiConnectRequest) -> Dict[str, Any]:
    ensure_root()
    nmcli_available()

    ssid = req.ssid.strip()
    if not ssid:
        raise HTTPException(status_code=400, detail="SSID is required.")

    if req.psk and req.psk.strip():
        out = run(["nmcli", "dev", "wifi", "connect", ssid, "password", req.psk.strip()], timeout=60)
    else:
        out = run(["nmcli", "dev", "wifi", "connect", ssid], timeout=60)

    return {"ok": True, "result": out.strip()}

# ---- WireGuard endpoints (flag + save file) ---------------------------------

@app.get("/api/wg/status")
def wg_status() -> Dict[str, Any]:
    cfg = load_config()
    wg = cfg.get("wireguard", {})
    conf_path = Path((wg.get("conf_path") or str(USER_CONFIG_DIR / "wg0.conf")))

    return {
        "enabled": bool(wg.get("enabled", False)),
        "conf_path": str(conf_path),
        "conf_exists": conf_path.exists(),
        "config_path": str(CONFIG_PATH),
    }

@app.post("/api/wg/enable")
def wg_enable(req: WgEnableRequest) -> Dict[str, Any]:
    ensure_root()
    cfg = load_config()
    cfg.setdefault("wireguard", {})
    cfg["wireguard"]["enabled"] = bool(req.enabled)
    # keep conf_path
    if not cfg["wireguard"].get("conf_path"):
        cfg["wireguard"]["conf_path"] = str(USER_CONFIG_DIR / "wg0.conf")
    save_config(cfg)
    return {"ok": True, "enabled": cfg["wireguard"]["enabled"]}

@app.post("/api/wg/upload")
async def wg_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    ensure_root()
    cfg = load_config()
    wg = cfg.setdefault("wireguard", {})

    # Always store as user-config/wg0.conf
    conf_path = USER_CONFIG_DIR / "wg0.conf"

    _ = safe_filename(file.filename or "wg0.conf")
    content = (await file.read()).decode("utf-8", errors="replace")
    validate_wireguard_conf(content)

    conf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = conf_path.parent / ".wg-upload.tmp"
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(conf_path)

    wg["conf_path"] = str(conf_path)
    save_config(cfg)

    return {"ok": True, "saved_as": str(conf_path)}

# ---- Stream endpoints (enabled + url + optional input/ffmpeg_args) -----------

@app.get("/api/stream")
def get_stream_config() -> Dict[str, Any]:
    cfg = load_config()
    st = cfg.get("stream", {}) or {}
    return {
        "enabled": bool(st.get("enabled", False)),
        "url": (st.get("url") or ""),
        "input": (st.get("input") or ""),
        "ffmpeg_args": (st.get("ffmpeg_args") or ""),
    }

@app.post("/api/stream")
def set_stream_config(req: StreamConfigRequest) -> Dict[str, Any]:
    ensure_root()

    url = (req.url or "").strip()
    # If set, do basic sanity check
    if url and not re.match(r"^(https?://|rtmp://|rtmps://)", url):
        raise HTTPException(status_code=400, detail="Stream URL must start with http(s):// or rtmp(s)://")

    cfg = load_config()
    cfg.setdefault("stream", {})
    cfg["stream"]["enabled"] = bool(req.enabled)
    cfg["stream"]["url"] = url
    cfg["stream"]["input"] = (req.input or "").strip()
    cfg["stream"]["ffmpeg_args"] = (req.ffmpeg_args or "").strip()

    save_config(cfg)
    return {"ok": True, "stream": cfg["stream"]}