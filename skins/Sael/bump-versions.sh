#!/usr/bin/env bash
# bump-versions.sh — auto cache-bust instruments.js / forecast.js / allseasons.js
#
# Replaces the ?v=N query string on each <script src="...js?v=N"> tag in
# index.html with the first 8 chars of that file's own content hash, so the
# query string only ever changes when the file's content actually changes.
# This removes the manual "remember to bump the number" step entirely, and
# also means you can never bump the wrong file's version by mistake.
#
# Usage: run this from the skin's source directory (wherever index.html,
# instruments.js, forecast.js, allseasons.js all live together) BEFORE
# uploading to the server. Then upload all four files as normal.
#
#   ./bump-versions.sh
#   # ... then your usual upload/rsync/scp step ...
#
# Requires: sha256sum (or shasum on macOS — see SHA_CMD below).

set -euo pipefail

HTML_FILE="index.html"
JS_FILES=("instruments.js" "forecast.js" "allseasons.js")

if [[ ! -f "$HTML_FILE" ]]; then
  echo "Error: $HTML_FILE not found in current directory." >&2
  exit 1
fi

# macOS has shasum, not sha256sum — fall back automatically.
if command -v sha256sum >/dev/null 2>&1; then
  SHA_CMD="sha256sum"
else
  SHA_CMD="shasum -a 256"
fi

for f in "${JS_FILES[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "Warning: $f not found, skipping." >&2
    continue
  fi

  hash=$($SHA_CMD "$f" | cut -c1-8)

  # Replace instruments.js?v=ANYTHING with instruments.js?v=<hash>, etc.
  # Works whether the existing value is a number (the old scheme) or
  # already a hash from a previous run.
  sed -i.bak -E "s|(${f//./\\.}\\?v=)[A-Za-z0-9]+|\\1${hash}|g" "$HTML_FILE"

  echo "$f -> v=$hash"
done

rm -f "${HTML_FILE}.bak"
echo "Done. $HTML_FILE updated — now upload index.html together with whichever .js files changed."