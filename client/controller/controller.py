#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
TOOL_CONFIG_DIR = Path(ROOT_DIR, "tool-config")
USER_CONFIG_DIR = Path(ROOT_DIR, "user-config")

CONFIG_PATH = Path(USER_CONFIG_DIR, "config.json")
STATUS_PATH = Path(USER_CONFIG_DIR, "status.json")

# Hotspot config
HOSTAPD_CONF_SRC = Path(TOOL_CONFIG_DIR, "hostapd.conf")
DNSMASQ_CONF_SRC = Path(TOOL_CONFIG_DIR, "dnsmasq-hotspot.conf")

# Where we "activate" hotspot config on the system
DNSMASQ_DROPIN = Path("/etc/dnsmasq.d/99-mydevice-hotspot.conf")

# WireGuard + Stream
WG_DIR = Path("/etc/wireguard")
STREAM_ENV = Path(USER_CONFIG_DIR, "stream.env")

# -----------------------------------------------------------------------------
# Single-radio "pause & scan" settings
# -----------------------------------------------------------------------------
SCAN_INTERVAL_S = 60          # how often to scan while hotspot is on
HOTSPOT_PAUSE_S = 2.0         # time to wait after stopping hostapd/dnsmasq before scanning
SCAN_TIMEOUT_S = 25           # how long to wait for scan results before resuming hotspot
WIFI_DEVICE = "wlan0"

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)

def systemctl_cmd(args: list[str], check: bool = True) -> None:
    sh(["systemctl"] + args, check=check)

def systemctl(action: str, unit: str, check: bool = True) -> None:
    systemctl_cmd([action, unit], check=check)

def is_unit_active(unit: str) -> bool:
    cp = sh(["systemctl", "is-active", unit], check=False)
    return cp.returncode == 0 and cp.stdout.strip() == "active"

def stop_unit(unit: str) -> None:
    systemctl("stop", unit, check=False)
    systemctl("disable", unit, check=False)

def start_or_restart_unit(unit: str) -> None:
    systemctl("enable", unit, check=False)
    systemctl("restart", unit, check=False)

def write_status(obj: Dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(obj, indent=2) + "\n")

def read_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        default = {
            "wireguard": {"enabled": False, "conf_path": ""},
            "stream": {"enabled": False, "input": "", "ffmpeg_args": ""},
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(default, indent=2) + "\n")
        return default
    return json.loads(CONFIG_PATH.read_text())

def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except Exception:
        return 0.0

def internet_up(timeout_s: float = 2.0) -> bool:
    try:
        sh(["ping", "-c", "1", "-W", str(int(timeout_s)), "1.1.1.1"], check=True)
        return True
    except Exception:
        return False

def has_default_route() -> bool:
    try:
        cp = sh(["ip", "route", "show", "default"], check=False)
        return cp.returncode == 0 and "default" in cp.stdout
    except Exception:
        return False

def wg_handshake_ok(iface: str, max_age_s: int = 120) -> bool:
    try:
        cp = sh(["wg", "show", iface, "latest-handshakes"], check=False)
        if cp.returncode != 0:
            return False
        now = int(time.time())
        for line in cp.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            ts = int(parts[1])
            if ts == 0:
                continue
            if (now - ts) <= max_age_s:
                return True
        return False
    except Exception:
        return False

