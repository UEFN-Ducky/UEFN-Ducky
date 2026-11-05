"""python -m frontend — GUI by default, ``bridge`` for IDE-spawned MCP stdio, or ``agent-host`` (supervisor child)."""

import os
import sys

# Same as frontend.launcher.limit_blas_threads, for the agent host that skips the launcher.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "agent-host":
    # Before the launcher: no single-instance lock, window, IDE apply or skill sync.
    from frontend.agent_host.main import main as agent_host_main

    raise SystemExit(agent_host_main(sys.argv[2:]))

from frontend.launcher import main

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
