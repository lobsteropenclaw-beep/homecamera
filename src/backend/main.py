import os
import requests
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from lorex_controller import LorexController
from storage_service import StorageService
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()


# Serving HLS files (m3u8 + segments) without cache headers is critical:
# ffmpeg reuses segment filenames after a watchdog restart (delete_segments
# means seg000.ts gets overwritten with new content). If the browser cached
# the old seg000.ts, hls.js will replay stale frames and the dashboard freezes
# even though the server is healthy. We strip etag/last-modified and force
# no-store so every HLS fetch hits disk fresh.
class NoCacheHLS(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/hls/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            for h in ("etag", "last-modified"):
                if h in response.headers:
                    del response.headers[h]
        return response


app.add_middleware(NoCacheHLS)

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


def _lorex_status(cam: dict) -> dict:
    """Quick reachability check for a Lorex camera."""
    import socket
    try:
        s = socket.create_connection((cam["ip"], 554), timeout=2)
        s.close()
        online = True
    except OSError:
        online = False
    return {
        "id": cam["id"],
        "name": cam["name"],
        "type": "lorex",
        "status": "online" if online else "offline",
        "ip": cam["ip"],
        "stream": f"/hls/{cam['id']}/stream.m3u8",
        "ptz": True,
    }


def _ezviz_status(cam: dict) -> dict:
    """Quick reachability check for an Ezviz camera."""
    import socket
    try:
        s = socket.create_connection((cam["ip"], 554), timeout=2)
        s.close()
        online = True
    except OSError:
        online = False
    return {
        "id": cam["id"],
        "name": cam["name"],
        "type": "ezviz",
        "status": "online" if online else "offline",
        "ip": cam["ip"],
        "stream": f"/hls/{cam['id']}/stream.m3u8",
        "ptz": False,
    }


def _wyze_rtsp_status(cam: dict) -> dict:
    """Quick reachability check for a Wyze camera with RTSP firmware."""
    import socket
    try:
        s = socket.create_connection((cam["ip"], 554), timeout=2)
        s.close()
        online = True
    except OSError:
        online = False
    return {
        "id": cam["id"],
        "name": cam["name"],
        "type": "wyze_rtsp",
        "status": "online" if online else "offline",
        "ip": cam["ip"],
        "stream": f"/hls/{cam['id']}/stream.m3u8",
        "ptz": False,
    }


@app.get("/api/status")
def get_status():
    camera_list = [_lorex_status(c) for c in LOREX_CAMERAS]
    camera_list += [_ezviz_status(c) for c in EZVIZ_CAMERAS]
    camera_list += [_wyze_rtsp_status(c) for c in WYZE_RTSP_CAMERAS]

    return {
        "nvr_status": "active",
        "storage_path": os.path.abspath("./recordings"),
        "cameras": camera_list,
    }


@app.get("/api/snapshot/{camera_id}")
def get_snapshot(camera_id: str):
    """Snapshot endpoint — ONVIF digest for Lorex, event thumbnail for Wyze."""
    # Lorex: fetch via ONVIF HTTP snapshot with Digest auth
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
        # Try reconnecting once
        cam = next((c for c in LOREX_CAMERAS if c["id"] == cmd.camera_id), None)
        if not cam:
            raise HTTPException(status_code=404, detail=f"Unknown camera: {cmd.camera_id}")
        ctrl = LorexController(cam["ip"], lorex_user, lorex_pass, port=80)
        if ctrl.connect():
            _ptz_controllers[cmd.camera_id] = ctrl
        else:
            raise HTTPException(status_code=503, detail=f"Cannot connect to PTZ on {cmd.camera_id}")

    dx, dy = _PTZ_MOVES[cmd.action]
    ok = ctrl.relative_move(dx, dy)
    if not ok:
        raise HTTPException(status_code=502, detail="PTZ move failed")
    return {"status": "success", "camera_id": cmd.camera_id, "action": cmd.action}


@app.get("/api/diag/{camera_id}")
def diag(camera_id: str):
    """Diagnostic state for one camera — dashboard logs this when recovery fails
    repeatedly so we can see whether the issue is server-side (ffmpeg dead, no
    fresh segments) or client-side (server fine, browser stuck)."""
    import re, time as _time
    if not re.match(r'^[a-zA-Z0-9_]+$', camera_id):
        raise HTTPException(400)

    pidfile = Path(f"{camera_id}.pid")
    logfile = Path(f"ffmpeg_{camera_id}.log")
    hls_dir = Path("src/frontend/hls") / camera_id
    rec_dir = Path("recordings") / camera_id
    now = _time.time()

    watchdog_pid = None
    watchdog_alive = False
    if pidfile.exists():
        try:
            watchdog_pid = int(pidfile.read_text().strip())
            os.kill(watchdog_pid, 0)
            watchdog_alive = True
        except (ValueError, OSError):
            pass

    latest_seg = latest_seg_age = m3u8_age = None
    if hls_dir.exists():
        segs = sorted(hls_dir.glob("seg*.ts"), key=lambda p: p.stat().st_mtime, reverse=True)
        if segs:
            latest_seg = segs[0].name
            latest_seg_age = round(now - segs[0].stat().st_mtime, 1)
        m3u8 = hls_dir / "stream.m3u8"
        if m3u8.exists():
            m3u8_age = round(now - m3u8.stat().st_mtime, 1)

    latest_rec = latest_rec_age = None
    if rec_dir.exists():
        recs = sorted(rec_dir.glob("*.ts"), key=lambda p: p.stat().st_mtime, reverse=True)
        if recs:
            latest_rec = recs[0].name
            latest_rec_age = round(now - recs[0].stat().st_mtime, 1)

    restart_count = 0
    last_log_lines: list = []
    if logfile.exists():
        try:
            content = logfile.read_text(errors="replace")
            restart_count = content.count("restarting in 5s")
            non_empty = [l for l in content.splitlines() if l.strip()]
            last_log_lines = non_empty[-5:]
        except Exception:
            pass

    return {
        "camera_id": camera_id,
        "watchdog_pid": watchdog_pid,
        "watchdog_alive": watchdog_alive,
        "latest_segment": latest_seg,
        "latest_segment_age_s": latest_seg_age,
        "m3u8_age_s": m3u8_age,
        "latest_recording": latest_rec,
        "latest_recording_age_s": latest_rec_age,
        "restart_count": restart_count,
        "last_log_lines": last_log_lines,
    }


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
