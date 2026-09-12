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

One round trip, full ICE on both sides, no trickle. The site plugin runs as a
short subprocess per request and cannot hold a connection, so trickle ICE
would cost a mailbox write per candidate.

1. The viewer creates its peer connection and DataChannels, sets a
   `recvonly` video transceiver, creates the offer and **waits for ICE
   gathering to complete** (cap 2.5 s).
2. The viewer posts `{type:"ud-direct-offer", session, offer:{type,sdp}, ice, protocol}`
   to its parent (the site's `/ducky` page) with `postMessage`.
3. The parent calls the existing desktop mailbox: `desktop-rpc` with
   `method: "rtc_connect"`, `args: {session, offer, ice, protocol}`, then
   polls `desktop-rpc-result`. `rtc_connect` needs the `uefn-ducky.remote`
   permission and is allow-listed on both the site and the desktop.
4. The desktop's mailbox loop hands the offer to the WebView2 page as a
   `direct_rtc` panel event (`session`, `offer`, `ice`, `ts`). The page
   answers `sendonly` video, waits for its own ICE gathering, and returns
   the full answer through `direct_rtc_answer`. Stale events (`ts` older
   than 30 s, replayed from the event backlog) are ignored.
5. The mailbox result is `{answer:{type,sdp}, fingerprint, version, protocol}`;
   the parent posts `{type:"ud-direct-answer", session, answer, desktop:{version, protocol}}`
   back to the iframe. Errors come back as `{type:"ud-direct-error", session, error}`.
6. The viewer reports `{type:"ud-direct-report", state, reason, connect_ms, candidate}`
   as it goes; on `failed` the parent swaps the iframe to the tunnel host.

Local end-to-end runs skip the parent: `?direct=local&desktop=` makes the
viewer POST the same payload to `/__panel_api/direct_rtc_connect` on the
desktop's own panel server.

**Plain objects only.** Never pass `RTCSessionDescription` or
`RTCIceCandidate` across a bridge; pywebview's serializer breaks on them.

## Remote config

Before loading the panel iframe the `/ducky` page asks the site:

```json
POST collect/remote-config  →  {"direct_enabled": true, "panel_base": "https://panel.uefnducky.org", "protocol": 1, "ice": [ {"urls": "stun:…"}, {"urls": ["turn:…"], "username": …, "credential": …} ]}
```

- `direct_enabled: false` → the page goes straight to the tunnel path (kill switch;
  admins flip it with `admin-remote` action `save-direct`).
- `ice` is STUN-only until a Cloudflare Realtime TURN key is saved
  (`admin-remote` action `save-turn`); then it carries 10-minute TURN
  credentials minted per page load.
- `panel_base` is where the panel bundle lives: `<panel_base>/latest/` first,
  `<panel_base>/<desktop version>/` when the desktop is older than `latest`
  (the page checks `<panel_base>/versions.json`).

## Fallback ladder

1. Direct (host / srflx / relay, all in one ICE run since TURN servers are
   in the same config). Budget 8 s from answer set to `connected`.
2. On `failed`, the `/ducky` page removes the iframe and runs today's tunnel
   flow (`remote_endpoint` → `u-<id>.uefnducky.org` iframe). Nothing changes
   for that user compared to before this work.
3. A page that never reports anything within 30 s is treated as failed.

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
