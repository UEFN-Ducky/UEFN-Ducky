#!/bin/bash
# Linux launcher: test, then release-build for one target. CI and S23's bundler call this.
#   native_launcher/linux-build.sh [x86_64-unknown-linux-gnu|aarch64-unknown-linux-gnu]
# Needs: libgtk-3-dev (and for a cross arm64 build: gcc-aarch64-linux-gnu + libgtk-3-dev:arm64).
# Output: $CARGO_TARGET_DIR (default native_launcher/target)/<target>/release/Ducky-Launcher
set -euo pipefail
cd "$(dirname "$0")"
target="${1:-x86_64-unknown-linux-gnu}"
host="$(rustc -vV | sed -n 's/^host: //p')"
if [ "$target" != "$host" ]; then
  export PKG_CONFIG_ALLOW_CROSS=1
  export PKG_CONFIG_PATH="/usr/lib/${target%%-unknown-*}-linux-gnu/pkgconfig"
else
  cargo test --workspace --target "$target"
fi
cargo build --release --target "$target" -p ducky-launcher
echo "built ${CARGO_TARGET_DIR:-target}/$target/release/Ducky-Launcher"
