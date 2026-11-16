-- The blob sweep (repos/ledger.sweep_blobs) runs at the end of every agent turn and lists
-- every before/after blob a run entry references. Without these indexes it scanned the
-- whole run_entries table, `doc` column included (635 MB on one PC), inside the write
-- transaction: 0.6-0.9 s warm and far longer cold, and every chat save waited behind it
-- (Nov 16 2026: chat save batches of 15.7 s, 9.8 s and 6.0 s that wrote 0 bytes).
CREATE INDEX IF NOT EXISTS ix_entries_before_blob ON run_entries(before_blob) WHERE before_blob<>'';
CREATE INDEX IF NOT EXISTS ix_entries_after_blob ON run_entries(after_blob) WHERE after_blob<>'';
