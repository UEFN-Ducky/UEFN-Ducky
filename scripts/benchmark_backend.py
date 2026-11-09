"""Repeatable source-only measurements; never opens the user's application data.

Run with the same interpreter and --output before/after. Timings include every
sample, including fast calls. tracemalloc peaks are Python allocations, not RSS.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
import weakref


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'ducky_app'))
    with tempfile.TemporaryDirectory(prefix='ducky-backend-bench-') as tmp:
        os.environ['LOCALAPPDATA'] = tmp
        os.environ['APPDATA'] = tmp
        os.environ['DUCKY_SEARCH_AUTOINDEX'] = '0'
        from backend.store import db
        from backend.store.repos import chats
        from frontend.ui_web import call_pools, panel_api

        report = {'python': sys.version, 'samples': 7, 'scope': 'isolated source, synthetic data'}
        api = panel_api.PanelApi.__new__(panel_api.PanelApi)
        api.list_running_agents = lambda: []
        for n in (100, 1000, 10000):
            cid = f'bench-{n}'
            chats.conv_save('_no_project', {'id': cid, 'title': 'Benchmark'}, messages=[
                {'role': 'user', 'text': f'{i}: ' + 'x' * 1024} for i in range(n)
            ])
            elapsed, peaks, decoded = [], [], []
            decode_name = '_display_message' if hasattr(chats, '_display_message') else '_message'
            original = getattr(chats, decode_name)
            count = [0]
            def counted(*a, **kw):
                count[0] += 1
                return original(*a, **kw)
            setattr(chats, decode_name, counted)
            try:
                for _ in range(7):
                    chats._opened_forget(cid)
                    gc.collect()
                    count[0] = 0
                    tracemalloc.start()
                    start = time.perf_counter()
                    rows = api.load_messages(cid, limit=50)
                    elapsed.append((time.perf_counter() - start) * 1000)
                    peaks.append(tracemalloc.get_traced_memory()[1])
                    tracemalloc.stop()
                    decoded.append(count[0])
                    assert len(rows) == 50
                report[f'chat_{n}'] = {'median_ms': statistics.median(elapsed),
                    'p95_ms': max(elapsed), 'peak_python_bytes': max(peaks), 'decoded': decoded}
            finally:
                setattr(chats, decode_name, original)

        call_pools._drop_cached(None)
        for i in range(2000):
            call_pools.call_shared('group_members', lambda key: 'x' * 4096, [str(i)])
        report['cache_after_2000_keys'] = len(call_pools._cache)
        time.sleep(1.2)
        report['cache_after_ttl_idle'] = len(call_pools._cache)

        class Payload:
            pass
        payload = Payload()
        ref = weakref.ref(payload)
        done = threading.Event()
        pool = call_pools._Pool('benchmark', 1)
        pool.submit(lambda value: done.set(), payload)
        del payload
        assert done.wait(5)
        time.sleep(.05)
        gc.collect()
        report['idle_worker_retains_payload'] = ref() is not None
        # Connections must close before TemporaryDirectory removes the database on Windows.
        db.reset_for_tests()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
