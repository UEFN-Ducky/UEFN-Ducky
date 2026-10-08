---
description: "Worked plugin examples for app extensions: Text to Image backends, AI providers, sounds and hooks, walkthroughs"
metadata:
  order: 5
  label: "Plugin examples: images, AI, sounds, tours"
  default_enabled: false
  load_condition: "Building a plugin that adds an image generator, an AI model provider, sounds, app hooks or a first-run walkthrough"
---

# Plugin examples: images, AI, sounds, tours

Every plugin still needs `agent.tools`, a tool per action, a workflow node plus a
template that uses it, and `skills/<id>/SKILL.md` (`ai_plugins`).

## 1. Image generators (Text to Image backends)

A plugin can add a backend to the built-in **Text to Image** node
(`image.generate`). The node lists every backend the plugins turned on here declare,
plus each AI gateway's own image node, and each one names its own cost. Check
`list_workflow_nodes` → `image.generate` → `backends` first: the user may already have
the one they want.

```json
"secret_keys": ["acme_images_key"],
"contributes": {
  "agent.tools": { "category": "acme_images", "intent_pattern": "\\b(acme images?)\\b",
                   "destructive_tools": ["acme_images_generate"] },
  "settings.tabs": [{ "id": "Acme Images", "label": "Acme Images", "icon": "duck", "ui": "sections" }],
  "settings.sections": [{ "tab": "Acme Images", "id": "key", "title": "Acme Images",
    "properties": [{ "id": "acme_images_key", "type": "secret", "label": "API key", "testable": true }] }],
  "automations": {
    "image_generators": [{
      "id": "acme_flash", "label": "Acme Flash", "tool": "acme_images_generate",
      "prompt_arg": "prompt", "args": { "model": "flash" },
      "paid": true, "cost": "$0.04 an image",
      "config_fields": [{ "id": "size", "label": "Size", "type": "select", "options": ["1024x1024", "1536x1024", "1024x1536"] }]
    }],
    "nodes": [{ "id": "acme_images.generate", "label": "Acme image", "group": "Acme Images", "exec": false,
      "config_fields": [{ "id": "spend", "label": "Spend ($0.04 an image)", "type": "boolean" }],
      "inputs": [{ "id": "prompt", "label": "Prompt", "type": "text", "required": true }],
      "outputs": [{ "id": "image", "label": "Image", "type": "image" }] }],
    "templates": [{ "id": "acme-images-try", "label": "Try Acme Flash", "graph": {
      "nodes": [
        { "id": "q", "type": "input.text", "x": 0, "y": 0, "config": { "value": "A cozy duck pond at sunset" } },
        { "id": "g", "type": "acme_images.generate", "x": 300, "y": 0, "config": {} },
        { "id": "p", "type": "util.preview", "x": 600, "y": 0, "config": {} }],
      "edges": [
        { "source": "q", "target": "g", "kind": "data", "source_pin": "text", "target_pin": "prompt" },
        { "source": "g", "target": "p", "kind": "data", "source_pin": "image", "target_pin": "value" }] } }]
  }
}
```

| `image_generators` field | What |
|---|---|
| `id`, `label` | The backend's id (what `config.backend` names) and its name in the picker |
| `tool` | Your MCP tool that makes the picture |
| `prompt_arg` | Which argument gets the prompt (default `prompt`) |
| `args` | Fixed arguments always passed (a model name, a style) |
| `cost` | What it costs, in words, shown in the picker and in the spend message |
| `credits` | Or a credit estimate instead of `cost` |
| `paid` | `true`: the node's Spend switch must be on even when `credits` is 0 |
| `config_fields` | Its own settings in the node's details; their values are passed as arguments |

The node calls your tool with the prompt, `args`, the chosen settings, `wait=True`,
`output_dir=<the run's folder>` and, when paid, `confirm_spend=True`. The tool must
save the picture in `output_dir` and return `downloaded: [paths]`.

