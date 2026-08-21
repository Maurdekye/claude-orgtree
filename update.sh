#!/usr/bin/env bash
# orgtree update script -- pull the latest changes and redeploy.
#
#   ./update.sh
#   ./update.sh --expose-admin      # DANGEROUS, see below
#   ORGTREE_EXPOSE_ADMIN=1 ./update.sh   # same, for services
#
# The bash counterpart of update.ps1, step for step. Written for Linux and
# macOS; it also runs under Git Bash / MSYS on Windows, where the two things
# that cannot be POSIX -- finding and killing the process holding a TCP port --
# fall back to netstat + taskkill.
#
# Steps: venv -> git pull -> npm install + build the UI -> pip install ->
# restart the backend (which serves the built UI) -> health-check.
#
# It runs orgtree from a repo-local .venv, created on first use, so the
# installed dependency set is exactly what requirements.txt says rather than
# whatever a shared system Python happens to hold.
#
# ORGTREE_EXPOSE_ADMIN=1 binds the ADMIN api to 0.0.0.0 instead of loopback
# (--expose-admin is a convenience switch that sets it). The admin
# api has no password, no token and no login -- reaching the port IS the
# credential -- so this hands anyone who finds it full control of every org and
# of any folder an agent has been granted. It is a switch you type, never a
# setting: nothing in the app, the org docs or the environment can turn it on,
# which means no agent can either. To share ONE org with someone, make it a
# kiosk instead (secret URL, hard limits) and open a tunnel to the public port.
#
# Environment respected: ORGTREE_DATA, ORGTREE_PUBLIC_PORT,
#   PYTHON=/path/to/python   use exactly that interpreter, skip the venv
#   ORGTREE_NO_VENV=1        stay on the system interpreter (old behaviour)

set -u

EXPOSE=0
for arg in "$@"; do
  case "$arg" in
    --expose-admin) EXPOSE=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "unknown option: $arg (try --help)" >&2
      exit 2 ;;
  esac
done

# colours only when someone is actually watching
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
  RED=$(tput setaf 1); GRN=$(tput setaf 2); YEL=$(tput setaf 3); OFF=$(tput sgr0)
else
  RED=''; GRN=''; YEL=''; OFF=''
fi
die() { printf '%s%s%s\n' "$RED" "$*" "$OFF" >&2; exit 1; }
note() { printf '%s%s%s\n' "$YEL" "$*" "$OFF"; }
good() { printf '%s%s%s\n' "$GRN" "$*" "$OFF"; }

ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT" || die "cannot cd to $ROOT"

# Windows-under-bash needs the native tools for ports and process kills
case "${OSTYPE:-}" in
  msys*|cygwin*|win32) WINDOWS=1 ;;
  *) WINDOWS=0 ;;
esac

# python: the first candidate that actually RUNS. Existence is not enough --
# Windows ships an App Execution Alias at ~/AppData/Local/Microsoft/WindowsApps
# /python3 that `command -v` finds happily and that then prints "Python was not
# found" and fails, so a which-style check picks a stub over the real
# interpreter sitting right behind it (hit on this machine, 2026-08-03).
# PYTHON overrides, and is validated too: it must be the interpreter that HAS
# the deps, which is the whole reason the override exists (a venv, normally).
py_works() { "$1" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; }
BOOT_PY=''
for cand in python3 python py; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if py_works "$cand"; then BOOT_PY=$cand; break; fi
done

# Run from a VIRTUALENV by default (repo-local .venv), created on first use.
#
# Until now orgtree installed into whatever `python` happened to be on PATH,
# which on a normal desktop is a system-wide interpreter shared with every
# other project. That makes the dependency set unknowable -- the exact
# condition behind the missing-websockets bug, where the app worked here and
# not on another machine because this box had the library for unrelated
# reasons. A venv makes "what is installed" equal to "what requirements.txt
# says", which is the only version of that question worth answering.
#
# Escape hatches, in precedence order:
#   PYTHON=/path/to/python   use exactly that interpreter, no venv logic
#   ORGTREE_NO_VENV=1        stay on the system interpreter (old behaviour)
# and if venv creation fails for any reason we warn and carry on rather than
# breaking a deployment that was working a minute ago.
VENV_DIR="$ROOT/.venv"
venv_py() {                      # the interpreter inside $VENV_DIR, if any
  for c in "$VENV_DIR/bin/python" "$VENV_DIR/Scripts/python.exe"; do
    [ -x "$c" ] && { echo "$c"; return; }
  done
}

PY=''
if [ -n "${PYTHON:-}" ]; then
  py_works "$PYTHON" || die "PYTHON=$PYTHON does not run -- check the path"
  PY=$PYTHON
