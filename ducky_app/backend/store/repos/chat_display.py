"""Read-only chat display reads: one account-scoped snapshot, newest first, opening
encrypted message parts only when a rendered field needs them. Kept apart from
chats.py (agent/export transcript reads never come here)."""
from __future__ import annotations

import json

from backend.store import account_rows, db
from backend.store.repos.chats import FMT_PARTS, _decode_parts, _interned, _owned, _part_reader


class _DisplayFields(dict):
    """Read-only UI projection: inflate only fields actually rendered, never cache
    it as a transcript or pass it to a writer. The owning read snapshot stays open."""

    def __init__(self, values, refs, read):
        super().__init__(values)
        self._refs = refs
        self._read = read

    def __getitem__(self, key):
        if key in self._refs:
            text = self._read(self._refs.pop(key))
            self[key] = json.loads(text) if text is not None else None
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self[key] if key in self else default


def _display_message(conn, row, account):
    """Open a shell and fetch individual encrypted parts on demand, not all of
    this conversation's blobs (nor all results of a many-tool turn)."""
    seq, body, fmt = row
    text = account_rows.open_text(str(body), account)
    envelope = json.loads(text, object_pairs_hook=_interned)
    if int(fmt) != FMT_PARTS:
        return int(seq), envelope

    def read(digest):
        def packed():
            part = conn.execute("SELECT data FROM message_parts WHERE hash=?", (digest,)).fetchone()
            return {} if part is None else {digest: bytes(part[0])}
        return _part_reader(packed, account)(digest)

    message = envelope['m']
    refs = {}
    blocks = {}
    for ref in envelope.get('x') or []:
        if len(ref) == 2:
            refs[ref[0]] = ref[1]
        elif len(ref) == 4 and ref[0] == 'blocks':
            blocks.setdefault(ref[1], {})[ref[2]] = ref[3]
        else:
            # Older/unusual envelope: preserve its full decoding semantics.
            return int(seq), _decode_parts(envelope, read)
    for index, fields in blocks.items():
        message['blocks'][index] = _DisplayFields(message['blocks'][index], fields, read)
    return int(seq), _DisplayFields(message, refs, read)


def display_messages(conv_id: str, *, before_seq: int | None = None):
    """Newest messages first from one account-scoped snapshot, with absolute
    sequence and last sequence. Close the generator after filling the UI page.
    This path is deliberately separate from agent/export transcript reads."""
    account = account_rows.current()
    conn = db.connect()
    own = not conn.in_transaction
    if own:
        conn.execute('BEGIN')
    try:
        if not _owned(conn, conv_id, account):
            return
        last = conn.execute('SELECT max(seq) FROM messages WHERE conv_id=?', (conv_id,)).fetchone()[0]
        if last is None:
            return
        end = int(last) if before_seq is None else min(int(last), before_seq)
        while end >= 0:
            rows = conn.execute(
                'SELECT seq, body, fmt FROM messages WHERE conv_id=? AND seq<=? ORDER BY seq DESC LIMIT 32',
                (conv_id, end),
            ).fetchall()
            if not rows:
                return
            for row in rows:
                seq, message = _display_message(conn, row, account)
                yield seq, int(last), message
            end = int(rows[-1][0]) - 1
    finally:
        if own:
            conn.execute('COMMIT')
