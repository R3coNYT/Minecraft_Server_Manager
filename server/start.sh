#!/bin/bash

# Vérifie si le FIFO existe, sinon le crée
FIFO="minecraft_cmd"

if [[ ! -p "$FIFO" ]]; then
    echo "Création du FIFO : $FIFO"
    mkfifo -m 600 "$FIFO"
fi

# Ouvrir le FIFO en arrière-plan pour éviter le blocage
tail -f "$FIFO" | java -Xmx4G -jar mohist.jar nogui | tee server.log &