```python
from __future__ import annotations

import tempfile
import time
import urllib.request
from pathlib import Path

URL = "https://api.acme-images.example/v1/generate"


def register(api) -> None:
    def _key() -> str:
        from backend.agent.secrets import get_key

        return (get_key("acme_images_key") or "").strip()

    def generate(prompt: str = "", model: str = "flash", size: str = "1024x1024",
                 output_dir: str = "", confirm_spend: bool = False) -> dict:
        if not prompt.strip():
            return {"ok": False, "error": "Give a prompt."}
        if not confirm_spend:
            return {"ok": False, "error": "Acme Flash costs $0.04 an image. Ask the user, then call again with confirm_spend=true."}
        if not _key():
            return {"ok": False, "error": "Add your Acme API key in Settings → Acme Images."}
        try:
            out = api.http_json("POST", URL, headers={"Authorization": f"Bearer {_key()}"},
                                json_body={"prompt": prompt, "model": model, "size": size}, timeout=120)
            folder = Path(output_dir or tempfile.mkdtemp(prefix="acme-images-"))
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"acme_{int(time.time() * 1000)}.png"
            with urllib.request.urlopen(out["image_url"], timeout=60) as resp:
                target.write_bytes(resp.read())
        except Exception as exc:
            return {"ok": False, "error": f"Acme: {exc}"}
        return {"ok": True, "status": "SUCCEEDED", "task_id": str(out.get("id") or ""), "downloaded": [str(target)]}

    @api.tool(listener=False)
    def acme_images_generate(prompt: str = "", model: str = "flash", size: str = "1024x1024",
                             wait: bool = True, output_dir: str = "", confirm_spend: bool = False) -> dict:
        """Make one picture with Acme ($0.04). Needs confirm_spend=true after the user agrees."""
        return generate(prompt, model, size, output_dir, confirm_spend)

    @api.register_pipeline_node("acme_images.generate")
    def node_generate(ctx: dict) -> dict:
        cfg, inputs = ctx.get("config") or {}, ctx.get("inputs") or {}
        if cfg.get("spend") is not True:
            return {"ok": False, "error": "Acme Flash costs $0.04 an image. Turn on Spend in this node's details."}
        made = generate(str(inputs.get("prompt") or ""), output_dir=str(ctx.get("artifact_dir") or ""), confirm_spend=True)
        if not made.get("ok"):
            return made
        path = made["downloaded"][0]
        return {"ok": True, "image": {"kind": "image", "path": path, "name": Path(path).name}}
```

## 2. AI providers

**Ducky AI is the platform gateway every site has**, and the Store has gateway plugins
for the big model vendors and local models (Settings → LLMs). Check those first
(`ducky_store_search`) and install one instead of building it. Build a provider plugin
only when the user asks for a service no gateway covers.

For AI **inside** a plugin you don't need a provider: panels call `llm.complete`
(`{system, user, model?}`, the user's chosen model), and workflows put an `llm.ask` or
`pipeline.agent` node before your node. Never ship or ask for a model vendor key in
your own plugin for that.

A provider (only when asked):

```json
"secret_keys": ["nimbus_llm"],
"contributes": {
  "llm.providers": [{ "id": "nimbus_llm", "label": "Nimbus", "kind": "secret", "secret_key": "nimbus_llm", "order": 120 }]
}
```

```python
from __future__ import annotations

BASE = "https://api.nimbus.example/v1"


def register(api) -> None:
    from backend.agent.model_fetch import ModelInfo
    from backend.agent.providers.base import StreamEvent, StreamEventKind

    class NimbusProvider:
        def __init__(self, api_key: str, model: str) -> None:
            self.api_key, self.model = api_key, model

        def _chat(self, system: str, messages: list) -> str:
            body = {"model": self.model, "messages": [{"role": "system", "content": system}]
                    + [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]}
            out = api.http_json("POST", f"{BASE}/chat", headers={"Authorization": f"Bearer {self.api_key}"},
                                json_body=body, timeout=120)
            return str(out.get("text") or "")

        async def stream_turn(self, *, system, messages, tools, cancel_event=None, cache=None):
            import asyncio

            try:
                text = await asyncio.to_thread(self._chat, system, messages)
            except Exception as exc:
                yield StreamEvent(kind=StreamEventKind.ERROR, error=str(exc))
                return
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=text)
            yield StreamEvent(kind=StreamEventKind.DONE, stop_reason="end_turn")

        async def test_connection(self):
            import asyncio

            try:
                await asyncio.to_thread(api.http_json, "GET", f"{BASE}/models",
                                        headers={"Authorization": f"Bearer {self.api_key}"}, timeout=15)
                return True, "Connected."
            except Exception as exc:
                return False, str(exc)

    def fetch_models(api_key: str, **_kw) -> list:
        out = api.http_json("GET", f"{BASE}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        return [ModelInfo(id=m["id"], display_name=m.get("name")) for m in out.get("models", [])]

    api.register_llm_provider("nimbus_llm", lambda key, model, **_kw: NimbusProvider(key, model),
                              fetch_models=fetch_models)
    # + an @api.tool (e.g. nimbus_models), a workflow node and template, and the bundled skill.
```

This one answers in one piece and doesn't call tools; agent chats need a provider
that streams and handles `tools` (`StreamEventKind.TOOL_CALLS`). Say so to the user.

## 3. Sounds and hooks

`sounds` adds sound files; `hooks` adds named moments. In Settings → Appearance →
Sounds the user puts a sound on any moment: the app's own (`tab.changed`,
`settings.opened`, `llms.settings`, `model.picker`, `store.opened`,
`agent.selected`, `agent.done`, `agent.error`, `verse.errors`) and the plugins'.
A plugin fires its own hooks with `api.emit_hook(hook_id, payload)`; the main window
plays the mapped sound once (nothing plays until the user maps one). Prefix hook ids
with the plugin id.

