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

for prefix in "" "rec_"; do
    pidfile="${prefix}ezviz_120.pid"
    if [ -f "$pidfile" ]; then
        PID=$(cat "$pidfile"); kill "$PID" 2>/dev/null; pkill -P "$PID" 2>/dev/null; rm -f "$pidfile"
    fi
done
pkill -f "ffmpeg.*${EZVIZ_120_IP}" 2>/dev/null
sleep 2

hls_dir="src/frontend/hls/ezviz_120"
mkdir -p "$hls_dir" recordings/ezviz_120
rm -f "${hls_dir}"/seg*.ts "${hls_dir}"/stream.m3u8

> ffmpeg_ezviz_120.log
start_watched "ezviz_120.pid" "ffmpeg_ezviz_120.log" \
    ffmpeg -rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1 \
    -i "rtsp://${EZVIZ_USER}:${EZVIZ_120_PASSWORD}@${EZVIZ_120_IP}/Streaming/Channels/102" \
    -c:v copy -c:a aac -f hls -hls_time 1 -hls_list_size 3 -hls_flags delete_segments+split_by_time \
    -hls_segment_filename "${hls_dir}/seg%03d.ts" \
    "${hls_dir}/stream.m3u8"
echo "[✓] ezviz_120 HLS watchdog PID: $(cat ezviz_120.pid)"

> rec_ezviz_120.log
start_watched "rec_ezviz_120.pid" "rec_ezviz_120.log" \
    ffmpeg -rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1 \
    -fflags +discardcorrupt \
    -i "rtsp://${EZVIZ_USER}:${EZVIZ_120_PASSWORD}@${EZVIZ_120_IP}/Streaming/Channels/101" \
    -c:v copy -c:a aac \
    -f segment -segment_time 300 -strftime 1 -reset_timestamps 1 \
    "recordings/ezviz_120/%Y%m%d_%H%M%S.ts"
echo "[✓] ezviz_120 recording watchdog PID: $(cat rec_ezviz_120.pid)"
