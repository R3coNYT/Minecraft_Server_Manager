"""Synchronisation avec le serveur de fichiers d'un launcher personnalisé.

Deux flux, deux sources de vérité :

* le **contenu** du modpack vient du serveur de fichiers du launcher : MSM le
  lit et installe sur le serveur Minecraft ce qui le concerne ;
* l'**état activé / désactivé** d'un mod vient de MSM : il est poussé vers le
  serveur de fichiers, qui le répercute aux joueurs.

Le contrat complet est décrit dans ``docs/LAUNCHER_INTEGRATION.md``.
"""
