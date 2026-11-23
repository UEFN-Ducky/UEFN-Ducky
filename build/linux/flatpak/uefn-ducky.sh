#!/bin/sh
# Flatpak entry point (/app/bin/uefn-ducky). Shells and git found on PATH are the
# host-run shims, so the app's terminal runs the user's real system, not the sandbox.
#   --self-test   checks that host commands work (flatpak-spawn --host true)
export PATH="/app/libexec/host-bin:$PATH"
if [ "${1:-}" = "--self-test" ]; then
  if flatpak-spawn --host true; then
    echo "self-test: host commands OK"
    exit 0
  fi
  echo "self-test: flatpak-spawn --host failed (needs --talk-name=org.freedesktop.Flatpak)" >&2
  exit 1
fi
exec /app/lib/uefn-ducky/uefn-ducky "$@"
