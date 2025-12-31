# Nudge Quickstart

Skip objectionable content on your AppleTV in 5 minutes.

## 1. Find Your AppleTV

```bash
uv run nudge scan
```

```
Found 2 device(s):

Name                 Address         Identifier           Paired
-----------------------------------------------------------------
Living Room          192.168.1.x     AA:BB:CC:DD:EE:F1    No
       Bedroom       192.168.1.x     AA:BB:CC:DD:EE:F2    No
```

## 2. Pair With It

```bash
uv run nudge pair
```

Select your device and enter the PIN shown on your TV screen.

## 3. Get Subtitles

1. Find your movie subtitles on a site like [OpenSubtitles.org](https://www.opensubtitles.org)
2. Download the `.srt` file (match language and release if possible)
3. Save it somewhere accessible (e.g., `~/Downloads/movie.srt`)

## 4. Start Your Movie

Play the movie on your AppleTV. Pause it near the beginning.

## 5. Test Subtitle Timing

```bash
uv run nudge test ~/Downloads/movie.srt
```

The test will:
- Detect what's playing on your AppleTV
- Parse the subtitles for words in your wordlist
- Jump to each nudge point so you can verify timing

**If timing is off**, adjust with `--offset`:

```bash
# Subtitles are 2 seconds early
uv run nudge test ~/Downloads/movie.srt --offset -2

# Subtitles are 3 seconds late
uv run nudge test ~/Downloads/movie.srt --offset 3
```

## 6. Save Content

Once timing is correct:

```bash
uv run nudge test ~/Downloads/movie.srt --offset -2 --save
```

This saves:
- Content metadata (title, duration, ID)
- Subtitle file for language detection
- Time offset for this content

## 7. Add Scene Nudges (Optional)

Edit the content file to add violence, sex, or other scene skips:

```bash
# Find the content file
uv run nudge list
```

Edit the content file (default: `nudge-data/content/{id}.json`):

```json
{
  "nudges": {
    "violence": [
      {"time": "45:30 - 45:45", "note": "Fight scene"},
      {"time": "1:32:00 - 1:32:20", "note": "Car chase crash"}
    ],
    "sex": [
      {"time": "28:15 - 28:45", "note": "Bedroom scene"}
    ],
    "other": []
  }
}
```

Use `start - end` format (e.g., `"1:32:00 - 1:32:20"`) - duration is calculated automatically.

## 8. Start the Service

```bash
uv run nudge start
```

The service runs in the background, monitoring all paired AppleTVs.

## 9. Monitor via Web

Open http://localhost:8080 in your browser to see:
- Connected devices and what's playing
- Matched content and nudge counts
- Service status

## Quick Reference

| Command | Description |
|---------|-------------|
| `uv run nudge scan` | Find AppleTVs on network |
| `uv run nudge pair` | Pair with an AppleTV |
| `uv run nudge test <srt>` | Test subtitle timing |
| `uv run nudge test <srt> --save` | Save after testing |
| `uv run nudge list` | List saved content |
| `uv run nudge nudges <title>` | View nudges for content |
| `uv run nudge verify <title>` | Verify content loads without errors |
| `uv run nudge simulate <title>` | Rapidly test all nudges on device |
| `uv run nudge export` | Export content database to zip |
| `uv run nudge import <zip>` | Import content database from zip |
| `uv run nudge start` | Start background service |
| `uv run nudge stop` | Stop service |
| `uv run nudge status` | Check service status |
| `uv run nudge on/off` | Enable/disable skipping |

## Tips

- **Faster testing**: Use `--device XX:XX:XX:XX:XX:XX` to skip device scanning
- **Debug mode**: `uv run nudge start --debug` for verbose logging
- **View nudges**: `uv run nudge nudges "Movie Title"` to see all skip points
- **Verify content**: `uv run nudge verify "Movie Title"` to check for parsing errors
- **Test nudges**: `uv run nudge simulate "Movie Title"` to rapidly test all nudges on the AppleTV
- **Backup**: `uv run nudge export` to backup your content database
