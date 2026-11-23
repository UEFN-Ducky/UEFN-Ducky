#!/bin/sh
# Runs the command it is linked as (bash, git, ...) on the host, in the same folder,
# with the terminal's TERM; stdin/stdout/stderr (the terminal's pty) pass straight through.
exec flatpak-spawn --host --watch-bus --directory="$PWD" --env=TERM="${TERM:-xterm-256color}" "$(basename "$0")" "$@"
