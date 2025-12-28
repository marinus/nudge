# Nudge Data Files

Nudge uses two types of files: **user configuration** (at project root) and **runtime data** (in data directory).

## Directory Structure

```
./
├── config.json        # User configuration (specifies data_dir, wordlist, etc.)
├── wordlist.txt       # Words to filter (one per line, regex supported)
└── nudge-data/        # Data directory (configurable via config.json)
    ├── credentials.conf   # AppleTV pairing credentials
    ├── nudge.log          # Service log
    ├── playback.log       # Playback history
    ├── nudge.pid          # Service PID file
    ├── nudge.state        # On/off state ("on" or "off")
    ├── nudge.pause        # Pause expiry timestamp (for CLI test mode)
    ├── nudge.debug        # Debug flag (exists = debug enabled)
    └── content/           # Content files
        ├── {content_id}.json   # Content metadata + scene nudges
        └── {content_id}.srt    # Subtitles for language detection
```

## Content Matching

When content plays on an AppleTV, nudge matches it to stored content files:

1. **Metadata retrieval** - Get from AppleTV:
   - `title` - Content title
   - `duration` - Total length in seconds
   - `app` - App name (Netflix, Prime Video, etc.)
   - `store_id` - iTunes/content store identifier (if available)

2. **Content ID generation**:
   - If `store_id` exists → use it directly (e.g., `282551004`)
   - Otherwise → SHA256 hash of `{title}|{duration}|{app}`, first 16 chars

3. **File lookup** - Look for `content/{content_id}.json`

## Nudge Types

Nudges are skip points in content. There are two categories:

### Language Nudges (computed at runtime)
- Detected by parsing the SRT file against the wordlist
- Updates to `wordlist.txt` take effect immediately
- No need to re-save content when wordlist changes

### Scene Nudges (stored in JSON)
Manually added to the content JSON file:
- `violence` - Fight scenes, gore, etc.
- `sex` - Sexual content, nudity
- `other` - Drug use, disturbing imagery, etc.

## Content JSON Format

```json
{
  "id": "282551004",
  "title": "The Fugitive",
  "app": "Netflix",
  "duration": 7814,
  "srt_offset": 0.0,
  "created": "2025-12-27T13:30:42",
  "updated": "2025-12-27T13:30:42",
  "nudges": {
    "violence": [
      {"time": "32:15", "duration": 8, "note": "Fight in parking garage"},
      {"time": "1:45:30", "duration": 12, "note": "Train crash scene"}
    ],
    "sex": [],
    "other": [
      {"time": "58:20", "duration": 3, "note": "Graphic surgery"}
    ]
  }
}
```

Note: Times use `MM:SS` or `HH:MM:SS` format for readability. Seconds also work (e.g., `1935` instead of `"32:15"`).

### Fields

| Field | Description |
|-------|-------------|
| `id` | Content identifier (store ID or hash) |
| `title` | Content title |
| `app` | Source app name |
| `duration` | Total duration in seconds |
| `srt_offset` | Time offset applied to SRT timestamps |
| `nudges.violence` | Violence scene skip points |
| `nudges.sex` | Sexual content skip points |
| `nudges.other` | Other objectionable content |

### Nudge Entry Format

```json
{"time": "1:23:45", "duration": 10, "note": "Description"}
```

- `time` - Start time as timestamp (`HH:MM:SS`, `MM:SS`) or seconds (`5025`)
- `duration` - Duration to skip in seconds
- `note` - Optional description

### Timestamp Format

Time values support multiple formats for convenience:

| Format | Example | Seconds |
|--------|---------|---------|
| Seconds | `120.5` | 120.5 |
| MM:SS | `"2:00"` | 120.0 |
| HH:MM:SS | `"1:30:45"` | 5445.0 |

Example using timestamps:
```json
{
  "nudges": {
    "violence": [
      {"time": "45:30", "duration": 5, "note": "Fight scene"},
      {"time": "1:23:45", "duration": 10, "note": "Battle sequence"}
    ]
  }
}
```

