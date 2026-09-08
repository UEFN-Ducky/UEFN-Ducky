"""The host's mutation table and the listener's capture table must agree.

They live in different processes and answer different questions — "does this
change anything?" here, "how do I read the before-state?" there — but they are
about the same commands. A capture spec for a command the host thinks is a read
would be silently dropped: :func:`editor_record.build` returns None for reads, so
the listener's work would be captured, sent, and thrown away.

Loading ``ducky_capture`` here is itself part of the assertion. It keeps every
``unreal`` and ``listener.*`` import inside a function precisely so this test can
exist; a stray top-level import would fail at the ``exec_module`` below.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from backend.workspace.editor_ops import EDITOR_OPS, MUT_READ, classify, is_mutation

CAPTURE_MODULE = Path(__file__).resolve().parents[2] / "uefn_listener" / "listener" / "ducky_capture.py"


def load_capture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ducky_capture_parity", CAPTURE_MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def capture() -> ModuleType:
    return load_capture()


def test_the_capture_module_loads_without_unreal(capture: ModuleType) -> None:
    assert capture.CAPTURE, "the listener's capture table is empty"
    assert capture.CAPTURE_VERSION >= 1


def test_every_captured_command_is_a_mutation_on_the_host(capture: ModuleType) -> None:
    reads = sorted(name for name in capture.CAPTURE if not is_mutation(name))
    assert reads == [], (
        f"the listener captures {reads}, but the host classifies them as reads, so "
        "every sidecar they produce would be discarded"
    )


def test_every_captured_command_is_in_the_host_table(capture: ModuleType) -> None:
    missing = sorted(name for name in capture.CAPTURE if name not in EDITOR_OPS)
    assert missing == [], (
        f"{missing} have capture specs but no row in EDITOR_OPS, so they would be "
        "classified as unknown-opaque and lose their kind and facet"
    )


def test_the_two_tables_agree_on_kind_and_facet(capture: ModuleType) -> None:
    """The host's kind/facet is the fallback when a sidecar does not carry them."""
    disagreements = []
    for name, spec in capture.CAPTURE.items():
        host = classify(name)
        if host.mutates == MUT_READ:
            continue  # covered by its own test, with a better message
        if (host.kind, host.slot) != (spec.kind, spec.facet):
            disagreements.append(f"{name}: host {host.kind}/{host.slot} vs listener {spec.kind}/{spec.facet}")
    assert disagreements == []


def test_a_captured_creation_is_marked_as_one_on_the_host(capture: ModuleType) -> None:
    """`created` on the listener feeds the host's delete carve-out; it must expect it."""
    for name, spec in capture.CAPTURE.items():
        if spec.created is None:
            continue
        assert classify(name).creates, (
            f"{name} reports what it created, but the host does not have it marked as "
            "creating anything, so a revert would never try to remove it"
        )
