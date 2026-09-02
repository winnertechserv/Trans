#!/usr/bin/env bash
# Headless daily backup. Runs whether or not the web app is open.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 app/backup.py snapshot scheduled