Note: Timestamps must be quoted strings in JSON. Seconds can be numbers or strings.

## Wordlist Format

The `wordlist.txt` file contains patterns to match in subtitles:

```
# Comments start with #
shit
fuck
damn
ass\b
hell\b
```

- One pattern per line
- Supports regex (e.g., `\b` for word boundary)
- Case insensitive matching

## Adding New Content

1. Start playing the content on AppleTV
2. Run: `nudge test /path/to/subtitles.srt --save`
3. Edit the JSON file to add scene nudges if needed

### Faster Testing with Device Identifier

To skip the device scan (which can be slow), provide the device MAC address directly:

```bash
# First time: run scan to get device identifier
nudge scan

# Use identifier for fast subsequent tests
nudge test movie.srt --device XX:XX:XX:XX:XX:XX --save
```

The device identifier is shown in `nudge scan` output and `nudge status`.

### Overwrite Protection

If content already exists, you'll get an error when trying to save:

```
Error: Content already exists!
  ID:    282551004
  Title: The Fugitive

Use --force to overwrite.
```

To update existing content, use `--force`:

```bash
nudge test movie.srt --device XX:XX:XX:XX:XX:XX --save --force
```

## Viewing Nudges

Use `nudge nudges` to see all nudges for content and how they'll be evaluated:

```bash
nudge nudges Tron           # partial title match
nudge nudges 414711948      # content ID
nudge nudges 414711948.json # filename
```

Example output:

```
Nudges for: Tron: Legacy (414711948)
Duration: 2:05:06 (7506s)

  Time               Duration  Type       Note
  ──────────────────────────────────────────────────────────────────────
  31:27 (1887s)         2.1s  language   Damn it!
  56:05 (3365s)         4.8s  language   For centuries we've dreamed of g...
  1:16:35 (4595s)       5.0s  other      Bar scene

Total: 3 nudges (2 language, 1 other)
```

This shows:
- **Time**: Timestamp and offset in seconds
- **Duration**: How long the skip lasts
- **Type**: `language` (from SRT + wordlist) or scene type (`violence`, `sex`, `other`)
- **Note**: Description (from SRT text or JSON note field)

## Testing Nudges (Dryrun)

Test all nudges by jumping through them on the AppleTV:

```bash
nudge dryrun Midway                      # Test all nudges
nudge dryrun Midway --device XX:XX:XX    # Specific device
nudge dryrun Midway --start 5            # Start from nudge #5
nudge dryrun Midway --delay 3            # 3s delay between nudges
```

The content must be playing on the AppleTV. The dryrun will:
1. Pause the service (to avoid conflicts)
2. Jump to just before each nudge
3. Wait for playback to reach the nudge window
4. Trigger the skip and verify it worked
5. Resume the service when done

Example output:

```
Dryrun: Midway (2019) - 51 nudges

[1/51]  7:25 (445s) language
        "Better not crash that damn plane."
        Jumping to 443s... waiting... TRIGGERED -> 449s ✓

[2/51]  8:20 (500s) language
        "McClusky's about ready..."
        Jumping to 498s... waiting... TRIGGERED -> 504s ✓

[3/51]  8:51 (531s) language
        "It's probably 'cause..."
        Jumping to 529s... waiting... MISSED (position: 535s) ✗

Summary: 49/51 triggered, 2 missed
```

## Config Options

The `config.json` file at project root controls all settings:

```json
{
  "data_dir": "nudge-data",
  "wordlist": "wordlist.txt",
  "service_log": "nudge.log",
  "playback_log": "playback.log",
  "poll_interval": 5,
  "monitor_interval": 0.5,
  "nudge_lead": 1,
  "nudge_buffer": 2,
  "web_enabled": true,
  "web_host": "0.0.0.0",
  "web_port": 8080
}
```

