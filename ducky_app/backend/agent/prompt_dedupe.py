"""Exact-block prompt dedupe — strip duplicate paste-sized chunks, not words."""

from __future__ import annotations

DEFAULT_MIN_CHARS = 80


def _split_blocks(text: str) -> list[str]:
    """Split on blank lines; keep fenced ```…``` regions atomic."""
    lines = text.split("\n")
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal buf
        if buf:
            blocks.append("\n".join(buf))
            buf = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                # Starting a fence: close any open prose block first.
                flush()
                in_fence = True
                buf.append(line)
            else:
                buf.append(line)
                flush()
                in_fence = False
            continue
        if in_fence:
            buf.append(line)
            continue
        if stripped == "":
            flush()
            continue
        buf.append(line)
    flush()
    return blocks


def dedupe_exact_blocks(text: str, *, min_chars: int = DEFAULT_MIN_CHARS) -> str:
    """Keep first occurrence of each exact paste-sized block; leave short repeats.

    Blocks are blank-line separated paragraphs, or whole fenced code regions.
    Matching is exact after ``\\r\\n`` → ``\\n`` normalization (not fuzzy / word-level).
    """
    if not text:
        return text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _split_blocks(normalized)
    if len(blocks) < 2:
        return normalized

    seen: set[str] = set()
    out: list[str] = []
    for block in blocks:
        if len(block) >= min_chars:
            if block in seen:
                continue
            seen.add(block)
        out.append(block)
    return "\n\n".join(out)


if __name__ == "__main__":
    big = "A" * 80
    assert dedupe_exact_blocks(f"{big}\n\n{big}") == big
    assert dedupe_exact_blocks("ok\n\nok") == "ok\n\nok"
    fenced = "```\n" + ("x" * 80) + "\n```"
    assert dedupe_exact_blocks(f"{fenced}\n\n{fenced}") == fenced
    print("prompt_dedupe ok")
