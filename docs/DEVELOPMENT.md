# Développement

## Prérequis

- Python 3.11 ou supérieur
- Git
- (facultatif) Java, uniquement pour tester avec de vrais serveurs Minecraft —
  la suite de tests n'en a **pas** besoin

## Installation

```bash
cd backend
python -m venv .venv
```

Activation de l'environnement :

```bash
source .venv/bin/activate
```

Sous Windows (PowerShell) :

```bash
.venv\Scripts\activate
```

Puis :

```bash
pip install -e ".[dev]"
```

Sous Windows, les extras `windows` ajoutent l'arrêt atomique par Job Object et le
support ConPTY. Ils sont facultatifs — sans eux, MSM se replie sur un parcours de
l'arbre des processus :

```bash
pip install -e ".[dev,windows]"
```

## Configuration

```bash
cp .env.example .env
```

Générer une clé secrète et la renseigner dans `.env` :

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

En développement, une clé éphémère est générée automatiquement si le champ reste
vide. En production, son absence empêche le démarrage — c'est volontaire.

## Base de données

```bash
alembic upgrade head
```

Après toute modification d'un modèle :

```bash
alembic revision --autogenerate -m "description du changement"
```

Relire systématiquement la migration générée : l'autogénération ne détecte ni les
renommages de colonnes ni les migrations de données.

## Tests

```bash
pytest
```

Uniquement les tests unitaires (rapides, aucun processus lancé) :

```bash
pytest tests/unit -q
```

Avec la couverture :

```bash
pytest --cov=msm --cov-report=term-missing
```

Les tests d'intégration lancent de **vrais processus**, pilotés par
`tests/fixtures/fake_minecraft_server.py` — un faux serveur Minecraft en Python
qui reproduit le format de logs, le message de fin de démarrage et la réponse à
`stop`. Il permet de vérifier l'isolation des processus en quelques secondes,
sans Java, et sur les deux systèmes d'exploitation.

Le test le plus important du dépôt est
`test_stop_only_affects_target_server` : il garantit qu'arrêter un serveur n'en
touche aucun autre. S'il devient rouge, rien d'autre n'a d'importance.

## Qualité

```bash
ruff check .
```

```bash
ruff format .
```

```bash
mypy msm
```

## Lancer le backend

```bash
python -m msm.main
```

L'API répond alors sur <http://127.0.0.1:8000/api/v1/health> et la documentation
interactive sur <http://127.0.0.1:8000/api/docs> (désactivée en production).

> **Un seul worker.** MSM détient les tubes d'entrée et de sortie des serveurs
> Minecraft. Avec plusieurs workers, chaque processus n'aurait qu'une vision
> partielle et personne ne saurait qui possède quel PID.

## Conventions

| Règle | Raison |
|---|---|
| `msm/core/` ne dépend d'aucun framework | permet de tester le domaine sans démarrer d'application |
| Aucune commande shell construite par concaténation | `argv` est toujours une liste ; aucun interpréteur ne s'intercale |
| Aucun arrêt par motif (`pkill -f`) | un signal ne cible qu'un identifiant de groupe précis |
| Toute erreur métier porte `cause` et `remediation` | l'interface affiche « Cause / Action » sans cas particulier |
| Les commentaires expliquent *pourquoi*, pas *quoi* | le code dit déjà ce qu'il fait |
| Français pour les messages destinés à l'utilisateur | l'interface est en français |
