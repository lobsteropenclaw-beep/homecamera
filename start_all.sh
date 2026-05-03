#!/bin/bash

# HomeCamera Unified Start Script
# Must be run from the project root: /path/to/homecamera/

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

# Helper: start a command in a self-restarting watchdog loop.
# Usage: start_watched <pidfile> <logfile> <cmd...>
# Saves the watchdog PID so stop_all.sh can kill the loop and its child.
start_watched() {
    local pidfile=$1 logfile=$2; shift 2
    (
        trap 'pkill -P $$ 2>/dev/null; exit 0' TERM INT
        while true; do
            "$@" >> "$logfile" 2>&1
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Process exited (code $?), restarting in 5s..." >> "$logfile"
            sleep 5
        done
    ) &
    local pid=$!
    disown $pid
    echo $pid > "$pidfile"
}

# 3. Stop any old server/streams
OLD_PID=$(lsof -ti :8000 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "[!] Killing old server process ($OLD_PID) on port 8000..."
    kill "$OLD_PID" 2>/dev/null
    sleep 1
fi
# Kill old watchdog loops and their ffmpeg children
for pidfile in lorex_127.pid lorex_122.pid rec_lorex_127.pid rec_lorex_122.pid ezviz_78.pid rec_ezviz_78.pid ezviz_120.pid rec_ezviz_120.pid wyze_126.pid wyze_105.pid; do
    if [ -f "$pidfile" ]; then
        OLD=$(cat "$pidfile")
        kill "$OLD" 2>/dev/null
        pkill -P "$OLD" 2>/dev/null
        rm -f "$pidfile"
    fi
done
sleep 1

# 4. Mount NAS (if not already mounted)
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
# Without this, a missing/dropped NAS mount would silently fall through to local.
REC_FS=$(df "$MOUNT_POINT" | awk 'NR==2 {print $1}')
if [[ "$REC_FS" != *"${NAS_IP}"* ]]; then
    echo "[✗] SAFETY CHECK FAILED: $MOUNT_POINT is on '$REC_FS', not NAS."
    echo "[✗] Refusing to start — recordings would land on local disk."
    exit 1
fi
echo "[✓] Recordings path verified on NAS: $REC_FS"

# 5. Start Lorex HLS streams via ffmpeg (auto-restarts on disconnect)
mkdir -p src/frontend/hls/lorex_127 src/frontend/hls/lorex_122

LOREX_RTSP_OPTS="-rtsp_transport tcp -timeout 5000000 -use_wallclock_as_timestamps 1"
# omit_endlist: don't write #EXT-X-ENDLIST when ffmpeg exits, so hls.js keeps
# polling for new segments after a watchdog restart instead of giving up.
LOREX_HLS_OPTS="-c:v copy -c:a aac -f hls -hls_time 1 -hls_list_size 3 -hls_flags delete_segments+split_by_time+omit_endlist"

for cam_id in lorex_127 lorex_122; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    eval cam_ip=\$$ip_var
    rtsp_url="rtsp://${LOREX_USER}:${LOREX_PASSWORD}@${cam_ip}:554/cam/realmonitor?channel=1&subtype=1&unicast=true&proto=Onvif"
    hls_dir="src/frontend/hls/${cam_id}"
    echo "[...] Starting HLS stream for ${cam_id} (${cam_ip})..."
    # Truncate log on first start, then append on restarts
    > "ffmpeg_${cam_id}.log"
    start_watched "${cam_id}.pid" "ffmpeg_${cam_id}.log" \
        ffmpeg $LOREX_RTSP_OPTS \
        -i "$rtsp_url" \
        $LOREX_HLS_OPTS \
        -hls_segment_filename "${hls_dir}/seg%03d.ts" \
        "${hls_dir}/stream.m3u8"
    echo "[✓] ${cam_id} HLS watchdog PID: $(cat ${cam_id}.pid)"
done

# 5b. Start Ezviz HLS streams (URL paths differ by model)
# Audio is dropped (-an): Ezviz substream audio has erratic timestamps that
# back up the AAC encoder and cause ffmpeg to exit every minute or two,
# breaking the dashboard live preview. Recording (5d) keeps audio via main stream.
mkdir -p src/frontend/hls/ezviz_78 src/frontend/hls/ezviz_120
for cam_id in ezviz_78 ezviz_120; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    pw_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_PASSWORD"
    eval cam_ip=\$$ip_var
    eval cam_pw=\$$pw_var
    case "$cam_id" in
        ezviz_78)  sub_path="/H.264Preview_01_sub" ;;
        ezviz_120) sub_path="/Streaming/Channels/102" ;;
    esac
    rtsp_url="rtsp://${EZVIZ_USER}:${cam_pw}@${cam_ip}${sub_path}"
    hls_dir="src/frontend/hls/${cam_id}"
    echo "[...] Starting HLS stream for ${cam_id} (${cam_ip})..."
    > "ffmpeg_${cam_id}.log"
    start_watched "${cam_id}.pid" "ffmpeg_${cam_id}.log" \
        ffmpeg $LOREX_RTSP_OPTS \
        -an \
        -i "$rtsp_url" \
        -c:v copy \
        -f hls -hls_time 1 -hls_list_size 3 -hls_flags delete_segments+split_by_time+omit_endlist \
        -hls_segment_filename "${hls_dir}/seg%03d.ts" \
        "${hls_dir}/stream.m3u8"
    echo "[✓] ${cam_id} HLS watchdog PID: $(cat ${cam_id}.pid)"
done

