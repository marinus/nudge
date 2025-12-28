"""Nudge CLI interface."""

import argparse

from nudge import __version__
import asyncio
from pathlib import Path
import sys

from nudge.atv import (
    connect_to_device,
    get_paired_devices_with_status_sync,
    get_playing_metadata,
    pair_device_sync,
    scan_devices_sync,
    set_position,
)
from nudge.content import (
    delete_content,
    generate_content_id,
    get_content_directory,
    get_content_path,
    list_content,
    load_content,
    load_content_with_nudges,
    save_content,
)
from nudge.service import (
    get_pause_remaining,
    get_status,
    get_state,
    is_paused,
    is_running,
    pause_service,
    run_service,
    set_debug,
    set_state,
    start_daemon,
    stop_daemon,
    unpause_service,
)
from nudge.config import get_playback_log_path, get_service_log_path, get_web_enabled, get_web_host, get_web_port
from nudge.storage import get_wordlist_path
from nudge.subtitles import format_timestamp, load_wordlist, parse_srt


def cmd_scan(args):
    """Handle the scan command."""
    print("Scanning for AppleTV devices...")

    devices = scan_devices_sync(timeout=args.timeout, check_paired=True)

    if not devices:
        print("No AppleTV devices found.")
        return 1

    print(f"\nFound {len(devices)} device(s):\n")
    print(f"{'Name':<20} {'Address':<15} {'Identifier':<20} {'Paired'}")
    print("-" * 65)

    for device in devices:
        paired = "Yes" if device["paired"] else "No"
        print(f"{device['name']:<20} {device['address']:<15} {device['identifier']:<20} {paired}")

    return 0


def cmd_pair(args):
    """Handle the pair command."""
    if args.device:
        # Pair with specific device
        identifiers = [args.device]
    else:
        # Discover and pair with all devices
        print("Scanning for AppleTV devices...")
        devices = scan_devices_sync(timeout=args.timeout)

        if not devices:
            print("No AppleTV devices found.")
            return 1

        print(f"Found {len(devices)} device(s):\n")
        for i, device in enumerate(devices, 1):
            print(f"  {i}. {device['name']} ({device['identifier']})")

        print()
        selection = input("Enter device number to pair (or 'all' for all devices): ").strip()

        if selection.lower() == "all":
            identifiers = [d["identifier"] for d in devices]
        elif selection.isdigit() and 1 <= int(selection) <= len(devices):
            identifiers = [devices[int(selection) - 1]["identifier"]]
        else:
            print("Invalid selection.")
            return 1

    def pin_callback(protocol):
        """Prompt user for PIN."""
        return input(f"    Enter PIN shown on AppleTV for {protocol.name}: ").strip()

    def status_callback(message):
        """Print status message."""
        print(message)

    success = True
    for identifier in identifiers:
        print(f"\nPairing with {identifier}...")
        result = pair_device_sync(identifier, pin_callback, status_callback, args.timeout)
        if not result:
            success = False

    return 0 if success else 1


