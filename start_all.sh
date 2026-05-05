#!/bin/bash

# HomeCamera Unified Start Script
# Must be run from the project root: /path/to/homecamera/
#
# Architecture:
#   mediamtx → connects to each camera over RTSP, handles reconnection
#              internally, exposes HLS on :8888 and writes recordings to NAS.
#   FastAPI  → proxies /hls/* to mediamtx, hosts the dashboard SPA, exposes
#              PTZ + diag endpoints.
# Replaced 10 per-camera ffmpeg watchdogs with a single mediamtx process.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "--- Starting HomeCamera Integration Suite ---"
echo "[i] Working directory: $SCRIPT_DIR"

# 1. Load environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
    echo "[✓] Environment variables loaded."
else
    echo "[!] .env file not found. Please create it first."
    exit 1
fi

# 2. Activate virtual environment
if [ ! -f venv/bin/activate ]; then
    echo "[!] Virtual environment not found at ./venv"
    exit 1
fi
source venv/bin/activate
echo "[✓] Python environment activated."

# 3. Stop any old server / mediamtx / leftover ffmpeg watchdogs
OLD_PID=$(lsof -ti :8000 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "[!] Killing old server process ($OLD_PID) on port 8000..."
    kill "$OLD_PID" 2>/dev/null
    sleep 1
fi
if [ -f mediamtx.pid ]; then
    OLD=$(cat mediamtx.pid)
    if kill -0 "$OLD" 2>/dev/null; then
        echo "[!] Stopping old mediamtx (PID $OLD)..."
        kill "$OLD" 2>/dev/null
        sleep 1
    fi
    rm -f mediamtx.pid
fi
# Clean up any straggler watchdogs from the previous (pre-mediamtx) architecture
for pidfile in lorex_127.pid lorex_122.pid rec_lorex_127.pid rec_lorex_122.pid \
               ezviz_78.pid rec_ezviz_78.pid ezviz_120.pid rec_ezviz_120.pid \
               wyze_126.pid wyze_105.pid; do
    if [ -f "$pidfile" ]; then
        OLD=$(cat "$pidfile")
        kill "$OLD" 2>/dev/null
        pkill -P "$OLD" 2>/dev/null
        rm -f "$pidfile"
    fi
done
sleep 1

# 4. Mount NAS (if not already mounted) — recordings must land here, not local disk
NAS_SHARE="//${NAS_USER}:${NAS_PASSWORD}@${NAS_IP}/${NAS_SHARE_NAME}"
MOUNT_POINT="./recordings"
mkdir -p "$MOUNT_POINT"
if mount | grep -q "$MOUNT_POINT"; then
    echo "[✓] NAS already mounted at $MOUNT_POINT."
else
    echo "[...] NAS not mounted. Mounting $NAS_SHARE → $MOUNT_POINT ..."
    mount_smbfs "$NAS_SHARE" "$MOUNT_POINT" || { echo "[✗] Failed to mount NAS. Aborting."; exit 1; }
    echo "[✓] NAS mounted successfully."
fi

# 4b. Safety check: confirm recordings path is on NAS (smbfs), not local disk.
REC_FS=$(df "$MOUNT_POINT" | awk 'NR==2 {print $1}')
if [[ "$REC_FS" != *"${NAS_IP}"* ]]; then
    echo "[✗] SAFETY CHECK FAILED: $MOUNT_POINT is on '$REC_FS', not NAS."
    echo "[✗] Refusing to start — recordings would land on local disk."
    exit 1
fi
echo "[✓] Recordings path verified on NAS: $REC_FS"

# 5. Start mediamtx
if [ ! -f mediamtx.yml ]; then
    echo "[✗] mediamtx.yml not found. Copy mediamtx.yml.example and fill in credentials."
    exit 1
fi
if ! command -v mediamtx >/dev/null 2>&1; then
    echo "[✗] mediamtx not installed. Install via: brew install mediamtx"
    exit 1
fi

echo "[...] Starting mediamtx..."
> mediamtx.log
nohup mediamtx mediamtx.yml > mediamtx_console.log 2>&1 &
MEDIAMTX_PID=$!
echo "$MEDIAMTX_PID" > mediamtx.pid

# Wait up to 10 s for mediamtx HTTP API to come up — proves config parsed and
# server bound its listeners. After that we can query individual path readiness.
echo "[...] Waiting for mediamtx API..."
for i in $(seq 1 10); do
    sleep 1
    if curl -s http://127.0.0.1:9997/v3/paths/list >/dev/null 2>&1; then
        echo "[✓] mediamtx is up (PID $MEDIAMTX_PID)"
        break
    fi
done
if ! curl -s http://127.0.0.1:9997/v3/paths/list >/dev/null 2>&1; then
    echo "[✗] mediamtx did not start. Check mediamtx_console.log:"
    tail -20 mediamtx_console.log
    exit 1
fi

# 6. Start the Backend API (survives terminal close via nohup)
echo "[...] Starting Dashboard on http://localhost:8000"
nohup python src/backend/main.py > server.log 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > server.pid

# 7. Wait up to 15s for server and report camera readiness
echo "[...] Waiting for server to start..."
for i in $(seq 1 15); do
    sleep 1
    if curl -s http://localhost:8000/ > /dev/null 2>&1; then
        echo "[✓] Dashboard is live at http://localhost:8000  (PID $SERVER_PID)"
        sleep 2
        # mediamtx-side readiness per path
        for cam in lorex_127 lorex_122 ezviz_78 ezviz_120 wyze_126 wyze_105; do
            ready=$(curl -s "http://127.0.0.1:9997/v3/paths/get/${cam}" \
                | python3 -c "import json,sys; print(json.load(sys.stdin).get('ready', False))" 2>/dev/null || echo "?")
            if [ "$ready" = "True" ]; then
                echo "[✓] ${cam}: stream ready"
            else
                echo "[!] ${cam}: not ready yet — see /api/diag/${cam} or mediamtx.log"
            fi
        done
        echo ""
        echo "[i] Logs:    tail -f $SCRIPT_DIR/server.log  (FastAPI)"
        echo "[i] Logs:    tail -f $SCRIPT_DIR/mediamtx.log  (RTSP/HLS server)"
        echo "[i] Diag:    curl localhost:8000/api/diag/<cam_id>"
        echo "[i] Stop:    bash $SCRIPT_DIR/stop_all.sh"
        exit 0
    fi
done

echo "[!] Server did not respond after 15 seconds. Check logs:"
tail -20 server.log
exit 1
