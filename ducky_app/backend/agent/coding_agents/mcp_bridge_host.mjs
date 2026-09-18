/**
 * Host for UEFN MCP stdio on Windows.
 *
 * Default: spawn DUCKY_BRIDGE_ARGV with windowsHide (no console flash).
 * When DUCKY_SHARED_MCP=1: connect to the shared daemon pipe; on failure
 * fall back to the dedicated Bridge.exe spawn.
 *
 * Env:
 *   DUCKY_BRIDGE_ARGV — JSON array: [command, ...args]
 *   DUCKY_SHARED_MCP — "1" to try the named-pipe daemon first
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

const raw = process.env.DUCKY_BRIDGE_ARGV || "[]";
let argv;
try {
  argv = JSON.parse(raw);
} catch {
  console.error("DUCKY_BRIDGE_ARGV must be a JSON array");
  process.exit(1);
}
if (!Array.isArray(argv) || argv.length < 1) {
  console.error("DUCKY_BRIDGE_ARGV empty");
  process.exit(1);
}

function spawnDedicated() {
  const [command, ...args] = argv.map(String);
  const child = spawn(command, args, {
    stdio: "inherit",
    windowsHide: true,
    env: process.env,
  });
  child.on("error", (err) => {
    console.error(String(err && err.stack ? err.stack : err));
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    if (signal) process.exit(1);
    process.exit(code == null ? 1 : code);
  });
}

function appDataState() {
  const root = process.env.LOCALAPPDATA || process.env.APPDATA || "";
  if (!root) return null;
  return path.join(root, "UEFN-Ducky", "shared_mcp.json");
}

function connectSocket(host, port) {
  return new Promise((resolve, reject) => {
    const sock = net.connect({ host, port: Number(port) });
    sock.once("connect", () => resolve(sock));
    sock.once("error", reject);
  });
}

function identityFromEnv() {
  const out = {};
  const map = [
    ["DUCKY_RUN_ID", "run_id"],
    ["DUCKY_CONV_ID", "conv_id"],
    ["DUCKY_PROFILE_ID", "profile_id"],
    ["DUCKY_DUCKY_NAME", "ducky_name"],
    ["DUCKY_MODEL", "model"],
    ["DUCKY_CODING_AGENT", "coding_agent"],
    ["DUCKY_GROUP_ID", "group_id"],
    ["DUCKY_LEADER_CONV_ID", "leader_conv_id"],
  ];
  for (const [env, key] of map) {
    const val = (process.env[env] || "").trim();
    if (val) out[key] = val;
  }
  return out;
}

function encodeFrame(obj) {
  const body = Buffer.from(JSON.stringify(obj), "utf8");
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length, 0);
  return Buffer.concat([header, body]);
}

class FrameReader {
  constructor() {
    this.buf = Buffer.alloc(0);
  }
  push(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    const out = [];
    while (this.buf.length >= 4) {
      const n = this.buf.readUInt32BE(0);
      if (this.buf.length < 4 + n) break;
      const raw = this.buf.subarray(4, 4 + n);
      this.buf = this.buf.subarray(4 + n);
      out.push(JSON.parse(raw.toString("utf8")));
    }
    return out;
  }
}

class McpStdio {
  constructor() {
    this.buf = Buffer.alloc(0);
  }
  push(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    const msgs = [];
    while (true) {
      const text = this.buf.toString("utf8");
      const split = text.indexOf("\r\n\r\n");
      if (split < 0) break;
      const headers = text.slice(0, split);
      const match = /Content-Length:\s*(\d+)/i.exec(headers);
      if (!match) {
        this.buf = this.buf.subarray(split + 4);
        continue;
      }
      const n = Number(match[1]);
      const headerBytes = Buffer.byteLength(text.slice(0, split + 4), "utf8");
      if (this.buf.length < headerBytes + n) break;
      const body = this.buf.subarray(headerBytes, headerBytes + n).toString("utf8");
      this.buf = this.buf.subarray(headerBytes + n);
      msgs.push(JSON.parse(body));
    }
    return msgs;
  }
}

function writeMcp(msg) {
  const body = Buffer.from(JSON.stringify(msg), "utf8");
  process.stdout.write(`Content-Length: ${body.length}\r\n\r\n`);
  process.stdout.write(body);
}


function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function startDaemon() {
  const args = argv.map(String);
  const command = args.shift();
  if (!args.includes("--shared-daemon")) args.push("--shared-daemon");
  const child = spawn(command, args, {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
    env: process.env,
  });
  child.unref();
}

async function tryShared() {
  const stateFile = appDataState();
  startDaemon();
  const deadline = Date.now() + 30000;
  let sock = null;
  let token = "";
  let key = {};
  let host = "127.0.0.1";
  let port = 0;
  while (Date.now() < deadline) {
    try {
      if (stateFile && fs.existsSync(stateFile)) {
        const state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
        token = String(state.token || "");
        key = state.key && typeof state.key === "object" ? state.key : {};
        host = String(state.host || "127.0.0.1");
        port = Number(state.port || 0);
      }
      if (!token || !port) throw new Error("not ready");
      sock = await connectSocket(host, port);
      break;
    } catch {
      sock = null;
      await sleep(80);
    }
  }
  if (!sock || !token) {
    if (sock) sock.destroy();
    return false;
  }
  const frames = new FrameReader();
  const stdio = new McpStdio();
  let helloDone = false;
  let helloReject = false;
  sock.write(
    encodeFrame({
      op: "hello",
      token,
      key,
      identity: identityFromEnv(),
    }),
  );
  sock.on("data", (chunk) => {
    for (const msg of frames.push(chunk)) {
      if (!helloDone) {
        helloDone = true;
        if (msg.op !== "hello_ok") helloReject = true;
        continue;
      }
      if (msg.op === "mcp") {
        if (msg.error) {
          writeMcp({ jsonrpc: "2.0", id: msg.id, error: msg.error });
        } else {
          writeMcp({ jsonrpc: "2.0", id: msg.id, result: msg.result ?? {} });
        }
      }
    }
  });
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("hello timeout")), 8000);
    const check = setInterval(() => {
      if (helloDone) {
        clearTimeout(t);
        clearInterval(check);
        resolve();
      }
    }, 10);
    sock.once("error", reject);
  }).catch(() => {
    helloReject = true;
  });
  if (helloReject) {
    sock.destroy();
    return false;
  }
  sock.on("close", () => process.exit(0));
  process.stdin.on("data", (chunk) => {
    for (const msg of stdio.push(chunk)) {
      if (msg.method === "notifications/cancelled") {
        sock.write(encodeFrame({ op: "cancel", id: msg.params && msg.params.requestId }));
        continue;
      }
      sock.write(
        encodeFrame({
          op: "mcp",
          id: msg.id,
          method: msg.method,
          params: msg.params || {},
        }),
      );
    }
  });
  process.stdin.on("end", () => {
    try {
      sock.write(encodeFrame({ op: "bye" }));
    } catch {
      /* closed */
    }
    sock.end();
  });
  return true;
}

if (String(process.env.DUCKY_SHARED_MCP || "") === "1") {
  tryShared()
    .then((ok) => {
      if (!ok) spawnDedicated();
    })
    .catch(() => spawnDedicated());
} else {
  spawnDedicated();
}
