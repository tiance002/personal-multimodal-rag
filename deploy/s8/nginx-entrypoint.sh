#!/bin/sh
set -eu
token=$(cat /run/secrets/api_token)
case "$token" in ''|*[!A-Za-z0-9_-]*) echo 'S8_PRIVATE_TOKEN_INVALID' >&2; exit 1;; esac
[ "${#token}" -ge 32 ] && [ "${#token}" -le 256 ] || exit 1
[ -s /run/secrets/ui.htpasswd ] || { echo 'S8_UI_PASSWORD_FILE_REQUIRED' >&2; exit 1; }
umask 077
# Shell builtins keep the token out of subprocess arguments and frontend assets.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    *__S8_API_TOKEN__*) printf '%s%s%s\n' "${line%%__S8_API_TOKEN__*}" "$token" "${line#*__S8_API_TOKEN__}";;
    *) printf '%s\n' "$line";;
  esac
done < /etc/nginx/s8.conf.template > /etc/nginx/conf.d/default.conf
unset token
exec nginx -g 'daemon off;'
