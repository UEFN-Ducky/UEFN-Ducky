"""Self-check: translation batch parser."""

from __future__ import annotations


def main() -> None:
    from backend.tools.integrations.translation_service import parse_batch_response

    mapping, missing = parse_batch_response(
        '{"Support":"Soporte","Store":"Tienda"}',
        ["Support", "Store", "Account"],
    )
    assert mapping["Support"] == "Soporte"
    assert mapping["Store"] == "Tienda"
    assert missing == ["Account"]

    mapping2, missing2 = parse_batch_response(
        "```json\n{\"Hi\":\"Hola\"}\n```",
        ["Hi"],
    )
    assert mapping2 == {"Hi": "Hola"} and missing2 == []

    print("test_translation_service: ok")


if __name__ == "__main__":
    main()


def test_self_check(monkeypatch) -> None:
    """Collected wrapper: this file was a `main()` self-check pytest never ran (store test plan, step 0).

    The self-check mutates os.environ (LOCALAPPDATA and friends) without restoring it;
    monkeypatch snapshots the environment so later tests keep the isolated AppData.
    """
    import os as _os

    _snapshot = dict(_os.environ)
    try:
        main()
    finally:
        for _k in list(_os.environ):
            if _k not in _snapshot:
                monkeypatch.delenv(_k, raising=False)
        for _k, _v in _snapshot.items():
            if _os.environ.get(_k) != _v:
                monkeypatch.setenv(_k, _v)
