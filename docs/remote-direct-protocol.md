# Remote View direct protocol

Phone ↔ PC over one WebRTC peer connection. The site does signaling only.
This document is the contract both repos build against. Version it; the
phone and the desktop refuse to talk across a major mismatch.

```
PROTOCOL_VERSION = 1
```

## Roles

- **Viewer** — the panel bundle running in the phone browser on the site
  origin (`https://uefnducky.org/ducky`). Offerer.
- **Desktop** — the app's WebView2 page (Chromium) on the PC. Answerer.
- **Site** — DuckyOS plugin `uefn-ducky`. Relays signaling, stores presence,
  serves the bundle. Never sees media or RPC.

## Peer connection

- One `RTCPeerConnection` per viewer session. `bundlePolicy: max-bundle`,
  `rtcpMuxPolicy: require`.
- ICE servers: Cloudflare STUN, Google STUN, plus TURN when the site hands
  out credentials (`ice` field in the session grant).
- Viewer creates the DataChannels **before** the offer so they ride the
  first negotiation. Desktop never creates channels except `stream:*`
  replies.
- Video: the desktop adds its cropped screen track on demand when the viewer
  sends `rpc watch_window`. That triggers renegotiation initiated by the
  desktop (`signal` message with a new offer; roles flip for that round).

## DataChannels

| Label | Ordered | Reliable | Direction | Payload |
|---|---|---|---|---|
| `rpc` | yes | yes | viewer → desktop, replies back | JSON text |
| `events` | yes | yes | desktop → viewer | JSON text |
| `blob` | yes | yes | viewer → desktop request, chunks back | JSON text + binary |
| `input` | yes | yes | viewer → desktop | JSON text (existing window input) |
| `stream:<id>` | yes | yes | both | binary or text frames |

Message size cap on every channel: **16 KiB**. Safari drops anything over
64 KiB; 16 keeps headroom for framing.

### `rpc`

Request:

```json
{"id": 17, "method": "list_chats", "args": [ ... ]}
```

Reply:

```json
{"id": 17, "ok": true, "result": <any>}
{"id": 17, "ok": false, "error": "method not allowed"}
```

- `method` must not be in `REMOTE_DENY` (desktop enforces; the list is the
  same one `/__panel_api` uses).
- Replies larger than 16 KiB are split: `{"id":17,"ok":true,"part":0,"of":3,"data":"<json slice>"}`.
  Viewer concatenates `data` in order and parses once.
- Timeout on the viewer: 30 s, then `{"ok":false,"error":"timeout"}` locally.
- Long-running UI requests (`require_click` style) keep the same shape; the
  desktop sends the reply when the user answers.

### `events`

One JSON object per message, identical to what `/__panel_events` returns
today (`{"cursor": n, "events": [...]}` flattened to individual events with
their sequence number):

```json
{"seq": 8123, "event": { "type": "...", ... }}
```

Viewer feeds each into the existing agent event bus. Gaps in `seq` trigger a
one-shot `rpc replay_events {"since": lastSeq}`.

### `blob`

Request (text):

```json
{"id": 4, "get": "/plugin-ui/foo/index.html", "etag": "W/\"abc\""}
```

Reply header (text), then binary chunks in order, then done (text):

```json
{"id": 4, "status": 200, "type": "text/html", "etag": "W/\"def\"", "size": 48211}
<binary chunk ≤ 16 KiB> ...
{"id": 4, "done": true}
```

- `304` reply has no chunks: `{"id":4,"status":304}`.
- Allowed prefixes only: `plugin-ui/`, `user-sounds/`, `tool-captures/`,
  `duckies/custom/`, `model-files/`. Anything else → `{"status":403}`.
- Backpressure: desktop waits on `bufferedAmountLowThreshold = 64 KiB`
  before sending the next chunk.

### `input`

Unchanged from the tunnel design: `{type: down|up|move|wheel|keydown|keyup|size, ...}`.
Desktop passes it to `window_input` for the watched HWND.

### `stream:<id>`

Opened by the viewer with `rpc stream_open {"kind": "terminal"|"lsp", "target": <session id>}`
→ desktop replies `{"id": "<stream id>"}` and opens a channel with that
label. Desktop bridges it to the local WebSocket the session already
exposes. Text frames map to text, binary to binary. Closing either side
closes the other.

## Signaling (via the site)

All signaling messages are JSON envelopes:

```json
{"v": 1, "session": "<32 hex>", "from": "viewer"|"desktop", "type": "offer"|"answer"|"ice"|"bye"|"need-tunnel", "payload": {...}}
```

- `offer` / `answer` payload: `{"type": "offer"|"answer", "sdp": "..."}` —
  **plain objects only**. Never pass `RTCSessionDescription` or
  `RTCIceCandidate` across a bridge; pywebview's serializer breaks on them.
- `ice` payload: `{"candidate": "...", "sdpMid": "...", "sdpMLineIndex": n}`.
- `bye`: the sender is closing the session.
- `need-tunnel`: viewer gave up on direct + TURN; desktop starts the tunnel
  fallback and updates presence with the host.

Ordering: the receiver buffers `ice` until the matching description is
applied. Both sides keep candidates until the answer has been **sent**, not
just created.

Transport of the envelopes is site-specific and documented in
`docs/remote-direct-signaling.md` (site actions, auth, polling cadence).

## Session grant

The viewer asks the site for a session before offering:

```json
POST collect/rtc-session  →  {"session": "<32 hex>", "ice": [ {"urls": ..., "username": ..., "credential": ...} ], "direct_enabled": true, "desktop": {"version": "1.2.42", "online": true, "tunnel_host": "u-abc.uefnducky.org"|null}}
```

- `direct_enabled: false` → viewer goes straight to the tunnel path (kill switch).
- `ice` may be STUN-only when no TURN key is configured.

## Fallback ladder (viewer)

1. Direct (host / srflx). Budget 8 s from offer sent to `connected`.
2. TURN, if `ice` contained relay servers. Same budget, restarted.
3. `need-tunnel` → wait for presence `tunnel_host`, then load the tunnel
   page. Budget 45 s (tunnel start + DNS).
4. Explain and offer Retry.

Once connected, a `disconnected`/`failed` state re-enters the ladder at
step 1 without reloading the page; RPC calls queue for up to 30 s.

## Version rules

- Viewer bundle version must equal desktop version (from presence). If the
  desktop is older, the viewer shows "update the desktop app" and makes no
  RPC calls. If the viewer is older (stale cache), it reloads once.
- `PROTOCOL_VERSION` mismatch → viewer treats as "update the desktop app".

## Security

- Desktop accepts an offer only for `session` ids it obtained from the site
  for the logged-in user; anything else is dropped without reply.
- `rpc` enforces `REMOTE_DENY`. `blob` enforces the prefix allowlist.
- DTLS fingerprint of the desktop is published in presence
  (`dtls_fingerprint`); the viewer compares it to the answer's `a=fingerprint`
  and aborts on mismatch.
- TURN credentials expire in 10 minutes; the site mints one per session.
