"""Skill packs in the open Agent Skills layout (SKILL.md + references/*.md).

Compatibility shim: the code lives in the modules below, one job each. Import from
them directly; this re-export goes away once every caller has moved.

    layout       folder layout, archive format and provenance names
    passes       one skills pass (memo shared by every lookup inside it)
    frontmatter  SKILL.md frontmatter, provenance tags, LICENSE.txt text
    ownership    which desktop plugin owns which pack
    discovery    where packs live, pack ids
    manifest     a pack's manifest, its cache, subskill bodies
    selection    deny-list and per-pack subskill allowlists
    revision     fingerprint of the skill index on disk
    seeding      bundled packs -> AppData
    skill_index  the agent's skill index and lazy subskill reads
    authoring    create / edit / delete packs and subskills
    archive      export / import .ducky-skill-pack zips
"""

from __future__ import annotations

import sys
import types

from backend.skills.archive import (  # noqa: F401
    _apply_export_meta_commercial,
    export_skill_pack_bytes,
    export_skill_pack_to_zip,
    import_skill_pack_from_bytes,
    import_skill_pack_from_zip,
    validate_skill_pack_archive,
)
from backend.skills.authoring import (  # noqa: F401
    _appdata_pack_file,
    _bump_pack_version,
    _reference_path,
    create_skill_node,
    create_skill_pack,
    create_subskill,
    delete_skill_pack,
    delete_subskill,
    get_skill_pack_files,
    get_skill_pack_graph,
    reset_skill_pack,
    save_pack_manifest,
    save_skill_layout,
    save_skill_node,
    save_subskill,
)
from backend.skills.discovery import (  # noqa: F401
    _bundled_skill_packs_dir,
    _is_bundled_pack_id,
    _is_pack_dir,
    _pack_roots,
    _reference_files,
    appdata_dir,
    appdata_skill_packs_dir,
    bundled_skill_packs_dir,
    list_pack_ids,
    normalize_pack_id,
    normalize_subskill_id,
)
from backend.skills.frontmatter import (  # noqa: F401
    _commercial_fields_from_meta,
    _dump_scalar,
    _index_tag_label,
    _is_user_origin_file,
    _license_text_for_pack,
    _meta_map,
    _origin_from_meta,
    _pack_origin_tag,
    _parse_scalar,
    _read,
    _read_json,
    _stamp_ref_origin,
    _subskill_origin_tag,
    pack_index_tag,
    parse_frontmatter,
    serialize_frontmatter,
    subskill_index_tag,
)
from backend.skills.layout import (  # noqa: F401
    _PACK_ID_RE,
    _PLAIN_SCALAR_RE,
    _SUBSKILL_ID_RE,
    CORE_ID,
    DEFAULT_COMMERCIAL_LICENSE,
    EXPORT_FORMAT,
    EXPORT_FORMAT_VERSION,
    EXPORT_META_FILE,
    LICENSE_FILE,
    ORIGIN_PLUGIN,
    ORIGIN_SHIPPED,
    ORIGIN_STORE,
    ORIGIN_USER,
    PACK_FILE,
    REFERENCES_DIR,
    SKILL_PACKS_DIR,
    SOURCE_IMPORT,
    SOURCE_STORE,
)
from backend.skills.manifest import (  # noqa: F401
    _manifest_cache_get,
    _manifest_cache_put,
    _manifest_from_skill_dir,
    _pack_stamp,
    _pack_stamp_uncached,
    _subskill_rel_path,
    list_root_subskills,
    list_skill_packs,
    list_subskills,
    load_pack_manifest,
    read_subskill_body,
    subskill_body_path,
    subskill_parent_id,
)
from backend.skills.ownership import (  # noqa: F401
    _is_plugin_owned_pack_id,
    _plugin_owned_skill_map,
    plugin_owner_for_skill,
)
from backend.skills.passes import _PASS_MEMO, pass_cached, pass_memo, shared_pass_memo  # noqa: F401
from backend.skills.revision import (  # noqa: F401
    _NEW_CHAT_SKILL_TEXT,
    _skills_changed,
    invalidate_skills_revision,
    skills_revision,
)
from backend.skills.seeding import (  # noqa: F401
    _MOVED_TO_PLUGIN_SKILLS,
    SEED_MARKER_FILE,
    _copy_pack_tree,
    _current_app_version,
    _last_seeded_app_version,
    _materialize_pack_to_appdata,
    _merge_pack_tree,
    _pack_version,
    _seed_marker_path,
    _write_pack_license_file,
    _write_seeded_app_version,
    seed_skill_packs,
)
from backend.skills.selection import (  # noqa: F401
    _ACTIVE_DISABLED_PACKS,
    _ACTIVE_ENABLED_SUBSKILLS,
    SkillSelection,
    active_disabled_packs,
    active_enabled_subskills,
    default_selection_from_settings,
    default_skill_selection,
    default_subskills_for_pack,
    merge_selection,
    normalize_disabled_packs,
    normalize_enabled_packs,
    normalize_enabled_skills,
    normalize_enabled_subskills,
    resolve_conversation_selection,
    set_active_disabled_packs,
    set_active_enabled_subskills,
)
from backend.skills.skill_index import (  # noqa: F401
    _ALWAYS_ON_PROMPT_CAP,
    _WHOLE_READ_MAX,
    _find_section,
    _fit_whole_sections,
    _is_specialist_pack,
    _label_with_blurb,
    _specialist_opted_in,
    _subskill_allowed,
    build_skill_prompt,
    build_skill_prompt_compact,
    conversation_skill_text,
    load_skill_text,
    skill_read_subskill,
)
from backend.skills.versions import next_skill_version, skill_version, skill_version_key  # noqa: F401


# Before the split every function here looked its helpers up in this module, so a test
# that patched ``store.X`` changed what every caller saw. Keep that true for the shim's
# life: setting a name here also sets it in each split module still holding the same object.
def _split_modules() -> tuple[types.ModuleType, ...]:
    from backend.skills import (
        archive,
        authoring,
        discovery,
        frontmatter,
        layout,
        manifest,
        ownership,
        passes,
        revision,
        seeding,
        selection,
        skill_index,
    )

    return (archive, authoring, discovery, frontmatter, layout, manifest, ownership, passes,
            revision, seeding, selection, skill_index)


_MISSING = object()


class _ShimModule(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        old = self.__dict__.get(name, _MISSING)
        if old is not _MISSING:
            for module in _split_modules():
                if module.__dict__.get(name, _MISSING) is old:
                    setattr(module, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ShimModule
