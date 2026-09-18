-- 0008: Pipelines share the automations table (kind + description).

ALTER TABLE automations ADD COLUMN kind TEXT NOT NULL DEFAULT 'automation';
ALTER TABLE automations ADD COLUMN description TEXT NOT NULL DEFAULT '';
