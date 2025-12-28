"""AppleTV discovery and connection management."""

import asyncio
from typing import Callable

import pyatv
from pyatv.const import DeviceModel, PairingRequirement, Protocol
from pyatv.storage.file_storage import FileStorage

from nudge.storage import get_credentials_path

# AppleTV device models
APPLETV_MODELS = {
    DeviceModel.Gen2,
    DeviceModel.Gen3,
    DeviceModel.Gen4,
    DeviceModel.Gen4K,
}

# Protocols needed for metadata and playback control
# Companion: remote control, AirPlay: metadata
PAIRING_PROTOCOLS = [Protocol.Companion, Protocol.AirPlay]


def get_storage() -> FileStorage:
    """Get the file storage for credentials."""
    return FileStorage(str(get_credentials_path()), asyncio.get_event_loop())


async def scan_devices(timeout: int = 5, check_paired: bool = False) -> list[dict]:
    """
    Scan the network for AppleTV devices.

    Args:
        timeout: Scan timeout in seconds.
        check_paired: Whether to check pairing status.

    Returns:
        List of discovered AppleTV devices with name, model, address, identifier, and paired status.
    """
    devices = []

    storage = None
    if check_paired:
        storage = get_storage()
        await storage.load()

    atvs = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout, storage=storage)

    for atv in atvs:
        # Only include actual AppleTV devices
        if atv.device_info.model not in APPLETV_MODELS:
            continue

        # Get the primary identifier (prefer MAC address)
        identifier = None
        for id_str in atv.all_identifiers:
            # MAC addresses have colons
            if ":" in id_str and len(id_str) == 17:
                identifier = id_str
                break

        # Fallback to first identifier if no MAC found
        if not identifier and atv.all_identifiers:
            identifier = atv.all_identifiers[0]

        # Check if device is paired (has Companion credentials)
        paired = False
        if check_paired:
            companion = atv.get_service(Protocol.Companion)
            if companion and companion.credentials:
                paired = True

        devices.append({
            "name": atv.name,
            "model": str(atv.device_info.model),
            "address": str(atv.address),
            "identifier": identifier,
            "paired": paired,
        })

    return devices


def scan_devices_sync(timeout: int = 5, check_paired: bool = False) -> list[dict]:
    """Synchronous wrapper for scan_devices."""
    return asyncio.run(scan_devices(timeout, check_paired))


async def scan_with_storage(timeout: int = 5) -> list:
    """Scan for AppleTV devices with stored credentials loaded."""
    storage = get_storage()
    await storage.load()

    atvs = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout, storage=storage)

    # Filter to only AppleTV devices
    return [atv for atv in atvs if atv.device_info.model in APPLETV_MODELS]


async def pair_device(
    identifier: str,
    pin_callback: Callable[[Protocol], str],
    status_callback: Callable[[str], None],
    timeout: int = 5,
) -> bool:
    """
    Pair with an AppleTV device.

    Args:
        identifier: Device identifier (MAC address or UUID).
        pin_callback: Function that takes protocol name and returns PIN entered by user.
        status_callback: Function to report status messages.
        timeout: Scan timeout in seconds.

    Returns:
        True if pairing was successful, False otherwise.
    """
    storage = get_storage()
    await storage.load()

    status_callback(f"Scanning for device {identifier}...")

    atvs = await pyatv.scan(
        asyncio.get_event_loop(),
        identifier=identifier,
        timeout=timeout,
        storage=storage,
    )

    if not atvs:
        status_callback(f"Device {identifier} not found.")
        return False

    atv_conf = atvs[0]
    status_callback(f"Found {atv_conf.name} at {atv_conf.address}")

    paired_any = False

    for protocol in PAIRING_PROTOCOLS:
        service = atv_conf.get_service(protocol)
        if service is None:
            continue

        if service.pairing == PairingRequirement.NotNeeded:
            status_callback(f"  {protocol.name}: pairing not needed")
            continue

        if service.credentials:
            status_callback(f"  {protocol.name}: already paired")
            paired_any = True
            continue

        status_callback(f"  {protocol.name}: starting pairing...")

        pairing = await pyatv.pair(atv_conf, protocol, asyncio.get_event_loop(), storage=storage)

        try:
            await pairing.begin()

            if pairing.device_provides_pin:
                pin = pin_callback(protocol)
                if not pin:
                    status_callback(f"  {protocol.name}: pairing cancelled")
                    await pairing.close()
                    continue
                pairing.pin(pin)
            else:
                status_callback(f"  {protocol.name}: enter PIN shown on this device on your AppleTV")

            await pairing.finish()

            if pairing.has_paired:
                status_callback(f"  {protocol.name}: paired successfully")
                paired_any = True
            else:
                status_callback(f"  {protocol.name}: pairing failed")

        except Exception as e:
            status_callback(f"  {protocol.name}: pairing error - {e}")
        finally:
            await pairing.close()

    # Save credentials
    await storage.save()
    status_callback("Credentials saved.")

    return paired_any


