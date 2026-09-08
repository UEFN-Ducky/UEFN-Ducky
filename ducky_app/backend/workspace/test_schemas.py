"""Every schema is a valid Draft 2020-12 document and every fixture validates against it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMAS_DIR = Path(__file__).parent / "schemas"
FIXTURES_DIR = SCHEMAS_DIR / "fixtures"
SCHEMA_NAMES = ("changeset_run", "lane_set", "changeset_export", "file_history_entry")


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def validate(name: str, instance: dict) -> list[str]:
    validator = Draft202012Validator(load_schema(name))
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in validator.iter_errors(instance)]


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_fixture_validates(name: str) -> None:
    assert validate(name, load_fixture(name)) == []


# Contract fixtures that are test tables rather than schema instances.
TABLE_FIXTURES = {"lane_glob_cases"}


def test_every_schema_has_a_fixture_and_vice_versa() -> None:
    schemas = {p.name.removesuffix(".schema.json") for p in SCHEMAS_DIR.glob("*.schema.json")}
    fixtures = {p.stem for p in FIXTURES_DIR.glob("*.json")} - TABLE_FIXTURES
    assert schemas == fixtures == set(SCHEMA_NAMES)


def test_legacy_history_entry_without_attribution_still_validates() -> None:
    legacy = {
        "id": "1700000000000",
        "path": "Content/Verse/a.verse",
        "saved_at": 1700000000,
        "bytes": 3,
        "preview": "abc",
        "content_hash": "0123456789abcdef",
        "content": "abc",
    }
    assert validate("file_history_entry", legacy) == []


def test_changeset_run_rejects_unknown_status_and_extra_keys() -> None:
    run = load_fixture("changeset_run")
    run["status"] = "flying"
    assert any("flying" in e for e in validate("changeset_run", run))
    run = load_fixture("changeset_run")
    run["entries"][0]["surprise"] = 1
    assert validate("changeset_run", run)
