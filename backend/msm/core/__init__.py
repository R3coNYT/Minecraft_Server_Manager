"""Domaine métier de MSM.

Ce paquet ne dépend **d'aucun** framework (ni FastAPI, ni SQLAlchemy, ni asyncio) :
il contient la logique pure — états, permissions, analyse de logs, construction de
commandes. C'est ce qui le rend intégralement testable sans démarrer d'application.
"""

from msm.core.states import ServerState

__all__ = ["ServerState"]
