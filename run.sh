#!/usr/bin/env bash
# Yt-api: Ubuntu / Railway container startup (without Docker)
set -Eeuo pipefail

API_PORT="${API_PORT:-8010}"
SCREEN_NAME="${SCREEN_NAME:-yt_fast_api}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/api.log"
VENV_DIR="$PROJECT_DIR/venv"

say() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }
trap 'echo "ERROR at line $LINENO; existing API screen was not intentionally stopped until startup stage." >&2' ERR

cd "$PROJECT_DIR"
[[ -f app.py && -f requirements.txt ]] || fail "Place start_api.sh next to app.py and requirements.txt (current: $PROJECT_DIR)"
[[ -f .env ]] || fail "Missing .env; create it with API_KEY etc. Existing files will not be overwritten."
mkdir -p "$LOG_DIR"

# Pull only for a clean checkout; don't lose local edits.
if [[ "${AUTO_GIT_PULL:-1}" == "1" ]] && command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [[ -z "$(git status --porcelain --untracked-files=no)" ]] && [[ -n "$(git remote)" ]]; then
    say "Checking Git updates (fast-forward only)..."
    if ! GIT_TERMINAL_PROMPT=0 git pull --ff-only; then
      echo "WARNING: git pull failed; continuing with existing code (check credentials/network)."
    fi
  else
    echo "WARNING: Git has tracked changes or no remote; skipping git pull."
  fi
fi

# Packages are installed only if their commands/files are missing.
packages=()
command -v python3 >/dev/null 2>&1 || packages+=(python3)
python3 -c 'import venv' >/dev/null 2>&1 || packages+=(python3-venv)
command -v curl >/dev/null 2>&1 || packages+=(curl)
command -v unzip >/dev/null 2>&1 || packages+=(unzip)
command -v ffmpeg >/dev/null 2>&1 || packages+=(ffmpeg)
command -v ffprobe >/dev/null 2>&1 || packages+=(ffmpeg)
command -v screen >/dev/null 2>&1 || packages+=(screen)
[[ -f /etc/ssl/certs/ca-certificates.crt ]] || packages+=(ca-certificates)
if (( ${#packages[@]} )); then
  command -v apt-get >/dev/null 2>&1 || fail "apt-get not found. Install: ${packages[*]}"
  [[ "$(id -u)" -eq 0 ]] || fail "Run as root for missing apt packages: ${packages[*]}"
  say "Installing missing OS packages: ${packages[*]}"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
fi

# Deno, including stable PATH for screen sessions.
if ! command -v deno >/dev/null 2>&1; then
  if [[ -x "$HOME/.deno/bin/deno" ]]; then
    export PATH="$HOME/.deno/bin:$PATH"
  else
    say "Installing Deno..."
    curl -fsSL https://deno.land/install.sh | sh
    export PATH="$HOME/.deno/bin:$PATH"
  fi
fi
command -v deno >/dev/null 2>&1 || fail "Deno binary not found after installation."
DENO_BIN="$(command -v deno)"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  say "Creating Python virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

say "Checking Python dependencies..."
STAMP="$VENV_DIR/.yt_api_requirements.sha256"
CURRENT_HASH="$(sha256sum requirements.txt | awk '{print $1}')"
if [[ ! -f "$STAMP" || "$(cat "$STAMP")" != "$CURRENT_HASH" ]] || ! "$VENV_DIR/bin/python" -c 'import flask, gunicorn, yt_dlp, requests' >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" -m pip install -r requirements.txt
  echo "$CURRENT_HASH" > "$STAMP"
fi

# Stop ONLY the dedicated screen; never kill other services on port 8010.
if screen -list | grep -Eq "[.]${SCREEN_NAME}[[:space:]]"; then
  say "Stopping previous screen: $SCREEN_NAME"
  screen -S "$SCREEN_NAME" -X quit
  sleep 2
fi

# Don't start a second listener on the same port.
if command -v python3 >/dev/null 2>&1; then
  if ! python3 - "$API_PORT" <<'PY'
import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(('0.0.0.0',int(sys.argv[1])))
except OSError as e:
    print('Port unavailable:',e); sys.exit(1)
finally: s.close()
PY
  then
    fail "Port $API_PORT is already used by a process outside the managed screen. Check it; no process was killed."
  fi
fi

say "Starting API in screen '$SCREEN_NAME', port $API_PORT..."
: > "$LOG_FILE"
# The child shell loads .env; PATH includes Deno independently of interactive .bashrc.
screen -dmS "$SCREEN_NAME" bash -c '
  set -Eeuo pipefail
  cd "$1"
  export PATH="$(dirname "$2"):$PATH"
  set -a
  source .env
  set +a
  export COOKIE_FILE="${COOKIE_FILE:-$PWD/cookies.txt}"
  exec ./venv/bin/gunicorn -w 1 --threads 8 -b "0.0.0.0:$3" --timeout 60 --access-logfile - --error-logfile - app:app >> "$4" 2>&1
' _ "$PROJECT_DIR" "$DENO_BIN" "$API_PORT" "$LOG_FILE"

sleep 3
if ! screen -list | grep -Eq "[.]${SCREEN_NAME}[[:space:]]"; then
  tail -60 "$LOG_FILE" || true
  fail "API screen exited. See: $LOG_FILE"
fi

if curl -fsS --max-time 8 "http://127.0.0.1:$API_PORT/" >/dev/null; then
  say "SUCCESS: API online at http://127.0.0.1:$API_PORT/"
else
  echo "WARNING: Screen is running but HTTP health check failed. Recent logs:"
  tail -35 "$LOG_FILE" || true
fi
printf 'SCREEN: %s\nLOGS: %s\n' "$SCREEN_NAME" "$LOG_FILE"
echo "View logs: tail -f '$LOG_FILE'"
echo "View screen: screen -r '$SCREEN_NAME'"
