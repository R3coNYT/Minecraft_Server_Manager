"""Traductions françaises : hébergement des comptes (dossiers, quotas, ports)."""

MESSAGES: dict[str, str] = {
    # Réglages
    "Hosting settings updated.": "Réglages d'hébergement modifiés.",
    "Invalid port range.": "Plage de ports invalide.",
    "The range must lie between {floor} and 65535, its start before its end.": "La plage doit être comprise entre {floor} et 65535, son début avant sa fin.",
    "Enter a range such as 25565 to 25664.": "Saisir une plage comme 25565 à 25664.",
    "Invalid folder.": "Dossier invalide.",
    "{path} is not an absolute path.": "{path} n'est pas un chemin absolu.",
    "Enter a full path, such as /data/minecraft/users.": "Saisir un chemin complet, comme /data/minecraft/users.",
    "Limits of {username} updated.": "Limites de {username} modifiées.",
    # Ports
    "No free port.": "Aucun port libre.",
    "Every port from {start} to {end} is already taken.": "Tous les ports de {start} à {end} sont déjà pris.",
    "An administrator must widen the port range.": "Un administrateur doit élargir la plage de ports.",
    "server.properties corrected before start ({keys}): the port is {port}, RCON and query stay off.": "server.properties corrigé avant le démarrage ({keys}) : le port est {port}, RCON et query restent désactivés.",
    # Quotas
    "Server limit reached.": "Limite de serveurs atteinte.",
    "Your account can have {count} server(s).": "Votre compte peut avoir {count} serveur(s).",
    "Delete a server, or ask an administrator for more.": "Supprimer un serveur, ou en demander davantage à un administrateur.",
    "Too much memory.": "Trop de mémoire.",
    "A server of this account can use {limit} MB at most.": "Un serveur de ce compte peut utiliser {limit} Mo au plus.",
    "Lower the maximum memory, or ask an administrator for more.": "Réduire la mémoire maximale, ou en demander davantage à un administrateur.",
    "Not enough memory left.": "Plus assez de mémoire disponible.",
    "Its owner's servers online would use {needed} MB, above the {limit} MB allowed.": "Les serveurs en ligne de son propriétaire utiliseraient {needed} Mo, au-delà des {limit} Mo permis.",
    "Stop another server of this account first.": "Arrêter d'abord un autre serveur de ce compte.",
    "Disk space used up.": "Espace disque épuisé.",
    "The servers of this account use {used} MB out of {limit} MB.": "Les serveurs de ce compte utilisent {used} Mo sur {limit} Mo.",
    "Delete backups or files, or ask an administrator for more space.": "Supprimer des sauvegardes ou des fichiers, ou demander plus d'espace à un administrateur.",
}
