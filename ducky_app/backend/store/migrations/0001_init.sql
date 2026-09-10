-- 0001: foundation (phase 0). Applied inside one transaction by db.migrate().

CREATE TABLE meta (
  key     TEXT PRIMARY KEY,
  value   TEXT NOT NULL,
  updated REAL NOT NULL
);

CREATE TABLE projects (
  id          TEXT PRIMARY KEY,            -- project slug, same rule as project_chats.project_slug
  path        TEXT NOT NULL,
  name        TEXT NOT NULL DEFAULT '',
  last_opened REAL NOT NULL DEFAULT 0,
  created     REAL NOT NULL,
  deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_projects_recent ON projects(deleted, last_opened DESC);

CREATE TABLE settings (
  key     TEXT PRIMARY KEY,
  value   TEXT NOT NULL,                   -- JSON; absence of a row means "default"
  updated REAL NOT NULL
);
