#!/bin/bash
# Script pour surveiller minecraft_input.txt et envoyer les messages au serveur Minecraft via le FIFO

FIFO="minecraft_cmd"  # Le FIFO pour envoyer les commandes
INPUT_FILE="minecraft_input.txt"  # Fichier des messages à envoyer

while true; do
  if [ -s "$INPUT_FILE" ]; then
    while read -r line; do
      echo "$line" > "$FIFO"
    done < "$INPUT_FILE"
    # Vider le fichier après envoi des messages
    > "$INPUT_FILE"
  fi
  sleep 1
done

