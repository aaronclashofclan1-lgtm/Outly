"""Fixtures communes aux tests.

Aucun test ne touche le réseau ni le quota réel : les transports HTTP sont
remplacés par des files de réponses et l'horloge est figée.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from outly import db
from outly.config import Config
from outly.llm.client import ReponseHttpLlm
from outly.youtube_api import ReponseHttp

DEBUT = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class HorlogeFactice:
    """Horloge contrôlable : les tests décident quand le temps passe."""

    def __init__(self, debut: datetime = DEBUT):
        self._maintenant = debut

    def maintenant(self) -> datetime:
        return self._maintenant

    def avancer(self, secondes: float) -> None:
        self._maintenant += timedelta(seconds=secondes)


class TempsMonotone:
    """Horloge monotone + sommeil factice, pour le rythme du client LLM."""

    def __init__(self):
        self.valeur = 0.0
        self.dormies: list[float] = []

    def maintenant(self) -> float:
        return self.valeur

    def dormir(self, secondes: float) -> None:
        self.dormies.append(secondes)
        self.valeur += secondes


class TransportFactice:
    """Transport HTTP YouTube piloté par des réponses programmées.

    Les réponses sont consommées dans l'ordre ; un tuple vide lève une erreur
    explicite plutôt que de simuler silencieusement un succès.
    """

    def __init__(self, reponses: list[ReponseHttp] | None = None):
        self.reponses = list(reponses or [])
        self.appels: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict, timeout: int) -> ReponseHttp:
        self.appels.append((url, dict(params)))
        if not self.reponses:
            raise AssertionError(f"Aucune réponse programmée pour {url}")
        return self.reponses.pop(0)


class TransportLlmFactice:
    def __init__(self, reponses: list[ReponseHttpLlm] | None = None):
        self.reponses = list(reponses or [])
        self.requetes: list[dict] = []

    def __call__(self, url: str, entetes: dict, corps: dict, timeout: int) -> ReponseHttpLlm:
        self.requetes.append({"url": url, "entetes": entetes, "corps": corps})
        if not self.reponses:
            raise AssertionError(f"Aucune réponse LLM programmée pour {url}")
        return self.reponses.pop(0)


def reponse_llm(contenu: str, statut: int = 200) -> ReponseHttpLlm:
    return ReponseHttpLlm(
        statut,
        {"choices": [{"message": {"content": contenu}}]} if statut == 200 else {"error": {"message": contenu}},
    )


@pytest.fixture
def connexion() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.migrer(conn)
    yield conn
    conn.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        racine=tmp_path,
        fichier_db=tmp_path / "data" / "test.sqlite3",
        dossier_sortie=tmp_path / "out",
    )


@pytest.fixture
def horloge() -> HorlogeFactice:
    return HorlogeFactice()


def dates_recentes(nombre: int, intervalle_jours: int = 7) -> list[str]:
    """N dates ISO espacées, de la plus récente à la plus ancienne."""
    return [
        (DEBUT - timedelta(days=intervalle_jours * rang)).isoformat(timespec="seconds")
        for rang in range(nombre)
    ]
