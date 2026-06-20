#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-biblio-admin.test}"
PORT="${2:-8001}"
CADDYFILE="/opt/homebrew/etc/Caddyfile"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo $0 ${DOMAIN} ${PORT}" >&2
  exit 1
fi

cp /etc/hosts "/etc/hosts.bibliobon-data.${STAMP}.bak"
cp "${CADDYFILE}" "${CADDYFILE}.bibliobon-data.${STAMP}.bak"

if ! grep -Eq "^[[:space:]]*127\\.0\\.0\\.1[[:space:]].*\\b${DOMAIN}\\b" /etc/hosts; then
  printf "\n127.0.0.1 %s\n" "${DOMAIN}" >> /etc/hosts
fi

if ! grep -Fq "http://${DOMAIN}" "${CADDYFILE}"; then
  {
    printf "\nhttp://%s {\n" "${DOMAIN}"
    printf "\treverse_proxy localhost:%s\n" "${PORT}"
    printf "}\n"
  } >> "${CADDYFILE}"
fi

if command -v brew >/dev/null 2>&1; then
  brew services restart caddy
else
  caddy reload --config "${CADDYFILE}"
fi

curl -I "http://${DOMAIN}"