def cmd_test(args):
    """Handle the test command."""
    srt_path = Path(args.srt_file)

    if not srt_path.exists():
        print(f"Error: File not found: {srt_path}")
        return 1

    # Check for wordlist
    wordlist_path = get_wordlist_path()
    if not wordlist_path.exists():
        print(f"Error: Wordlist not found at {wordlist_path}")
        print("Create a wordlist file with one word per line.")
        return 1

    # Load wordlist and parse SRT
    wordlist = load_wordlist(wordlist_path)
    if not wordlist:
        print("Error: Wordlist is empty.")
        return 1

    print(f"Loaded {len(wordlist)} words from wordlist.")

    offset = args.offset if args.offset is not None else 0.0
    nudges = parse_srt(srt_path, wordlist, offset=offset)
    print(f"Found {len(nudges)} nudge points in {srt_path.name}")

    if not nudges:
        print("No matches found. Check your wordlist or subtitle file.")
        return 1

    # Show nudge preview
    print(f"\n{'#':<4} {'Time':<10} {'Duration':<10} {'Text'}")
    print("-" * 70)
    for i, nudge in enumerate(nudges, 1):
        time_str = format_timestamp(nudge["time"])
        print(f"{i:<4} {time_str:<10} {nudge['duration']:<10.1f} {nudge['text'][:40]}")

    # Get device - skip scan if identifier provided directly
    def is_mac_address(s):
        return s and ":" in s and len(s) == 17

    if args.device and is_mac_address(args.device):
        # Direct connection without scan
        device = {"identifier": args.device, "name": args.device}
        print(f"\nConnecting to {args.device}...")
    else:
        # Scan for devices
        print("\nScanning for paired AppleTVs...")
        devices = get_paired_devices_with_status_sync()

        if not devices:
            print("Error: No paired AppleTV devices found.")
            print("Run 'nudge pair' first.")
            return 1

        # Select device
        if args.device:
            # Find device by name
            device = None
            for d in devices:
                if d["identifier"] == args.device or d["name"] == args.device:
                    device = d
                    break
            if not device:
                print(f"Error: Device '{args.device}' not found.")
                return 1
        elif len(devices) == 1:
            device = devices[0]
        else:
            print(f"\nMultiple paired AppleTVs found:\n")
            for i, d in enumerate(devices, 1):
                status = d["status"]
                if d["title"]:
                    status = f"{status}: {d['title']} ({d['app']})"
                print(f"  {i}. {d['name']} - {status}")

            print()
            selection = input("Select device number: ").strip()
            if not selection.isdigit() or not (1 <= int(selection) <= len(devices)):
                print("Invalid selection.")
                return 1
            device = devices[int(selection) - 1]

        print(f"\nConnecting to {device['name']}...")

    # Pause service if running
    service_was_running = is_running()
    if service_was_running:
        print("Pausing service during test...")
        pause_service()

    # Run async test
    try:
        return asyncio.run(_run_test(device["identifier"], nudges, srt_path, args.offset, args.save, args.force))
    finally:
        # Unpause service
        if service_was_running:
            print("Resuming service...")
            unpause_service()


async def _run_test(identifier: str, nudges: list[dict], srt_path: Path, offset: float | None, save: bool, force: bool) -> int:
    """Run the interactive test session or save directly."""
    atv = await connect_to_device(identifier)
    if not atv:
        print("Error: Could not connect to device.")
        return 1

    try:
        # Get current playback info
        metadata = await get_playing_metadata(atv)
        if not metadata:
            print("Error: Nothing is playing on this device.")
            print("Start playing the content first, then run test.")
            return 1

        print(f"\nContent detected:")
        print(f"  Title:    {metadata['title']}")
        print(f"  App:      {metadata['app']}")
        print(f"  Duration: {format_timestamp(metadata['duration'])}")
        print(f"  Position: {format_timestamp(metadata['position'])}")

        content_id = generate_content_id(
            metadata["title"],
            metadata["duration"],
            metadata["app"],
            metadata.get("store_id"),
        )
        print(f"  ID:       {content_id}")

        # Display offset if explicitly specified
        if offset is not None and offset != 0:
            print(f"\n  Offset:   {offset:+.1f}s applied")

        if save:
            # Check if content already exists
            existing = load_content(content_id)
            if existing and not force:
                print(f"\nError: Content already exists!")
                print(f"  ID:    {content_id}")
                print(f"  Title: {existing.get('title')}")
                print(f"\nUse --force to overwrite.")
                return 1

            # Save content metadata and SRT file
            action = "Overwriting" if existing else "Saving"
            print(f"\n{action} content...")
            json_path = save_content(
                content_id=content_id,
                title=metadata["title"],
                app=metadata["app"],
                duration=metadata["duration"],
                srt_offset=offset,
                srt_source=srt_path,
            )
            # Load to get computed nudge count
            _, all_nudges = load_content_with_nudges(content_id)
            print(f"  Saved to: {json_path}")
            print(f"  Content ID: {content_id}")
            print(f"  Language nudges: {len(all_nudges)}")
        else:
            # Interactive test
            print("\n" + "=" * 50)
            print("TEST MODE")
            print("=" * 50)
            print("Commands: ENTER=next, r=replay, q=quit\n")

            input("Press ENTER to start test...")

            for i, nudge in enumerate(nudges, 1):
                time_str = format_timestamp(nudge["time"])
                # Jump 1 second before nudge to hear context
                jump_time = max(0, nudge["time"] - 1)
                print(f"\n[{i}/{len(nudges)}] Nudge at {time_str}")
                print(f"  Expected: \"{nudge['text'][:50]}\"")

                await set_position(atv, jump_time)

                while True:
                    cmd = input("> ").strip().lower()
                    if cmd == "":
                        break
                    elif cmd == "r":
                        print(f"  Replaying...")
                        await set_position(atv, jump_time)
                    elif cmd == "q":
                        print("Test cancelled.")
                        return 0

            print("\n" + "=" * 50)
            print("TEST COMPLETE")
            print("=" * 50)
            print("\nTo save this content, run again with --save")

        return 0

    finally:
        await asyncio.sleep(0.5)  # Allow pending requests to complete
        atv.close()


