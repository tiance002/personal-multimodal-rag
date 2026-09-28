#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  printf '%s\n' 'installer must run as root' >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ROOT=/srv/rag-eval
MARKER="$ROOT/.managed-by-eval-center"
SERVICE_SOURCE="$SCRIPT_DIR/rag-eval.service"
SERVICE_TARGET=/etc/systemd/system/rag-eval.service

CODE_SHA="$(cat "$PAYLOAD_ROOT/CODE_SHA")"
if [[ ! -d "$PAYLOAD_ROOT/eval_center" ]] || [[ ! "$CODE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' 'deployment payload is incomplete' >&2
  exit 1
fi

if [[ -e "$ROOT" && ! -f "$MARKER" ]]; then
  if find "$ROOT" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    printf '%s\n' 'target directory is non-empty and is not managed by this installer' >&2
    exit 1
  fi
fi

if ! getent passwd rag-eval >/dev/null; then
  if ! getent group rag-eval >/dev/null; then
    groupadd --system rag-eval
  fi
  useradd --system --gid rag-eval --home-dir "$ROOT" --no-create-home --shell /usr/sbin/nologin rag-eval
elif ! getent group rag-eval >/dev/null; then
  printf '%s\n' 'rag-eval account exists without its expected group' >&2
  exit 1
fi

if [[ -e "$SERVICE_TARGET" && ! -f "$MARKER" ]]; then
  printf '%s\n' 'refusing to replace an unmanaged service' >&2
  exit 1
fi

install -d -o root -g rag-eval -m 0750 "$ROOT" "$ROOT/releases" "$ROOT/configs"
install -d -o root -g rag-eval -m 2750 "$ROOT/incoming"
install -d -o rag-eval -g rag-eval -m 0700 "$ROOT/datasets" "$ROOT/experiments" "$ROOT/reports" "$ROOT/logs"
install -d -o root -g root -m 0700 "$ROOT/backups"

APP_DIGEST="$(cd "$PAYLOAD_ROOT"; { find eval_center -type f -print0 | sort -z | xargs -0 sha256sum; sha256sum CODE_SHA; } | sha256sum | cut -d ' ' -f 1)"
RELEASE_DIR="$ROOT/releases/$APP_DIGEST"
if [[ -e "$RELEASE_DIR" ]]; then
  if [[ ! -f "$RELEASE_DIR/.release-id" ]] || [[ "$(cat "$RELEASE_DIR/.release-id")" != "$APP_DIGEST" ]]; then
    printf '%s\n' 'an incomplete or unrecognized release directory already exists' >&2
    exit 1
  fi
else
  install -d -o root -g rag-eval -m 0750 "$RELEASE_DIR" "$RELEASE_DIR/eval_center"
  cp -a "$PAYLOAD_ROOT/eval_center/." "$RELEASE_DIR/eval_center/"
  cp "$PAYLOAD_ROOT/CODE_SHA" "$RELEASE_DIR/CODE_SHA"
  printf '%s\n' "$APP_DIGEST" > "$RELEASE_DIR/.release-id"
  chown -R root:rag-eval "$RELEASE_DIR"
  find "$RELEASE_DIR" -type d -exec chmod 0750 {} +
  find "$RELEASE_DIR" -type f -exec chmod 0640 {} +
fi

if [[ -e "$ROOT/app" && ! -L "$ROOT/app" ]]; then
  printf '%s\n' 'app path exists and is not a managed symlink' >&2
  exit 1
fi
if [[ -e "$ROOT/app.next" && ! -L "$ROOT/app.next" ]]; then
  printf '%s\n' 'app.next path exists and is not a symlink' >&2
  exit 1
fi

# Preserve the live registry, old release and unit before additive migration.
BACKUP_DIR="$ROOT/backups/deploy-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -o root -g root -m 0700 "$BACKUP_DIR"
readlink "$ROOT/app" > "$BACKUP_DIR/previous-release" || true
if [[ -f "$SERVICE_TARGET" ]]; then cp -a "$SERVICE_TARGET" "$BACKUP_DIR/rag-eval.service"; fi
if [[ -f "$ROOT/configs/runtime.env" ]]; then cp -a "$ROOT/configs/runtime.env" "$BACKUP_DIR/runtime.env"; fi
if [[ -f "$ROOT/experiments/registry.sqlite3" ]]; then
  python3 - "$ROOT/experiments/registry.sqlite3" "$BACKUP_DIR/registry.sqlite3" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('backup integrity failed')
PY
fi
printf 'EVAL_CENTER_CODE_SHA=%s\n' "$CODE_SHA" > "$ROOT/configs/runtime.env"
chown root:rag-eval "$ROOT/configs/runtime.env"
chmod 0640 "$ROOT/configs/runtime.env"
ln -sfn "$RELEASE_DIR" "$ROOT/app.next"
mv -Tf "$ROOT/app.next" "$ROOT/app"

printf '%s\n' 'rag-eval managed deployment v1' > "$MARKER"
chown root:rag-eval "$MARKER"
chmod 0640 "$MARKER"
install -o root -g root -m 0644 "$SERVICE_SOURCE" "$SERVICE_TARGET"
systemctl daemon-reload
systemctl enable rag-eval.service
systemctl restart rag-eval.service

ready=0
for _attempt in $(seq 1 15); do
  if curl --fail --silent http://127.0.0.1:8787/healthz | python3 -c 'import json,sys; raise SystemExit(json.load(sys.stdin).get("git_sha") != sys.argv[1])' "$CODE_SHA"; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  systemctl --no-pager --full status rag-eval.service >&2 || true
  printf '%s\n' 'loopback health check failed' >&2
  exit 1
fi

printf 'rag-eval active on 127.0.0.1:8787 code=%s backup=%s release=%s\n' "$CODE_SHA" "$BACKUP_DIR" "$APP_DIGEST"
