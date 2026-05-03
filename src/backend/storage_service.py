import os
import subprocess
import time
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class StorageService:
    def __init__(self, nas_path, retention_days=7):
        self.nas_path = nas_path
        self.retention_days = retention_days
        
        if not os.path.exists(self.nas_path):
            os.makedirs(self.nas_path)
            logging.info(f"Created storage directory: {self.nas_path}")

    def record_stream(self, stream_url, camera_name, segment_time="00:15:00"):
        """
        Starts a background ffmpeg process to record the stream in segments.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_pattern = os.path.join(self.nas_path, f"{camera_name}_{timestamp}_%03d.mp4")
        
        command = [
            "ffmpeg",
            "-i", stream_url,
            "-c", "copy",
            "-map", "0",
            "-f", "segment",
            "-segment_time", segment_time,
            "-reset_timestamps", "1",
            output_pattern
        ]
        
        logging.info(f"Starting recording for {camera_name} to {output_pattern}")
        # Run in background
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def cleanup_old_files(self):
        """
        Deletes files older than retention_days.
        """
        now = time.time()
        for f in os.listdir(self.nas_path):
            file_path = os.path.join(self.nas_path, f)
            if os.stat(file_path).st_mtime < now - (self.retention_days * 86400):
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    logging.info(f"Deleted old recording: {f}")

if __name__ == "__main__":
    # Test logic
    storage = StorageService("./recordings")
    storage.cleanup_old_files()
    print("Storage service initialized.")