# 5c. Start Wyze HLS streams + 24/7 recording in a single ffmpeg per camera.
# Wyze V2 RTSP firmware only allows one concurrent RTSP connection, so HLS and
# recording must share one input stream via multiple output mapping.
# Audio is dropped (-an): Wyze V2 audio has erratic timestamps that cause the
# AAC encoder to back up and stall the entire pipeline. Longer read timeout
# (20s) tolerates the V2's frequent video stalls without triggering restarts.
for cam_id in wyze_126 wyze_105; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    eval cam_ip=\$$ip_var
    rtsp_url="rtsp://${WYZE_RTSP_USER}:${WYZE_RTSP_PASSWORD}@${cam_ip}:554/live"
    hls_dir="src/frontend/hls/${cam_id}"
    mkdir -p "$hls_dir" "recordings/${cam_id}"
    echo "[...] Starting HLS + recording for ${cam_id} (${cam_ip})..."
    > "ffmpeg_${cam_id}.log"
    start_watched "${cam_id}.pid" "ffmpeg_${cam_id}.log" \
        ffmpeg -rtsp_transport tcp -timeout 20000000 -use_wallclock_as_timestamps 1 \
        -fflags +discardcorrupt \
        -an \
        -i "$rtsp_url" \
        -c:v copy \
        -f hls -hls_time 1 -hls_list_size 3 -hls_flags delete_segments+split_by_time+omit_endlist \
        -hls_segment_filename "${hls_dir}/seg%03d.ts" \
        "${hls_dir}/stream.m3u8" \
        -c:v copy \
        -f segment -segment_time 300 -strftime 1 -reset_timestamps 1 \
        "recordings/${cam_id}/%Y%m%d_%H%M%S.ts"
    echo "[✓] ${cam_id} watchdog PID: $(cat ${cam_id}.pid)"
done

# 6. Start 24/7 recording (main stream HEVC, auto-restarts on disconnect)
mkdir -p recordings/lorex_127 recordings/lorex_122 recordings/ezviz_78 recordings/ezviz_120 recordings/wyze_126 recordings/wyze_105
for cam_id in lorex_127 lorex_122; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    eval cam_ip=\$$ip_var
    rtsp_url="rtsp://${LOREX_USER}:${LOREX_PASSWORD}@${cam_ip}:554/cam/realmonitor?channel=1&subtype=0&unicast=true&proto=Onvif"
    echo "[...] Starting 24/7 recording for ${cam_id} (2K)..."
    > "rec_${cam_id}.log"
    start_watched "rec_${cam_id}.pid" "rec_${cam_id}.log" \
        ffmpeg -rtsp_transport tcp -timeout 5000000 \
        -use_wallclock_as_timestamps 1 \
        -fflags +discardcorrupt \
        -i "$rtsp_url" \
        -c:v copy -c:a aac \
        -f segment -segment_time 300 -strftime 1 -reset_timestamps 1 \
        "recordings/${cam_id}/%Y%m%d_%H%M%S.ts"
    echo "[✓] ${cam_id} recording watchdog PID: $(cat rec_${cam_id}.pid)"
done

# 6b. Start Ezviz 24/7 recordings (main stream, URL paths differ by model)
for cam_id in ezviz_78 ezviz_120; do
    ip_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_IP"
    pw_var="$(echo "$cam_id" | tr '[:lower:]' '[:upper:]')_PASSWORD"
    eval cam_ip=\$$ip_var
    eval cam_pw=\$$pw_var
    case "$cam_id" in
        ezviz_78)  main_path="/H.264" ;;
        ezviz_120) main_path="/Streaming/Channels/101" ;;
    esac
    rtsp_url="rtsp://${EZVIZ_USER}:${cam_pw}@${cam_ip}${main_path}"
    echo "[...] Starting 24/7 recording for ${cam_id}..."
    > "rec_${cam_id}.log"
    start_watched "rec_${cam_id}.pid" "rec_${cam_id}.log" \
        ffmpeg -rtsp_transport tcp -timeout 5000000 \
        -use_wallclock_as_timestamps 1 \
        -fflags +discardcorrupt \
        -i "$rtsp_url" \
        -c:v copy -c:a aac \
        -f segment -segment_time 300 -strftime 1 -reset_timestamps 1 \
        "recordings/${cam_id}/%Y%m%d_%H%M%S.ts"
    echo "[✓] ${cam_id} recording watchdog PID: $(cat rec_${cam_id}.pid)"
done


# 7. Start the Backend API (survives terminal close via nohup)
echo "[...] Starting Dashboard on http://localhost:8000"
nohup python src/backend/main.py > server.log 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > server.pid

# 8. Wait up to 15s for server and first HLS segment
echo "[...] Waiting for server to start..."
for i in $(seq 1 15); do
    sleep 1
    if curl -s http://localhost:8000/ > /dev/null 2>&1; then
        echo "[✓] Dashboard is live at http://localhost:8000  (PID $SERVER_PID)"
        sleep 3
        for cam_id in lorex_127 lorex_122 ezviz_78 ezviz_120 wyze_126 wyze_105; do
            seg_count=$(ls src/frontend/hls/${cam_id}/*.ts 2>/dev/null | wc -l | tr -d ' ')
            if [ "$seg_count" -gt 0 ]; then
                echo "[✓] ${cam_id}: HLS stream active (${seg_count} segments)"
            else
                echo "[!] ${cam_id}: HLS segments not yet ready — check ffmpeg_${cam_id}.log"
            fi
        done
        echo ""
        echo "[i] Logs:  tail -f $SCRIPT_DIR/server.log"
        echo "[i] Stop:  bash $SCRIPT_DIR/stop_all.sh"
        exit 0
    fi
done

echo "[!] Server did not respond after 15 seconds. Check logs:"
tail -20 server.log
exit 1
