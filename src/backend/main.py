import os
import requests
import httpx
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from lorex_controller import LorexController
from storage_service import StorageService
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()


# ── mediamtx topology ──────────────────────────────────────────────────────────
# mediamtx runs on localhost: HLS on 8888, HTTP API on 9997. Each camera has
# either one path (Wyze, single RTSP allowed) or two (Lorex/Ezviz: a main-stream
# path used for recording, and a `_live` sub-stream path used for browser HLS).
# This dict maps cam_id → (live_path_for_hls, recording_path_for_storage).
MEDIAMTX_HLS = "http://127.0.0.1:8888"
MEDIAMTX_API = "http://127.0.0.1:9997"

# Lorex credentials & known cameras
lorex_user = os.getenv("LOREX_USER", "admin")
lorex_pass = os.getenv("LOREX_PASSWORD", "")

LOREX_CAMERAS = [
    {"id": "lorex_127", "name": "Lorex Cam 1", "ip": os.getenv("LOREX_127_IP")},
    {"id": "lorex_122", "name": "Lorex Cam 2", "ip": os.getenv("LOREX_122_IP")},
]

ezviz_user = os.getenv("EZVIZ_USER", "admin")

EZVIZ_CAMERAS = [
    {"id": "ezviz_78",  "name": "Ezviz CV248", "ip": os.getenv("EZVIZ_78_IP")},
    {"id": "ezviz_120", "name": "Ezviz C6H",   "ip": os.getenv("EZVIZ_120_IP")},
]

wyze_rtsp_user = os.getenv("WYZE_RTSP_USER", "")
wyze_rtsp_pass = os.getenv("WYZE_RTSP_PASSWORD", "")

WYZE_RTSP_CAMERAS = [
    {"id": "wyze_126", "name": "Wyze Cam V2 - 1", "ip": os.getenv("WYZE_126_IP")},
    {"id": "wyze_105", "name": "Wyze Cam V2 - 2", "ip": os.getenv("WYZE_105_IP")},
]


def _hls_path(cam_id: str, cam_type: str) -> str:
    """mediamtx path used for browser HLS playback (sub stream where available)."""
    if cam_type in ("lorex", "ezviz"):
        return f"{cam_id}_live"
    return cam_id  # Wyze: single stream serves both HLS and recording


def _recording_path(cam_id: str) -> str:
    """mediamtx path used for 24/7 recording (always main stream / cam_id)."""
    return cam_id


storage = StorageService("./recordings")

# Initialize PTZ controllers for Lorex cameras at startup
_ptz_controllers: dict = {}

def _init_ptz():
    for cam in LOREX_CAMERAS:
        ctrl = LorexController(cam["ip"], lorex_user, lorex_pass, port=80)
        if ctrl.connect():
            _ptz_controllers[cam["id"]] = ctrl
            print(f"[PTZ] Connected to {cam['id']} at {cam['ip']}")
        else:
            print(f"[PTZ] Could not connect to {cam['id']} at {cam['ip']}")

_init_ptz()

_PTZ_MOVES = {
    "up":    (0.0,  0.5),
    "down":  (0.0, -0.5),
    "left":  (-0.5, 0.0),
    "right": (0.5,  0.0),
}


class PTZCommand(BaseModel):
    camera_id: str
    action: str


def _socket_online(ip: str, port: int = 554, timeout: float = 2.0) -> bool:
    import socket
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _camera_status(cam: dict, cam_type: str, ptz: bool) -> dict:
    return {
        "id": cam["id"],
        "name": cam["name"],
        "type": cam_type,
        "status": "online" if _socket_online(cam["ip"]) else "offline",
        "ip": cam["ip"],
        "stream": f"/hls/{_hls_path(cam['id'], cam_type)}/index.m3u8",
        "ptz": ptz,
    }


@app.get("/api/status")
def get_status():
    cams = [_camera_status(c, "lorex", True) for c in LOREX_CAMERAS]
    cams += [_camera_status(c, "ezviz", False) for c in EZVIZ_CAMERAS]
    cams += [_camera_status(c, "wyze_rtsp", False) for c in WYZE_RTSP_CAMERAS]
    return {
        "nvr_status": "active",
        "storage_path": os.path.abspath("./recordings"),
        "cameras": cams,
    }


