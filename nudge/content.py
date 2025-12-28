"""Content storage and management."""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from nudge.config import get_base_dir
from nudge.storage import get_wordlist_path
from nudge.subtitles import load_wordlist, parse_srt


def get_content_directory() -> Path:
    """Get and ensure the content directory exists."""
    content_dir = get_base_dir() / "content"
    content_dir.mkdir(parents=True, exist_ok=True)
    return content_dir


def generate_content_id(title: str, duration: int, app: str, store_id: str | None = None) -> str:
    """
    Generate a content ID from metadata.

    Uses store_id if available, otherwise generates hash from title/duration/app.
    """
    if store_id:
        return store_id
    key = f"{title}|{duration}|{app}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def get_content_path(content_id: str) -> Path:
    """Get path to content JSON file."""
    return get_content_directory() / f"{content_id}.json"


def get_srt_path(content_id: str) -> Path:
    """Get path to content SRT file."""
    return get_content_directory() / f"{content_id}.srt"


def load_content(content_id: str) -> dict | None:
    """Load content metadata by ID (without computing nudges)."""
    path = get_content_path(content_id)
    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(value) -> float:
    """
    Parse time value - supports seconds (float/int) or timestamp (HH:MM:SS, MM:SS).

    Examples:
        123.5 -> 123.5
        "123.5" -> 123.5
        "01:23:45" -> 5025.0
        "23:45" -> 1425.0
    """
    if isinstance(value, (int, float)):
        return float(value)

    value = str(value)
    if ":" in value:
        parts = value.split(":")
        try:
            if len(parts) == 3:
                hours, mins, secs = parts
                return int(hours) * 3600 + int(mins) * 60 + float(secs)
            elif len(parts) == 2:
                mins, secs = parts
                return int(mins) * 60 + float(secs)
        except ValueError:
            pass

    return float(value)


def load_content_with_nudges(content_id: str) -> tuple[dict | None, list[dict]]:
    """
    Load content and compute all nudges.

    Returns:
        Tuple of (content dict, list of all nudges sorted by time).
        Language nudges are computed from SRT + wordlist.
        Scene nudges (violence, sex, other) come from stored content.
    """
    content = load_content(content_id)
    if not content:
        return None, []

    all_nudges = []

    # Compute language nudges from SRT + wordlist
    srt_path = get_srt_path(content_id)
    if srt_path.exists():
        wordlist_path = get_wordlist_path()
        if wordlist_path.exists():
            wordlist = load_wordlist(wordlist_path)
            if wordlist:
                offset = content.get("srt_offset", 0.0)
                language_nudges = parse_srt(srt_path, wordlist, offset)
                all_nudges.extend(language_nudges)

    # Add scene nudges from stored content
    nudges_dict = content.get("nudges", {})
    for category in ["violence", "sex", "other"]:
        for nudge in nudges_dict.get(category, []):
            all_nudges.append({
                "time": parse_time(nudge["time"]),
                "duration": parse_time(nudge.get("duration", 5)),
                "type": category,
                "text": nudge.get("note", ""),
            })

    # Sort all nudges by time
    all_nudges.sort(key=lambda x: x["time"])

    return content, all_nudges


def save_content(
    content_id: str,
    title: str,
    app: str,
    duration: int,
    srt_offset: float = 0.0,
    srt_source: Path | None = None,
) -> Path:
    """
    Save content metadata to storage.

    Args:
        content_id: Content identifier.
        title: Content title.
        app: App name (Netflix, etc.).
        duration: Duration in seconds.
        srt_offset: Time offset for SRT parsing.
        srt_source: Path to source SRT file to copy.

    Returns:
        Path to saved content JSON.
    """
    get_content_directory()  # Ensure directory exists

    # Load existing content to preserve manual nudges
    existing = load_content(content_id)

    content = {
        "id": content_id,
        "title": title,
        "app": app,
        "duration": duration,
        "srt_offset": srt_offset,
        "created": existing.get("created") if existing else datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "nudges": existing.get("nudges", {}) if existing else {
            "violence": [],
            "sex": [],
            "other": [
                {"time": "2:46:39", "duration": 5.0, "note": "Example - delete or edit this"}
            ],
        },
    }

    # Save JSON
    json_path = get_content_path(content_id)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)

    # Copy SRT file
    if srt_source and srt_source.exists():
        srt_dest = get_srt_path(content_id)
        shutil.copy2(srt_source, srt_dest)

    return json_path


def count_nudges(content_id: str) -> int:
    """Count total nudges for content (language + scene)."""
    _, nudges = load_content_with_nudges(content_id)
    return len(nudges)


def list_content() -> list[dict]:
    """List all stored content."""
    content_dir = get_content_directory()
    content_list = []

    for json_file in content_dir.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                content_id = data.get("id")
                content_list.append({
                    "id": content_id,
                    "title": data.get("title"),
                    "app": data.get("app"),
                    "nudges": count_nudges(content_id),
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return content_list


def delete_content(content_id: str) -> bool:
    """Delete content by ID."""
    json_path = get_content_path(content_id)
    srt_path = get_srt_path(content_id)

    deleted = False
    if json_path.exists():
        json_path.unlink()
        deleted = True
    if srt_path.exists():
        srt_path.unlink()
        deleted = True

    return deleted