| Option | Description |
|--------|-------------|
| `data_dir` | Directory for runtime data (logs, content, credentials, state) |
| `wordlist` | Path to wordlist file |
| `poll_interval` | Seconds between device discovery polls |
| `monitor_interval` | Seconds between position checks during playback |
| `nudge_lead` | Seconds to start skip before nudge (catches early audio) |
| `nudge_buffer` | Seconds added after nudge ends (ensures word is skipped) |
| `web_enabled` | Enable web interface |
| `web_host` | Web server bind address |
| `web_port` | Web server port |

## Time Calculations

When a nudge is configured at a specific time, the service uses `nudge_lead` and `nudge_buffer` to determine when to trigger and where to skip to.

### Nudge Window

The **nudge window** is the time range during which the service will trigger a skip:

```
window_start = nudge_time - nudge_lead
window_end   = nudge_time + nudge_duration
```

Example with `nudge_lead=1`:
- Nudge at 120s with 5s duration
- Window: 119s to 125s
- If position enters this window, skip is triggered

### Skip Target

When a skip is triggered, the **skip target** includes a buffer to ensure the content is fully skipped:

```
skip_to = nudge_time + nudge_duration + nudge_buffer
```

Example with `nudge_buffer=2`:
- Nudge at 120s with 5s duration
- Skip target: 120 + 5 + 2 = 127s

### Timeline Visualization

```
Timeline:
    ───────[LEAD]────[NUDGE CONTENT]────[BUFFER]───────
           ↑                            ↑
      window_start                   skip_to
      (trigger here)              (land here)

Example: nudge_time=120, duration=5, lead=1, buffer=2

    115s   116s   117s   118s   119s   120s   121s   ...   125s   126s   127s   128s
    ─────────────────────────────┬─────┬───────────────────┬─────────────┬──────────
                                 │     │    NUDGE CONTENT  │             │
                                 │     └───────────────────┘             │
                            window_start=119              window_end=125  skip_to=127
```

### Why Lead and Buffer?

- **Lead time** (`nudge_lead`): Catches audio that starts slightly before the subtitle timestamp. Subtitles often appear a moment after audio begins.

- **Buffer time** (`nudge_buffer`): Ensures the word/scene is fully passed before resuming. Accounts for timing variations and ensures clean skip.

### Seek Behavior

When you seek backward more than 10 seconds, previously-triggered nudges ahead of the new position are reset and can trigger again. This allows re-testing nudges without restarting the service.

## Debug Mode

Start the service with `--debug` to enable verbose logging:

```bash
nudge start --debug
nudge start --debug --foreground  # See logs in terminal
```

Debug mode logs:
- Playback position when it changes (not when paused/buffering)
- Next upcoming nudge with countdown
- Nudge window entry detection
- Missed nudge warnings (when position skips past a window)
- Seek detection (clears enforced nudges when seeking backward)

Example debug output:

```
17:13:03 | INFO  | Master Bedroom | Playing | Midway (2019)
17:13:03 | DEBUG | Master Bedroom | Content ID: 1504294067 (store_id)
17:13:03 | DEBUG | Master Bedroom | Duration: 8288s (2:18:08) | App: Apple TV
17:13:03 | INFO  | Master Bedroom | MATCHED | 51 nudges (50 language, 1 other)
17:13:03 | DEBUG | Master Bedroom | pos=1205s (20:05) | Monitoring started
17:13:03 | DEBUG | Master Bedroom | pos=1205s (20:05) | next=1228s (20:28) in 22.5s
17:13:11 | DEBUG | Master Bedroom | pos=1227s (20:27) | window=1228s-1231s | ENTERING
17:13:11 | INFO  | Master Bedroom | pos=1228s (20:28) | NUDGE language -> 1232s (20:32)
17:15:29 | DEBUG | Master Bedroom | pos=1276s (21:16) | MISSED 1274s (21:14) - past window
```

To disable debug mode, restart without `--debug`:

```bash
nudge stop && nudge start
```
