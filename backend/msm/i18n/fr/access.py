"""Traductions françaises : droits, propriétaires et membres des serveurs."""

MESSAGES: dict[str, str] = {
    # Refus
    "Your access lacks the “{permission}” permission required to {action}.": "Votre accès n'a pas la permission « {permission} », nécessaire pour {action}.",
    "Your access lacks the “{permission}” permission.": "Votre accès n'a pas la permission « {permission} ».",
    "Ask the server's owner to give you more rights on it.": "Demander au propriétaire du serveur de vous donner plus de droits dessus.",
    "edit this server": "modifier ce serveur",
    "change the server's folder": "changer le dossier du serveur",
    "change how the server is launched": "changer la façon dont le serveur est lancé",
    "choose whether it starts with MSM": "choisir s'il démarre avec MSM",
    "manage who can access this server": "gérer qui a accès à ce serveur",
    "view scheduled tasks": "voir les tâches programmées",
    "view the launcher integration": "voir l'intégration du launcher",
    # Noms de serveurs, uniques par propriétaire
    "You already have a server named “{name}”.": "Vous avez déjà un serveur nommé « {name} ».",
    "Its owner already has a server named “{name}”.": "Son propriétaire a déjà un serveur nommé « {name} ».",
    "Server “{name}”: starts with MSM set to {value}.": "Serveur « {name} » : démarrage avec MSM réglé sur {value}.",
    "yes": "oui",
    "no": "non",
    # Membres
    "Invalid role.": "Rôle invalide.",
    "A server can only be shared as admin or as viewer.": "Un serveur ne se partage qu'en tant qu'admin ou que lecteur.",
    "Choose “admin” or “viewer”.": "Choisir « admin » ou « lecteur ».",
    "No active account is called “{username}”.": "Aucun compte actif ne s'appelle « {username} ».",
    "Check the username: it is the one shown in the top right corner.": "Vérifier le pseudo : c'est celui affiché en haut à droite.",
    "This account owns the server.": "Ce compte est le propriétaire du serveur.",
    "“{username}” already has every right on it.": "« {username} » a déjà tous les droits dessus.",
    "Choose another account.": "Choisir un autre compte.",
    "Server “{server}” shared with {username} ({role}).": "Serveur « {server} » partagé avec {username} ({role}).",
    "Member not found.": "Membre introuvable.",
    "This account has no access to the server.": "Ce compte n'a pas accès au serveur.",
    "Refresh the member list.": "Rafraîchir la liste des membres.",
    "{username} no longer has access to server “{server}”.": "{username} n'a plus accès au serveur « {server} ».",
    # Comptes
    "This account still owns servers.": "Ce compte possède encore des serveurs.",
    "{username} owns {count} server(s): {names}.": "{username} possède {count} serveur(s) : {names}.",
    "Delete its servers first.": "Supprimer d'abord ses serveurs.",
}
