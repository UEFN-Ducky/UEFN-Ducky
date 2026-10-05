"""The V8 sandbox Custom code nodes run in (mini-racer).

One isolate per run, always closed. The code is the node's module with its two
``export`` keywords blanked out in place, so line and column numbers in errors are the
ones in the editor. No eval / new Function (a V8 flag set once per process), no
WebAssembly, timers, require, fetch, process or files: the only way out is the frozen
``ducky`` object, whose calls run in Python on their own threads.

Every piece of JavaScript after setup runs inside an eval task this module tracks, so
Stop can end it from any thread, even while the isolate is busy (a ``while (true)``
after an ``await`` blocks mini-racer's event loop until the task is cancelled).
"""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import json
import logging
import math
import re
import threading
import time
from typing import Any, Callable, Mapping

from backend.automations.code_api import MAX_LOG_CHARS, MAX_RESULT_BYTES, MAX_SLEEP_S

_log = logging.getLogger("automations.jsrt")

FILE_NAME = "ducky-node.js"
_PREFIX = '"use strict";'
_FLAGS = ("--single-threaded", "--disallow-code-generation-from-strings")
_NODE_EXPORT = re.compile(r"^([ \t]*)(export)([ \t]+const[ \t]+node\b)", re.M)
_RUN_EXPORT = re.compile(r"^([ \t]*)(export[ \t]+default)([ \t]+(?:async[ \t]+)?function[ \t]*run\b)", re.M)
_FRAME = re.compile(re.escape(FILE_NAME) + r":(\d+):(\d+)")
_WHERE = re.compile(r"^(?:[^\n:]*):(\d+): (.*)$")
_PATHS = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|opt|private)/)[^\s'\"<>|:*?]*")
_STOP_GRACE_S = 1.5

_init_lock = threading.Lock()
_engine: Any = None
_engine_error = ""

Problem = dict[str, Any]
Host = Mapping[str, Callable[..., Any]]


class HostError(Exception):
    """A ducky call failed; the message goes to the JavaScript as an Error."""


class EngineMissing(RuntimeError):
    pass


def _load() -> Any:
    """mini-racer, started once per process with eval / new Function turned off."""
    global _engine, _engine_error
    with _init_lock:
        if _engine is not None:
            return _engine
        if _engine_error:
            raise EngineMissing(_engine_error)
        try:
            import py_mini_racer
            from py_mini_racer import LibAlreadyInitializedError, init_mini_racer

            try:
                init_mini_racer(flags=_FLAGS)
            except LibAlreadyInitializedError:
                pass  # someone else started it: every isolate checks eval is off before it runs code
        except Exception as exc:  # the DLL is missing or won't load
            _log.warning("Custom code engine unavailable: %s", exc)
            _engine_error = "Custom code can't run on this PC: its JavaScript engine is missing."
            raise EngineMissing(_engine_error) from exc
        _engine = py_mini_racer
        return _engine


def strip_exports(code: str) -> str:
    """Blank the two ``export`` keywords in place (same lines, same columns)."""
    code = _NODE_EXPORT.sub(lambda m: m.group(1) + " " * len(m.group(2)) + m.group(3), code, count=1)
    return _RUN_EXPORT.sub(lambda m: m.group(1) + re.sub(r"[^\n]", " ", m.group(2)) + m.group(3), code, count=1)


def sanitize(message: Any, limit: int = 500) -> str:
    """A short message for the person or the code: no traceback, no paths on this PC."""
    text = str(message or "").strip()
    if "Traceback (most recent call last)" in text:
        lines = [line for line in text.splitlines() if line.strip()]
        text = lines[-1] if lines else "failed"
    text = _PATHS.sub(lambda m: "…/" + re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1], text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _column(line: int, col: int) -> int:
    return max(1, col - len(_PREFIX)) if line == 1 else max(1, col)


def _where(text: str) -> tuple[int | None, int | None, str]:
    """Line, column and message from a V8 error report ("name.js:3: TypeError: …")."""
    head, *rest = (text or "").split("\n")
    match = _WHERE.match(head.strip())
    line, message = (int(match.group(1)), match.group(2)) if match else (None, head.strip())
    col = None
    frame = _FRAME.search(text)
    if frame and (line is None or int(frame.group(1)) == line):
        line, col = int(frame.group(1)), int(frame.group(2))
    elif line is not None and len(rest) >= 2 and "^" in rest[1]:
        col = rest[1].index("^") + 1
    if line is not None and col is not None:
        col = _column(line, col)
    return line, col, message


def check_syntax(code: str) -> list[Problem]:
    """Syntax errors in a Custom code module, without running any of it."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _check_syntax(code)
    # mini-racer would borrow the caller's running event loop; check on a thread of its own.
    box: list[list[Problem]] = []
    worker = threading.Thread(target=lambda: box.append(_check_syntax(code)), name="custom-code-check", daemon=True)
    worker.start()
    worker.join(10)
    return box[0] if box else [{"line": 1, "col": 1, "message": "Checking the code took too long.", "severity": "warning"}]


def _check_syntax(code: str) -> list[Problem]:
    try:
        engine = _load()
    except EngineMissing as exc:
        return [{"line": 1, "col": 1, "message": str(exc), "severity": "error"}]
    # Wrapped in a function that is never called: V8 parses all of it (inner functions
    # too) and reports the first syntax error.
    source = "(function(){" + _PREFIX + strip_exports(code) + "\n})"
    try:
        with engine.MiniRacer() as mr:
            mr.eval(source, timeout_sec=5)
    except engine.JSTimeoutException:
        return [{"line": 1, "col": 1, "message": "Checking the code took too long.", "severity": "warning"}]
    except engine.JSEvalException as exc:
        line, col, message = _where(str(exc))
        if line is not None and col is not None and line == 1:
            col = max(1, col - len("(function(){"))
        return [{"line": line or 1, "col": col or 1, "message": sanitize(message, 300), "severity": "error"}]
    return []


def _json(value: Any) -> str:
    """JSON the JavaScript side can parse (NaN / Infinity become null)."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
    except ValueError:
        return json.dumps(_finite(value), ensure_ascii=False, default=str)


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


_HARNESS = r"""
(function (notify, inputJson, settingsJson, nodesJson, runJson) {
  "use strict";
  const send = (message) => notify(JSON.stringify(message));
  const waiting = new Map();
  const state = { cancelled: false };
  let nextId = 1;
  const call = async (name, args) => {
    const id = nextId++;
    const reply = await new Promise((resolve) => { waiting.set(id, resolve); send([id, name, args]); });
    state.cancelled = !!reply.cancelled;
    if (!reply.ok) throw new Error(reply.error);
    return reply.value;
  };
  const text = (value) => {
    if (typeof value === "string") return value;
    try { const json = JSON.stringify(value); return json === undefined ? String(value) : json; }
    catch (e) { return String(value); }
  };
  const freeze = (value) => {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.freeze(value);
      for (const key of Object.keys(value)) freeze(value[key]);
    }
    return value;
  };
  const log = (...values) => { send([0, "log", values.map(text).join(" ")]); };
  const ducky = freeze({
    tool: (name, args) => call("tool", [String(name), args === undefined ? {} : args]),
    builtin: (type, input, config) => call("builtin", [String(type), input === undefined ? {} : input, config === undefined ? {} : config]),
    expr: (source, scope) => call("expr", [String(source), scope === undefined ? null : scope]),
    template: (value, scope) => call("template", [value, scope === undefined ? null : scope]),
    log,
    sleep: (seconds) => call("sleep", [Number(seconds)]),
    cancelled: () => state.cancelled,
    settings: JSON.parse(settingsJson),
    nodes: JSON.parse(nodesJson),
    run: JSON.parse(runJson),
  });
  const fixed = { configurable: false, enumerable: false, writable: false };
  Object.defineProperty(globalThis, "__ducky_deliver", { ...fixed, value: (id, raw) => {
    const resolve = waiting.get(id);
    if (!resolve) return;
    waiting.delete(id);
    resolve(JSON.parse(raw));
  } });
  Object.defineProperty(globalThis, "__ducky_go", { ...fixed, value: async () => {
    let done;
    try {
      if (typeof run !== "function") throw new Error("The code needs: export default async function run(input, ducky)");
      const out = await run(JSON.parse(inputJson), ducky);
      if (out !== undefined && out !== null && (typeof out !== "object" || Array.isArray(out))) {
        throw new Error("run must return an object, like { text: \"…\" }");
      }
      done = { ok: true, json: JSON.stringify(out === undefined ? null : out) };
    } catch (e) {
      const error = e instanceof Error;
      done = { ok: false, name: error ? String(e.name || "Error") : "", message: error ? String(e.message) : text(e), stack: error ? String(e.stack || "") : "" };
    }
    send([0, "done", done]);
  } });
  globalThis.console = freeze({ log, info: log, warn: log, error: log, debug: log });
  // The only way out is ducky: no WebAssembly, and nothing that runs JavaScript later
  // outside a task Stop can end (timers, Atomics.waitAsync, finalizers).
  for (const name of ["WebAssembly", "Atomics", "SharedArrayBuffer", "FinalizationRegistry", "WeakRef"]) delete globalThis[name];
  setTimeout = undefined;
  clearTimeout = undefined;
})
"""


class _Run:
    """One run of one code module: owns its isolate, event loop and worker thread."""

    def __init__(self, code: str, input_json: str, settings_json: str, nodes_json: str, run_json: str,
                 host: Host, cancel_event: threading.Event | None, memory_mb: int):
        self.script = _PREFIX + strip_exports(code) + "\nvoid 0;\n//# sourceURL=" + FILE_NAME
        self.args = (input_json, settings_json, nodes_json, run_json)
        self.host = host
        self.cancel_event = cancel_event or threading.Event()
        self.memory = max(16, int(memory_mb)) * 1024 * 1024
        self.captured = contextvars.copy_context()
        self.finished = threading.Event()
        # Guards the isolate between the loop thread and stop(): the running task ids,
        # and whether the isolate was closed (after which nothing may touch it).
        self.lock = threading.Lock()
        self.tasks: set[Any] = set()
        self.native: tuple[Any, Any] | None = None  # (dll, context pointer) while open
        self.log: list[str] = []
        self.log_size = 0
        self.log_sent = 0.0
        self.log_pending = False
        self.outcome: dict[str, Any] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.main: asyncio.Task[Any] | None = None
        self.mr: Any = None
        self.done: asyncio.Future[Any] | None = None
        self.deliveries: set[asyncio.Task[Any]] = set()
        self.stopping = False

    # ------------------------------------------------------------------ worker thread

    def work(self) -> None:
        loop = asyncio.new_event_loop()
        self.loop = loop
        try:
            self.main = loop.create_task(self._main())
            loop.run_until_complete(self.main)
        except asyncio.CancelledError:
            self._finish({"ok": False, "stopped": True, "error": {"message": "Stopped", "line": None, "col": None}})
        except Exception as exc:  # never a traceback to the person
            _log.exception("custom code run failed")
            self._finish(_failure(sanitize(exc)))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            if self.log_pending:
                self._stream_log()
            self.finished.set()
            with _running_lock:
                _running.discard(self)

    async def _main(self) -> None:
        engine = _load()
        self.mr = mr = engine.MiniRacer()
        ctx = mr._ctx  # pinned mini-racer 0.14: its notification and cancelable-task plumbing
        with self.lock:
            self.native = (ctx._dll, ctx._ctx)
        try:
            mr.set_hard_memory_limit(self.memory)
            self.done = asyncio.get_running_loop().create_future()
            if mr.eval("(function(){try{return (0, eval)('1')===1}catch(e){return false}})()") is not False:
                self._finish(_failure("Custom code can't run safely here: restart Ducky and try again."))
                return
            with ctx._register_js_notification(self._message) as notify:
                mr.eval(_HARNESS)(notify, *self.args)
                await self._eval(self.script)
                await self._eval("__ducky_go(); void 0")
                done = await self.done
            self._finish(self._result(done))
        except engine.JSOOMException:
            self._finish(self._out_of_memory())
        except engine.JSEvalException as exc:
            if self.stopping:
                raise asyncio.CancelledError from exc
            if mr.was_hard_memory_limit_reached():
                self._finish(self._out_of_memory())
            else:
                line, col, message = _where(str(exc))
                self._finish(_failure(sanitize(message, 300), line, col))
        finally:
            # Every eval task must be over before the isolate is freed.
            pending = [task for task in self.deliveries if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            with self.lock:
                self.native = None
            self.mr = None
            mr.close()

    def _out_of_memory(self) -> dict[str, Any]:
        return _failure(f"Out of memory: custom code may use at most {self.memory // (1024 * 1024)} MB.")

    async def _eval(self, code: str) -> Any:
        """mr.eval_cancelable, keeping the task id so stop() can end it from another thread."""
        ctx = self.mr._ctx
        handle = ctx._python_to_value_handle(code)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def done(val_handle: Any) -> None:
            if future.done():
                return
            try:
                future.set_result(ctx._value_handle_to_python(val_handle))
            except Exception as exc:
                future.set_exception(exc)

        with ctx._register_cancelable_mr_task_callback(done) as callback_id:
            with self.lock:
                task_id = ctx._dll.mr_eval(ctx._ctx, handle.raw, callback_id)
                self.tasks.add(task_id)
            try:
                return await future
            finally:
                with self.lock:
                    self.tasks.discard(task_id)
                    ctx._dll.mr_cancel_task(ctx._ctx, task_id)

    def _message(self, val_handle: Any) -> None:
        """A message from the JavaScript (on the loop): a ducky call, a log line or the end."""
        try:
            raw = self.mr._ctx._value_handle_to_python(val_handle)[0]
            rid, name, args = json.loads(raw)
        except Exception:
            return
        if name == "log":
            self._log_line(str(args))
        elif name == "done":
            if self.done is not None and not self.done.done():
                self.done.set_result(args)
        else:
            threading.Thread(target=self._host_call, args=(int(rid), str(name), args), name="custom-code-call", daemon=True).start()

    def _host_call(self, rid: int, name: str, args: Any) -> None:
        try:
            value = self.captured.copy().run(self._dispatch, name, list(args) if isinstance(args, list) else [args])
            reply = {"ok": True, "value": value}
        except HostError as exc:
            reply = {"ok": False, "error": sanitize(exc) or f"{name} failed"}
        except Exception as exc:
            _log.warning("custom code ducky.%s failed: %s", name, exc, exc_info=True)
            reply = {"ok": False, "error": sanitize(exc) or f"{name} failed"}
        reply["cancelled"] = self.cancel_event.is_set()
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, rid, _json(reply))
        except RuntimeError:
            pass  # the run already ended

    def _dispatch(self, name: str, args: list[Any]) -> Any:
        if name == "sleep":
            try:
                seconds = min(max(float(args[0] if args else 0), 0.0), float(MAX_SLEEP_S))
            except (TypeError, ValueError):
                raise HostError("sleep needs a number of seconds") from None
            if self.cancel_event.wait(seconds if math.isfinite(seconds) else 0):
                raise HostError("Stopped")
            return None
        fn = self.host.get(name)
        if fn is None or name == "log":
            raise HostError(f"ducky.{name} isn't available")
        return fn(*args)

    def _deliver(self, rid: int, payload: str) -> None:
        if self.mr is None or self.done is None or self.done.done() or self.stopping:
            return

        async def deliver() -> None:
            try:
                await self._eval(f"__ducky_deliver({rid}, {json.dumps(payload)}); void 0")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # out of memory, or a runaway after the call: the run ends
                if self.done is not None and not self.done.done():
                    self.done.set_exception(exc)

        task = asyncio.ensure_future(deliver())
        self.deliveries.add(task)
        task.add_done_callback(self.deliveries.discard)

    def _log_line(self, line: str) -> None:
        self.log.append(line)
        self.log_size += len(line) + 1
        while self.log_size > MAX_LOG_CHARS * 2 and len(self.log) > 1:
            self.log_size -= len(self.log.pop(0)) + 1
        # Live view: at most ~10 updates a second; the last one is sent when the run ends.
        if time.monotonic() - self.log_sent >= 0.1:
            self._stream_log()
        else:
            self.log_pending = True

    def _stream_log(self) -> None:
        self.log_sent, self.log_pending = time.monotonic(), False
        stream = self.host.get("log")
        if stream is None:
            return
        try:
            self.captured.copy().run(stream, self.log_text())
        except Exception:
            _log.debug("custom code log stream failed", exc_info=True)

    def log_text(self) -> str:
        text = "\n".join(self.log)
        return text if len(text) <= MAX_LOG_CHARS else "…" + text[-(MAX_LOG_CHARS - 1):]

    def _result(self, done: Any) -> dict[str, Any]:
        if not isinstance(done, dict):
            return _failure("The code ended without a result.")
        if not done.get("ok"):
            line, col = None, None
            frame = _FRAME.search(str(done.get("stack") or ""))
            if frame:
                line, col = int(frame.group(1)), _column(int(frame.group(1)), int(frame.group(2)))
            name, message = str(done.get("name") or ""), str(done.get("message") or "")
            text = message if name in ("", "Error") else f"{name}: {message}"
            return _failure(sanitize(text or "failed", 300), line, col)
        raw = str(done.get("json") or "null")
        if len(raw.encode("utf-8")) > MAX_RESULT_BYTES:
            return _failure(f"run returned more than {MAX_RESULT_BYTES // (1024 * 1024)} MB.")
        value = json.loads(raw)
        return {"ok": True, "value": value if isinstance(value, dict) else {}}

    def _finish(self, outcome: dict[str, Any]) -> None:
        if self.outcome is None:
            self.outcome = outcome

    # ------------------------------------------------------------------ caller thread

    def stop(self) -> None:
        """End the run from another thread: terminate what V8 runs, then the loop's task."""
        self.stopping = True
        with self.lock:
            if self.native is not None:
                dll, pointer = self.native
                for task_id in list(self.tasks):
                    try:
                        dll.mr_cancel_task(pointer, task_id)
                    except Exception:
                        _log.debug("custom code stop: cancel failed", exc_info=True)
        loop, main = self.loop, self.main
        if loop is not None and main is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(main.cancel)
            except RuntimeError:
                pass


_running: set[_Run] = set()
_running_lock = threading.Lock()


@atexit.register
def _stop_all() -> None:
    """An isolate still open when Python exits hangs the exit: end every run first."""
    with _running_lock:
        jobs = list(_running)
    for job in jobs:
        job.cancel_event.set()
        job.stop()
    for job in jobs:
        job.finished.wait(_STOP_GRACE_S)


def _failure(message: str, line: int | None = None, col: int | None = None) -> dict[str, Any]:
    return {"ok": False, "error": {"message": message, "line": line, "col": col}}


def run(
    code: str,
    input: dict[str, Any],
    *,
    settings: dict[str, Any],
    nodes: dict[str, Any],
    run_info: dict[str, Any],
    host: Host,
    cancel_event: threading.Event | None,
    memory_mb: int = 256,
) -> dict[str, Any]:
    """Run a Custom code module's ``run(input, ducky)`` in a fresh isolate.

    ``host`` maps ducky call names (tool, builtin, expr, template) to Python functions
    called on their own threads inside a copy of this thread's context; they raise
    :class:`HostError` (or anything) to throw in the JavaScript. ``host["log"]``, if
    given, gets the whole log text after each line (on the run's loop, keep it quick).

    Returns ``{"ok", "value", "log", "error": {"message","line","col"} | None,
    "stopped", "ms"}``; ``value`` is what run returned (an object)."""
    started = time.monotonic()
    try:
        _load()
    except EngineMissing as exc:
        return {"ok": False, "value": None, "log": "", "error": {"message": str(exc), "line": None, "col": None}, "stopped": False, "ms": 0}
    job = _Run(code, _json(input or {}), _json(settings or {}), _json(nodes or {}), _json(run_info or {}), host, cancel_event, memory_mb)
    worker = threading.Thread(target=job.work, name="custom-code", daemon=True)
    with _running_lock:
        _running.add(job)  # until its worker ends (it removes itself)
    worker.start()
    stopped = False
    while not job.finished.wait(0.05):
        if job.cancel_event.is_set():
            stopped = True
            job.stop()
            job.finished.wait(_STOP_GRACE_S)
            break
    outcome = job.outcome or _failure("Stopped" if stopped else "The code ended without a result.")
    if stopped or (not outcome.get("ok") and job.cancel_event.is_set()):  # e.g. a sleep Stop woke up
        outcome = {"ok": False, "stopped": True, "error": {"message": "Stopped", "line": None, "col": None}}
    return {
        "ok": bool(outcome.get("ok")),
        "value": outcome.get("value") if outcome.get("ok") else None,
        "log": job.log_text(),
        "error": None if outcome.get("ok") else outcome.get("error"),
        "stopped": bool(outcome.get("stopped")),
        "ms": int((time.monotonic() - started) * 1000),
    }
