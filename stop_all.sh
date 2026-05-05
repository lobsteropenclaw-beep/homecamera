#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Stop FastAPI server
if [ -f server.pid ]; then
    PID=$(cat server.pid)
    if kill "$PID" 2>/dev/null; then
        echo "[✓] Stopped server (PID $PID)"
    else
        echo "[!] Server PID $PID was not running."
    fi
    rm -f server.pid
else
    OLD_PID=$(lsof -ti :8000 2>/dev/null)
    if [ -n "$OLD_PID" ]; then
        kill "$OLD_PID" && echo "[✓] Stopped server on port 8000"
    fi
fi

# Stop mediamtx (it'll cleanly close all RTSP sessions and flush recordings)
if [ -f mediamtx.pid ]; then
    PID=$(cat mediamtx.pid)
    if kill "$PID" 2>/dev/null; then
        echo "[✓] Stopped mediamtx (PID $PID)"
    fi
    rm -f mediamtx.pid
fi

# Clean up straggler watchdogs from the pre-mediamtx architecture, if any
for cam_id in lorex_127 lorex_122 ezviz_78 ezviz_120 wyze_126 wyze_105; do
    for prefix in "" "rec_"; do
        pidfile="${prefix}${cam_id}.pid"
        if [ -f "$pidfile" ]; then
            PID=$(cat "$pidfile")
            kill "$PID" 2>/dev/null
            pkill -P "$PID" 2>/dev/null
            echo "[✓] Stopped legacy ${pidfile%.pid} (PID $PID)"
            rm -f "$pidfile"
        fi
    done
done
