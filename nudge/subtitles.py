"""Subtitle parsing and nudge detection."""

import re
from pathlib import Path


def load_wordlist(filepath: Path) -> list[str]:
    """Load words from wordlist file."""
    if not filepath.exists():
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        return [
            line.strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


def parse_srt_timestamp(timestamp: str) -> float:
    """Parse SRT timestamp to seconds."""
    # Format: HH:MM:SS,mmm
    match = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})", timestamp.strip())
    if not match:
        return 0.0

    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_srt(filepath: Path, wordlist: list[str], offset: float = 0.0) -> list[dict]:
    """
    Parse SRT file and find nudge points based on wordlist.

    Args:
        filepath: Path to SRT file.
        wordlist: List of words to detect.
        offset: Time offset in seconds (positive or negative).

    Returns:
        List of nudge dicts with time, duration, type, and text.
    """
    nudges = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Split into subtitle blocks
    blocks = re.split(r"\n\n+", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # Line 0: sequence number
        # Line 1: timestamp
        # Lines 2+: text
        try:
            timestamp_line = lines[1]
            text_lines = lines[2:]
        except IndexError:
            continue

        # Parse timestamp line: "00:05:20,000 --> 00:05:23,000"
        match = re.match(r"(.+?)\s*-->\s*(.+)", timestamp_line)
        if not match:
            continue

        start_ts, end_ts = match.groups()
        start_time = parse_srt_timestamp(start_ts)
        end_time = parse_srt_timestamp(end_ts)
        duration = end_time - start_time

        # Combine text lines
        text = " ".join(line.strip() for line in text_lines)
        text_lower = text.lower()

        # Check for bad words (wordlist entries are regexes)
        for pattern in wordlist:
            if re.search(pattern, text_lower):
                # Apply offset
                adjusted_time = start_time + offset
                if adjusted_time < 0:
                    adjusted_time = 0

                nudges.append({
                    "time": round(adjusted_time, 1),
                    "duration": round(duration + 1, 1),  # Add 1s buffer
                    "type": "language",
                    "text": text,
                })
                break  # Only add once per subtitle block

    # Sort by time
    nudges.sort(key=lambda x: x["time"])

    return nudges
