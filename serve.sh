#!/usr/bin/env bash
# Control the FastAPI service (app.main:app) over my_rag_stories.json.
#
#   ./serve.sh start              launch in background, write a pidfile
#   ./serve.sh stop               TERM it, KILL only if it ignores TERM
#   ./serve.sh restart            stop, then start
#   ./serve.sh status             pid, uptime, RSS, listening port, /api/health
#   ./serve.sh logs [-n N]        follow the uvicorn log
#   ./serve.sh health             pretty-printed /api/health
#   ./serve.sh open               print the URLs the UI answers on
#   ./serve.sh keep               watchdog: bring the site back if its pid dies
#
# Host/port come from the environment (defaults below) or flags:
#   HOST=0.0.0.0 PORT=9000 ./serve.sh start
#   ./serve.sh start --host 0.0.0.0 --port 9000
#
# SITE_URL (the public origin: canonical URLs, og:image, /sitemap.xml) comes
# from the environment, from .run/env, or from the default below — see
# deploy/env.example, the template you copy into .run/env.

set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Public origin for canonical URLs, og:image and /sitemap.xml (app/main.py
# reads SITE_URL; empty is what leaves the sitemap 404ing and og:image
# relative). Precedence: exported shell env > .run/env > this default, and
# start() persists the resolved value into .run/env so restarts keep it.
# systemd's EnvironmentFile reads that same file on the deploy box.
SITE_URL_DEFAULT="https://feimatrix.com"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
PIDFILE="$RUN_DIR/serve.pid"
LOGFILE="$RUN_DIR/serve.log"
ENVFILE="$RUN_DIR/env"

UVICORN="$ROOT/.venv/bin/uvicorn"
APP="app.main:app"

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

# --- arg parsing: command + optional --host/--port -------------------------
cmd="${1:-}"
if [[ $# -gt 0 ]]; then shift; fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -n)     N="$2"; shift 2 ;;
    *)      usage ;;
  esac
done

BASE="http://localhost:$PORT"

# --- helpers ---------------------------------------------------------------
pid_port() {
  # Echo the --port a uvicorn pid is actually bound to.
  local pid="$1"
  tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null |
    awk '/^--port$/ { getline p; print p; exit }'
}

running_pid() {
  # Echo the live uvicorn pid tracked by the pidfile, else nothing.
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null && printf '%s\n' "$pid"
}

orphans() {
  # uvicorn instances that are running but not tracked by the pidfile.
  local pids self pid out=""
  pids="$(pgrep -f "uvicorn $APP" || true)"
  self="$(running_pid || true)"
  for pid in $pids; do
    [[ "$pid" == "$self" ]] || out="$out $pid"
  done
  [[ -n "$out" ]] && printf '%s\n' "${out# }"
}

wait_gone() {
  # wait_gone PID — true once the pid has exited.
  local pid="$1" i
  for i in {1..20}; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.25
  done
  return 1
}

wait_up() {
  local i
  for i in {1..40}; do
    if curl -fsS -o /dev/null --max-time 1 "$BASE/api/health" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

env_get() {
  # env_get KEY — value of KEY in .run/env (last assignment wins), else nothing.
  # Same KEY=VALUE shape systemd's EnvironmentFile reads.
  [[ -f "$ENVFILE" ]] || return 0
  sed -n "s|^$1=||p" "$ENVFILE" 2>/dev/null | tail -n 1
}

site_url() {
  # Echo the origin to hand app/main.py: exported env wins, then .run/env,
  # then the shipped default. Never empty — an empty SITE_URL is what keeps
  # /sitemap.xml 404, robots.txt without its Sitemap line and og:image
  # relative.
  if [[ -n "${SITE_URL:-}" ]]; then
    printf '%s\n' "$SITE_URL"
    return
  fi
  local saved
  saved="$(env_get SITE_URL || true)"
  printf '%s\n' "${saved:-$SITE_URL_DEFAULT}"
}

# --- commands --------------------------------------------------------------
start() {
  [[ -x "$UVICORN" ]] || {
    echo "找不到 $UVICORN — 先运行: uv pip install -r requirements.txt --python .venv"
    exit 1
  }

  local pid bound
  pid="$(running_pid || true)"
  if [[ -n "$pid" ]]; then
    bound="$(pid_port "$pid" || true)"
    if [[ -n "$bound" && "$bound" != "$PORT" ]]; then
      echo "already up: pid $pid on :$bound (you asked for :$PORT) — stop it first, or reuse :$bound"
    else
      echo "already up: pid ${pid} on :${bound:-$PORT} (see ./serve.sh status)"
    fi
    return 0
  fi

  mkdir -p "$RUN_DIR"

  # /api/upload needs X-Upload-Token == $UPLOAD_TOKEN (main.py hard-gates on
  # it). Reuse a persisted token so restarts don't need manual export; an
  # explicitly exported UPLOAD_TOKEN always wins and is saved for next time.
  # The file is KEY=VALUE so systemd's EnvironmentFile can read it too.
  if [[ -z "${UPLOAD_TOKEN:-}" && -f "$RUN_DIR/upload_token" ]]; then
    UPLOAD_TOKEN="$(sed 's/^UPLOAD_TOKEN=//' "$RUN_DIR/upload_token")"
  fi
  if [[ -z "${UPLOAD_TOKEN:-}" ]]; then
    UPLOAD_TOKEN="$("$(dirname "$UVICORN")/python" -c 'import secrets; print(secrets.token_hex(16))')"
    printf 'UPLOAD_TOKEN=%s\n' "$UPLOAD_TOKEN" >"$RUN_DIR/upload_token"
    chmod 600 "$RUN_DIR/upload_token"
    echo "generated upload token, saved to $RUN_DIR/upload_token"
  else
    printf 'UPLOAD_TOKEN=%s\n' "$UPLOAD_TOKEN" >"$RUN_DIR/upload_token"
    chmod 600 "$RUN_DIR/upload_token"
  fi
  export UPLOAD_TOKEN

  # SITE_URL: resolve (exported env > .run/env > default), record it for the
  # next restart, and hand it to uvicorn. This is what turns on /sitemap.xml,
  # the Sitemap line in robots.txt and the absolute canonical/og:* URLs.
  SITE_URL="$(site_url)"
  export SITE_URL
  if ! grep -q '^SITE_URL=' "$ENVFILE" 2>/dev/null; then
    printf 'SITE_URL=%s\n' "$SITE_URL" >>"$ENVFILE"
    echo "recorded SITE_URL in $ENVFILE (edit that file to change the origin)"
  fi

  # uvicorn refuses a busy port, so surface the reason instead of a bind error.
  local extra p bound
  for p in $(orphans || true); do
    bound="$(pid_port "$p" || true)"
    if [[ "${bound:-}" == "$PORT" ]]; then
      echo "an untracked instance is already listening on :$PORT (pid $p) — run ./serve.sh stop first"
      exit 1
    fi
  done

  nohup "$UVICORN" "$APP" --host "$HOST" --port "$PORT" --workers 1 \
    >>"$LOGFILE" 2>&1 &
  echo $! >"$PIDFILE"

  if wait_up; then
    pid="$(running_pid)"
    printf 'started  pid %s  host %s  port %s  log %s\n' "$pid" "$HOST" "$PORT" "$LOGFILE"
    printf 'site     %s (canonical URLs, og:image, /sitemap.xml)\n' "$SITE_URL"
    printf 'health   %s -> %s\n' "$BASE/api/health" \
      "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/health")"
  else
    pid="$(running_pid || true)"
    if [[ -z "$pid" ]]; then
      echo "did not come up — last lines:"
      tail -n 15 "$LOGFILE"
      exit 1
    fi
    echo "pid $pid is alive but /api/health is not answering yet; check ./serve.sh logs"
  fi
}

stop() {
  local pid extra
  pid="$(running_pid || true)"
  extra="$(orphans || true)"

  if [[ -z "$pid" && -z "$extra" ]]; then
    echo "not running"
    return 0
  fi

  for pid in ${pid:-} ${extra:-}; do
    kill -TERM "$pid" 2>/dev/null || continue
    if wait_gone "$pid"; then
      printf 'stopped  pid %s\n' "$pid"
    else
      kill -KILL "$pid" 2>/dev/null || true
      printf 'killed   pid %s (ignored TERM)\n' "$pid"
    fi
  done

  rm -f "$PIDFILE"
}

status() {
  local pid extra code
  pid="$(running_pid || true)"
  extra="$(orphans || true)"

  if [[ -z "$pid" && -z "$extra" ]]; then
    echo "stopped"
    [[ -f "$LOGFILE" ]] && printf 'log: %s (%s bytes)\n' "$LOGFILE" "$(wc -c <"$LOGFILE")"
    return 1
  fi

  for pid in ${pid:-} ${extra:-}; do
    local tag=tracked up rss
    [[ " $extra " == *" $pid "* ]] && tag=untracked
    up="$(ps -o etime= -p "$pid" | tr -d ' ')"
    rss="$(ps -o rss= -p "$pid" | awk '{printf "%.0f MB", $1/1024}')"
    printf 'pid %s  %s  up %s  rss %s\n' "$pid" "$tag" "${up:-?}" "${rss:-?}"
  done

  local first port code
  first="${pid:-${extra%% *}}"
  port="$(pid_port "$first" || true)"
  port="${port:-$PORT}"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://localhost:$port/api/health" || echo 000)"
  printf 'health http://localhost:%s/api/health -> %s\n' "$port" "$code"
  printf 'ui     http://localhost:%s/ · /story?id=<id>\n' "$port"
  printf 'site   %s (origin the crawler tags + sitemap use)\n' "$(site_url)"
}

logs() {
  [[ -f "$LOGFILE" ]] || { echo "no log yet: $LOGFILE"; return 1; }
  tail -n "${N:-40}" -f "$LOGFILE"
}

health() {
  curl -fsS --max-time 5 "$BASE/api/health" | "$ROOT/.venv/bin/python" -m json.tool
}

open() {
  printf 'browse   http://localhost:%s/\n' "$PORT"
  printf 'search   http://localhost:%s/?q=school%%20uniform\n' "$PORT"
  printf 'tags     http://localhost:%s/?mode=tags\n' "$PORT"
  printf 'random   http://localhost:%s/?mode=random\n' "$PORT"
  printf 'story    http://localhost:%s/story?id=<id>\n' "$PORT"
  printf 'api      http://localhost:%s/api/health · docs /docs\n' "$PORT"
  printf 'public   %s/ · /story?id=<id> · /sitemap.xml (the origin crawler tags use)\n' "$(site_url)"
}

keep() {
  # Foreground watchdog. start() launches the site detached (nohup), so this
  # loop only watches the pidfile's process: a crash, an OOM kill or a bad
  # deploy brings it back within 10 s. Five failed restarts in a row gives up
  # instead of spinning forever — that is the import-time failure case (bad
  # JSON, missing UPLOAD_TOKEN), where retrying can never help.
  # Ctrl-C stops the watcher and leaves the site alone; to run it detached:
  #   nohup ./serve.sh keep >>.run/keep.log 2>&1 &
  local down=0
  printf 'watching (Ctrl-C stops the watcher, not the site)\n'
  while :; do
    if [[ -n "$(running_pid || true)" ]]; then
      down=0
      sleep 10
      continue
    fi
    down=$((down + 1))
    if [[ $down -ge 5 ]]; then
      printf 'gone 5 restarts in a row — giving up, last log lines:\n'
      tail -n 15 "$LOGFILE" 2>/dev/null
      return 1
    fi
    printf 'instance gone (attempt %s) — restarting\n' "$down"
    start || true
    sleep 5
  done
}

case "$cmd" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    logs ;;
  health)  health ;;
  open)    open ;;
  keep)    keep ;;
  ""|-h|--help|help) usage ;;
  *) echo "unknown command: $cmd"; usage ;;
esac
