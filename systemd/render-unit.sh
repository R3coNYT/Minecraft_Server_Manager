#!/usr/bin/env bash
#
# Génère l'unité systemd de MSM à partir du modèle. Partagé par install.sh et
# update.sh : une mise à jour doit installer la nouvelle unité *avant* d'arrêter
# le service, pour que l'arrêt suive déjà les nouvelles règles.
#
#   render-unit.sh MODELE SORTIE UTILISATEUR GROUPE CODE CONFIG DONNEES JOURNAUX SERVEURS
#
# Les chemins ajoutés à la main dans `ReadWritePaths` d'une unité existante —
# des sauvegardes sur un autre disque, une seconde racine de serveurs — sont
# conservés : une mise à jour ne doit pas retirer au service un droit d'écriture
# dont il a besoin.
set -euo pipefail

[[ $# -eq 9 ]] || { echo "Usage : $0 MODELE SORTIE UTILISATEUR GROUPE CODE CONFIG DONNEES JOURNAUX SERVEURS" >&2; exit 2; }
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

# Écriture atomique : un `daemon-reload` ne doit jamais lire une unité tronquée.
printf '%s\n' "$rendered" > "${output}.tmp"
mv "${output}.tmp" "$output"
