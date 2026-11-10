"""Text to vector for skill and tool search.

The model is a model2vec static embedding (``minishlab/potion-base-8M``, MIT): a
token embedding matrix. Encoding a text is tokenizing it, looking up each token's
row and taking the mean, then L2-normalising — the same steps as model2vec's
``StaticModel.encode``, done here with numpy and ``tokenizers`` so the app ships no
model2vec, safetensors, torch or onnxruntime. ``build/fetch_search_model.py`` writes
the files; the frozen app carries them under ``models/search``.

Nothing here touches the network. A missing model folder makes :func:`get_embedder`
return ``None`` and search runs on keywords alone.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Sequence

_log = logging.getLogger("uefn_ducky.search")

MATRIX_FILE = "embeddings.f16.npy"
TOKENIZER_FILE = "tokenizer.json"
# model2vec's default encode() limit; texts are cut to this many tokens.
MAX_TOKENS = 512

_lock = threading.Lock()
_loaded: Any = None  # Embedder, or the reason it could not load (str)


def model_dir() -> Path | None:
    """Folder holding the model files, or None when this build has none."""
    candidates: list[Path] = []
    override = os.environ.get("DUCKY_SEARCH_MODEL_DIR", "").strip()
    if override:
        candidates.append(Path(override))
    try:
        from frontend.bundle_root import packaged_data_root

        root = packaged_data_root()
    except Exception:  # noqa: BLE001
        root = None
    if root is not None:
        candidates.append(root / "models" / "search")
    # Source checkout: build/fetch_search_model.py writes here.
    candidates.append(Path(__file__).resolve().parents[3] / "build" / "search-model")
    for folder in candidates:
        if (folder / MATRIX_FILE).is_file() and (folder / TOKENIZER_FILE).is_file():
            return folder
    return None


class Embedder:
    """One loaded model. ``encode`` is thread-safe."""

    def __init__(self, folder: Path) -> None:
        import numpy as np
        from tokenizers import Tokenizer

        # No rayon thread pool (~5 MB private for good): batches here are small and the
        # indexer runs in the background, so one thread is fast enough.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.folder = folder
        # Mapped, not read: the 15 MB matrix stays file-backed (shared, pageable) instead
        # of private bytes in every process that searches.
        self.matrix = np.load(folder / MATRIX_FILE, allow_pickle=False, mmap_mode="r")
        self.dim = int(self.matrix.shape[1])
        config: dict[str, Any] = {}
        try:
            config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.normalize = bool(config.get("normalize", True))
        # Stored with every vector: a different model re-embeds every row.
        self.model_id = _model_id(folder)
        self.tokenizer = Tokenizer.from_file(str(folder / TOKENIZER_FILE))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(MAX_TOKENS)
        vocab = self.tokenizer.get_vocab()
        self.unk_id = _unk_id(self.tokenizer, vocab)
        lengths = sorted(len(tok) for tok in vocab)
        median = lengths[len(lengths) // 2] if lengths else 4
        # model2vec cuts the text to max_length * median token length before tokenizing.
        self.max_chars = MAX_TOKENS * max(1, int(median))

    def encode(self, texts: Sequence[str]):
        """float32 array (len(texts), dim); rows are unit length (zero for empty text)."""
        import numpy as np

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        if not texts:
            return out
        cut = [str(t or "")[: self.max_chars] for t in texts]
        encodings = self.tokenizer.encode_batch(cut, add_special_tokens=False)
        for i, enc in enumerate(encodings):
            ids = [t for t in enc.ids if t != self.unk_id] if self.unk_id is not None else list(enc.ids)
            if not ids:
                continue
            out[i] = self.matrix[ids].astype(np.float32).mean(axis=0)
        if self.normalize:
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            out /= norms
        return out


def _unk_id(tokenizer: Any, vocab: dict[str, int]) -> int | None:
    try:
        model = json.loads(tokenizer.to_str()).get("model") or {}
        unk = model.get("unk_token")
        if isinstance(unk, str) and unk in vocab:
            return vocab[unk]
    except (ValueError, AttributeError):
        pass
    return vocab.get("[UNK]")


def get_embedder() -> Embedder | None:
    """The process-wide model, loaded on first use; None when unavailable (logged once)."""
    global _loaded
    current = _loaded
    if isinstance(current, Embedder):
        return current
    if isinstance(current, str):
        return None
    with _lock:
        if isinstance(_loaded, Embedder):
            return _loaded
        if isinstance(_loaded, str):
            return None
        folder = model_dir()
        if folder is None:
            _loaded = "no model folder"
            _log.warning("search: no meaning model in this build; keyword search only")
            return None
        try:
            _loaded = Embedder(folder)
        except Exception as exc:  # noqa: BLE001 — search must keep working on keywords
            _loaded = f"{type(exc).__name__}: {exc}"
            _log.warning("search: meaning model failed to load (%s); keyword search only", type(exc).__name__)
            return None
        return _loaded


def unavailable_reason() -> str:
    return _loaded if isinstance(_loaded, str) else ""


def _model_id(folder: Path) -> str:
    manifest = {}
    try:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    name = str(manifest.get('model') or 'potion-base-8M').rsplit('/', 1)[-1]
    rev = str(manifest.get('revision') or '')[:8]
    return f'{name}@{rev}' if rev else name


def model_status() -> dict[str, Any]:
    """Availability is not validation/loading. A settings read never allocates
    the matrix or tokenizer; actual search keeps get_embedder's fallback."""
    current = _loaded
    if isinstance(current, Embedder):
        return {'model': current.model_id, 'model_loaded': True, 'model_available': True, 'model_error': ''}
    folder = model_dir()
    return {'model': _model_id(folder) if folder is not None else '', 'model_loaded': False,
            'model_available': folder is not None,
            'model_error': current if isinstance(current, str) else ('' if folder is not None else 'no model folder')}


def reset_for_tests() -> None:
    global _loaded
    with _lock:
        _loaded = None