def cmd_start(args):
    """Handle the start command."""
    # Set debug mode before starting
    set_debug(args.debug)

    if args.foreground:
        mode = "foreground" + (" (debug)" if args.debug else "")
        print(f"Starting service in {mode} (Ctrl+C to stop)...")
        run_service()
    else:
        if args.debug:
            print("Starting service with debug logging...")
        start_daemon()
    return 0


def cmd_stop(args):
    """Handle the stop command."""
    stop_daemon()
    return 0


def cmd_status(args):
    """Handle the status command."""
    status = get_status()

    if status["running"]:
        print(f"Service:  running (PID: {status['pid']})")
    else:
        print("Service:  stopped")

    # Check pause state
    remaining = get_pause_remaining()
    if remaining:
        mins, secs = divmod(remaining, 60)
        print(f"Paused:   yes ({mins}m {secs}s remaining)")
    else:
        print("Paused:   no")

    print(f"Nudge:    {status['state']}")

    # Show web URL if enabled and running
    if status["running"] and get_web_enabled():
        host = get_web_host()
        port = get_web_port()
        display_host = "localhost" if host == "0.0.0.0" else host
        print(f"Web:      http://{display_host}:{port}")

    print(f"Logs:     {get_service_log_path()}")
    print(f"Playback: {get_playback_log_path()}")

    # Show Apple TV status
    print("\nApple TVs:")
    try:
        devices = get_paired_devices_with_status_sync(timeout=3)
        if not devices:
            print("  No paired devices found")
        else:
            for device in devices:
                name = device["name"]
                device_status = device["status"]
                if device_status == "Playing" and device.get("title"):
                    title = device["title"]
                    app = device.get("app") or "Unknown"
                    print(f"  {name}: Playing \"{title}\" ({app})")
                elif device_status == "Paused" and device.get("title"):
                    title = device["title"]
                    app = device.get("app") or "Unknown"
                    print(f"  {name}: Paused \"{title}\" ({app})")
                else:
                    print(f"  {name}: {device_status}")
    except Exception as e:
        print(f"  Error getting device status: {e}")

    return 0


def cmd_on(args):
    """Handle the on command."""
    set_state("on")
    if is_paused():
        unpause_service()
        print("Nudge enforcement enabled (unpaused).")
    else:
        print("Nudge enforcement enabled.")
    return 0


def cmd_off(args):
    """Handle the off command."""
    set_state("off")
    print("Nudge enforcement disabled.")
    return 0


def cmd_list(args):
    """Handle the list command."""
    content = list_content()

    if not content:
        print("No content stored.")
        return 0

    print(f"\n{'Title':<35} {'Nudges':<8} {'File'}")
    print("-" * 80)

    for item in sorted(content, key=lambda x: x["title"]):
        filename = get_content_path(item['id']).name
        title = item['title'][:33] + ".." if len(item['title']) > 35 else item['title']
        print(f"{title:<35} {item['nudges']:<8} {filename}")

    print(f"\nTotal: {len(content)} content item(s)")
    print(f"Location: {get_content_path('').parent}")
    return 0


def cmd_remove(args):
    """Handle the remove command."""
    content_id = args.content_id

    if delete_content(content_id):
        print(f"Removed content: {content_id}")
        return 0
    else:
        print(f"Content not found: {content_id}")
        return 1


