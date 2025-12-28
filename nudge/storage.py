"""Storage management for nudge configuration and credentials."""

from nudge.config import get_base_dir, get_wordlist_file

# Pause timeout in seconds (10 minutes)
PAUSE_TIMEOUT = 600


def get_credentials_path():
    """Get the path to the credentials file."""
    return get_base_dir() / "credentials.conf"


def get_wordlist_path():
    """Get the path to the wordlist file."""
    return get_wordlist_file()


def get_pid_path():
    """Get the path to the PID file."""
    return get_base_dir() / "nudge.pid"


def get_state_path():
    """Get the path to the state file."""
    return get_base_dir() / "nudge.state"


def get_pause_path():
    """Get the path to the pause file."""
    return get_base_dir() / "nudge.pause"


def get_debug_path():
    """Get the path to the debug flag file."""
    return get_base_dir() / "nudge.debug"
