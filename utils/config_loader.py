import json
from pathlib import Path

def load_config():
    config_path = Path(__file__).parent.parent / "data" / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
