"""Création d'un serveur Minecraft de zéro.

Un serveur « ajouté » existe déjà sur le disque ; un serveur **créé** part d'un
dossier vide : MSM le crée, y télécharge la distribution choisie (Vanilla,
Paper, Purpur, Fabric, NeoForge, Mohist, Youer), lance l'installeur quand il y
en a un (NeoForge), écrit le CLUF et le port, puis l'enregistre.

* :mod:`jobs` — la tâche de fond, ses étapes et leur état, lus par l'interface ;
* :mod:`files` — les écritures dans le dossier (CLUF, port, mémoire) ;
* :mod:`installer` — l'exécution de l'installeur officiel de NeoForge.

L'orchestration vit dans :mod:`msm.services.provisioning_service`.
"""
