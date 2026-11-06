"""python -m frontend — GUI by default, ``bridge`` for IDE-spawned MCP stdio, or ``agent-host`` (supervisor child)."""

import os
import sys

# Same as frontend.launcher.limit_blas_threads, for the agent host that skips the launcher.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "agent-host":
    # Before the launcher: no single-instance lock, window, IDE apply or skill sync.
    from frontend.agent_host.main import main as agent_host_main

    raise SystemExit(agent_host_main(sys.argv[2:]))

if __name__ == "__main__" and "--backend" in sys.argv[1:]:
    # Ducky-Launcher.exe's backend: no logo splash, low-memory prompt or window (no Tk, no webview).
    from frontend.native_host.headless import main as backend_main

    raise SystemExit(backend_main())

from frontend.launcher import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
