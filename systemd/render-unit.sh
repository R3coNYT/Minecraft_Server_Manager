#!/usr/bin/env bash
#
# Renders MSM's systemd unit from the template. Shared by install.sh and
# update.sh: an update must install the new unit *before* stopping the service,
# so that stopping already follows the new rules.
#
#   render-unit.sh TEMPLATE OUTPUT USER GROUP CODE CONFIG DATA LOGS SERVERS
#
# Paths added by hand to `ReadWritePaths` in an existing unit — backups on
# another disk, a second servers root — are kept: an update must not take away
# a write permission the service needs.
set -euo pipefail

[[ $# -eq 9 ]] || { echo "Usage: $0 TEMPLATE OUTPUT USER GROUP CODE CONFIG DATA LOGS SERVERS" >&2; exit 2; }
template="$1" output="$2" user="$3" group="$4" home="$5" config="$6" data="$7" logs="$8" servers="$9"

rendered="$(sed -e "s|__MSM_USER__|${user}|g" \
                -e "s|__MSM_GROUP__|${group}|g" \
                -e "s|__MSM_HOME__|${home}|g" \
                -e "s|__MSM_CONFIG__|${config}|g" \
                -e "s|__MSM_DATA__|${data}|g" \
                -e "s|__MSM_LOGS__|${logs}|g" \
                -e "s|__MSM_SERVERS__|${servers}|g" \
                "$template")"

if [[ -f "$output" ]]; then
  existing="$(grep -m1 '^ReadWritePaths=' "$output" | cut -d= -f2- || true)"
  wanted="$(printf '%s\n' "$rendered" | grep -m1 '^ReadWritePaths=' | cut -d= -f2-)"
  merged="$wanted"
  for path in $existing; do
    [[ " $merged " == *" $path "* ]] || merged="$merged $path"
  done
  rendered="$(printf '%s\n' "$rendered" | awk -v line="ReadWritePaths=$merged" \
    '/^ReadWritePaths=/ { print line; next } { print }')"
fi

# Atomic write: a `daemon-reload` must never read a truncated unit.
printf '%s\n' "$rendered" > "${output}.tmp"
mv "${output}.tmp" "$output"
