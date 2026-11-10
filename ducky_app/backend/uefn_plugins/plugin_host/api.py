"""The host surface a plugin's ``register(api)`` receives."""

from __future__ import annotations

import functools
import json
import threading
from typing import Any, Callable

from backend.process_role import is_app


class _ChangesetApi:
    """Plug a desktop-plugin mutation into the Changes ledger.

    Two ways to record, same shape: return ``_ducky`` on a tool result, or call
    ``api.changeset.record(...)`` for a batch that is not one MCP call.
    """

    def __init__(self, api: "_PluginApi") -> None:
        self._api = api

    def record(
        self,
        *,
        command: str,
        kind: str,
        ident: str,
        facet: str = "",
        before: Any = None,
        inverse: list[dict[str, Any]] | None = None,
        created: list[dict[str, Any]] | None = None,
        targets: list[dict[str, Any]] | None = None,
        summary: str = "",
        revertable: str = "auto",
        reason: str = "",
        params: dict[str, Any] | None = None,
        slot: str = "",
    ) -> None:
        """Record one mutation. Never raises; never fails the tool."""
        try:
            from backend.workspace.editor_ops import slot_path
            from backend.workspace.editor_record import record as record_change

            prog = self._api.plugin_id
            sidecar = {
                "program": prog,
                "kind": kind,
                "facet": facet,
                "slot": slot or slot_path(kind, ident, facet, program=prog),
                "before": before,
                "inverse": list(inverse or []),
                "created": list(created or []),
                "targets": list(targets or [{"kind": kind, "id": ident, "label": ident, "path": ident}]),
                "revertable": revertable,
                "reason": reason,
                "summary": summary or command.replace("_", " "),
            }
            record_change(command, params or {}, {"_ducky": sidecar}, ok=True)
        except Exception:
            pass


class _CryptoApi:
    """AES-256-GCM from the app's own cryptography, so a plugin that seals data never
    depends on which modules the EXE happens to bundle (the shared-spaces provider died
    on every team change when 1.2.392 shipped without ``ciphers.aead``)."""

    @staticmethod
    def aead_seal(key: bytes, nonce: bytes, data: bytes, aad: bytes | None = None) -> bytes:
        """Ciphertext plus its 16-byte tag. ``key`` is 32 bytes, ``nonce`` 12."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(bytes(key)).encrypt(bytes(nonce), bytes(data), None if aad is None else bytes(aad))

    @staticmethod
    def aead_open(key: bytes, nonce: bytes, data: bytes, aad: bytes | None = None) -> bytes:
        """The plaintext. Raises cryptography's ``InvalidTag`` when the data, key, nonce or
        ``aad`` don't match, the same as importing cryptography directly."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(bytes(key)).decrypt(bytes(nonce), bytes(data), None if aad is None else bytes(aad))


