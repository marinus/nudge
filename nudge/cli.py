"""Nudge CLI interface."""

import argparse
import json
import zipfile

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
    get_srt_path,
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


def cmd_export(args):
    """Handle the export command."""
    output_path = Path(args.output) if args.output else Path("nudge-export.zip")

    # Ensure .zip extension
    if not output_path.suffix == ".zip":
        output_path = output_path.with_suffix(".zip")

    content_dir = get_content_directory()
    content_list = list_content()

    if not content_list:
        print("No content to export.")
        return 1

    print(f"Exporting {len(content_list)} content item(s)...")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        exported = 0
        for item in content_list:
            content_id = item["id"]
            title = item["title"]

            # Export JSON
            json_path = get_content_path(content_id)
            if json_path.exists():
                zf.write(json_path, f"content/{json_path.name}")
                print(f"  {json_path.name:<20} {title}")
                exported += 1

            # Export SRT if exists
            srt_path = get_srt_path(content_id)
            if srt_path.exists():
                zf.write(srt_path, f"content/{srt_path.name}")

        # Export wordlist if requested
        if not args.no_wordlist:
            wordlist_path = get_wordlist_path()
            if wordlist_path.exists():
                zf.write(wordlist_path, "wordlist.txt")
                print(f"  wordlist.txt")

    print(f"\nExported to: {output_path}")
    print(f"Total: {exported} content item(s)")
    return 0


