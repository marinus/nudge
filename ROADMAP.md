# Nudge Roadmap

## Basic Service ✓

Manual setup with subtitle files.

- [x] AppleTV discovery and pairing
- [x] Content matching by title/duration/store ID
- [x] Language nudges from SRT + wordlist
- [x] Scene nudges (violence, sex, other)
- [x] Background service with position monitoring
- [x] Web interface for status
- [x] CLI for testing and management
- [x] Debug mode with seek detection

## Content Sharing

Share nudge configurations with other users.

- [ ] Export content as shareable file (JSON or archive)
- [ ] Import content from other users
- [ ] Community repository for popular movies/shows
- [ ] Sync content between devices
- [ ] Conflict resolution for duplicate content

## Auto Subtitles

Automatically fetch subtitles when content is detected.

- [ ] Integrate with OpenSubtitles API
- [ ] Auto-search by title + duration
- [ ] Download best matching SRT
- [ ] Store credentials securely
- [ ] Fallback to manual if no match
- [ ] Rate limiting and caching

## Auto Time Correction

Automatically align subtitle timing to audio.

- [ ] Audio fingerprinting of content
- [ ] Compare against subtitle timestamps
- [ ] Calculate optimal offset
- [ ] Apply correction automatically
- [ ] Confidence scoring (fall back to manual if low)
- [ ] Learn from manual corrections
