#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Stop watchdog loops and their ffmpeg children
for cam_id in lorex_127 lorex_122 ezviz_78 ezviz_120 wyze_126 wyze_105; do
    for prefix in "" "rec_"; do
        pidfile="${prefix}${cam_id}.pid"
        if [ -f "$pidfile" ]; then
            PID=$(cat "$pidfile")
            kill "$PID" 2>/dev/null
            pkill -P "$PID" 2>/dev/null
            if [ $? -eq 0 ] || kill -0 "$PID" 2>/dev/null; then
                echo "[✓] Stopped ${pidfile%.pid} (PID $PID)"
            fi
            rm -f "$pidfile"
        fi
    done
done

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
    else
        echo "[i] No server running."
    fi
fi