def cmd_import(args):
    """Handle the import command."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"File not found: {input_path}")
        return 1

    if not zipfile.is_zipfile(input_path):
        print(f"Not a valid zip file: {input_path}")
        return 1

    force = args.force
    dry_run = args.dry_run

    if dry_run:
        print(f"Dry run - no changes will be made\n")

    print(f"Importing from {input_path}...")
    print()

    content_dir = get_content_directory()
    new_count = 0
    overwrite_count = 0
    skip_count = 0

    with zipfile.ZipFile(input_path, "r") as zf:
        # Get list of files in archive
        names = zf.namelist()

        # Process content files
        json_files = [n for n in names if n.startswith("content/") and n.endswith(".json")]

        for json_name in json_files:
            # Read JSON to get title
            try:
                with zf.open(json_name) as f:
                    data = json.load(f)
                    title = data.get("title", "Unknown")
                    content_id = data.get("id", Path(json_name).stem)
            except (json.JSONDecodeError, KeyError):
                title = "Unknown"
                content_id = Path(json_name).stem

            # Check if exists
            dest_json = content_dir / Path(json_name).name
            exists = dest_json.exists()

            # Determine action
            if exists and not force:
                status = "SKIP - exists"
                skip_count += 1
            elif exists:
                status = "OVERWRITE"
                overwrite_count += 1
            else:
                status = "NEW"
                new_count += 1

            print(f"  {Path(json_name).name:<20} {title:<30} [{status}]")

            # Extract if not dry run and not skipping
            if not dry_run and status != "SKIP - exists":
                zf.extract(json_name, content_dir.parent)

                # Also extract SRT if exists
                srt_name = json_name.replace(".json", ".srt")
                if srt_name in names:
                    zf.extract(srt_name, content_dir.parent)

        # Import wordlist if present and requested
        if "wordlist.txt" in names and not args.no_wordlist:
            wordlist_dest = get_wordlist_path()
            if wordlist_dest.exists() and not force:
                print(f"  wordlist.txt                                      [SKIP - exists]")
            else:
                status = "OVERWRITE" if wordlist_dest.exists() else "NEW"
                print(f"  wordlist.txt                                      [{status}]")
                if not dry_run:
                    with zf.open("wordlist.txt") as src:
                        wordlist_dest.write_bytes(src.read())

    print()
    summary = f"Imported: {new_count} new"
    if overwrite_count > 0:
        summary += f", {overwrite_count} overwritten"
    if skip_count > 0:
        summary += f", {skip_count} skipped"
    if dry_run:
        summary += " (dry run)"
    print(summary)

    return 0


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


def cmd_verify(args):
    """Handle the verify command - verify content loads without errors."""
    query = args.content

    content_id = find_content_id(query)
    if not content_id:
        print(f"Content not found: {query}")
        print("\nUse 'nudge list' to see available content.")
        return 1

    content = load_content(content_id)
    if not content:
        print(f"Failed to load content: {content_id}")
        return 1

    print(f"Verifying: {content['title']} ({content_id})")
    print()

    errors = []
    warnings = []

    # Check nudges structure
    nudges_dict = content.get("nudges", {})
    if not nudges_dict:
        warnings.append("No nudges section found")

    # Import parse functions
    from nudge.content import parse_time_range, parse_time

    total_nudges = 0
    for category in ["violence", "sex", "other"]:
        category_nudges = nudges_dict.get(category, [])
        for i, nudge in enumerate(category_nudges, 1):
            total_nudges += 1
            prefix = f"[{category}#{i}]"

            # Check time field
            if "time" not in nudge:
                errors.append(f"{prefix} Missing 'time' field")
                continue

            try:
                start_time, range_duration = parse_time_range(nudge["time"])
                if start_time < 0:
                    errors.append(f"{prefix} Negative start time: {start_time}")

                # Check duration
                if range_duration is not None:
                    if range_duration <= 0:
                        errors.append(f"{prefix} Invalid duration from range: {range_duration:.1f}s (end before start?)")
                    elif range_duration > 600:
                        warnings.append(f"{prefix} Very long duration: {range_duration:.1f}s ({range_duration/60:.1f} min)")
                elif "duration" not in nudge:
                    warnings.append(f"{prefix} No duration specified, using default (5s)")
                else:
                    dur = parse_time(nudge["duration"])
                    if dur <= 0:
                        errors.append(f"{prefix} Invalid duration: {dur}")
                    elif dur > 600:
                        warnings.append(f"{prefix} Very long duration: {dur:.1f}s ({dur/60:.1f} min)")

            except Exception as e:
                errors.append(f"{prefix} Parse error: {e}")

    # Try full load to catch any other issues
    try:
        _, all_nudges = load_content_with_nudges(content_id)
    except Exception as e:
        errors.append(f"Failed to load nudges: {e}")
        all_nudges = []

    # Report results
    if errors:
        print("ERRORS:")
        for err in errors:
            print(f"  ✗ {err}")
        print()

    if warnings:
        print("WARNINGS:")
        for warn in warnings:
            print(f"  ⚠ {warn}")
        print()

    if errors:
        print(f"FAILED: {len(errors)} error(s) found")
        return 1

    print(f"OK: {len(all_nudges)} nudges loaded successfully")
    if warnings:
        print(f"    ({len(warnings)} warning(s))")
    return 0


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


def cmd_simulate(args):
    """Handle the simulate command - rapidly test all nudges."""
    query = args.content
    start_index = args.start - 1 if args.start else 0

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

    # Pause service if running
    service_was_running = is_running() and not is_paused()
    if service_was_running:
        print("Pausing service for simulation...")
        pause_service()

    print(f"\nSimulate: {content['title']} - {len(nudges)} nudges")
    print()

    # Find device
    if args.device and ":" in args.device:
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

    try:
        result = asyncio.run(run_simulate(
            identifier, device_name, nudges, start_index, content['title'], args.pause
        ))
    finally:
        if service_was_running:
            print("\nResuming service...")
            unpause_service()

    return result


async def run_simulate(
    identifier: str,
    name: str,
    nudges: list[dict],
    start_index: int,
    title: str,
    pause_mode: bool,
) -> int:
    """Execute simulate - rapidly jump through all nudges."""
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
        print("Start playing the content first, then run simulate.")
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

            # Calculate positions
            approach_pos = max(0, nudge_time - 1)  # 1 second before nudge
            window_start = nudge_time - nudge_lead
            window_end = nudge_time + nudge_duration
            skip_to = nudge_time + nudge_duration + nudge_buffer

            # Jump to 1s before nudge
            print(f"[{i}/{len(nudges)}] Jumping to {format_timestamp(approach_pos)}...", end="", flush=True)
            await set_position(atv, approach_pos)
            await asyncio.sleep(1.5)  # Wait for seek

            # Wait for nudge window (up to 5 seconds)
            in_window = False
            for _ in range(20):
                metadata = await get_playing_metadata(atv)
                if not metadata:
                    await asyncio.sleep(0.25)
                    continue

                pos = metadata.get("position", 0)
                if window_start <= pos < window_end:
                    in_window = True
                    # Perform skip
                    await set_position(atv, skip_to)
                    await asyncio.sleep(0.5)

                    # Verify
                    metadata = await get_playing_metadata(atv)
                    final_pos = metadata.get("position", 0) if metadata else skip_to
                    if final_pos >= skip_to - 2:
                        print(f"\n       {format_timestamp(nudge_time)} | NUDGE {nudge_type} -> {format_timestamp(final_pos)} ✓")
                        triggered += 1
                        # Play 1s after skip so user can see/hear where it landed
                        await asyncio.sleep(1.0)
                    else:
                        print(f"\n       {format_timestamp(nudge_time)} | SKIP FAILED ✗")
                        missed += 1
                    break

                await asyncio.sleep(0.25)

            if not in_window:
                print(f"\n       {format_timestamp(nudge_time)} | TIMEOUT ✗")
                missed += 1

            # Pause mode - wait for user
            if pause_mode and i < len(nudges):
                input("       Press ENTER for next...")

        print()
        print(f"Complete: {triggered}/{total} nudges triggered" + (f", {missed} missed" if missed else ""))

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

    # export command
    export_parser = subparsers.add_parser("export", help="Export content database to zip")
    export_parser.add_argument(
        "output",
        nargs="?",
        help="Output zip file (default: nudge-export.zip)",
    )
    export_parser.add_argument(
        "--no-wordlist",
        action="store_true",
        help="Exclude wordlist from export",
    )
    export_parser.set_defaults(func=cmd_export)

    # import command
    import_parser = subparsers.add_parser("import", help="Import content database from zip")
    import_parser.add_argument(
        "input",
        help="Input zip file to import",
    )
    import_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite existing content",
    )
    import_parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be imported without making changes",
    )
    import_parser.add_argument(
        "--no-wordlist",
        action="store_true",
        help="Don't import wordlist even if present",
    )
    import_parser.set_defaults(func=cmd_import)

    # nudges command
    nudges_parser = subparsers.add_parser("nudges", help="Show nudges for content")
    nudges_parser.add_argument(
        "content",
        help="Content ID, filename, or partial title (e.g., '414711948', 'Tron')",
    )
    nudges_parser.set_defaults(func=cmd_nudges)

    # verify command
    verify_parser = subparsers.add_parser("verify", help="Verify content loads without errors")
    verify_parser.add_argument(
        "content",
        help="Content ID, filename, or partial title",
    )
    verify_parser.set_defaults(func=cmd_verify)

    # simulate command
    simulate_parser = subparsers.add_parser("simulate", help="Rapidly test all nudges")
    simulate_parser.add_argument(
        "content",
        help="Content ID, filename, or partial title",
    )
    simulate_parser.add_argument(
        "--device", "-D",
        help="Device name or MAC address",
    )
    simulate_parser.add_argument(
        "--start", "-s",
        type=int,
        default=1,
        help="Start from nudge number (default: 1)",
    )
    simulate_parser.add_argument(
        "--pause", "-p",
        action="store_true",
        help="Pause after each nudge for manual review",
    )
    simulate_parser.set_defaults(func=cmd_simulate)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
