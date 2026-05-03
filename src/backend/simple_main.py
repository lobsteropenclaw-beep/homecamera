from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

@app.get("/api/status")
def get_status():
    return {
        "status": "simple_mode",
        "cameras": [
            {"id": "test", "name": "Simple Test Cam", "type": "test", "status": "online"}
        ]
    }

app.mount("/", StaticFiles(directory="src/frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
