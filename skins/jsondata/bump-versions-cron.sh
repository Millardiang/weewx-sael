#!/usr/bin/env bash
# bump-versions-cron.sh — runs unattended on the server via cron (set up
# through Webmin's "Scheduled Cron Jobs" module), watching the skin
# directory and rewriting index.html's ?v= query strings whenever
# instruments.js / forecast.js / allseasons.js actually change content.
#
# Re-running this with no file changes is a harmless no-op — the version
# strings are content hashes, so they only change when the file content
# does. Safe to run every minute.
#
# SETUP (one-time):
#   1. Edit SKIN_DIR below to the real path on the server where index.html
#      and the three .js files live (wherever you currently upload them to
#      via Webmin's File Manager).
#   2. chmod +x bump-versions-cron.sh
#   3. In Webmin: System -> Scheduled Cron Jobs -> Create a new scheduled
#      cron job. Command: the full path to this script, e.g.
#        /path/to/bump-versions-cron.sh
#      Schedule: "Every minute" is fine — it's a no-op when nothing changed.
#   4. Save. From then on, just upload edited files via Webmin as normal;
#      within a minute the version strings update themselves and you never
#      touch index.html's <script> tags by hand again.

set -euo pipefail

# >>> EDIT THIS to the real path on your server <<<
SKIN_DIR="/var/www/html/sael"

LOG_FILE="$SKIN_DIR/bump-versions.log"
HTML_FILE="$SKIN_DIR/index.html"
JS_FILES=("instruments.js" "forecast.js" "allseasons.js")

cd "$SKIN_DIR" || { echo "$(date -Iseconds) ERROR: SKIN_DIR not found: $SKIN_DIR" >> "$LOG_FILE"; exit 1; }

if [[ ! -f "$HTML_FILE" ]]; then
  echo "$(date -Iseconds) ERROR: index.html not found in $SKIN_DIR" >> "$LOG_FILE"
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  SHA_CMD="sha256sum"
else
  SHA_CMD="shasum -a 256"
fi

CHANGED=0
for f in "${JS_FILES[@]}"; do
  [[ -f "$f" ]] || continue

  hash=$($SHA_CMD "$f" | cut -c1-8)
  current=$(grep -oE "${f//./\\.}\\?v=[A-Za-z0-9]+" "$HTML_FILE" | head -1 | sed -E 's/.*v=//')

  if [[ "$current" != "$hash" ]]; then
    sed -i -E "s|(${f//./\\.}\\?v=)[A-Za-z0-9]+|\\1${hash}|g" "$HTML_FILE"
    echo "$(date -Iseconds) $f: v=$current -> v=$hash" >> "$LOG_FILE"
    CHANGED=1
  fi
done

if [[ "$CHANGED" -eq 0 ]]; then
  : # nothing changed, stay quiet — don't spam the log every minute
fi