def format_timestamp(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    else:
        return f"{mins}:{secs:02d}"


def find_content_id(query: str) -> str | None:
    """
    Find content ID from query string.

    Supports:
    - Exact content ID (e.g., "414711948")
    - Filename (e.g., "414711948.json")
    - Partial title match (e.g., "Tron")
    """
    # Try as exact ID first
    if load_content(query):
        return query

    # Try removing .json extension
    if query.endswith(".json"):
        content_id = query[:-5]
        if load_content(content_id):
            return content_id

    # Try partial title match
    query_lower = query.lower()
    for item in list_content():
        if query_lower in item["title"].lower():
            return item["id"]

    return None


def cmd_nudges(args):
    """Handle the nudges command - show nudges for content."""
    query = args.content

    content_id = find_content_id(query)
    if not content_id:
        print(f"Content not found: {query}")
        print("\nUse 'nudge list' to see available content.")
        return 1

    content, nudges = load_content_with_nudges(content_id)
    if not content:
        print(f"Failed to load content: {content_id}")
        return 1

    # Header
    duration_str = format_timestamp(content.get("duration", 0))
    print(f"\nNudges for: {content['title']} ({content_id})")
    print(f"Duration: {duration_str} ({content.get('duration', 0)}s)")
    print()

    if not nudges:
        print("No nudges configured.")
        return 0

    # Table header
    print(f"  {'Time':<18} {'Duration':>8}  {'Type':<10} Note")
    print("  " + "─" * 70)

    # Count by type
    type_counts = {}

    for nudge in nudges:
        time_sec = nudge["time"]
        duration = nudge.get("duration", 5)
        nudge_type = nudge.get("type", "unknown")
        text = nudge.get("text", "")

        # Format time as "H:MM:SS (1234s)"
        time_str = f"{format_timestamp(time_sec)} ({int(time_sec)}s)"

        # Truncate text
        if len(text) > 35:
            text = text[:32] + "..."

        print(f"  {time_str:<18} {duration:>6.1f}s  {nudge_type:<10} {text}")

        type_counts[nudge_type] = type_counts.get(nudge_type, 0) + 1

    # Summary
    print()
    type_summary = ", ".join(f"{count} {t}" for t, count in sorted(type_counts.items()))
    print(f"Total: {len(nudges)} nudges ({type_summary})")

    return 0


def cmd_dryrun(args):
    """Handle the dryrun command - test all nudges by jumping through them."""
    query = args.content
    start_index = args.start - 1 if args.start else 0  # Convert to 0-based
    delay = args.delay

    # Find content
    content_id = find_content_id(query)
    if not content_id:
        print(f"Content not found: {query}")
        print("\nUse 'nudge list' to see available content.")
        return 1

    content, nudges = load_content_with_nudges(content_id)
    if not content:
        print(f"Failed to load content: {content_id}")
        return 1

    if not nudges:
        print(f"No nudges configured for: {content['title']}")
        return 1

    # Validate start index
    if start_index >= len(nudges):
        print(f"Start index {args.start} exceeds nudge count ({len(nudges)})")
        return 1

    # Pause service if running (avoid conflict)
    service_was_running = is_running() and not is_paused()
    if service_was_running:
        print("Pausing service for dryrun...")
        pause_service()

    print(f"\nDryrun: {content['title']} - {len(nudges)} nudges")
    print()

    # Find device
    if args.device and ":" in args.device:
        # MAC address provided - skip scan
        identifier = args.device
        device_name = "Device"
    else:
        print("Scanning for devices...")
        devices = scan_devices_sync(timeout=3, check_paired=True)
        paired = [d for d in devices if d["paired"]]

        if not paired:
            print("No paired devices found.")
            return 1

        if args.device:
            # Find by name
            matches = [d for d in paired if args.device.lower() in d["name"].lower()]
            if not matches:
                print(f"Device not found: {args.device}")
                return 1
            device = matches[0]
        else:
            device = paired[0]

        identifier = device["identifier"]
        device_name = device["name"]

    print(f"Using device: {device_name}")
    print(f"Starting from nudge #{start_index + 1}")
    print()

    # Run the dryrun
    try:
        result = asyncio.run(run_dryrun(
            identifier, device_name, nudges, start_index, delay, content['title']
        ))
    finally:
        # Resume service if we paused it
        if service_was_running:
            print("\nResuming service...")
            unpause_service()

    return result


async def run_dryrun(
    identifier: str,
    name: str,
    nudges: list[dict],
    start_index: int,
    delay: float,
    title: str,
) -> int:
    """Execute dryrun - jump through all nudges."""
    from nudge.atv import connect_to_device, get_playing_metadata, set_position
    from nudge.config import get_nudge_buffer, get_nudge_lead

    atv = await connect_to_device(identifier)
    if not atv:
        print(f"Could not connect to device")
        return 1

    # Verify content is playing
    print("Checking playback... ", end="", flush=True)
    metadata = await get_playing_metadata(atv)
    if not metadata:
        print("FAILED")
        print("\nError: Content must be playing on the AppleTV.")
        print("Start playing the content first, then run dryrun.")
        atv.close()
        return 1

    current_title = metadata.get("title", "Unknown")
    current_pos = metadata.get("position", 0)
    print(f"OK")
    print(f"Playing: {current_title} at {format_timestamp(current_pos)}")
    print()

    nudge_lead = get_nudge_lead()
    nudge_buffer = get_nudge_buffer()

    triggered = 0
    missed = 0
    total = len(nudges) - start_index

    try:
        for i, nudge in enumerate(nudges[start_index:], start=start_index + 1):
            nudge_time = nudge["time"]
            nudge_duration = nudge["duration"]
            nudge_type = nudge.get("type", "language")
            nudge_text = nudge.get("text", "")[:35]
            if len(nudge.get("text", "")) > 35:
                nudge_text += "..."

            # Format nudge info
            time_str = format_timestamp(nudge_time)
            print(f"[{i}/{len(nudges)}]  {time_str} ({int(nudge_time)}s) {nudge_type}")
            if nudge_text:
                print(f"        \"{nudge_text}\"")

            # Jump to just before the nudge
            approach_pos = max(0, nudge_time - nudge_lead - 1)
            print(f"        Jumping to {int(approach_pos)}s... ", end="", flush=True)

            # Seek and wait for AppleTV to process
            await set_position(atv, approach_pos)
            await asyncio.sleep(2.0)  # AppleTV needs time to seek and buffer

            # Wait for playback to resume (up to 10 attempts)
            metadata = None
            for attempt in range(10):
                try:
                    metadata = await get_playing_metadata(atv)
                    if metadata:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            if not metadata:
                print("SEEK FAILED (no playback) ✗")
                missed += 1
                # Try to recover - wait and continue
                await asyncio.sleep(2.0)
                continue

            current_pos = metadata.get("position", 0)
            print(f"at {int(current_pos)}s... ", end="", flush=True)

            # Wait for position to enter nudge window
            window_start = nudge_time - nudge_lead
            window_end = nudge_time + nudge_duration
            skip_to = nudge_time + nudge_duration + nudge_buffer

            # Wait up to 10 seconds for position to reach nudge window
            max_wait = 10.0
            check_interval = 0.3
            elapsed = 0.0
            in_window = False

            while elapsed < max_wait:
                await asyncio.sleep(check_interval)
                elapsed += check_interval

                metadata = await get_playing_metadata(atv)
                if not metadata:
                    continue  # Brief interruption, keep waiting

                pos = metadata.get("position", 0)

                # Check if we're in the nudge window
                if window_start <= pos < window_end:
                    in_window = True
                    print(f"in window... ", end="", flush=True)

                    # Simulate nudge - skip forward
                    await set_position(atv, skip_to)
                    await asyncio.sleep(1.0)

                    # Verify skip worked
                    metadata = await get_playing_metadata(atv)
                    if metadata:
                        final_pos = metadata.get("position", 0)
                        if final_pos >= skip_to - 2:
                            print(f"SKIP -> {int(final_pos)}s ✓")
                            triggered += 1
                        else:
                            print(f"SKIP FAILED (at {int(final_pos)}s) ✗")
                            missed += 1
                    else:
                        print(f"SKIP -> {int(skip_to)}s ✓")
                        triggered += 1
                    break

            if not in_window:
                # Never reached the window
                print(f"TIMEOUT (never reached window) ✗")
                missed += 1

            # Delay before next nudge
            if i < len(nudges):
                await asyncio.sleep(delay)

        print()
        print(f"Summary: {triggered}/{total} triggered, {missed} missed")

        return 0 if missed == 0 else 1

    except KeyboardInterrupt:
        print("\n\nInterrupted")
        return 1
    finally:
        await asyncio.sleep(0.5)
        atv.close()


def main():
    parser = argparse.ArgumentParser(
        prog="nudge",
        description="Automatically skip objectionable content on AppleTV.",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"nudge {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan for AppleTV devices")
    scan_parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Scan timeout in seconds (default: 5)",
    )
    scan_parser.set_defaults(func=cmd_scan)

    # pair command
    pair_parser = subparsers.add_parser("pair", help="Pair with AppleTV device(s)")
    pair_parser.add_argument(
        "device",
        nargs="?",
        help="Device identifier to pair with (optional, will prompt if not provided)",
    )
    pair_parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Scan timeout in seconds (default: 5)",
    )
    pair_parser.set_defaults(func=cmd_pair)

    # test command
    test_parser = subparsers.add_parser("test", help="Test and import content from SRT file")
    test_parser.add_argument(
        "srt_file",
        help="Path to SRT subtitle file",
    )
    test_parser.add_argument(
        "--device",
        help="Device name or MAC address (MAC address skips scan for faster startup)",
    )
    test_parser.add_argument(
        "--offset",
        type=float,
        default=None,
        help="Time offset in seconds (positive or negative)",
    )
    test_parser.add_argument(
        "--save",
        action="store_true",
        help="Save content after successful test",
    )
    test_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing content (use with --save)",
    )
    test_parser.set_defaults(func=cmd_test)

    # start command
    start_parser = subparsers.add_parser("start", help="Start the nudge service")
    start_parser.add_argument(
        "--foreground", "-f",
        action="store_true",
        help="Run in foreground (don't daemonize)",
    )
    start_parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug logging (verbose position tracking)",
    )
    start_parser.set_defaults(func=cmd_start)

    # stop command
    stop_parser = subparsers.add_parser("stop", help="Stop the nudge service")
    stop_parser.set_defaults(func=cmd_stop)

    # status command
    status_parser = subparsers.add_parser("status", help="Show service status")
    status_parser.set_defaults(func=cmd_status)

    # on command
    on_parser = subparsers.add_parser("on", help="Enable nudge enforcement")
    on_parser.set_defaults(func=cmd_on)

    # off command
    off_parser = subparsers.add_parser("off", help="Disable nudge enforcement")
    off_parser.set_defaults(func=cmd_off)

    # list command
    list_parser = subparsers.add_parser("list", help="List stored content")
    list_parser.set_defaults(func=cmd_list)

    # remove command
    remove_parser = subparsers.add_parser("remove", help="Remove stored content")
    remove_parser.add_argument(
        "content_id",
        help="Content ID to remove (use 'nudge list' to see IDs)",
    )
    remove_parser.set_defaults(func=cmd_remove)

    # nudges command
    nudges_parser = subparsers.add_parser("nudges", help="Show nudges for content")
    nudges_parser.add_argument(
        "content",
        help="Content ID, filename, or partial title (e.g., '414711948', 'Tron')",
    )
    nudges_parser.set_defaults(func=cmd_nudges)

    # dryrun command
    dryrun_parser = subparsers.add_parser("dryrun", help="Test nudges by jumping through them")
    dryrun_parser.add_argument(
        "content",
        help="Content ID, filename, or partial title",
    )
    dryrun_parser.add_argument(
        "--device", "-D",
        help="Device name or MAC address (uses first paired device if not specified)",
    )
    dryrun_parser.add_argument(
        "--start", "-s",
        type=int,
        default=1,
        help="Start from nudge number (default: 1)",
    )
    dryrun_parser.add_argument(
        "--delay", "-d",
        type=float,
        default=2.0,
        help="Delay between nudges in seconds (default: 2)",
    )
    dryrun_parser.set_defaults(func=cmd_dryrun)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
