// Copy Three.js glTF Draco decoder binaries into public/ so the panel can
// preview KHR_draco_mesh_compression GLBs offline (no CDN).
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const srcDir = resolve(webRoot, "node_modules/three/examples/jsm/libs/draco/gltf");
const destDir = resolve(webRoot, "public/draco");
const files = ["draco_decoder.js", "draco_decoder.wasm", "draco_wasm_wrapper.js"];

if (!existsSync(srcDir)) {
  console.error("[draco-assets] three package missing — run npm install in web/");
  process.exit(1);
}

mkdirSync(destDir, { recursive: true });
for (const name of files) {
  cpSync(resolve(srcDir, name), resolve(destDir, name));
}