```json
"contributes": {
  "sounds": [
    { "id": "cha_ching", "label": "Cha-ching", "file": "assets/sounds/cha_ching.mp3" }
  ],
  "hooks": [
    { "id": "card_shop.sold", "label": "Card sold" }
  ]
}
```

```python
def register(api) -> None:
    data = api.data

    def sell(card_id: str = "") -> dict:
        card = data.get(f"card.{card_id}") if card_id else None
        if not card:
            return {"ok": False, "error": f"No card {card_id!r}."}
        try:
            data.put(f"card.{card_id}", {**card, "sold": True})
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        api.emit_hook("card_shop.sold", {"card": card_id})  # plays the sound the user put on "Card sold"
        return {"ok": True, "card": card_id}

    @api.tool(listener=False)
    def card_shop_sell(card_id: str = "") -> dict:
        """Mark a card sold (plays the Card sold sound if the user set one)."""
        return sell(card_id)

    @api.register_pipeline_node("card_shop.sell")
    def node_sell(ctx: dict) -> dict:
        return sell(str((ctx.get("config") or {}).get("card_id") or ""))
```

- `api.emit_hook` only fires hooks this plugin declares; it returns
  `{"ok": False, "error": …}` for any other id.
- To show the user where to map sounds: `ducky_ui_show` with
  `navigate: "settings.appearance"`, target `settings.tab.appearance`.
- Sounds are not workflow steps: a node that does something fires the hook.

## 4. Walkthroughs (first-run tour)

A tour that starts the first time the plugin is turned on. Each step points at a
**registered target id** (`ducky_ui_list_targets(route, query)` lists them). The
plugin's own header button is `header.button.<button id>` in its walkthrough
(everywhere else, e.g. `ducky_ui_show`, it is `header.button.<plugin id>.<button id>`);
its Settings tab is `settings.tab.<tab id>`.

```json
"walkthrough": {
  "id": "card_shop",
  "title": "Card Shop",
  "auto_start": "first_enable",
  "steps": [
    { "target": "header.button.main", "title": "Card Shop is on",
      "body": "Open your shop's cards here.", "advance": "require_click" },
    { "target": "header.automations", "title": "Workflows",
      "body": "Card Shop's nodes and the List shop cards template are in Workflows.", "advance": "next" }
  ]
}
```

- `advance`: `next` (a Next button) or `require_click` (waits for the user to click
  the highlighted control).
- On a narrow window the header buttons fold into a menu, so a header-button step
  shows only once that menu is open; a first step on `shell.header` (the top bar)
  always shows.
- `auto_start`: `first_enable` or `never`.
- To show something once in a chat instead, use `ducky_ui_show` with `steps`, or
  `ducky_walkthrough_run(steps)`.