elif [ "${ORGTREE_NO_VENV:-}" = "1" ]; then
  [ -n "$BOOT_PY" ] || die "no working python found -- install Python 3.11+"
  PY=$BOOT_PY
else
  PY=$(venv_py)
  if [ -z "$PY" ]; then
    [ -n "$BOOT_PY" ] || die "no working python found -- install Python 3.11+ or set PYTHON=/path/to/python"
    note "creating the virtualenv at .venv (first run) ..."
    if "$BOOT_PY" -m venv "$VENV_DIR" >/dev/null 2>&1; then
      PY=$(venv_py)
    fi
    if [ -z "$PY" ] || ! py_works "$PY"; then
      note "could not create .venv -- falling back to the system interpreter"
      note "(install the venv module, or set ORGTREE_NO_VENV=1 to silence this)"
      PY=$BOOT_PY
      rm -rf "$VENV_DIR" 2>/dev/null
    fi
  fi
fi
[ -n "$PY" ] || die "no working python found -- install Python 3.11+ or set PYTHON=/path/to/python"
case "$PY" in
  "$VENV_DIR"*) PY_KIND=' [.venv]' ;;
  *) PY_KIND=' [system -- deps are shared with every other project]' ;;
esac
echo "python: $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))$PY_KIND"

# ---- ONE DEPLOY AT A TIME -------------------------------------------------
# Nothing serialized these until 2026-08-09 (peer question, neoja). Two runs
# race on the git index, on npm's node_modules, and on stopping/starting the
# same port. `flock` on a lockfile beside the data root; the FD is held for
# the life of the process, so the lock releases however this exits.
_LOCK="${ORGTREE_DATA:-$HOME/orgtree}/.update.lock"
mkdir -p "$(dirname "$_LOCK")" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
  exec 9>"$_LOCK"
  if ! flock -n 9; then
    echo "another orgtree update is already running -- this one exits rather than racing it on git, npm and the port."
    exit 0
  fi
fi

BEFORE=$(git rev-parse --short HEAD 2>/dev/null) || die "not a git checkout: $ROOT"
echo "== orgtree update (currently $BEFORE) =="

# -- 1 - pull ---------------------------------------------------------------
# report a DIRTY TREE before pulling, always (peer report 2026-08-09, neoja):
# their self-update restarted every org, advanced nothing, and logged no
# reason. --ff-only refuses on some dirt and sails past the rest; an operator
# reading the log must be able to see which.
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
  echo "-- working tree is DIRTY (the pull may refuse):"
  echo "$DIRTY"
  # ⚠ REFUSE, don't just report (redteam hazard flag 2026-08-11): this script
  # builds the WORKING TREE, not HEAD — a deploy over someone's half-finished
  # edits ships a backend no commit contains. Doc-only dirt (docs/, *.md) is
  # the curator's normal working state and builds nothing, so it passes. So
  # does dirt THE BUILD ITSELF WRITES (external report 2026-08-12): some npm
  # versions recompute frontend/package-lock.json on install, which lands
  # AFTER this guard — deploy N would trip deploy N+1 forever.
  # ORGTREE_ALLOW_DIRTY=1 overrides, for the operator who owns the dirt.
  BUILDING=$(echo "$DIRTY" | cut -c4- | grep -vE '^docs/' | grep -vE '\.md$' \
             | grep -vE '^frontend/package-lock\.json$' || true)
  # the lockfile leg (redteam objection to the 4b2729e pass-list): the
  # exemption above is VERIFIED after npm install rather than trusted —
  # snapshot the dirty lockfile's hash now; dirt the install does not itself
  # reproduce (a hand edit npm rewrites) refuses before the restart.
  # Residual on the record: a hand edit npm reproduces verbatim passes both
  # shapes; package.json edits are caught by the main guard.
  LOCK_HASH_BEFORE=""
  if git status --porcelain -- frontend/package-lock.json | grep -q .; then
    LOCK_HASH_BEFORE=$(git hash-object frontend/package-lock.json)
  fi
  if [ -n "$BUILDING" ] && [ "${ORGTREE_ALLOW_DIRTY:-}" != "1" ]; then
    echo "REFUSING to deploy: uncommitted changes in files this build would ship:"
    echo "$BUILDING" | sed 's/^/    /'
    die "Commit or revert them first -- or ORGTREE_ALLOW_DIRTY=1 to ship the tree as it stands."
  fi
