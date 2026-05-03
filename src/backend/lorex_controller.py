from onvif import ONVIFCamera
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LorexController:
    def __init__(self, ip, user, password, port=80):
        self.ip = ip
        self.user = user
        self.password = password
        self.port = port
        self.camera = None
        self.ptz = None
        self.media = None
        self.profile = None

    def connect(self):
        try:
            # We use the standard ONVIF port 80 (or 8000/8899 depending on model)
            self.camera = ONVIFCamera(self.ip, self.port, self.user, self.password)
            self.media = self.camera.create_media_service()
            self.ptz = self.camera.create_ptz_service()
            self.profile = self.media.GetProfiles()[0]
            logging.info(f"Successfully connected to Lorex NVR at {self.ip}")
            return True
        except Exception as e:
            logging.error(f"Failed to connect to Lorex NVR: {e}")
            return False

    def relative_move(self, dx, dy, zoom=0, duration=1.5):
        """Pan/tilt by running ContinuousMove for `duration` seconds then stopping."""
        if not self.ptz:
            logging.error("Not connected to camera PTZ service.")
            return False
        import time
        try:
            req = self.ptz.create_type('ContinuousMove')
            req.ProfileToken = self.profile.token
            req.Velocity = {'PanTilt': {'x': dx, 'y': dy}, 'Zoom': {'x': zoom}}
            self.ptz.ContinuousMove(req)
            time.sleep(duration)
            self.ptz.Stop({'ProfileToken': self.profile.token})
            logging.info(f"ContinuousMove on {self.ip}: dx={dx}, dy={dy} for {duration}s")
            return True
        except Exception as e:
            logging.error(f"ContinuousMove error on {self.ip}: {e}")
            return False

    def move(self, x, y, zoom=0, duration=1):
        """Move at continuous velocity for duration seconds then stop."""
        if not self.ptz:
            logging.error("Not connected to camera PTZ service.")
            return

        try:
            request = self.ptz.create_type('ContinuousMove')
            request.ProfileToken = self.profile.token
            request.Velocity = {
                'PanTilt': {'x': x, 'y': y},
                'Zoom': {'x': zoom}
            }
            self.ptz.ContinuousMove(request)
            import time
            time.sleep(duration)
            self.ptz.Stop({'ProfileToken': self.profile.token})
            logging.info(f"Moved Lorex camera: x={x}, y={y}")
        except Exception as e:
            logging.error(f"Error moving camera: {e}")

if __name__ == "__main__":
    # Example usage (placeholders)
    # controller = LorexController("192.168.x.x", "admin", "your_password")
    # if controller.connect():
    #     controller.move(0, 0.5) # Tilt Up
    print("Lorex controller initialized.")
