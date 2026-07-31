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
