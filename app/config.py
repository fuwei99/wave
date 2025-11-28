import os
import json
from dotenv import load_dotenv

load_dotenv()

# Config file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

class ConfigLoader:
    def __init__(self):
        self._cache = {}
        self._last_mtime = 0
        self.config_file = CONFIG_FILE
        self._reload_if_needed()

    def _reload_if_needed(self):
        try:
            if not os.path.exists(self.config_file):
                return

            current_mtime = os.stat(self.config_file).st_mtime
            if current_mtime > self._last_mtime:
                # File changed, reload
                print(f"DEBUG: Reloading configuration from {self.config_file}")
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                self._last_mtime = current_mtime
        except Exception as e:
            print(f"ERROR: Failed to reload config: {e}")

    def get(self, key, default=None):
        self._reload_if_needed()
        # Priority: Env Var > Config File > Default
        env_val = os.getenv(key)
        if env_val is not None:
            return env_val
            
        val = self._cache.get(key)
        if val is not None:
            return val
        return default

    def get_bool(self, key, default=False):
        val = self.get(key)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    def get_int(self, key, default=0):
        val = self.get(key)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

_loader = ConfigLoader()

# Wavespeed Configuration
WAVESPEED_API_URL = "https://wavespeed.ai/center/default/api/v1/model_run/wavespeed-ai/z-image/turbo"
WAVESPEED_REFERER = "https://wavespeed.ai/models/wavespeed-ai/z-image/turbo"

def __getattr__(name):
    # Dynamic property access
    if name == "WAVESPEED_COOKIE":
        raw_cookies = _loader.get("WAVESPEED_COOKIE", "")
        if isinstance(raw_cookies, str):
            return [c.strip() for c in raw_cookies.split(',') if c.strip()]
        return []
    
    if name == "R2_ENABLED":
        return _loader.get_bool("R2_ENABLED", False)
        
    if name == "PORT":
        return _loader.get_int("PORT", 8001)
        
    if name == "API_KEY":
        return _loader.get("API_KEY", "sk-123456")

    # R2 Configs
    if name in ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_URL"]:
        return _loader.get(name, "")

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")