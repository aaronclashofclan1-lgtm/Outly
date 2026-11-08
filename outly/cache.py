"""Cache local des réponses de l'API YouTube.

Objectif : ne jamais redemander une donnée déjà récupérée récemment, donc ne
jamais dépenser deux fois le même quota.

Contrainte légale : les Developer Policies YouTube (section III.E.4) imposent de
rafraîchir ou supprimer les données API sous 30 jours. Le TTL est donc plafonné
à 30 jours, quoi qu'on écrive dans config.yaml.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

PLAFOND_LEGAL_JOURS = 30


def _empreinte(endpoint: str, params: dict[str, Any]) -> str:
    """Empreinte stable d'une requête, indépendante de l'ordre des paramètres.

    La clé API ne doit jamais entrer dans les paramètres transmis ici : elle
    n'influence pas la réponse et n'a rien à faire dans le cache.
    """
    canonique = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{endpoint}|{canonique}".encode("utf-8")).hexdigest()


class Cache:
    def __init__(
        self,
        conn: sqlite3.Connection,
        horloge: Callable[[], datetime] | None = None,
        plafond_jours: int = PLAFOND_LEGAL_JOURS,
    ):
        self._conn = conn
        self._horloge = horloge or (lambda: datetime.now(timezone.utc))
        self.plafond_jours = min(plafond_jours, PLAFOND_LEGAL_JOURS)

    @staticmethod
    def empreinte(endpoint: str, params: dict[str, Any]) -> str:
        return _empreinte(endpoint, params)

    def lire(self, empreinte: str) -> dict | None:
        """Renvoie la charge utile si elle existe encore, sinon None."""
        maintenant = self._horloge().astimezone(timezone.utc)
        ligne = self._conn.execute(
            "SELECT charge, expire_le FROM cache WHERE empreinte = ?", (empreinte,)
        ).fetchone()
        if ligne is None:
            return None
        try:
            expiration = datetime.fromisoformat(ligne["expire_le"])
        except ValueError:
            return None
        if expiration <= maintenant:
            # Nettoyage opportuniste : une entrée périmée ne sert plus à rien.
            self._conn.execute("DELETE FROM cache WHERE empreinte = ?", (empreinte,))
            self._conn.commit()
            return None
        try:
            return json.loads(ligne["charge"])
        except json.JSONDecodeError:
            return None

    def ecrire(
        self,
        empreinte: str,
        endpoint: str,
        charge: Any,
        ttl_jours: int,
    ) -> None:
        maintenant = self._horloge().astimezone(timezone.utc)
        jours = max(1, min(int(ttl_jours), self.plafond_jours))
        expiration = maintenant + timedelta(days=jours)
        self._conn.execute(
            "INSERT INTO cache (empreinte, endpoint, charge, recupere_le, expire_le) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(empreinte) DO UPDATE SET charge = excluded.charge, "
            "recupere_le = excluded.recupere_le, expire_le = excluded.expire_le",
            (
                empreinte,
                endpoint,
                json.dumps(charge, ensure_ascii=False),
                maintenant.isoformat(timespec="seconds"),
                expiration.isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def purger(self, plus_vieux_que_jours: int = PLAFOND_LEGAL_JOURS) -> int:
        """Supprime les entrées plus anciennes que N jours. Renvoie le compte."""
        jours = max(0, min(int(plus_vieux_que_jours), 3650))
        limite = self._horloge().astimezone(timezone.utc) - timedelta(days=jours)
        curseur = self._conn.execute(
            "DELETE FROM cache WHERE recupere_le < ?", (limite.isoformat(timespec="seconds"),)
        )
        self._conn.commit()
        return int(curseur.rowcount or 0)

    def vider(self) -> int:
        curseur = self._conn.execute("DELETE FROM cache")
        self._conn.commit()
        return int(curseur.rowcount or 0)

    def compter(self) -> int:
        ligne = self._conn.execute("SELECT COUNT(*) AS n FROM cache").fetchone()
        return int(ligne["n"]) if ligne else 0