fi
git pull --ff-only || die "git pull FAILED -- resolve manually. Nothing was rebuilt and nothing was restarted."
AFTER=$(git rev-parse --short HEAD)
if [ "$AFTER" = "$BEFORE" ]; then
  # ORGTREE_ONLY_IF_BEHIND is an OPT-IN for callers who genuinely only want new
  # REMOTE code. It is NOT the self-restart's flag any more (D-142,
  # 2026-08-21) and nothing in this repo sets it. It used to be set by the
  # agent tool, and that made the tool unable to deploy a commit made on this
  # machine, silently: a deploy of a locally-made commit never moves HEAD
  # during the pull, so "HEAD advanced" is not a test for "is there anything
  # to ship". Kept for a scheduled job that wants the old meaning and accepts
  # that a local commit will not deploy under it. Mirrors update.ps1.
  if [ "${ORGTREE_ONLY_IF_BEHIND:-}" = "1" ]; then
    echo "already up to date ($AFTER) -- NOT restarting: a self-update with nothing to deploy would cut every org's turn for no gain"
    exit 0
  fi
  echo "already up to date ($AFTER) -- redeploying anyway"
else
  echo "updated $BEFORE -> $AFTER"
  git --no-pager log --oneline "$BEFORE..$AFTER"
fi

# -- 2 - frontend -----------------------------------------------------------
printf '\n== building the UI ==\n'
cd "$ROOT/frontend" || die "no frontend/ directory"
npm install --no-audit --no-fund || die "npm install failed"
if [ -n "${LOCK_HASH_BEFORE:-}" ] && [ "${ORGTREE_ALLOW_DIRTY:-}" != "1" ]; then
  LOCK_HASH_AFTER=$(git hash-object package-lock.json)
  if [ "$LOCK_HASH_AFTER" != "$LOCK_HASH_BEFORE" ]; then
    die "REFUSING: frontend/package-lock.json was dirty BEFORE the install and the install rewrote it — that dirt was a hand edit, not npm's own recomputation. Commit or checkout the lockfile, then redeploy (ORGTREE_ALLOW_DIRTY=1 overrides). Nothing was restarted."
  fi
  echo "note: package-lock.json carries npm's own recomputation (stable under this npm) — tolerated; consider committing it once"
fi

# esbuild self-heal. Vite builds with esbuild, whose binary ships as an
# OPTIONAL per-platform package (@esbuild/<os>-<cpu>) rather than a postinstall
# download. npm has a long-standing bug where a tree installed once can end up
# missing those optional packages (npm/cli#4828), and the symptom is an opaque
# build failure that has repeatedly been misdiagnosed -- most recently as npm
# blocking postinstall scripts, which is NOT the cause: esbuild is fine with
# --ignore-scripts (measured 2026-08-03 on npm 11.6.2; esbuild 0.25.12 and
# 0.28.1 both transform successfully with scripts fully blocked).
# The reliable fix is a clean reinstall, so do that automatically instead of
# leaving the next person to guess. Never edit package.json to "allow scripts":
# it fixes nothing here, and rewriting the lockfile can DROP other platforms'
# optional entries and break the very machines it was meant to help.
if ! node -e "require('esbuild').transformSync('let x=1')" 2>/dev/null; then
  note "esbuild is not usable -- clean reinstall (npm optional-deps bug)"
  rm -rf node_modules
  npm install --no-audit --no-fund || die "npm install failed"
  if ! node -e "require('esbuild').transformSync('let x=1')" 2>/dev/null; then
    printf '%sesbuild still broken after a clean reinstall.\n' "$RED" >&2
    printf 'Check that node/npm match your platform (nvm switches can leave\n' >&2
    printf 'a tree built for another arch), then delete package-lock.json too.%s\n' "$OFF" >&2
    exit 1
  fi
  good "esbuild repaired"
fi

npm run build || die "UI build failed"
cd "$ROOT" || exit 1

# -- 3 - backend deps -------------------------------------------------------
printf '\n== python deps ==\n'
"$PY" -m pip install -q -r requirements.txt || die "pip install failed"

# -- 3b - CLI version (No.44) ----------------------------------------------
# Sandbox images are tagged with the host CLI's version and rebuild on demand
# when it changes -- nothing to do here beyond reporting it.
if command -v claude >/dev/null 2>&1; then
  CLI_VER=$(claude --version 2>/dev/null | head -n 1)
  [ -n "$CLI_VER" ] && echo "Claude CLI: $CLI_VER (sandbox images rebuild automatically when this changes)"
fi

# -- 4 - restart the backend ------------------------------------------------
DATA_ROOT=${ORGTREE_DATA:-$HOME/orgtree}
PORT=7360
if [ -f "$DATA_ROOT/.port" ]; then
  FILE_PORT=$(tr -d '[:space:]' < "$DATA_ROOT/.port")
  case "$FILE_PORT" in ''|*[!0-9]*) : ;; *) PORT=$FILE_PORT ;; esac
fi

