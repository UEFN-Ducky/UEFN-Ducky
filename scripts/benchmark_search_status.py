"""Cold status probe in isolated AppData; Python allocations, not process RSS."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ducky_app'))
with tempfile.TemporaryDirectory(prefix='ducky-status-bench-') as tmp:
    os.environ['LOCALAPPDATA'] = os.environ['APPDATA'] = tmp
    os.environ['DUCKY_SEARCH_AUTOINDEX'] = '0'
    from backend.search import embedder, indexer
    from backend.store import db
    db.connect()  # database migration is not part of the model/status measurement
    tracemalloc.start()
    start = time.perf_counter()
    result = indexer.status()
    report = {'elapsed_ms': (time.perf_counter() - start) * 1000,
              'peak_python_bytes': tracemalloc.get_traced_memory()[1],
              'loaded_by_status': isinstance(embedder._loaded, embedder.Embedder),
              'model': result.get('model'), 'model_error': result.get('model_error')}
    tracemalloc.stop()
    db.reset_for_tests()
Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, indent=2))
