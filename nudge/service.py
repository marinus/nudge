"""Nudge background service."""

import asyncio
import logging
import os
import signal
import sqlite3
import sys
from datetime import datetime, date

from nudge import __version__

from nudge.atv import (
    connect_to_device,
    get_artwork,
    get_paired_devices_with_status,
    get_playing_metadata,
    set_position,
)
from nudge.config import (
    get_monitor_interval,
    get_nudge_buffer,
    get_nudge_lead,
    get_playback_log_path,
    get_poll_interval,
    get_service_log_path,
    get_web_enabled,
    get_web_host,
    get_web_port,
)
from nudge.content import generate_content_id, load_content_with_nudges
from nudge.storage import get_pid_path, get_pause_path, get_state_path, get_debug_path, get_watch_history_db_path, PAUSE_TIMEOUT


def format_time(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def format_pos(seconds: float) -> str:
    """Format position as 'Xs (M:SS)' for logging."""
    return f"{int(seconds)}s ({format_time(seconds)})"


def is_debug() -> bool:
    """Check if debug mode is enabled."""
    return get_debug_path().exists()


def set_debug(enabled: bool):
    """Enable or disable debug mode."""
    debug_path = get_debug_path()
    if enabled:
        debug_path.write_text("1")
    elif debug_path.exists():
        debug_path.unlink()


def setup_logging() -> logging.Logger:
    """Setup service logging."""
    logger = logging.getLogger("nudge")

    # Set level based on debug mode
    debug_mode = is_debug()
    logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)

    # File handler
    file_handler = logging.FileHandler(get_service_log_path())
    file_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(file_handler)

    # Console handler (for foreground mode)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(console_handler)

    if debug_mode:
        logger.info("Debug mode enabled")

    return logger


def write_pid():
    """Write current PID to file."""
    pid_path = get_pid_path()
    pid_path.write_text(str(os.getpid()))


def remove_pid():
    """Remove PID file."""
    pid_path = get_pid_path()
    if pid_path.exists():
        pid_path.unlink()


def read_pid() -> int | None:
    """Read PID from file."""
    pid_path = get_pid_path()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def is_running() -> bool:
    """Check if service is running."""
    pid = read_pid()
    if pid is None:
        return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        # Process not running, clean up stale PID file
        remove_pid()
        return False


def get_state() -> str:
    """Get current nudge state (on/off)."""
    state_path = get_state_path()
    if not state_path.exists():
        return "on"  # Default to on
    return state_path.read_text().strip()


def set_state(state: str):
    """Set nudge state."""
    state_path = get_state_path()
    state_path.write_text(state)


def pause_service(timeout: int = PAUSE_TIMEOUT) -> bool:
    """
    Pause the service for a specified duration.

    Args:
        timeout: Pause duration in seconds (default: 10 minutes)

    Returns:
        True if pause was set, False otherwise.
    """
    pause_path = get_pause_path()
    expiry = datetime.now().timestamp() + timeout
    pause_path.write_text(str(expiry))
    return True


def unpause_service():
    """Remove the pause file."""
    pause_path = get_pause_path()
    if pause_path.exists():
        pause_path.unlink()


def is_paused() -> bool:
    """
    Check if service is paused.

    Returns:
        True if paused and not expired, False otherwise.
    """
    pause_path = get_pause_path()
    if not pause_path.exists():
        return False

    try:
        expiry = float(pause_path.read_text().strip())
        if datetime.now().timestamp() > expiry:
            # Pause expired, clean up
            pause_path.unlink()
            return False
        return True
    except (ValueError, OSError):
        return False


def get_pause_remaining() -> int | None:
    """Get remaining pause time in seconds, or None if not paused."""
    pause_path = get_pause_path()
    if not pause_path.exists():
        return None

    try:
        expiry = float(pause_path.read_text().strip())
        remaining = expiry - datetime.now().timestamp()
        if remaining <= 0:
            pause_path.unlink()
            return None
        return int(remaining)
    except (ValueError, OSError):
        return None


