"""Skill pack folder layout, archive format and provenance names.

Each pack is a standard skill folder that works as-is in Claude Code, Cursor,
or any agent that understands the SKILL.md convention:

    skill_packs/<pack_id>/
        SKILL.md            # frontmatter (name, description, metadata) + core guidance
        references/<id>.md  # optional detail files, loaded on demand
"""

from __future__ import annotations

import re

EXPORT_FORMAT = "ducky-skill-pack"
EXPORT_FORMAT_VERSION = 3
EXPORT_META_FILE = "export.json"
LICENSE_FILE = "LICENSE.txt"
DEFAULT_COMMERCIAL_LICENSE = "All Rights Reserved"

SKILL_PACKS_DIR = "skill_packs"
PACK_FILE = "SKILL.md"
REFERENCES_DIR = "references"
CORE_ID = "core"

# Provenance stamps in SKILL.md / references frontmatter metadata.
ORIGIN_USER = "user"
ORIGIN_STORE = "store"
ORIGIN_SHIPPED = "shipped"
ORIGIN_PLUGIN = "plugin"
SOURCE_STORE = "store"
SOURCE_IMPORT = "import"

_PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SUBSKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PLAIN_SCALAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./ -]*$")