def parse_kv_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    text = path.read_text(errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def parse_dnsmasq_conf(path: Path) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    text = path.read_text(errors="replace")

    m = re.search(r"^interface=(.+)$", text, flags=re.M)
    if m:
        cfg["interface"] = m.group(1).strip()

    m = re.search(r"^address=/#/(.+)$", text, flags=re.M)
    if m:
        cfg["portal_ip"] = m.group(1).strip()

    return cfg

def wireguard_iface_from_conf(conf_path: Optional[Path]) -> Optional[str]:
    if not conf_path:
        return None
    if conf_path.name.endswith(".conf") and conf_path.stem:
        return conf_path.stem
    return None

def nmcli_scan_networks() -> list[dict]:
    """
    Best-effort scan. Returns a list of dicts: [{"ssid":..., "signal":..., "security":...}, ...]
    Uses nmcli. If NetworkManager isn't present/active, returns [].
    """
    # Trigger rescan (best-effort)
    sh(["nmcli", "device", "wifi", "rescan", "ifname", WIFI_DEVICE], check=False)

    cp = sh(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", WIFI_DEVICE],
        check=False,
    )
    if cp.returncode != 0:
        return []

    nets: list[dict] = []
    for line in cp.stdout.strip().splitlines():
        # nmcli -t uses ":" separators; SSIDs can technically contain ":" but it's rare.
        parts = (line.split(":") + ["", "", ""])[:3]
        ssid, signal, sec = (p.strip() for p in parts)
        if not ssid:
            continue
        nets.append({"ssid": ssid, "signal": signal, "security": sec})
    # strongest first
    def key(n: dict) -> int:
        try:
            return -int(n.get("signal") or 0)
        except Exception:
            return 0
    nets.sort(key=key)
    return nets

# -----------------------------------------------------------------------------
# Hotspot
# -----------------------------------------------------------------------------
def enable_hotspot() -> None:
    if not HOSTAPD_CONF_SRC.exists():
        raise RuntimeError(f"Missing hotspot hostapd config: {HOSTAPD_CONF_SRC}")
    if not DNSMASQ_CONF_SRC.exists():
        raise RuntimeError(f"Missing hotspot dnsmasq config: {DNSMASQ_CONF_SRC}")

    hostapd_kv = parse_kv_file(HOSTAPD_CONF_SRC)
    dm = parse_dnsmasq_conf(DNSMASQ_CONF_SRC)

    iface = hostapd_kv.get("interface") or dm.get("interface")
    if not iface:
        raise RuntimeError("Hotspot interface not found in hostapd.conf or dnsmasq-hotspot.conf")

    ip = dm.get("portal_ip") or "192.168.4.1"

    # Ensure wlan0 is in a clean state (AP mode will be set by hostapd)
    sh(["ip", "link", "set", iface, "down"], check=False)
    sh(["ip", "addr", "flush", "dev", iface], check=False)
    sh(["ip", "link", "set", iface, "up"], check=False)
    sh(["ip", "addr", "add", f"{ip}/24", "dev", iface], check=False)

    os.makedirs(DNSMASQ_DROPIN.parent, exist_ok=True)
    sh(["ln", "-sf", str(DNSMASQ_CONF_SRC), str(DNSMASQ_DROPIN)], check=False)

    # Write local hostapd.conf to default location
    etc_hostapd_dir = Path("/etc/hostapd")
    etc_hostapd_dir.mkdir(parents=True, exist_ok=True)
    target = etc_hostapd_dir / "hostapd.conf"
    target.write_text(HOSTAPD_CONF_SRC.read_text(errors="replace"))
    os.chmod(target, 0o600)

    systemctl("unmask", "hostapd", check=False)
    systemctl("restart", "dnsmasq", check=False)
    systemctl("restart", "hostapd", check=True)

    if not is_unit_active("hostapd"):
        raise RuntimeError("hostapd did not become active; check: journalctl -u hostapd")

def disable_hotspot() -> None:
    systemctl("stop", "hostapd", check=False)
    systemctl("stop", "dnsmasq", check=False)

    sh(["rm", "-f", str(DNSMASQ_DROPIN)], check=False)

    if HOSTAPD_CONF_SRC.exists():
        iface = parse_kv_file(HOSTAPD_CONF_SRC).get("interface")
        if iface:
            sh(["ip", "addr", "flush", "dev", iface], check=False)

# -----------------------------------------------------------------------------
# Stream
# -----------------------------------------------------------------------------
def stream_should_run(cfg: Dict[str, Any], wg_state: str) -> bool:
    st = cfg.get("stream", {}) or {}
    if not bool(st.get("enabled", False)):
        return False

    url = st.get("url", "") or ""
    if not url:
        return False

    wg = cfg.get("wireguard", {}) or {}
    if bool(wg.get("enabled", False)):
        return wg_state == "up"
    return True

def restart_stream_service() -> None:
    systemctl("restart", "radio-station-stream.service", check=False)

def stop_stream_service() -> None:
    systemctl("stop", "radio-station-stream.service", check=False)

# -----------------------------------------------------------------------------
# WireGuard orchestration
# -----------------------------------------------------------------------------
def apply_wireguard(
    cfg: Dict[str, Any],
    *,
    hotspot_on: bool,
    net_ok: bool,
    cfg_changed: bool,
    last_wgconf_mtime: float,
) -> tuple[str, float]:
    wg_cfg = (cfg.get("wireguard", {}) or {})
    wg_enabled = bool(wg_cfg.get("enabled", False))
    conf_path_str = (wg_cfg.get("conf_path") or "").strip()
    conf_path = Path(conf_path_str) if conf_path_str else None

    iface = wireguard_iface_from_conf(conf_path) if conf_path else None
    if not iface:
        iface = "wg0"
    wg_unit = f"wg-quick@{iface}"

    conf_ok = bool(conf_path and conf_path.exists())
    want_wg_running = (not hotspot_on) and net_ok and wg_enabled and conf_ok

    wgconf_mtime = file_mtime(conf_path) if conf_path else 0.0
    wgconf_changed = wgconf_mtime != last_wgconf_mtime
    need_restart = cfg_changed or wgconf_changed

    if want_wg_running:
        WG_DIR.mkdir(parents=True, exist_ok=True)
        target = WG_DIR / f"{iface}.conf"
        try:
            if conf_path:
                if (
                    not target.exists()
                    or target.resolve() != conf_path.resolve()
                    or wgconf_changed
                ):
                    target.write_text(conf_path.read_text(errors="replace"))
                os.chmod(target, 0o600)
        except Exception as e:
            stop_unit(wg_unit)
            wg_state = f"error_prepare_conf:{e}"
        else:
            if (not is_unit_active(wg_unit)) or need_restart:
                start_or_restart_unit(wg_unit)
            wg_state = "up" if wg_handshake_ok(iface, 180) else "connecting"
    else:
        if is_unit_active(wg_unit):
            stop_unit(wg_unit)

        if hotspot_on:
            wg_state = "blocked_by_hotspot"
        elif not net_ok:
            wg_state = "blocked_no_uplink"
        elif not wg_enabled:
            wg_state = "disabled"
        elif not conf_ok:
            wg_state = "not_configured"
        else:
            wg_state = "stopped"

    return wg_state, wgconf_mtime

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def main() -> None:
    write_status({"status": "starting"})

    hotspot_on = False
    stream_on = False
    stream_state = "stopped"
    last_cfg_mtime = 0.0
    last_wgconf_mtime = 0.0

    last_scan_ts = 0.0
    last_scan_result: list[dict] = []

    while True:
        try:
            cfg_mtime = file_mtime(CONFIG_PATH)
            cfg = read_config()

            # Decide network vs hotspot
            net_ok = has_default_route() and internet_up()

            if not net_ok and not hotspot_on:
                enable_hotspot()
                hotspot_on = True
                last_scan_ts = time.time()

            if net_ok and hotspot_on:
                disable_hotspot()
                hotspot_on = False

            # If hotspot is on, periodically pause AP, scan, resume AP.
            # This prevents hostapd crashes caused by scanning while AP is active.
            now = time.time()
            if hotspot_on and (now - last_scan_ts) >= SCAN_INTERVAL_S:
                # Pause hotspot
                disable_hotspot()
                hotspot_on = False
                time.sleep(HOTSPOT_PAUSE_S)

                # Scan (best-effort). Note: this may still fail if NM isn't installed.
                last_scan_result = nmcli_scan_networks()
                last_scan_ts = time.time()

                # Resume hotspot if we are still offline
                net_ok = has_default_route() and internet_up()
                if not net_ok:
                    enable_hotspot()
                    hotspot_on = True

            # WireGuard orchestration
            cfg_changed = cfg_mtime != last_cfg_mtime
            wg_state, wgconf_mtime = apply_wireguard(
                cfg,
                hotspot_on=hotspot_on,
                net_ok=net_ok,
                cfg_changed=cfg_changed,
                last_wgconf_mtime=last_wgconf_mtime,
            )

            last_cfg_mtime = cfg_mtime
            last_wgconf_mtime = wgconf_mtime

            # Stream orchestration
            updated_stream_state = stream_should_run(cfg, wg_state) and net_ok
            if (updated_stream_state != stream_on) or (cfg_changed and updated_stream_state):
                stream_on = updated_stream_state
                if stream_on:
                    restart_stream_service()
                    stream_state = "running"
                else:
                    stop_stream_service()
                    stream_state = "stopped"

            write_status({
                "status": "ok",
                "network_ok": net_ok,
                "hotspot_on": hotspot_on,
                "wireguard": wg_state,
                "stream": stream_state,
                "last_wifi_scan_ts": int(last_scan_ts) if last_scan_ts else 0,
                "last_wifi_scan": last_scan_result[:30],  # cap size
                "ts": int(time.time()),
            })

        except Exception as e:
            write_status({"status": "error", "error": str(e), "ts": int(time.time())})

        time.sleep(3)

if __name__ == "__main__":
    main()
