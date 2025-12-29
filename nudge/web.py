"""Nudge web interface with WebSocket support."""

import json
import logging
import queue
import threading
from flask import Flask, jsonify, render_template_string, request, Response
from flask_sock import Sock

from nudge.content import load_content_with_nudges
from nudge.service import get_state, set_state, is_paused, get_pause_remaining, unpause_service

logger = logging.getLogger("nudge.web")

app = Flask(__name__)
sock = Sock(app)

# Connected WebSocket clients
_clients: list = []
_clients_lock = threading.Lock()

# Update queue for broadcasting
_update_queue: queue.Queue = queue.Queue()

# Reference to the service for artwork access
_service = None


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nudge</title>
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #fff;
        }
        .card {
            background: #16213e;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }
        .card h2 {
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #888;
            margin-bottom: 15px;
        }
        .device {
            background: #1e2a4a;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
            display: flex;
            gap: 15px;
        }
        .device:last-child {
            margin-bottom: 0;
        }
        .device-artwork {
            width: 80px;
            height: 80px;
            border-radius: 6px;
            background: #2a3a5a;
            flex-shrink: 0;
            overflow: hidden;
        }
        .device-artwork img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            pointer-events: none;
        }
        .device-artwork.no-art {
            display: flex;
            align-items: center;
            justify-content: center;
            color: #444;
            font-size: 24px;
        }
        .device-info {
            flex: 1;
            min-width: 0;
        }
        .device-name {
            font-weight: 600;
            font-size: 16px;
            margin-bottom: 8px;
        }
        .device-status {
            color: #888;
            font-size: 14px;
        }
        .device-status.playing {
            color: #4ade80;
        }
        .device-title {
            color: #fff;
            font-size: 14px;
            margin-top: 5px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .device-app {
            color: #888;
            font-size: 12px;
        }
        .device-matched {
            color: #4ade80;
            font-size: 12px;
            margin-top: 5px;
        }
        .device-unmatched {
            color: #888;
            font-size: 12px;
            margin-top: 5px;
        }
        .device-monitoring {
            color: #60a5fa;
            font-size: 11px;
            margin-top: 3px;
        }
        .no-devices {
            color: #666;
            text-align: center;
            padding: 20px;
        }
        .connection-info {
            text-align: center;
            color: #666;
            font-size: 12px;
            margin-top: 20px;
        }
        .connection-status {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .connection-status.connected { background: #4ade80; }
        .connection-status.disconnected { background: #f87171; }

        /* Modal styles */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }
        .modal-overlay.active {
            display: flex;
        }
        .modal {
            background: #16213e;
            border-radius: 12px;
            padding: 25px;
            max-width: 500px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5);
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 20px;
        }
        .modal-title {
            font-size: 18px;
            font-weight: 600;
            color: #fff;
        }
        .modal-close {
            background: none;
            border: none;
            color: #888;
            font-size: 24px;
            cursor: pointer;
            padding: 0;
            line-height: 1;
        }
        .modal-close:hover {
            color: #fff;
        }
        .modal-section {
            margin-bottom: 15px;
        }
        .modal-section-title {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #888;
            margin-bottom: 5px;
        }
        .modal-section-content {
            color: #fff;
            font-size: 14px;
        }
        .modal-nudges {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        .modal-nudge-cat {
            background: #1e2a4a;
            border-radius: 6px;
            padding: 10px 15px;
            text-align: center;
        }
        .modal-nudge-count {
            font-size: 24px;
            font-weight: 600;
            color: #4ade80;
        }
        .modal-nudge-label {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
        }
        .modal-id {
            font-family: monospace;
            font-size: 12px;
            color: #666;
            word-break: break-all;
        }
        .device-artwork.clickable {
            cursor: pointer;
            transition: transform 0.1s;
        }
        .device-artwork.clickable:hover {
            transform: scale(1.05);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Nudge</h1>

        <div class="card">
            <h2>Apple TVs</h2>
            <div id="devices">
                <div class="no-devices">Waiting for data...</div>
            </div>
        </div>

        <div class="connection-info">
            <span class="connection-status disconnected" id="ws-status"></span>
            <span id="ws-text">Connecting...</span>
        </div>
    </div>

    <div class="modal-overlay" id="modal-overlay" onclick="closeModal(event)">
        <div class="modal" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div class="modal-title" id="modal-title">Content Info</div>
                <button class="modal-close" onclick="closeModal()">&times;</button>
            </div>
            <div id="modal-content">Loading...</div>
        </div>
    </div>

    <script>
        let ws = null;
        let reconnectTimeout = null;

        function connect() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

            ws.onopen = function() {
                document.getElementById('ws-status').className = 'connection-status connected';
                document.getElementById('ws-text').textContent = 'Live';
            };

            ws.onclose = function() {
                document.getElementById('ws-status').className = 'connection-status disconnected';
                document.getElementById('ws-text').textContent = 'Reconnecting...';
                reconnectTimeout = setTimeout(connect, 2000);
            };

            ws.onerror = function() {
                ws.close();
            };

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                updateUI(data);
            };
        }

        function updateUI(data) {
            // Update devices
            const devicesEl = document.getElementById('devices');
            if (!data.devices || data.devices.length === 0) {
                devicesEl.innerHTML = '<div class="no-devices">No paired Apple TVs found</div>';
            } else {
                devicesEl.innerHTML = data.devices.map(device => {
                    const statusClass = (device.status === 'Playing' || device.status === 'Monitoring') ? 'playing' : '';

                    // Artwork section - show for Playing, Paused, and Monitoring
                    let artworkHtml = '';
                    const isActive = device.status === 'Playing' || device.status === 'Paused' || device.status === 'Monitoring';
                    const clickable = device.matched && device.content_id ? 'clickable' : '';
                    const dataAttr = clickable ? `data-content-id="${device.content_id}"` : '';
                    if (device.has_artwork && isActive) {
                        // Add title as cache buster so image updates when content changes
                        const cacheBuster = encodeURIComponent(device.title || '');
                        artworkHtml = `
                            <div class="device-artwork ${clickable}" ${dataAttr}>
                                <img src="/api/artwork/${encodeURIComponent(device.identifier)}?t=${cacheBuster}" alt="Artwork">
                            </div>
                        `;
                    } else if (isActive) {
                        artworkHtml = `<div class="device-artwork no-art ${clickable}" ${dataAttr}>&#9654;</div>`;
                    }

                    let content = `
                        <div class="device">
                            ${artworkHtml}
                            <div class="device-info">
                                <div class="device-name">${escapeHtml(device.name)}</div>
                                <div class="device-status ${statusClass}">${escapeHtml(device.status)}</div>
                    `;
                    if (device.title) {
                        content += `
                                <div class="device-title">${escapeHtml(device.title)}</div>
                                <div class="device-app">${escapeHtml(device.app || '')}</div>
                        `;
                        if (device.matched) {
                            content += `<div class="device-matched">Known content (${device.nudge_count} nudges)</div>`;
                            if (device.monitoring) {
                                content += `<div class="device-monitoring">Monitoring active</div>`;
                            }
                        } else {
                            content += `<div class="device-unmatched">Unknown content</div>`;
                        }
                    }
                    content += '</div></div>';
                    return content;
                }).join('');
            }
        }

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function showContentInfo(contentId) {
            const modal = document.getElementById('modal-overlay');
            const modalContent = document.getElementById('modal-content');
            const modalTitle = document.getElementById('modal-title');

            modalContent.innerHTML = 'Loading...';
            modal.classList.add('active');

            fetch(`/api/content/${encodeURIComponent(contentId)}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        modalContent.innerHTML = `<div style="color: #f87171;">${escapeHtml(data.error)}</div>`;
                        return;
                    }

                    modalTitle.textContent = data.title || 'Content Info';

                    // Format duration
                    const hours = Math.floor(data.duration / 3600);
                    const mins = Math.floor((data.duration % 3600) / 60);
                    const durationStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;

                    let html = `
                        <div class="modal-section">
                            <div class="modal-section-title">App</div>
                            <div class="modal-section-content">${escapeHtml(data.app || 'Unknown')}</div>
                        </div>
                        <div class="modal-section">
                            <div class="modal-section-title">Duration</div>
                            <div class="modal-section-content">${durationStr}</div>
                        </div>
                        <div class="modal-section">
                            <div class="modal-section-title">Nudges</div>
                            <div class="modal-nudges">
                                <div class="modal-nudge-cat">
                                    <div class="modal-nudge-count">${data.nudges.language}</div>
                                    <div class="modal-nudge-label">Language</div>
                                </div>
                                <div class="modal-nudge-cat">
                                    <div class="modal-nudge-count">${data.nudges.violence}</div>
                                    <div class="modal-nudge-label">Violence</div>
                                </div>
                                <div class="modal-nudge-cat">
                                    <div class="modal-nudge-count">${data.nudges.sex}</div>
                                    <div class="modal-nudge-label">Sex</div>
                                </div>
                                <div class="modal-nudge-cat">
                                    <div class="modal-nudge-count">${data.nudges.other}</div>
                                    <div class="modal-nudge-label">Other</div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-section">
                            <div class="modal-section-title">SRT Offset</div>
                            <div class="modal-section-content">${data.srt_offset >= 0 ? '+' : ''}${data.srt_offset}s</div>
                        </div>
                        <div class="modal-section">
                            <div class="modal-section-title">Content ID</div>
                            <div class="modal-id">${escapeHtml(data.id)}</div>
                        </div>
                    `;

                    modalContent.innerHTML = html;
                })
                .catch(err => {
                    modalContent.innerHTML = `<div style="color: #f87171;">Failed to load content info</div>`;
                });
        }

        function closeModal(event) {
            if (event && event.target !== event.currentTarget) return;
            document.getElementById('modal-overlay').classList.remove('active');
        }

        // Close modal on escape key
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeModal();
        });

        // Handle clicks on artwork (event delegation)
        document.getElementById('devices').addEventListener('click', function(e) {
            const artwork = e.target.closest('.device-artwork.clickable');
            if (artwork && artwork.dataset.contentId) {
                showContentInfo(artwork.dataset.contentId);
            }
        });

        // Start connection
        connect();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    """Render the main page."""
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/toggle", methods=["POST"])
def api_toggle():
    """Toggle nudge state."""
    data = request.get_json()
    new_state = data.get("state", "on")

    if new_state not in ("on", "off"):
        return jsonify({"error": "Invalid state"}), 400

    set_state(new_state)

    # If turning on, also unpause
    if new_state == "on" and is_paused():
        unpause_service()

    return jsonify({"state": new_state})


@app.route("/api/content/<content_id>")
def api_content(content_id):
    """Get content details."""
    content, nudges = load_content_with_nudges(content_id)

    if not content:
        return jsonify({"error": "Content not found"}), 404

    # Count nudges by type
    nudge_counts = {"language": 0, "violence": 0, "sex": 0, "other": 0}
    for nudge in nudges:
        nudge_type = nudge.get("type", "other")
        if nudge_type in nudge_counts:
            nudge_counts[nudge_type] += 1
        else:
            nudge_counts["other"] += 1

    return jsonify({
        "id": content.get("id"),
        "title": content.get("title"),
        "app": content.get("app"),
        "duration": content.get("duration", 0),
        "srt_offset": content.get("srt_offset", 0),
        "created": content.get("created"),
        "updated": content.get("updated"),
        "nudges": nudge_counts,
    })


@app.route("/api/artwork/<identifier>")
def api_artwork(identifier):
    """Get artwork for a device."""
    global _service
    if not _service or not hasattr(_service, 'artwork_cache'):
        return Response(status=404)

    artwork = _service.artwork_cache.get(identifier)
    if not artwork:
        return Response(status=404)

    # Detect image type from magic bytes
    content_type = "image/jpeg"
    if artwork[:8] == b'\x89PNG\r\n\x1a\n':
        content_type = "image/png"

    return Response(
        artwork,
        mimetype=content_type,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@sock.route("/ws")
def websocket(ws):
    """WebSocket endpoint for real-time updates."""
    with _clients_lock:
        _clients.append(ws)

    logger.info("WebSocket client connected")

    try:
        while True:
            # Keep connection alive, receive any messages (we don't use them)
            try:
                ws.receive(timeout=1)
            except Exception:
                # Timeout is expected, just continue
                pass
    except Exception:
        pass
    finally:
        with _clients_lock:
            if ws in _clients:
                _clients.remove(ws)
        logger.info("WebSocket client disconnected")


def broadcast_update(data: dict):
    """Broadcast update to all connected WebSocket clients."""
    message = json.dumps(data)

    with _clients_lock:
        dead_clients = []
        for client in _clients:
            try:
                client.send(message)
            except Exception:
                dead_clients.append(client)

        # Remove dead clients
        for client in dead_clients:
            _clients.remove(client)


def get_current_state() -> dict:
    """Get current state for broadcasting."""
    return {
        "state": get_state(),
        "pause_remaining": get_pause_remaining(),
        "devices": [],  # Will be populated by service
    }


class WebServer:
    """Web server runner for integration with the service."""

    def __init__(self, host: str, port: int, service=None):
        self.host = host
        self.port = port
        self.thread = None
        self.service = service

    def start(self):
        """Start the web server in a background thread."""
        global _service
        _service = self.service

        # Disable Flask's default logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.WARNING)

        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="nudge-web"
        )
        self.thread.start()
        logger.info(f"Web interface started at http://{self.host}:{self.port}")

    def _run(self):
        """Run the Flask app."""
        app.run(
            host=self.host,
            port=self.port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    def broadcast(self, state: str, pause_remaining: int | None, devices: list):
        """Broadcast state update to all clients."""
        broadcast_update({
            "state": state,
            "pause_remaining": pause_remaining,
            "devices": devices,
        })