@app.get("/api/snapshot/{camera_id}")
def get_snapshot(camera_id: str):
    """Snapshot endpoint — ONVIF digest for Lorex cameras."""
    lorex = next((c for c in LOREX_CAMERAS if c["id"] == camera_id), None)
    if lorex:
        url = (f"http://{lorex['ip']}/onvifsnapshot/media_service/snapshot"
               f"?channel=1&subtype=0")
        try:
            resp = requests.get(
                url,
                auth=requests.auth.HTTPDigestAuth(lorex_user, lorex_pass),
                timeout=5,
            )
            resp.raise_for_status()
            return Response(content=resp.content, media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Lorex snapshot error: {e}")
    raise HTTPException(status_code=404, detail=f"Unknown camera: {camera_id}")


@app.post("/api/ptz")
def control_camera(cmd: PTZCommand):
    if cmd.action not in _PTZ_MOVES:
        raise HTTPException(status_code=400, detail=f"Unknown action: {cmd.action}")
    ctrl = _ptz_controllers.get(cmd.camera_id)
    if not ctrl:
        cam = next((c for c in LOREX_CAMERAS if c["id"] == cmd.camera_id), None)
        if not cam:
            raise HTTPException(status_code=404, detail=f"Unknown camera: {cmd.camera_id}")
        ctrl = LorexController(cam["ip"], lorex_user, lorex_pass, port=80)
        if ctrl.connect():
            _ptz_controllers[cmd.camera_id] = ctrl
        else:
            raise HTTPException(status_code=503, detail=f"Cannot connect to PTZ on {cmd.camera_id}")
    dx, dy = _PTZ_MOVES[cmd.action]
    if not ctrl.relative_move(dx, dy):
        raise HTTPException(status_code=502, detail="PTZ move failed")
    return {"status": "success", "camera_id": cmd.camera_id, "action": cmd.action}


# ── HLS proxy to mediamtx ─────────────────────────────────────────────────────
# Proxy /hls/<anything> → http://127.0.0.1:8888/<anything>. Lets the dashboard
# stay on a single port (8000) and lets us strip cache headers so the browser
# can never serve stale segments from before a mediamtx restart.
#
# mediamtx tracks HLS sessions via a hlsSession cookie. The proxy must:
#   1. Forward the browser's Cookie header upstream so mediamtx recognises the
#      session on subsequent requests for sub-playlists / segments.
#   2. Forward mediamtx's Set-Cookie back to the browser, but strip the
#      Path attribute — mediamtx sets Path=/<stream>/ which would prevent
#      the browser from sending the cookie back through /hls/<stream>/.
#   3. Rewrite redirect Location: from /<stream>/… to /hls/<stream>/… so the
#      browser stays inside the proxy.
import re

def _rewrite_cookie_path(value: str) -> str:
    """Strip cookie attributes mediamtx sets that would prevent the browser
    from sending the cookie back through this proxy:
      Path=/<stream>/  → would scope the cookie too narrowly
      Secure           → would require HTTPS (we serve HTTP locally)
      SameSite=None    → only meaningful with Secure
      Partitioned      → newer flag, mostly cross-site, irrelevant here"""
    for attr_re in (
        r";\s*Path=[^;]*",
        r";\s*Secure(?=;|$)",
        r";\s*SameSite=[^;]*",
        r";\s*Partitioned(?=;|$)",
    ):
        value = re.sub(attr_re, "", value, flags=re.I)
    return value


@app.get("/hls/{full_path:path}")
async def proxy_hls(full_path: str, request: Request):
    url = f"{MEDIAMTX_HLS}/{full_path}"
    if request.url.query:
        url += "?" + request.url.query
    upstream_headers = {}
    cookie = request.headers.get("cookie")
    if cookie:
        upstream_headers["cookie"] = cookie
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            r = await client.get(url, headers=upstream_headers)
    except httpx.RequestError as e:
        raise HTTPException(503, f"mediamtx unreachable: {e}")

    resp = Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/octet-stream"),
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"

    for cv in r.headers.get_list("set-cookie"):
        resp.headers.append("set-cookie", _rewrite_cookie_path(cv))

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("location", "")
        if loc.startswith("/"):
            resp.headers["location"] = "/hls" + loc
        else:
            resp.headers["location"] = loc
    return resp


# ── Diagnostics ───────────────────────────────────────────────────────────────
def _mediamtx_path_state(path_name: str) -> dict:
    """Query mediamtx HTTP API for one path's state. Returns {} on any error."""
    try:
        r = requests.get(f"{MEDIAMTX_API}/v3/paths/get/{path_name}", timeout=2)
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return {}


@app.get("/api/diag/{camera_id}")
def diag(camera_id: str):
    """Per-camera diagnostic state. Combines mediamtx's view of the path with
    on-disk recording freshness so we can tell at a glance whether (a) the
    upstream RTSP source is broken, (b) mediamtx is up but the player is stuck,
    or (c) recordings are still landing on the NAS."""
    import re, time as _time
    if not re.match(r'^[a-zA-Z0-9_]+$', camera_id):
        raise HTTPException(400)

    # Look up cam_type to know which mediamtx paths to inspect
    cam_type = None
    for c in LOREX_CAMERAS:
        if c["id"] == camera_id: cam_type = "lorex"; break
    if not cam_type:
        for c in EZVIZ_CAMERAS:
            if c["id"] == camera_id: cam_type = "ezviz"; break
    if not cam_type:
        for c in WYZE_RTSP_CAMERAS:
            if c["id"] == camera_id: cam_type = "wyze_rtsp"; break

    rec_path = camera_id
    live_path = _hls_path(camera_id, cam_type) if cam_type else camera_id

    rec_state = _mediamtx_path_state(rec_path)
    live_state = _mediamtx_path_state(live_path) if live_path != rec_path else rec_state

    # On-disk recording freshness
    rec_dir = Path("recordings") / rec_path
    latest_rec = latest_rec_age = None
    if rec_dir.exists():
        recs = sorted(rec_dir.glob("*.ts"), key=lambda p: p.stat().st_mtime, reverse=True)
        if recs:
            latest_rec = recs[0].name
            latest_rec_age = round(_time.time() - recs[0].stat().st_mtime, 1)

    return {
        "camera_id": camera_id,
        "live_path": live_path,
        "rec_path": rec_path,
        "live_ready": live_state.get("ready", False),
        "live_bytes_received": live_state.get("bytesReceived"),
        "live_readers": len(live_state.get("readers", [])),
        "rec_ready": rec_state.get("ready", False),
        "rec_bytes_received": rec_state.get("bytesReceived"),
        "rec_source_type": (rec_state.get("source") or {}).get("type"),
        "latest_recording": latest_rec,
        "latest_recording_age_s": latest_rec_age,
    }


@app.get("/api/diag")
def diag_all():
    """Whole-system diagnostic: every mediamtx path + global health."""
    try:
        r = requests.get(f"{MEDIAMTX_API}/v3/paths/list", timeout=3)
        return {"mediamtx": r.json()}
    except requests.RequestException as e:
        return {"error": f"mediamtx unreachable: {e}"}


# ── Recordings ────────────────────────────────────────────────────────────────
@app.get("/api/recordings/{camera_id}")
def list_recordings(camera_id: str):
    import time as _time
    rec_dir = Path("recordings") / camera_id
    if not rec_dir.exists():
        return {"camera_id": camera_id, "recordings": [], "recording": False}
    files = sorted(rec_dir.glob("*.ts"), reverse=True)
    now = _time.time()
    recording_active = bool(files) and (now - files[0].stat().st_mtime) < 30
    result = []
    for i, f in enumerate(files):
        stat = f.stat()
        active = recording_active and i == 0
        result.append({
            "filename": f.name,
            "playlist": f"/api/recordings/{camera_id}/{f.name}/stream.m3u8",
            "size_mb": round(stat.st_size / 1024 / 1024, 1),
            "active": active,
        })
    return {"camera_id": camera_id, "recordings": result, "recording": recording_active}


def _ts_duration(path: Path) -> float:
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=4,
        )
        return float(r.stdout.strip())
    except Exception:
        return 300.0


@app.get("/api/recordings/{camera_id}/{filename}/stream.m3u8")
def recording_stream(camera_id: str, filename: str):
    import re, time as _time
    if not re.match(r'^[a-zA-Z0-9_]+$', camera_id):
        raise HTTPException(400)
    if not re.match(r'^\d{8}_\d{6}\.ts$', filename):
        raise HTTPException(400)
    ts_path = Path("recordings") / camera_id / filename
    if not ts_path.exists():
        raise HTTPException(404)
    is_active = (_time.time() - ts_path.stat().st_mtime) < 30
    duration = _ts_duration(ts_path)
    ts_url = f"/recordings/{camera_id}/{filename}"
    m3u8 = (
        "#EXTM3U\n#EXT-X-VERSION:3\n"
        f"#EXT-X-TARGETDURATION:{int(duration) + 1}\n"
        f"#EXTINF:{duration:.3f},\n"
        f"{ts_url}\n"
    )
    if not is_active:
        m3u8 += "#EXT-X-ENDLIST\n"
    return Response(content=m3u8, media_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-cache"})


app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")
app.mount("/", StaticFiles(directory="src/frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
