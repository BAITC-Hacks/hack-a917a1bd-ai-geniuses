#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/frontend"
exec npm run dev -- --host 127.0.0.1

