#!/bin/bash

# Vérifie si le script est exécuté avec les privilèges root
if [ "$EUID" -ne 0 ]; then
  echo "Veuillez exécuter ce script en tant que root ou avec sudo."
  exit
fi

# Installer npm et pm2
echo "Installation de npm et pm2..."
apt update
apt install -y npm
npm install -g pm2

# Installer Python et créer l'environnement virtuel
echo "Installation de Python, pip et venv..."
apt install -y python3 python3-pip python3-venv
python3 -m venv venv
source venv/bin/activate

# Créer un fichier FIFO pour les commandes Minecraft
echo "Création du fichier FIFO pour les commandes Minecraft..."
mkdir -p server
mkfifo server/minecraft_cmd

# Installer les paquets Python nécessaires dans l'environnement virtuel
echo "Installation des paquets Python nécessaires..."
pip install gunicorn gevent

# Lancer l'application avec pm2
echo "Lancement de l'application avec pm2..."
pm2 start "gunicorn -k gevent -w 1 -b 0.0.0.0:5000 app:app" --name "minecraft_server_manager"
pm2 startup
pm2 save

echo "Configuration terminée. Votre serveur Minecraft Manager est prêt."
