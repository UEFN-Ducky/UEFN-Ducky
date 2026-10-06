"""The ``ducky`` object a Custom code node's JavaScript gets: one list (MANIFEST) that
the runtime, the editor's type hints and the agent tools all read."""

from __future__ import annotations

from typing import Any

# Size caps: one node's code, all code in one workflow, what run() may return.
MAX_CODE_BYTES = 64 * 1024
MAX_WORKFLOW_CODE_BYTES = 256 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_LOG_CHARS = 16 * 1024
MAX_SLEEP_S = 3600

# Node types that steer the run (starts, loops, branches, ends) or show it: a code
# node can't run them through ducky.builtin, and their Code tab is read-only.
FLOW_TYPES = frozenset(
    {
        "start.manual",
        "start.cron",
        "start.chat",
        "spotlight.step",
        "spotlight.closed",
        "flow.input",
        "flow.output",
        "flow.end",
        "flow.foreach",
        "flow.repeat",
        "flow.branch",
        "logic.if",
        "workflow.call",
        "fortnite.servers",
        "pipeline.finish",
        "util.preview",
        "code.js",
    }
)

# Built-ins code can't run through ducky.builtin either, and what it uses instead: Call
# tool would take any tool and any command built at run time past node.tools.
NOT_BUILTINS: dict[str, str] = {
    "tool.call": "tool.call can't run through ducky.builtin: call tools with ducky.tool and list each one in node.tools.",
}

BLANK_CODE = """// @ts-check
export const node = {
  kind: "step",
  inputs: [{ id: "text", type: "text", label: "Text" }],
  outputs: [{ id: "text", type: "text", label: "Text" }],
  settings: [],
  tools: [],
  builtins: [],
};

/** @param {Record<string, any>} input @param {import("ducky").Ducky} ducky */
export default async function run(input, ducky) {
  ducky.log("Got", input.text);
  return { text: input.text };
}
"""

# What the server writes into config for BLANK_CODE (a new Custom code node starts with these).
BLANK_PINS: dict[str, Any] = {
    "exec": True,
    "inputs": [{"id": "text", "label": "Text", "type": "text"}],
    "outputs": [{"id": "text", "label": "Text", "type": "text"}],
}
BLANK_USES: dict[str, list[str]] = {"tools": [], "builtins": []}

MANIFEST: list[dict[str, Any]] = [
    {
        "name": "tool",
        "ts": "tool(name: string, args?: Record<string, any>): Promise<any>",
        "doc": (
            "Calls an MCP tool listed in node.tools and returns its parsed JSON result; throws when the result has "
            "ok:false. Args are passed as they are ({{placeholders}} are not filled in); spend and confirm_spend are "
            "removed. A ducky_terminal_run command runs without the Allow/Deny pop-up only when this code is approved "
            "and the exact command is a plain string literal in it."
        ),
        "async": True,
    },
    {
        "name": "builtin",
        "ts": "builtin(type: string, input?: Record<string, any>, config?: Record<string, any>): Promise<Record<string, any>>",
        "doc": (
            "Runs a built-in node type listed in node.builtins with these input values and settings and returns its "
            "outputs. Paid generators spend only when this node's Spend switch is on or a person pressed play; a "
            "spend value passed here is ignored. Start, loop, branch and end nodes can't be run this way, and neither "
            "can Call tool (use ducky.tool)."
        ),
        "async": True,
    },
    {
        "name": "expr",
        "ts": "expr(source: string, scope?: Record<string, any>): Promise<any>",
        "doc": "Works out an expression exactly like the Expression node (its rules, not JavaScript's), over the run's fields plus scope.",
        "async": True,
    },
    {
        "name": "template",
        "ts": "template(text: string, scope?: Record<string, any>): Promise<any>",
        "doc": "Fills {{a.b}} placeholders like other nodes do, from the run's fields and nodes plus scope. A text that is only one placeholder keeps the value's type.",
        "async": True,
    },
    {
        "name": "log",
        "ts": "log(...values: any[]): void",
        "doc": "Adds a line to this step's log, shown live on the node while it runs and kept with the run (last 16 KB).",
        "async": False,
    },
    {
        "name": "sleep",
        "ts": "sleep(seconds: number): Promise<void>",
        "doc": "Waits (at most 3600 seconds). Ends early with an error when the run is stopped.",
        "async": True,
    },
    {
        "name": "cancelled",
        "ts": "cancelled(): boolean",
        "doc": "Whether the run was stopped (updated each time a ducky call returns).",
        "async": False,
    },
    {
        "name": "settings",
        "ts": "readonly settings: Readonly<Record<string, any>>",
        "doc": "This node's settings values (declared defaults filled in).",
        "async": False,
    },
    {
        "name": "nodes",
        "ts": "readonly nodes: Readonly<Record<string, Readonly<Record<string, any>>>>",
        "doc": "What each node that already ran in this run made: nodes[nodeId][pinId].",
        "async": False,
    },
    {
        "name": "run",
        "ts": "readonly run: Readonly<{ workflow_id: string; workflow_name: string; run_id: string; caller_conv_id: string }>",
        "doc": "Which workflow and run this is, and the chat it reports to (blank when none).",
        "async": False,
    },
]


def dts() -> str:
    """TypeScript declarations for ``import("ducky").Ducky`` (a module, so Monaco keeps it apart).
    ToolArgs is empty here; the Code tab adds each listed tool's arguments to it."""
    lines = ['declare module "ducky" {', "  export interface ToolArgs {}", "  export interface Ducky {"]
    for entry in MANIFEST:
        lines.append(f"    /** {entry['doc']} */")
        if entry["name"] == "tool":
            lines.append("    tool<K extends keyof ToolArgs>(name: K, args?: ToolArgs[K]): Promise<any>;")
        lines.append(f"    {entry['ts']};")
    lines += ["  }", "}", ""]
    return "\n".join(lines)