def log_playback(device_name: str, title: str, app: str, matched: bool):
    """Log playback start to playback log."""
    log_path = get_playback_log_path()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    match_status = "matched" if matched else "unmatched"

    with open(log_path, "a") as f:
        f.write(f"{timestamp} | {device_name} | {app} | {title} | {match_status}\n")


class NudgeService:
    """Background service for monitoring AppleTVs."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.running = False
        self.device_states: dict[str, dict] = {}  # Track state per device
        self.active_monitors: dict[str, asyncio.Task] = {}  # Active monitoring tasks
        self.web_server = None
        self.last_devices: list[dict] = []  # Cache for web broadcast
        self.artwork_cache: dict[str, bytes] = {}  # Artwork cache per device
        self._init_watch_history_db()

    def _init_watch_history_db(self):
        """Initialize the watch history SQLite database."""
        db_path = get_watch_history_db_path()
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    title TEXT NOT NULL,
                    UNIQUE(date, title)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON watch_history(date)")

    def record_watched(self, title: str):
        """Record a title as watched today (deduplicated)."""
        today = date.today().isoformat()
        time_str = datetime.now().strftime("%H:%M")

        db_path = get_watch_history_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO watch_history (date, time, title) VALUES (?, ?, ?)",
                    (today, time_str, title)
                )
        except sqlite3.Error:
            pass

    def get_watch_history(self, date_str: str) -> list:
        """Get watch history for a specific date."""
        db_path = get_watch_history_db_path()
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.execute(
                    "SELECT time, title FROM watch_history WHERE date = ? ORDER BY time",
                    (date_str,)
                )
                return [{"time": row[0], "title": row[1]} for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    async def run(self):
        """Main service loop."""
        write_pid()
        self.running = True

        # Enable nudge by default when service starts
        set_state("on")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop)

        # Start web server if enabled
        if get_web_enabled():
            from nudge.web import WebServer
            self.web_server = WebServer(get_web_host(), get_web_port(), service=self)
            self.web_server.start()

        self.logger.info(f"Service started (v{__version__})")

        was_paused = False
        poll_interval = get_poll_interval()
        try:
            while self.running:
                if is_paused():
                    if not was_paused:
                        self.logger.info("Service paused")
                        # Cancel active monitors
                        await self.cancel_all_monitors()
                        was_paused = True
                    self.broadcast_state()
                    await asyncio.sleep(poll_interval)
                    continue

                if was_paused:
                    self.logger.info("Service resumed")
                    was_paused = False

                await self.poll_devices()
                self.broadcast_state()
                await asyncio.sleep(poll_interval)
        except Exception as e:
            self.logger.error(f"Service error: {e}")
        finally:
            await self.cancel_all_monitors()
            remove_pid()
            self.logger.info("Service stopped")

    def stop(self):
        """Stop the service."""
        self.logger.info("Stop signal received")
        self.running = False

    def broadcast_state(self):
        """Schedule async broadcast to web clients (non-blocking)."""
        if not self.web_server:
            return
        asyncio.create_task(self._broadcast_state_async())

    async def _broadcast_state_async(self):
        """Broadcast current state to web clients."""
        try:
            # Build device list with match info from device states
            devices = []
            for device in self.last_devices:
                identifier = device["identifier"]
                state = self.device_states.get(identifier, {})

                # For monitored devices, use title/app from device_states (last_devices has None)
                title = device.get("title") or state.get("title")
                app = device.get("app") or state.get("app")

                device_info = {
                    "identifier": identifier,
                    "name": device["name"],
                    "status": device["status"],
                    "title": title,
                    "app": app,
                    "matched": state.get("matched", False),
                    "nudge_count": state.get("nudge_count", 0),
                    "content_id": state.get("content_id"),
                    "monitoring": identifier in self.active_monitors,
                    "has_artwork": identifier in self.artwork_cache,
                }

                devices.append(device_info)

            # Sort devices by name
            devices.sort(key=lambda d: d["name"].lower())

            # Get today's watch history
            today = date.today().isoformat()
            watch_history = self.get_watch_history(today)

            # Run blocking WebSocket broadcast in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.web_server.broadcast,
                get_state(),
                get_pause_remaining(),
                devices,
                watch_history,
            )
        except Exception:
            pass  # Don't let broadcast errors affect service

    async def cancel_all_monitors(self):
        """Cancel all active monitoring tasks."""
        for identifier, task in list(self.active_monitors.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.active_monitors.clear()

    async def poll_devices(self):
        """Poll all paired devices for playback status."""
        try:
            # Skip connecting to devices we're already monitoring
            skip = set(self.active_monitors.keys())
            devices = await get_paired_devices_with_status(skip_identifiers=skip)
        except Exception as e:
            self.logger.error(f"Failed to get devices: {e}")
            return

        self.last_devices = devices

        for device in devices:
            identifier = device["identifier"]
            name = device["name"]
            status = device["status"]
            title = device.get("title")
            app = device.get("app")

            # Skip state updates for actively monitored devices
            if status == "Monitoring":
                continue

            # Get previous state
            prev = self.device_states.get(identifier, {})
            prev_status = prev.get("status")
            prev_title = prev.get("title")

            # Detect playback start (transition to Playing with a title)
            if status == "Playing" and title:
                if prev_status != "Playing" or prev_title != title:
                    # Initialize device state before matching
                    self.device_states[identifier] = {
                        "status": status,
                        "title": title,
                        "app": app,
                        "matched": False,
                        "nudge_count": 0,
                        "content_id": None,
                    }
                    # New playback started - try to match content
                    await self.on_playback_start(identifier, name, title, app)
                    continue  # Skip the state update below since we already set it

            elif status == "Idle" and prev_status == "Playing":
                self.logger.info(f"{name} | Stopped")
                # Cancel monitor if running
                if identifier in self.active_monitors:
                    self.active_monitors[identifier].cancel()
                    del self.active_monitors[identifier]

            # Update state (preserve match info if present)
            prev_state = self.device_states.get(identifier, {})
            self.device_states[identifier] = {
                "status": status,
                "title": title,
                "app": app,
                "matched": prev_state.get("matched", False),
                "nudge_count": prev_state.get("nudge_count", 0),
                "content_id": prev_state.get("content_id"),
            }

    async def on_playback_start(self, identifier: str, name: str, title: str, app: str):
        """Handle playback start - try to match and start monitoring."""
        # Clear stale artwork when new content starts
        if identifier in self.artwork_cache:
            del self.artwork_cache[identifier]

        # Connect to get duration
        atv = await connect_to_device(identifier)
        if not atv:
            self.logger.warning(f"{name} | Could not connect")
            return

        try:
            metadata = await get_playing_metadata(atv)
            if not metadata:
                self.logger.info(f"{name} | Playing | {title}")
                self.record_watched(title)
                log_playback(name, title, app, matched=False)
                return

            duration = metadata.get("duration", 0)
            position = metadata.get("position", 0)

            # Try to match content
            store_id = metadata.get("store_id")
            content_id = generate_content_id(title, duration, app, store_id)
            try:
                content, nudges = load_content_with_nudges(content_id)
            except Exception as e:
                self.logger.error(f"{name} | Failed to load content {content_id}: {e}")
                content, nudges = None, []

            # Log content detection and record to watch history
            self.logger.info(f"{name} | Playing | {title}")
            self.record_watched(title)
            id_source = "store_id" if store_id else "hash"
            self.logger.debug(f"{name} | Content ID: {content_id} ({id_source})")
            self.logger.debug(f"{name} | Duration: {format_pos(duration)} | App: {app or 'Unknown'}")

            if content and nudges:
                # Count nudges by type
                type_counts = {}
                for n in nudges:
                    t = n.get("type", "other")
                    type_counts[t] = type_counts.get(t, 0) + 1
                type_summary = ", ".join(f"{c} {t}" for t, c in sorted(type_counts.items()))

                self.logger.info(f"{name} | MATCHED | {len(nudges)} nudges ({type_summary})")
                log_playback(name, title, app, matched=True)

                # Update match info in device state
                if identifier in self.device_states:
                    self.device_states[identifier]["matched"] = True
                    self.device_states[identifier]["nudge_count"] = len(nudges)
                    self.device_states[identifier]["content_id"] = content_id

                # Fetch artwork
                try:
                    artwork = await get_artwork(atv)
                    if artwork:
                        self.artwork_cache[identifier] = artwork
                except Exception:
                    pass

                # Start monitoring task
                if identifier in self.active_monitors:
                    self.active_monitors[identifier].cancel()

                task = asyncio.create_task(
                    self.monitor_device(identifier, name, atv, nudges)
                )
                self.active_monitors[identifier] = task
                return  # Don't close atv, monitor will use it
            else:
                self.logger.info(f"{name} | No match - content not configured")
                log_playback(name, title, app, matched=False)

                # Clear match info in device state
                if identifier in self.device_states:
                    self.device_states[identifier]["matched"] = False
                    self.device_states[identifier]["nudge_count"] = 0
                    self.device_states[identifier]["content_id"] = None

                # Fetch artwork for unmatched content too
                try:
                    artwork = await get_artwork(atv)
                    if artwork:
                        self.artwork_cache[identifier] = artwork
                except Exception:
                    pass

        finally:
            # Only close if not monitoring
            if identifier not in self.active_monitors:
                atv.close()

    async def monitor_device(self, identifier: str, name: str, atv, nudges: list[dict]):
        """Monitor playback position and enforce nudges."""
        enforced_nudges: set[float] = set()  # Track which nudges we've enforced
        nudge_state = get_state()
        monitor_interval = get_monitor_interval()
        nudge_lead = get_nudge_lead()
        nudge_buffer = get_nudge_buffer()
        last_position = -1  # Track for missed nudge detection

        # Sort nudges by time for future nudge detection
        sorted_nudges = sorted(nudges, key=lambda n: n["time"])

        # Get initial position for logging
        try:
            initial_meta = await get_playing_metadata(atv)
            if initial_meta:
                initial_pos = initial_meta.get("position", 0)
                self.logger.debug(f"{name} | pos={format_pos(initial_pos)} | Monitoring started")
        except Exception:
            pass

        try:
            while self.running and not is_paused():
                # Check nudge state
                current_state = get_state()
                if current_state != nudge_state:
                    nudge_state = current_state
                    if nudge_state == "on":
                        self.logger.info(f"{name} | Nudge enabled")
                    else:
                        self.logger.info(f"{name} | Nudge disabled")

                # Get current position
                try:
                    metadata = await get_playing_metadata(atv)
                except Exception:
                    metadata = None

                if not metadata:
                    self.logger.info(f"{name} | pos={format_pos(last_position) if last_position >= 0 else '?'} | Stopped")
                    break

                position = metadata.get("position", 0)

                # Detect seek backward - clear enforced nudges that are now ahead
                if last_position >= 0 and position < last_position - 10:
                    # Seeked backward by more than 10 seconds
                    enforced_nudges = {t for t in enforced_nudges if t < position}

                # Debug log current position (only when position changes)
                if position != last_position:
                    next_nudge = None
                    for nudge in sorted_nudges:
                        if nudge["time"] > position:
                            next_nudge = nudge
                            break

                    if next_nudge:
                        time_until = next_nudge["time"] - position
                        self.logger.debug(
                            f"{name} | pos={format_pos(position)} | "
                            f"next={format_pos(next_nudge['time'])} in {time_until:.1f}s"
                        )
                    else:
                        self.logger.debug(f"{name} | pos={format_pos(position)}")

                # Check for missed nudges (position jumped past a nudge window)
                if last_position >= 0 and nudge_state == "on" and position > last_position:
                    for nudge in sorted_nudges:
                        nudge_time = nudge["time"]
                        nudge_end = nudge_time + nudge["duration"]
                        # Missed if: was before window, now after window, not enforced
                        if (last_position < nudge_time - nudge_lead and
                            position >= nudge_end and
                            nudge_time not in enforced_nudges):
                            self.logger.debug(
                                f"{name} | pos={format_pos(position)} | "
                                f"MISSED {format_pos(nudge_time)} - past window"
                            )
                            enforced_nudges.add(nudge_time)  # Mark as handled

                last_position = position

                # Check nudges if enabled
                if nudge_state == "on":
                    for i, nudge in enumerate(sorted_nudges):
                        nudge_time = nudge["time"]
                        nudge_duration = nudge["duration"]
                        nudge_type = nudge.get("type", "language")

                        # Skip if already enforced
                        if nudge_time in enforced_nudges:
                            continue

                        # Check if we're in the nudge window (with lead time)
                        if nudge_time - nudge_lead <= position < nudge_time + nudge_duration:
                            # Calculate skip target
                            skip_to = nudge_time + nudge_duration + nudge_buffer

                            # Log window entry
                            window_start = nudge_time - nudge_lead
                            window_end = nudge_time + nudge_duration
                            self.logger.debug(
                                f"{name} | pos={format_pos(position)} | "
                                f"window={format_pos(window_start)}-{format_pos(window_end)} | ENTERING"
                            )

                            # Check for future nudges we might skip over
                            for future_nudge in sorted_nudges[i + 1:]:
                                future_time = future_nudge["time"]
                                if future_time <= skip_to and future_time not in enforced_nudges:
                                    # Would skip past an unenforced nudge - limit skip
                                    skip_to = future_time - 1
                                    self.logger.info(
                                        f"{name} | pos={format_pos(position)} | "
                                        f"NUDGE {nudge_type} -> {format_pos(skip_to)} (limited)"
                                    )
                                    break
                            else:
                                self.logger.info(
                                    f"{name} | pos={format_pos(position)} | "
                                    f"NUDGE {nudge_type} -> {format_pos(skip_to)}"
                                )

                            await set_position(atv, skip_to)
                            enforced_nudges.add(nudge_time)

                await asyncio.sleep(monitor_interval)

        except asyncio.CancelledError:
            self.logger.info(f"{name} | pos={format_pos(last_position) if last_position >= 0 else '?'} | Monitor cancelled")
        except Exception as e:
            self.logger.error(f"{name} | Monitor error: {e}")
        finally:
            atv.close()
            if identifier in self.active_monitors:
                del self.active_monitors[identifier]


def run_service():
    """Entry point for running the service."""
    logger = setup_logging()
    service = NudgeService(logger)
    asyncio.run(service.run())


def start_daemon():
    """Start service as a background daemon."""
    if is_running():
        print("Service is already running.")
        return False

    # Fork to background
    pid = os.fork()
    if pid > 0:
        # Parent process
        print(f"Service started (PID: {pid})")
        return True

    # Child process - become session leader
    os.setsid()

    # Fork again to prevent zombie
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    # Redirect standard file descriptors to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)

    # Run service
    run_service()
    return True


def stop_daemon() -> bool:
    """Stop the running service."""
    pid = read_pid()
    if pid is None:
        print("Service is not running.")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Service stopped (PID: {pid})")
        return True
    except OSError as e:
        print(f"Failed to stop service: {e}")
        remove_pid()
        return False


def get_status() -> dict:
    """Get service status."""
    running = is_running()
    state = get_state()
    pid = read_pid() if running else None

    return {
        "running": running,
        "pid": pid,
        "state": state,
    }
