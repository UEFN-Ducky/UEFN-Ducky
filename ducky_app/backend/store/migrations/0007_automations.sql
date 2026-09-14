-- 0007: Automations (desktop workflow graphs + last-N run log).

CREATE TABLE automations (
  id       TEXT PRIMARY KEY,
  name     TEXT NOT NULL DEFAULT '',
  enabled  INTEGER NOT NULL DEFAULT 1,
  graph    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
  runs     TEXT NOT NULL DEFAULT '[]',
  updated  REAL NOT NULL DEFAULT 0,
  last_run REAL NOT NULL DEFAULT 0
);
