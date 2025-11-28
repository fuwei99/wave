import uvicorn
import os
import sys

# Add the parent directory to sys.path to allow importing app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    from app import config
    
    print(f"Starting Wavespeed2API server on port {config.PORT}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT, reload=True)