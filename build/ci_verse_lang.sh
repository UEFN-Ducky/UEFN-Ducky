#!/usr/bin/env bash
# CI has no UEFN and this public repo can't hold Epic's Verse grammar, so a release build
# unpacks it from VERSE_LANG_ASSETS: base64 of a tar.gz of src/verse-editor/lang, made on the
# release PC with  tar -czf - -C <that folder> . | base64 -w0
#   ci_verse_lang.sh <dry_run true|false>
set -euo pipefail
lang=ducky_app/frontend/ui_web/web/src/verse-editor/lang
mkdir -p "$lang"
if [ -z "${VERSE_LANG_ASSETS:-}" ]; then
  if [ "${1:-false}" = "true" ]; then
    echo "::notice::dry run without VERSE_LANG_ASSETS: empty Verse grammar stand-ins"
    echo '{"scopeName": "source.verse", "patterns": [], "repository": {}}' > "$lang/verse.tmGrammar.json"
    echo '{}' > "$lang/language-configuration.json"
    echo '{}' > "$lang/verse-snippets.json"
    echo '{"name": "Verse Dark", "type": "dark", "colors": {}, "tokenColors": []}' > "$lang/verse-dark.tmTheme.json"
    exit 0
  fi
  echo "VERSE_LANG_ASSETS is not set: a release never ships the Verse editor without its grammar"; exit 1
fi
printf '%s' "$VERSE_LANG_ASSETS" | base64 -d | tar -xzf - -C "$lang"
ls -la "$lang"
