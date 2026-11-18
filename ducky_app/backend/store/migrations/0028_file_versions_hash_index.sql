-- The third subquery of the blob sweep (repos/ledger.sweep_blobs): every blob a file
-- version references. 0027 indexed the run_entries two.
CREATE INDEX IF NOT EXISTS ix_file_versions_hash ON file_versions(content_hash);