# who is holding the port? No single tool is present everywhere, so try the
# usual three and take the first that answers. Listeners only -- never match a
# client connection, which would kill an innocent process.
listeners() {
  if [ "$WINDOWS" = 1 ]; then
    netstat -ano -p tcp 2>/dev/null \
      | awk -v p=":$PORT" '$1=="TCP" && $2 ~ p"$" && $4=="LISTENING" {print $5}' \
      | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null
  elif command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :$PORT" 2>/dev/null \
      | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$'
  fi
}

printf '\n== restarting the backend (port %s) ==\n' "$PORT"
PIDS=$(listeners)
OLD_PIDS=$PIDS
if [ -n "${PIDS:-}" ]; then
  for pid in $PIDS; do
    echo "stopping old backend (pid $pid)"
    if [ "$WINDOWS" = 1 ]; then
      taskkill //PID "$pid" //F >/dev/null 2>&1
    else
      kill "$pid" 2>/dev/null        # ask nicely first
    fi
  done
  # give it a moment, then insist
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.2
    [ -z "$(listeners)" ] && break
  done
  if [ "$WINDOWS" != 1 ] && [ -n "$(listeners)" ]; then
    for pid in $(listeners); do kill -9 "$pid" 2>/dev/null; done
    sleep 0.5
  fi
  [ -n "$(listeners)" ] && die "port $PORT is still held -- stop that process and re-run"
fi

mkdir -p "$DATA_ROOT" || die "cannot create $DATA_ROOT"
OUT="$DATA_ROOT/backend.log"
ERRLOG="$DATA_ROOT/backend.err.log"
# the kiosk public listener is on by default (it serves nothing unless a kiosk
# org exists, and nothing reaches it from outside without a tunnel); set
# ORGTREE_PUBLIC_PORT yourself to override
export ORGTREE_PUBLIC_PORT=${ORGTREE_PUBLIC_PORT:-7361}

API_ARGS=(-m orgtree.api)
# ORGTREE_EXPOSE_ADMIN is what the backend reads (user ruling 2026-08-04, was
# an argv flag). --expose-admin is kept as a convenience that sets it for this
# launch; a service definition exports the variable and needs no switch.
[ "$EXPOSE" = 1 ] && export ORGTREE_EXPOSE_ADMIN=1
case "$(printf '%s' "${ORGTREE_EXPOSE_ADMIN:-}" | tr 'A-Z' 'a-z')" in
  1|true|yes|on) EXPOSE=1 ;;
esac
if [ "$EXPOSE" = 1 ]; then
  bar=$(printf '!%.0s' $(seq 74))
  printf '\n%s%s\n' "$RED" "$bar"
  echo '  ORGTREE_EXPOSE_ADMIN: the ADMIN api will listen on 0.0.0.0 with NO auth.'
  echo '  Anyone who can reach this port controls every org and can make'
  echo '  agents run commands on this machine. VPN/SSH tunnel only.'
  printf '%s%s\n\n' "$bar" "$OFF"
fi

# Detach properly. The child's own stdout/stderr go to the log files, but the
# SUBSHELL's descriptors have to be closed off too: under MSYS/Git Bash a
# backgrounded grandchild keeps the parent's pipe alive regardless of its own
# redirections, so `./update.sh | tee log` would hang forever after the script
# had finished all its work (reproduced in isolation 2026-08-03; `disown` does
# not fix it and `setsid` does not exist there). Redirecting the subshell is
# what actually releases it, and it costs nothing on Linux/macOS.
( cd "$ROOT/backend" && nohup "$PY" "${API_ARGS[@]}" >"$OUT" 2>"$ERRLOG" </dev/null & ) >/dev/null 2>&1

# -- 5 - health check -------------------------------------------------------
probe() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/orgs" 2>/dev/null
  else
    "$PY" - "$PORT" <<'EOF' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/orgs", timeout=2).read()
EOF
  fi
}
OK=0
for _ in $(seq 20); do
  sleep 0.5
  if probe; then OK=1; break; fi
done
# "something answers on the port" is NOT proof the restart happened: if the old
# process was never killed the health check passes against the very code we were
# trying to replace, and the script reports success. So compare pids.
NEW_PIDS=$(listeners)
STALE=0
if [ -n "${OLD_PIDS:-}" ] && [ -n "$NEW_PIDS" ]; then
  STALE=1
  for np in $NEW_PIDS; do
    case " $OLD_PIDS " in *" $np "*) : ;; *) STALE=0 ;; esac
  done
fi
if [ "$OK" = 1 ] && [ "$STALE" = 1 ]; then
  printf '
'
  die "the OLD backend is still serving (pid $NEW_PIDS) -- the restart did not take. Stop it and re-run."
fi
if [ "$OK" = 1 ]; then
  printf '\n'
  good "== up: http://localhost:$PORT ($AFTER) =="
else
  printf '\n'
  die "backend did not come up -- check $ERRLOG"
fi