def pair_device_sync(
    identifier: str,
    pin_callback: Callable[[Protocol], str],
    status_callback: Callable[[str], None],
    timeout: int = 5,
) -> bool:
    """Synchronous wrapper for pair_device."""
    return asyncio.run(pair_device(identifier, pin_callback, status_callback, timeout))


async def get_paired_devices_with_status(timeout: int = 5, skip_identifiers: set = None) -> list[dict]:
    """
    Get paired devices with their current playback status.

    Args:
        timeout: Scan timeout in seconds.
        skip_identifiers: Set of device identifiers to skip connecting to (for active monitors).

    Returns:
        List of paired devices with name, identifier, and playback info.
    """
    if skip_identifiers is None:
        skip_identifiers = set()

    storage = get_storage()
    await storage.load()

    atvs = await pyatv.scan(asyncio.get_event_loop(), timeout=timeout, storage=storage)

    devices = []
    for atv_conf in atvs:
        # Only AppleTV devices
        if atv_conf.device_info.model not in APPLETV_MODELS:
            continue

        # Check if paired
        companion = atv_conf.get_service(Protocol.Companion)
        if not companion or not companion.credentials:
            continue

        # Get identifier
        identifier = None
        for id_str in atv_conf.all_identifiers:
            if ":" in id_str and len(id_str) == 17:
                identifier = id_str
                break
        if not identifier and atv_conf.all_identifiers:
            identifier = atv_conf.all_identifiers[0]

        # Skip connecting to devices with active monitors
        if identifier in skip_identifiers:
            devices.append({
                "name": atv_conf.name,
                "identifier": identifier,
                "status": "Monitoring",
                "title": None,
                "app": None,
            })
            continue

        # Try to get playback status
        status = "Unknown"
        title = None
        app = None
        try:
            atv = await pyatv.connect(atv_conf, asyncio.get_event_loop(), storage=storage)
            try:
                playing = await atv.metadata.playing()
                if playing.device_state.name == "Playing":
                    status = "Playing"
                    title = playing.title
                elif playing.device_state.name == "Paused":
                    status = "Paused"
                    title = playing.title
                else:
                    status = "Idle"

                # Get current app if available
                try:
                    app_info = await atv.metadata.app
                    if app_info:
                        app = app_info.name
                except Exception:
                    pass
            finally:
                atv.close()
        except Exception:
            status = "Error"

        devices.append({
            "name": atv_conf.name,
            "identifier": identifier,
            "status": status,
            "title": title,
            "app": app,
        })

    return devices


def get_paired_devices_with_status_sync(timeout: int = 5) -> list[dict]:
    """Synchronous wrapper for get_paired_devices_with_status."""
    return asyncio.run(get_paired_devices_with_status(timeout))


async def connect_to_device(identifier: str, timeout: int = 5):
    """
    Connect to an AppleTV device.

    Args:
        identifier: Device identifier.
        timeout: Scan timeout.

    Returns:
        Connected AppleTV instance or None.
    """
    storage = get_storage()
    await storage.load()

    atvs = await pyatv.scan(
        asyncio.get_event_loop(),
        identifier=identifier,
        timeout=timeout,
        storage=storage,
    )

    if not atvs:
        return None

    atv = await pyatv.connect(atvs[0], asyncio.get_event_loop(), storage=storage)
    return atv


async def get_playing_metadata(atv) -> dict | None:
    """
    Get metadata for currently playing content.

    Returns:
        Dict with title, app, duration, position, store_id or None if nothing playing.
    """
    try:
        playing = await atv.metadata.playing()

        if playing.device_state.name not in ("Playing", "Paused"):
            return None

        # Get app name if available
        app_name = "Unknown"
        try:
            app_info = await atv.metadata.app
            if app_info:
                app_name = app_info.name
        except Exception:
            pass

        # Get store identifier if available
        store_id = None
        if playing.content_identifier:
            store_id = str(playing.content_identifier)
        elif playing.itunes_store_identifier:
            store_id = str(playing.itunes_store_identifier)

        return {
            "title": playing.title or "Unknown",
            "app": app_name,
            "duration": playing.total_time or 0,
            "position": playing.position or 0,
            "store_id": store_id,
        }
    except Exception:
        return None


async def set_position(atv, position: float) -> bool:
    """Set playback position."""
    try:
        await atv.remote_control.set_position(position)
        # Small delay to ensure command completes before any subsequent operations
        await asyncio.sleep(0.5)
        return True
    except Exception:
        return False


async def get_artwork(atv, width: int = 300, height: int = 300) -> bytes | None:
    """
    Get artwork for currently playing content.

    Returns:
        Artwork bytes (PNG/JPEG) or None if not available.
    """
    try:
        artwork = await atv.metadata.artwork(width=width, height=height)
        if artwork and artwork.bytes:
            return artwork.bytes
        return None
    except Exception:
        return None
