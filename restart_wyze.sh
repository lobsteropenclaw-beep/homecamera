#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

set -a; source .env; set +a
source venv/bin/activate

start_watched() {
    local pidfile=$1 logfile=$2; shift 2
    (
        trap 'pkill -P $$ 2>/dev/null; exit 0' TERM INT
        while true; do
            "$@" >> "$logfile" 2>&1
            local code=$?
            if [ $code -eq 0 ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Process exited (code 0 — clean EOF), restarting in 1s..." >> "$logfile"
                sleep 1
            else
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] Process exited (code $code), restarting in 5s..." >> "$logfile"
                sleep 5
            fi
        done
    ) &
    local pid=$!
    disown $pid
    echo $pid > "$pidfile"
}

# Kill old Wyze processes
for cam_id in wyze_126 wyze_105; do
    for prefix in "" "rec_"; do
        pidfile="${prefix}${cam_id}.pid"
        if [ -f "$pidfile" ]; then
            PID=$(cat "$pidfile")
            kill "$PID" 2>/dev/null
            pkill -P "$PID" 2>/dev/null
            rm -f "$pidfile"
        fi
    done
done
pkill -f "ffmpeg.*${WYZE_126_IP}" 2>/dev/null
pkill -f "ffmpeg.*${WYZE_105_IP}" 2>/dev/null
sleep 2

# Start combined HLS+recording (one RTSP connection per camera, no audio)
for cam_id in wyze_126 wyze_105; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    eval cam_ip=\$$ip_var
    rtsp_url="rtsp://${WYZE_RTSP_USER}:${WYZE_RTSP_PASSWORD}@${cam_ip}:554/live"
    hls_dir="src/frontend/hls/${cam_id}"
    mkdir -p "$hls_dir" "recordings/${cam_id}"
    > "ffmpeg_${cam_id}.log"
    start_watched "${cam_id}.pid" "ffmpeg_${cam_id}.log" \
        ffmpeg -rtsp_transport tcp -timeout 8000000 -use_wallclock_as_timestamps 1 \
        -fflags +discardcorrupt \
        -an \
        -i "$rtsp_url" \
        -c:v copy \
        -f hls -hls_time 1 -hls_list_size 3 -hls_flags delete_segments+split_by_time \
        -hls_segment_filename "${hls_dir}/seg%03d.ts" \
        "${hls_dir}/stream.m3u8" \
        -c:v copy \
        -f segment -segment_time 300 -strftime 1 -reset_timestamps 1 \
        "recordings/${cam_id}/%Y%m%d_%H%M%S.ts"
    echo "[✓] ${cam_id} combined watchdog PID: $(cat ${cam_id}.pid)"
done
