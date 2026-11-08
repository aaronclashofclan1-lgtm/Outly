"""Compteur de quota YouTube, local et honnête.

Depuis le 1er juin 2026, l'API utilise un quota « granulaire » :
  * `search.list` : 1 unité par appel, dans un compartiment propre limité à
    100 appels par jour ;
  * toutes les autres méthodes : 1 unité par appel, dans un pool commun de
    10 000 unités par jour.

L'API ne communique jamais le quota restant : ce journal local est la seule
source de vérité. Le jour de référence est celui de Google (minuit heure du
Pacifique), pas celui de la machine.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .models import iso_utc

FUSEAU_PACIFIQUE = ZoneInfo("America/Los_Angeles")

COMPARTIMENT_PRINCIPAL = "principal"
COMPARTIMENT_RECHERCHE = "search"

# Coût et compartiment de chaque méthode utilisée (tous à 1 unité en 2026).
COUTS: dict[str, tuple[str, int]] = {
    "search.list": (COMPARTIMENT_RECHERCHE, 1),
    "channels.list": (COMPARTIMENT_PRINCIPAL, 1),
    "playlistItems.list": (COMPARTIMENT_PRINCIPAL, 1),
    "videos.list": (COMPARTIMENT_PRINCIPAL, 1),
    "channelSections.list": (COMPARTIMENT_PRINCIPAL, 1),
}

# Plafonds officiels, utilisés pour les messages et la commande `quota`.
PLAFOND_RECHERCHES_JOUR = 100
PLAFOND_UNITES_JOUR = 10_000


def compartiment_et_cout(endpoint: str) -> tuple[str, int]:
    return COUTS.get(endpoint, (COMPARTIMENT_PRINCIPAL, 1))


def jour_pacifique(quand: datetime | None = None) -> str:
    """Date (AAAA-MM-JJ) du jour Google en cours, heure du Pacifique."""
    reference = quand or datetime.now(timezone.utc)
    return reference.astimezone(FUSEAU_PACIFIQUE).strftime("%Y-%m-%d")


@dataclass
class Totaux:
    unites_principal: int = 0
    unites_recherche: int = 0

    @property
    def total(self) -> int:
        return self.unites_principal + self.unites_recherche


class ErreurJournalQuota(Exception):
    """Le journal de quota ne peut plus être écrit (disque ou verrou)."""


class BudgetDepasse(Exception):
    """Levée avant un appel qui ferait dépasser un budget configuré.

    L'appelant doit s'arrêter proprement (code de sortie 3), pas planter.
    """

    def __init__(self, message: str, compartiment: str, portee: str):
        super().__init__(message)
        self.compartiment = compartiment
        self.portee = portee  # 'run' ou 'jour'


class CompteurQuota:
    """Journalise chaque appel et refuse ceux qui dépasseraient un budget."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: int | None,
        budget_unites_jour: int = 8000,
        budget_recherches_jour: int = 90,
        budget_unites_run: int = 2000,
        budget_recherches_run: int = 20,
        horloge: Callable[[], datetime] | None = None,
    ):
        self._conn = conn
        self.run_id = run_id
        self.budget_unites_jour = budget_unites_jour
        self.budget_recherches_jour = budget_recherches_jour
        self.budget_unites_run = budget_unites_run
        self.budget_recherches_run = budget_recherches_run
        self._horloge = horloge or (lambda: datetime.now(timezone.utc))

    # --- lecture -----------------------------------------------------------
    def _somme(self, condition: str, params: tuple) -> Totaux:
        lignes = self._conn.execute(
            "SELECT compartiment, SUM(unites) AS unites FROM api_calls "
            f"WHERE {condition} AND depuis_cache = 0 GROUP BY compartiment",
            params,
        ).fetchall()
        totaux = Totaux()
        for ligne in lignes:
            if ligne["compartiment"] == COMPARTIMENT_RECHERCHE:
                totaux.unites_recherche = int(ligne["unites"] or 0)
            else:
                totaux.unites_principal = int(ligne["unites"] or 0)
        return totaux

    def totaux_jour(self) -> Totaux:
        return self._somme("jour = ?", (jour_pacifique(self._horloge()),))

    def totaux_run(self) -> Totaux:
        if self.run_id is None:
            return Totaux()
        return self._somme("run_id = ?", (self.run_id,))

    # --- écriture ----------------------------------------------------------
    def verifier(self, endpoint: str) -> None:
        """Contrôle les budgets avant l'appel. Ne consomme rien."""
        compartiment, cout = compartiment_et_cout(endpoint)
        jour = self.totaux_jour()
        run = self.totaux_run()

        if compartiment == COMPARTIMENT_RECHERCHE:
            utilise_jour, plafond_jour = jour.unites_recherche, self.budget_recherches_jour
            utilise_run, plafond_run = run.unites_recherche, self.budget_recherches_run
            libelle = "recherches"
        else:
            utilise_jour, plafond_jour = jour.unites_principal, self.budget_unites_jour
            utilise_run, plafond_run = run.unites_principal, self.budget_unites_run
            libelle = "unités"

        if utilise_jour + cout > plafond_jour:
            raise BudgetDepasse(
                f"Budget de quota journalier atteint pour les {libelle} : "
                f"{utilise_jour}/{plafond_jour}. Arrêt propre ; relancez demain "
                "(le quota YouTube se réinitialise à minuit, heure du Pacifique).",
                compartiment,
                "jour",
            )
        if utilise_run + cout > plafond_run:
            raise BudgetDepasse(
                f"Budget de quota de cette exécution atteint pour les {libelle} : "
                f"{utilise_run}/{plafond_run}. Relancez avec un budget plus élevé "
                "dans config.yaml si nécessaire.",
                compartiment,
                "run",
            )

    def enregistrer(
        self,
        endpoint: str,
        unites: int,
        depuis_cache: bool = False,
        empreinte: str | None = None,
        http_status: int | None = None,
        erreur: str | None = None,
    ) -> None:
        # Une lecture servie par le cache ne coûte rien : l'écrire dans le
        # journal SQLite à chaque appel ralentissait fortement les runs et
        # sollicitait inutilement un disque externe.
        if depuis_cache:
            return
        compartiment, _ = compartiment_et_cout(endpoint)
        for tentative in range(3):
            try:
                self._conn.execute(
                    "INSERT INTO api_calls (run_id, jour, ts, endpoint, compartiment, unites, "
                    "depuis_cache, empreinte, http_status, erreur) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.run_id,
                        jour_pacifique(self._horloge()),
                        iso_utc(self._horloge()),
                        endpoint,
                        compartiment,
                        max(0, int(unites)),
                        0,
                        empreinte,
                        http_status,
                        erreur,
                    ),
                )
                self._conn.commit()
                return
            except sqlite3.OperationalError:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                if tentative == 2:
                    raise ErreurJournalQuota(
                        "Le journal de quota n'a pas pu être écrit. Le disque ou le "
                        "dossier de la base a refusé l'écriture (disque externe débranché, "
                        "saturé ou verrouillé). Fermez les autres instances d'Outly, "
                        "vérifiez le disque puis relancez."
                    )
                time.sleep(0.25 * (tentative + 1))

    # --- affichage ---------------------------------------------------------
    def rapport(self) -> str:
        jour = self.totaux_jour()
        run = self.totaux_run()
        lignes = [
            f"Quota du jour ({jour_pacifique(self._horloge())}, heure du Pacifique) :",
            f"  pool principal   : {jour.unites_principal} / {self.budget_unites_jour} unités "
            f"(plafond Google : {PLAFOND_UNITES_JOUR})",
            f"  recherches       : {jour.unites_recherche} / {self.budget_recherches_jour} appels "
            f"(plafond Google : {PLAFOND_RECHERCHES_JOUR})",
        ]
        if self.run_id is not None:
            lignes.append("Cette exécution :")
            lignes.append(
                f"  pool principal   : {run.unites_principal} / {self.budget_unites_run} unités"
            )
            lignes.append(
                f"  recherches       : {run.unites_recherche} / {self.budget_recherches_run} appels"
            )
        return "\n".join(lignes)
