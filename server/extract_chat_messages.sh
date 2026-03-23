#!/bin/bash

# Fichiers de logs
log_file="server.log"
output_file="chat_messages.log"

# Création du fichier de sortie s'il n'existe pas
touch "$output_file"
echo "Script démarré. Surveillance de $log_file pour les nouveaux messages de chat."

# Expressions régulières pour détecter les messages de chat et les messages du serveur
regex_player="^\[[0-9]{2}:[0-9]{2}:[0-9]{2} INFO\]: <([^>]+)> (.+)$"
regex_server="^\[[0-9]{2}:[0-9]{2}:[0-9]{2} INFO\]: \[Server\] (.+)$"

# Lire les messages existants dans le fichier de log avant de commencer la surveillance en temps réel
while read -r line; do
    if [[ $line =~ $regex_player ]]; then
        # Extraire le pseudo et le message pour les messages de joueur
        pseudo="${BASH_REMATCH[1]}"
        message="${BASH_REMATCH[2]}"
        echo "<$pseudo> $message" >> "$output_file"
    elif [[ $line =~ $regex_server ]]; then
        # Extraire le message pour les messages du serveur
        message="${BASH_REMATCH[1]}"
        echo "<Serveur> $message" >> "$output_file"
    fi
done < "$log_file"

# Boucle infinie pour vérifier en continu les nouveaux messages avec `tail`
tail -Fn0 "$log_file" | while read -r line; do
    if [[ $line =~ $regex_player ]]; then
        # Extraire le pseudo et le message pour les messages de joueur
        pseudo="${BASH_REMATCH[1]}"
        message="${BASH_REMATCH[2]}"
        echo "<$pseudo> $message" >> "$output_file"
        echo "Pseudo: $pseudo, Message: $message"
    elif [[ $line =~ $regex_server ]]; then
        # Extraire le message pour les messages du serveur
        message="${BASH_REMATCH[1]}"
        echo "<Serveur> $message" >> "$output_file"
        echo "Serveur: $message"
    fi
done

