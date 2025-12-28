"""Configuration management for nudge service."""

import json
from pathlib import Path

# Config file at project root
CONFIG_FILE = Path("./config.json").resolve()

DEFAULT_CONFIG = {
    "data_dir": "nudge-data",  # Directory for runtime data (logs, content, state)
    "wordlist": "wordlist.txt",  # Path to wordlist file
    "service_log": "nudge.log",
    "playback_log": "playback.log",
    "poll_interval": 5,        # Device discovery interval (seconds)
    "monitor_interval": 0.5,   # Position monitoring interval (seconds)
    "nudge_lead": 1,           # Start skip early (seconds before nudge)
    "nudge_buffer": 2,         # Extra time added after nudge (seconds)
    "web_enabled": True,       # Enable web interface
    "web_host": "0.0.0.0",     # Web server host
    "web_port": 8080,          # Web server port
}


def load_config() -> dict:
    """Load configuration from file, or return defaults."""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            # Merge with defaults for any missing keys
            return {**DEFAULT_CONFIG, **config}
    except (json.JSONDecodeError, OSError):
        return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save configuration to file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_base_dir() -> Path:
    """Get the data directory for runtime files (logs, content, state)."""
    config = load_config()
    data_dir = Path(config["data_dir"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_wordlist_file() -> Path:
    """Get the path to the wordlist file."""
    config = load_config()
    return Path(config["wordlist"]).resolve()


def get_content_dir() -> Path:
    """Get the content storage directory path."""
    return get_base_dir()


def get_service_log_path() -> Path:
    """Get the service log file path."""
    config = load_config()
    return get_base_dir() / config["service_log"]


def get_playback_log_path() -> Path:
    """Get the playback log file path."""
    config = load_config()
    return get_base_dir() / config["playback_log"]


def get_poll_interval() -> float:
    """Get the device discovery polling interval."""
    config = load_config()
    return float(config["poll_interval"])


def get_monitor_interval() -> float:
    """Get the position monitoring interval."""
    config = load_config()
    return float(config["monitor_interval"])


def get_nudge_lead() -> float:
    """Get the nudge lead time (start skip early)."""
    config = load_config()
    return float(config.get("nudge_lead", 1))


def get_nudge_buffer() -> float:
    """Get the nudge buffer time."""
    config = load_config()
    return float(config["nudge_buffer"])


def get_web_enabled() -> bool:
    """Get whether web interface is enabled."""
    config = load_config()
    return bool(config.get("web_enabled", True))


def get_web_host() -> str:
    """Get web server host."""
    config = load_config()
    return str(config.get("web_host", "0.0.0.0"))


def get_web_port() -> int:
    """Get web server port."""
    config = load_config()
    return int(config.get("web_port", 8080))
