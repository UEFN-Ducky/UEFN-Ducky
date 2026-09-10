-- 0002: phase 1 — settings and the small singleton stores.

-- API keys, DuckyOS session, plugin secrets. One DPAPI blob per row; the
-- database never holds a plaintext secret (docs/architecture/store.md, Encryption).
CREATE TABLE secrets (
  name    TEXT PRIMARY KEY,
  blob    BLOB NOT NULL,
  updated REAL NOT NULL
);

-- Window bounds, dock layout, editor workspace per project: JSON documents
-- keyed "bounds:<window>", "dock:<window>", "editor:<project slug>".
CREATE TABLE workspace_state (
  key     TEXT PRIMARY KEY,
  value   TEXT NOT NULL,
  updated REAL NOT NULL
);

-- Small cached documents that used to be one JSON file each
-- (models_cache.json, mcp_command_manifest.json).
CREATE TABLE cache_docs (
  key     TEXT PRIMARY KEY,
  value   TEXT NOT NULL,
  updated REAL NOT NULL
);

-- Store plugin key/value data (plugin_host_api cache + prefs). encrypted=1 rows
-- hold a base64 DPAPI blob in value instead of JSON.
CREATE TABLE plugin_kv (
  plugin_id TEXT NOT NULL,
  key       TEXT NOT NULL,
  value     TEXT NOT NULL,
  encrypted INTEGER NOT NULL DEFAULT 0,
  updated   REAL NOT NULL,
  PRIMARY KEY (plugin_id, key)
);