class _PluginApi:
    """Narrow host surface passed to plugin register(api)."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def is_app(self) -> bool:
        """True in the app window's process. ``register()`` also runs in every agent host and
        tool server, so start threads, timers, servers and ports only ``if api.is_app()``."""
        return is_app()

    def log(self, message: str) -> None:
        """One line in Settings → Logs under this plugin (personal data is scrubbed out)."""
        _log.info("[%s] %s", self.plugin_id, message)
        try:
            from frontend.error_log import record_activity

            record_activity(self.plugin_id, message)
        except Exception:
            pass

    def is_enabled(self) -> bool:
        """True when this plugin is enabled in Settings → Store."""
        return is_plugin_enabled(self.plugin_id)

    def listener(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one command to the live UEFN listener (sequencer / editor tools)."""
        from backend.bridge import REQUEST_TIMEOUT, send_command

        return send_command(
            command,
            params or {},
            timeout=REQUEST_TIMEOUT if timeout is None else float(timeout),
        )

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call any Ducky MCP tool in-process, under the same approval rules the agent gets.

        Routes through the shared dispatcher (``execute_tool``), so a destructive or
        outward tool still shows its Allow/Deny card and a plan-mode mutator is still
        refused. Returns the tool's parsed result (dict/list when it is JSON, else the
        raw string) and raises ``ValueError`` on failure. Safe from a plugin's sync code.
        """
        import asyncio

        from backend.agent.tools import execute_tool

        tool_name = (name or "").strip()
        if not tool_name:
            raise ValueError("call_tool requires a tool name")
        args = arguments if isinstance(arguments, dict) else {}

        async def _run() -> Any:
            return await execute_tool(tool_name, args)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_run())
        else:
            # A loop is already running on this thread — run on a helper thread so we
            # never nest event loops.
            box: dict[str, Any] = {}

            def _worker() -> None:
                try:
                    box["result"] = asyncio.run(_run())
                except BaseException as exc:  # noqa: BLE001 — surfaced below
                    box["error"] = exc

            t = threading.Thread(target=_worker, name=f"plugin-call-tool-{self.plugin_id}", daemon=True)
            t.start()
            t.join()
            if "error" in box:
                raise box["error"]
            result = box.get("result")

        if getattr(result, "ok", False):
            data = getattr(result, "data", None)
            if isinstance(data, str):
                text = data.strip()
                if text.startswith(("{", "[")):
                    try:
                        return json.loads(text)
                    except ValueError:
                        return data
            return data
        raise ValueError(getattr(result, "error", None) or f"tool failed: {tool_name}")

    @property
    def changeset(self) -> "_ChangesetApi":
        """Record a mutation in the Changes ledger so Revert can unwind it."""
        return _ChangesetApi(self)

    @property
    def crypto(self) -> "_CryptoApi":
        """``aead_seal(key, nonce, data, aad)`` / ``aead_open(...)``: AES-256-GCM from the app."""
        return _CryptoApi()

    def local_dir(self, name: str = "") -> str:
        """A plain folder on this PC for files the plugin can rebuild: media proxies, thumbnails,
        caches. Not synced and not encrypted (``data`` is for the plugin's real records); play a
        file from it with ``media_url(path)``. Erased with the plugin's data."""
        from backend.uefn_plugins.panel_presence import plugin_local_dir

        return str(plugin_local_dir(self.plugin_id, name))

    def media_url(self, path: str) -> str:
        """A seekable link (HTTP Range) to a local media file, for this plugin's panel to play:
        an original the person picked with ``files.pick`` or a proxy the plugin made.
        '' when the file type can't be played in the panel. Loopback only: not on a phone."""
        from backend.automations.files import media_url

        return media_url(path)

    def panel_state(self) -> dict[str, Any]:
        """Whether this plugin's panel is open and on screen: ``{open, visible, since, panels}``.
        Panel code runs only while its tab is the visible one; a tool that needs it can say so."""
        from backend.uefn_plugins.panel_presence import panel_state

        return panel_state(self.plugin_id)

    @property
    def live(self) -> Any:
        """Live editing on a team copy: ``join(room)``, ``leave()``, ``send(topic, data,
        to="")``, ``on(topic, callback)``, ``state()``. Changes made through ``api.data`` go
        out with sync, and the group pulls each one as soon as it's committed. See
        ``backend.uefn_plugins.live``."""
        from backend.uefn_plugins.live import LiveApi

        return LiveApi(self.plugin_id)

    @property
    def data(self) -> Any:
        """Host data service: docs (one JSON doc per entity) and files in the active
        scope. Never pick your own folder: rows and files are per account and scope,
        and shared scopes sync. See ``backend.uefn_plugins.scopes.PluginData``."""
        from backend.uefn_plugins.scopes import PluginData

        _DATA_PLUGINS.add(self.plugin_id)
        return PluginData(self.plugin_id)

    @property
    def storage(self) -> Any:
        """The account's General storage, shared by the person and every plugin: ``put_file(name,
        bytes, folder="")``, ``put(name, value, folder="")`` (a note or any JSON), ``items(folder,
        query)``, ``get(id, with_data=False)``, ``find(name, folder)``, ``delete(id)``, ``where()``.
        Synced with Your team like plugin data; what a plugin keeps here stays when it goes. See
        ``backend.uefn_plugins.general_storage``."""
        from backend.uefn_plugins.general_storage import StorageApi

        return StorageApi(self.plugin_id)

    @property
    def search(self) -> Any:
        """Make this plugin's own records findable by meaning and keywords: ``upsert(id, title,
        text, meta)``, ``delete(id)``, ``clear()``, ``query(text, limit)``. Only this plugin's
        records in its active data scope; removed on uninstall. Expose them to agents through
        one of the plugin's own tools. See ``backend.search.records.SearchApi``."""
        from backend.search.records import SearchApi

        return SearchApi(self.plugin_id)

    def http_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> Any:
        """JSON HTTP call (stdlib urllib). Vendor URLs and status enums stay in the plugin."""
        from backend.util.http import http_json as _http_json

        return _http_json(
            method,
            url,
            headers=headers,
            json_body=json_body,
            timeout=timeout,
        )

    def poll(
        self,
        get_status: Callable[..., Any],
        *,
        done: Callable[..., bool],
        failed: Callable[..., bool],
        interval: float = 5,
        max_attempts: int = 120,
    ) -> Any:
        """Poll get_status until done/failed. Raises TimeoutError."""
        from backend.util.http import poll as _poll

        return _poll(
            get_status,
            done=done,
            failed=failed,
            interval=interval,
            max_attempts=max_attempts,
        )

    def encode_image(self, path: str) -> str:
        """Local image path → data URI."""
        from backend.util.http import encode_image as _encode_image

        return _encode_image(path)

    def resolve_image(self, image: str) -> str:
        """Pass through http(s)/data URIs; encode local paths."""
        from backend.util.http import resolve_image as _resolve_image

        return _resolve_image(image)

    def chat_reference(
        self,
        *,
        trigger: str,
        id: str,
        label: str,
        href: str,
        group: str = "",
        description: str = "",
    ) -> bool:
        """Add one @ or / row. A bad href is dropped. Same idea as ``tool()``."""
        entry = normalize_chat_reference(
            {
                "trigger": trigger,
                "id": id,
                "label": label,
                "href": href,
                "group": group,
                "description": description,
            },
            self.plugin_id,
        )
        if not entry:
            return False
        with _LOCK:
            _CONTRIBUTIONS["chat_references"].append(entry)
        return True

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        intent: str | None = None,
        listener: bool = False,
    ) -> Any:
        """Register an MCP tool on the app's shared FastMCP server.

        Auto-gates on plugin enable + per-chat opt-in. Tool names are tracked for
        intent unlock and the Settings → MCPs tab — no manifest listing required.

        Pass ``listener=True`` only when the tool cannot start without the UEFN editor open:
        it is then refused while UEFN is closed. Every other tool runs whatever UEFN's state
        (a call that does reach the editor still fails fast on its own).
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = (name or getattr(func, "__name__", "") or "").strip()
            if not tool_name:
                raise ValueError("api.tool requires a function name or name=")

            from backend.server import mcp

            pid = self.plugin_id
            existing = mcp._tool_manager.get_tool(tool_name)
            if existing is not None:
                owner = _PLUGIN_TOOL_OWNER.get(tool_name)
                # Another plugin already owns this name — refuse.
                if owner is not None and owner != pid:
                    _log.error(
                        "[%s] tool name collision: %s — skipping registration",
                        self.plugin_id,
                        tool_name,
                    )
                    return func
                # Same plugin after reload_plugins() (MCP entry survives invalidate):
                # re-track ownership without double-registering on FastMCP.
                _record_plugin_api_tool(pid, tool_name, intent=intent, listener=listener)
                _log.debug("[%s] MCP tool already registered: %s", pid, tool_name)
                return func

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                if not uefn_agent_tools_allowed(pid):
                    raise ValueError(
                        f"Plugin '{pid}' tools are disabled — enable it in Settings → Store"
                    )
                blocked = sensitive_tool_block_reason(tool_name)
                if blocked:
                    raise ValueError(blocked)
                if pid in _DATA_PLUGINS:
                    try:  # a sync in the app process changed its data: its on_change hears it first
                        from backend.uefn_plugins.scopes import deliver_data_changes

                        deliver_data_changes(pid)
                    except Exception:  # noqa: BLE001 — the tool runs anyway
                        _log.debug("delivering data changes to %s failed", pid, exc_info=True)
                from backend.uefn_plugins.scopes import caller, write_group

                # One tool call is one change to a shared copy, made by the calling agent
                # (an MCP client of the bridge is an agent even without a ducky's name).
                actor, ducky = caller("user" if is_app() else "agent")
                import time as _time

                started = _time.time()
                with plugin_tool_slot(pid), write_group(pid, actor=actor, ducky=ducky, message=tool_name):
                    # A plugin that keeps declared folders: checked once its call marker is down
                    # (a move of its folders either waited for this call or finished first).
                    fresh = _folder_plugin_call(pid, tool_name, wrapper) if pid in _FOLDER_EPOCH else None
                    if fresh is not None:  # reloaded here: the call runs on the new plugin
                        return fresh(*args, **kwargs)
                    result = func(*args, **kwargs)
                try:
                    # A picture made in a chat shows inline in that turn's reply.
                    from frontend.ui_web.generated_images import note_tool_images

                    note_tool_images(tool_name, result)
                except Exception:
                    _log.debug("could not keep %s pictures for the chat", tool_name, exc_info=True)
                try:
                    # What the call made goes into Storage under this plugin (Local or the team).
                    from backend.uefn_plugins.auto_storage import after_plugin_tool

                    after_plugin_tool(pid, tool_name, result, started)
                except Exception:
                    _log.debug("could not keep %s outputs in Storage", tool_name, exc_info=True)
                # Plugins on the host data service: say which copy the tool read or changed.
                # ponytail: dict results only; string results would need parsing.
                if pid in _DATA_PLUGINS and isinstance(result, dict) and "scope" not in result:
                    from backend.uefn_plugins.scopes import active_scope, scope_name

                    result = {**result, "scope": scope_name(active_scope(pid))}
                return _with_panel_note(pid, result)

            mcp.tool(name=tool_name)(wrapper)
            _record_plugin_api_tool(pid, tool_name, intent=intent, listener=listener)
            _log.debug("[%s] MCP tool registered: %s", pid, tool_name)
            return wrapper

        if fn is not None:
            return decorator(fn)
        return decorator

    def register_tts(self, synthesize_fn: Any) -> None:
        """Register ``synthesize(text, voice_id) -> {audio_base64, mime}`` for this plugin."""
        register_tts_synthesizer(self.plugin_id, synthesize_fn)
        self.log("TTS synthesizer registered")

    def register_media_generator(self, kind: str, fn: Any, describe: dict[str, Any] | None = None) -> None:
        """Register a ``kind`` (image | image_edit | video | music) maker: ``fn(prompt, **options)``.

        ``describe`` = ``{label, secret_key?}``; it is listed by ``media_generators(kind)``
        only while ``secret_key`` has a saved credential.
        """
        register_media_generator(self.plugin_id, kind, fn, describe)
        self.log(f"{kind} generator registered")

    def asset_pack_path(self, pack_id: str) -> str | None:
        """Folder of this plugin's unpacked asset pack ``pack_id`` (plugin.json
        ``asset_packs``), or None until it has downloaded, passed its sha256 and unpacked."""
        from backend.uefn_plugins.asset_packs import asset_pack_path

        path = asset_pack_path(self.plugin_id, pack_id)
        return str(path) if path else None

    def asset_packs(self) -> list[dict[str, Any]]:
        """Progress of this plugin's asset packs for its panel: one
        ``{id, bytes, state, done, total, error, path?}`` per pack this PC needs."""
        from backend.uefn_plugins.asset_packs import status

        return status(self.plugin_id)

    def ensure_asset_packs(self) -> bool:
        """Fetch missing packs from the Store in the background (resuming a partial
        download). False when none are missing or a fetch is already running."""
        from backend.uefn_plugins.asset_packs import start_install
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(self.plugin_id) or {}
        if all(row["state"] == "ready" for row in self.asset_packs()):
            return False
        return start_install(self.plugin_id, manifest, str(manifest.get("store_slug") or ""))

    def connection(self, fn: Any, *, label: str = "", program: str = "") -> None:
        """Show this plugin in the header Connections menu.

        ``fn()`` must be cheap (socket/health only). Return ``{online, detail}``
        or ``{connected, detail}``. Same row gates revert when the ledger
        program matches ``program`` (default: this plugin id).
        """
        register_connection_probe(self.plugin_id, fn, label=label, program=program)
        self.log("connection probe registered")

    def people(self, fn: Any, *, label: str = "") -> None:
        """Show who this plugin knows is around in the header's Online tab, under ``label``.

        ``fn()`` must be cheap (return cached data, never wait on the network). Return a
        list of ``{id, name, detail, online}``; online people count toward the header badge.
        """
        register_people_provider(self.plugin_id, fn, label=label)
        self.log("people provider registered")

    def register_secret_test(self, secret_key: str, test_fn: Any) -> None:
        """Register Settings → Test for a secret field (``test_fn(api_key) -> {ok, detail}``)."""
        register_secret_tester(self.plugin_id, secret_key, test_fn)
        self.log(f"Secret tester registered: {secret_key}")

    def register_automation_node(self, node_type: str, handler: Any = None) -> Any:
        """Register a handler for a contributed automations node type.

        ``api.register_automation_node("email.send", fn)`` or decorator form.
        """
        from backend.automations.plugin import register_node

        def decorator(fn: Any) -> Any:
            register_node(self.plugin_id, node_type, fn)
            _log.debug("[%s] Automation node registered: %s", self.plugin_id, node_type)
            return fn

        if handler is not None:
            return decorator(handler)
        return decorator

    def register_pipeline_node(self, node_type: str, handler: Any = None) -> Any:
        """Alias of register_automation_node — same handler table, both palettes."""
        return self.register_automation_node(node_type, handler)

    def emit_automation(self, trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fire enabled workflows on this PC whose trigger matches ``trigger_id``
        (plugin API name kept from before Workflows)."""
        from backend.automations.runner import emit_trigger

        return emit_trigger(trigger_id, payload or {})

    def emit_hook(self, hook_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fire one of this plugin's ``contributes.hooks``: the sound the user put on it plays."""
        from backend.uefn_plugins.plugin_app_events import emit_hook

        return emit_hook(self.plugin_id, hook_id, payload)

    def set_appearance_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch Appearance to one of this plugin's own ``appearance.profiles`` (by its id)."""
        from backend.uefn_plugins.plugin_app_events import set_appearance_profile

        return set_appearance_profile(self.plugin_id, profile_id)

    def spotlight(
        self,
        *,
        window: str = "uefn",
        box: dict[str, Any] | None = None,
        title: str = "",
        body: str = "",
        steps: list[dict[str, Any]] | None = None,
        click: bool = False,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Spotlight a control in UEFN or another program. Same call as ``ducky_ui_show``.

        ``window`` is ``"uefn"``, a title pattern, or an hwnd. ``box`` is
        ``{x, y, w, h}`` as fractions of that window (0 to 1). The desktop dims
        and only the hole can be clicked. ``wait`` blocks until the user closes it
        and returns ``reason`` (``done``, ``close``, or ``esc``) and ``step``.
        """
        from backend.tools.panel.panel_ui import show

        return show(
            window=window, box=box, title=title, body=body, steps=steps, click=click, wait=wait,
        )

    def register_panel_rpc(self, name: str, fn: Any) -> None:
        """Register a PanelApi / bridge method: ``plugin_call(plugin_id, name, params)``.

        ``fn`` receives ``**params`` from the caller. Prefer returning plain dicts
        (never raise across the JS bridge unless the host should surface an error).
        """
        register_panel_rpc(self.plugin_id, name, fn)
        _log.debug("[%s] Panel RPC registered: %s", self.plugin_id, name)

    def register_tts_voices(self, list_voices_fn: Any) -> None:
        """Register ``list_voices() -> [{id, label}]`` — dynamic voices for the picker.

        Runs at runtime (e.g. fetch an account's cloned voices from a web API) and is
        merged on top of the static manifest ``tts.voices``. Keep it fast/cached — it is
        polled when a voice dropdown opens.
        """
        register_tts_voices_lister(self.plugin_id, list_voices_fn)
        self.log("TTS voices lister registered")

    def provide_spaces(self, impl: Any) -> None:
        """Provide shared spaces (a :class:`backend.uefn_plugins.spaces.Provider`): which
        spaces the account has, their sync, labels and web pages. One provider at a time,
        from a Store-signed install."""
        from backend.uefn_plugins import spaces

        spaces.register(self.plugin_id, impl)
        self.log("Shared spaces provider registered")

    def register_llm_provider(
        self,
        provider_id: str,
        factory: Any,
        *,
        fetch_models: Any = None,
        test_key_model: str = "",
        test_key: Any = None,
        tool_schema: str = "",
        key_optional: bool = False,
        normalize_secret: Any = None,
        **extra: Any,
    ) -> None:
        """Register an LLM provider factory for Settings → LLMs / make_provider."""
        register_llm_provider_factory(
            self.plugin_id,
            provider_id,
            factory,
            fetch_models=fetch_models,
            test_key_model=test_key_model,
            test_key=test_key,
            tool_schema=tool_schema,
            key_optional=key_optional,
            normalize_secret=normalize_secret,
            **extra,
        )
        self.log(f"LLM provider registered: {provider_id}")

    def register_coding_agent(
        self,
        agent_id: str,
        factory: Any,
        *,
        complete_one_shot: Any = None,
        test_key: Any = None,
        resolve_api_fallback: Any = None,
        aliases: list[str] | tuple[str, ...] | None = None,
        skills_dir: Any = None,
        native_skills: bool = False,
        thinking_env: Any = None,
        normalize_model: Any = None,
        before_launch: Any = None,
        on_needs_login: Any = None,
        settings_defaults: dict[str, Any] | None = None,
        install_help: str = "",
        supports_batch_complete: bool = False,
        **extra: Any,
    ) -> None:
        """Register a coding-agent adapter factory (detect/launch)."""
        register_coding_agent_factory(
            self.plugin_id,
            agent_id,
            factory,
            complete_one_shot=complete_one_shot,
            test_key=test_key,
            resolve_api_fallback=resolve_api_fallback,
            aliases=aliases,
            skills_dir=skills_dir,
            native_skills=native_skills,
            thinking_env=thinking_env,
            normalize_model=normalize_model,
            before_launch=before_launch,
            on_needs_login=on_needs_login,
            settings_defaults=settings_defaults,
            install_help=install_help,
            supports_batch_complete=supports_batch_complete,
            **extra,
        )
        self.log(f"Coding agent registered: {agent_id}")

    def register_ide_hookup(self, kind: str, *, label: str = "", auto_apply: bool = True) -> None:
        """Own IDE MCP + skills Apply for this gateway (Settings → LLMs detail).

        When ``auto_apply`` is True, best-effort write the UEFN MCP bridge and
        deploy skills into that IDE if its config path is reachable.
        """
        register_ide_hookup_factory(self.plugin_id, kind, label=label)
        self.log(f"IDE hookup registered: {kind}")
        if not auto_apply:
            return
        # Bridge stdio process must never rewrite mcp.json — Cursor reconnects and
        # cancels tools. Panel / ship_newest owns IDE apply.
        if not is_app():
            self.log(f"IDE auto-apply skipped ({kind}): not the app window")
            return
        from frontend.ide_apply import auto_apply_skip_reason

        reason = auto_apply_skip_reason()
        if reason:
            self.log(f"IDE auto-apply skipped ({kind}): {reason}")
            return
        # Off the plugin-load thread: config merge + skill deploy is disk-bound and
        # nothing in the UI needs it. Inline, three gateways serialized ~60s of
        # writes ahead of _LOADED, so the Store/Settings panes sat empty that long.
        _queue_ide_auto_apply(kind, self.log)


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.folders import _folder_plugin_call  # noqa: E402
from backend.uefn_plugins.plugin_host.gateways import (  # noqa: E402
    _queue_ide_auto_apply,
    register_coding_agent_factory,
    register_ide_hookup_factory,
    register_llm_provider_factory,
)
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled  # noqa: E402
from backend.uefn_plugins.plugin_host.panel_rpc import _with_panel_note, register_panel_rpc  # noqa: E402
from backend.uefn_plugins.plugin_host.services import (  # noqa: E402
    register_connection_probe,
    register_media_generator,
    register_people_provider,
    register_secret_tester,
    register_tts_synthesizer,
    register_tts_voices_lister,
)
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _CONTRIBUTIONS,
    _DATA_PLUGINS,
    _FOLDER_EPOCH,
    _LOCK,
    _PLUGIN_TOOL_OWNER,
    _log,
)
from backend.uefn_plugins.plugin_host.tool_registry import (  # noqa: E402
    _record_plugin_api_tool,
    plugin_tool_slot,
    sensitive_tool_block_reason,
    uefn_agent_tools_allowed,
)
from backend.uefn_plugins.plugin_host.ui_registry import normalize_chat_reference  # noqa: E402
