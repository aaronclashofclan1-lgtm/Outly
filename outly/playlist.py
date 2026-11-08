"""Playlist locale « À regarder plus tard ».

Une seule table SQLite (`watch_later`) stocke les vidéos ET les chaînes mises
de côté. Aucun appel réseau : ajouter un élément ne coûte pas une unité de
quota et fonctionne même quand le LLM ou YouTube sont indisponibles.

Le type est volontairement limité à `video` et `channel` : la contrainte CHECK
du schéma empêche une faute de frappe de créer une entrée fantôme.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import db
from .models import iso_utc

TYPES = ("video", "channel")
NOTE_MAX = 10


class ErreurPlaylist(Exception):
    """Ajout ou retrait impossible, avec un message affichable tel quel."""


@dataclass
class Element:
    """Une entrée de la playlist, telle que l'interface l'affiche."""

    element_id: str
    type: str
    titre: str
    sous_titre: str
    url: str
    channel_id: str | None
    ajoute_le: str
    meta: dict
    note_sur_10: int | None = None

    @property
    def etiquette_type(self) -> str:
        return "Vidéo" if self.type == "video" else "Chaîne"

    @property
    def note_lisible(self) -> str:
        return f"{self.note_sur_10}/10" if self.note_sur_10 is not None else "Non notée"


def _chemin(racine: Path) -> Path:
    """Base SQLite d'Outly, au même emplacement que la CLI et l'interface."""
    return Path(racine) / "data" / "radar.sqlite3"


def _connexion(racine: Path) -> sqlite3.Connection:
    conn = db.connexion(_chemin(racine))
    db.migrer(conn)
    return conn


@contextmanager
def _base(racine: Path):
    """Connexion courte, toujours fermée (même en cas d'erreur)."""
    conn = _connexion(racine)
    try:
        yield conn
    finally:
        conn.close()


def _normaliser_note(valeur: int | float | str | None) -> int | None:
    """Classement 0 à 10, ou None : même barème que les notes éditoriales."""
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        raise ErreurPlaylist("La note doit être un entier de 0 à 10.")
    if isinstance(valeur, int):
        note = valeur
    elif isinstance(valeur, float):
        if not float(valeur).is_integer():
            raise ErreurPlaylist("La note doit être un entier de 0 à 10.")
        note = int(valeur)
    else:
        texte = str(valeur).strip().casefold().replace("/10", "").strip()
        if texte in {"", "non notée", "non notee", "aucune", "-", "—"}:
            return None
        try:
            note = int(texte)
        except ValueError as probleme:
            raise ErreurPlaylist("La note doit être un entier de 0 à 10.") from probleme
    if not 0 <= note <= NOTE_MAX:
        raise ErreurPlaylist("La note doit être comprise entre 0 et 10.")
    return int(note)


def _verifier(type_element: str, titre: str) -> None:
    if type_element not in TYPES:
        raise ErreurPlaylist(f"Type de playlist inconnu : {type_element!r}.")
    if not str(titre).strip():
        raise ErreurPlaylist("Impossible d'enregistrer un élément sans titre.")


def ajouter(
    racine: Path,
    *,
    element_id: str,
    type: str,
    titre: str,
    sous_titre: str = "",
    url: str = "",
    channel_id: str | None = None,
    meta: dict | None = None,
    note_sur_10: int | str | None = None,
) -> None:
    """Ajoute ou rafraîchit une entrée. Un doublon n'est jamais créé.

    Réenregistrer un élément déjà présent ne touche jamais à son classement :
    un ajout depuis « Chaînes » ne doit pas effacer une note mise à la main.
    """
    _verifier(type, titre)
    if not str(element_id).strip():
        raise ErreurPlaylist("Impossible d'enregistrer un élément sans identifiant.")
    try:
        with _base(racine) as conn:
            conn.execute(
                """
                INSERT INTO watch_later (
                    element_id, type, titre, sous_titre, url, channel_id,
                    meta_json, note_sur_10, ajoute_le
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(element_id, type) DO UPDATE SET
                    titre      = excluded.titre,
                    sous_titre = excluded.sous_titre,
                    url        = excluded.url,
                    channel_id = excluded.channel_id,
                    meta_json  = excluded.meta_json,
                    ajoute_le  = excluded.ajoute_le
                """,
                (
                    str(element_id),
                    type,
                    str(titre).strip(),
                    str(sous_titre or "").strip(),
                    str(url or "").strip(),
                    channel_id,
                    json.dumps(meta, ensure_ascii=False, sort_keys=True) if meta else None,
                    _normaliser_note(note_sur_10),
                    iso_utc(),
                ),
            )
            conn.commit()
    except sqlite3.Error as probleme:
        raise ErreurPlaylist(f"Écriture dans la playlist impossible : {probleme}") from probleme


def ajouter_video(racine: Path, video: dict, chaine_titre: str = "") -> None:
    """Enregistre une vidéo outlier, avec son score et ses compteurs."""
    video_id = str(video.get("video_id") or "").strip()
    if not video_id:
        raise ErreurPlaylist("Cette vidéo n'a pas d'identifiant exploitable.")
    format_video = "Short" if video.get("est_short") else "longue"
    details = [chaine_titre] if chaine_titre else []
    score = video.get("score_outlier")
    if score is not None:
        details.append("×" + f"{float(score):.1f}".replace(".", ","))
    vues = video.get("vues")
    if vues is not None:
        details.append(f"{int(vues):,} vues".replace(",", " "))
    details.append(format_video)
    ajouter(
        racine,
        element_id=video_id,
        type="video",
        titre=str(video.get("titre") or "Vidéo sans titre").strip(),
        sous_titre=" · ".join(details),
        url=f"https://www.youtube.com/watch?v={video_id}",
        channel_id=video.get("channel_id"),
        meta={
            "score_outlier": score,
            "vues": vues,
            "duree_s": video.get("duree_s"),
            "publie_le": video.get("publie_le"),
            "est_short": bool(video.get("est_short")),
        },
    )


def ajouter_chaine(
    racine: Path,
    channel_id: str,
    titre: str,
    handle: str | None = None,
    abonnes: int | None = None,
    description: str | None = None,
) -> None:
    """Enregistre une chaîne, avec son handle et son audience si connus."""
    if not channel_id:
        raise ErreurPlaylist("Cette chaîne n'a pas d'identifiant exploitable.")
    details = []
    if handle:
        details.append("@" + handle.lstrip("@"))
    if abonnes is not None:
        details.append(f"{int(abonnes):,} abonnés".replace(",", " "))
    ajouter(
        racine,
        element_id=channel_id,
        type="channel",
        titre=titre,
        sous_titre=" · ".join(details),
        url=f"https://www.youtube.com/channel/{channel_id}",
        channel_id=channel_id,
        meta={"description": (description or "")[:4000], "handle": handle, "abonnes": abonnes},
    )


def lister(racine: Path, limite: int = 500) -> list[Element]:
    """Renvoie les entrées, de la plus récemment ajoutée à la plus ancienne."""
    chemin = _chemin(racine)
    if not chemin.exists():
        return []
    try:
        with _base(racine) as conn:
            lignes = conn.execute(
                "SELECT * FROM watch_later "
                "ORDER BY note_sur_10 IS NULL, note_sur_10 DESC, ajoute_le DESC, rowid DESC "
                "LIMIT ?",
                (int(limite),),
            ).fetchall()
    except sqlite3.Error:
        return []
    resultat: list[Element] = []
    for ligne in lignes:
        try:
            meta = json.loads(ligne["meta_json"]) if ligne["meta_json"] else {}
        except (TypeError, ValueError):
            meta = {}
        resultat.append(
            Element(
                element_id=str(ligne["element_id"]),
                type=str(ligne["type"]),
                titre=str(ligne["titre"]),
                sous_titre=str(ligne["sous_titre"] or ""),
                url=str(ligne["url"] or ""),
                channel_id=ligne["channel_id"],
                ajoute_le=str(ligne["ajoute_le"]),
                meta=meta if isinstance(meta, dict) else {},
                note_sur_10=(
                    int(ligne["note_sur_10"])
                    if ligne["note_sur_10"] is not None
                    else None
                ),
            )
        )
    return resultat


def noter(
    racine: Path, element_id: str, type: str, note_sur_10: int | str | None
) -> bool:
    """Classe un élément sur 10 (None efface la note). Renvoie True si modifié."""
    if type not in TYPES:
        raise ErreurPlaylist(f"Type de playlist inconnu : {type!r}.")
    note = _normaliser_note(note_sur_10)
    try:
        with _base(racine) as conn:
            curseur = conn.execute(
                "UPDATE watch_later SET note_sur_10 = ? WHERE element_id = ? AND type = ?",
                (note, str(element_id), type),
            )
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurPlaylist(f"Classement impossible : {probleme}") from probleme


def contexte_pour_cerveau(
    conn: sqlite3.Connection, limite: int = 25
) -> tuple[str, list[dict]]:
    """Vidéos et chaînes mises de côté, les mieux notées d'abord.

    Renvoie le bloc lisible par le LLM et un instantané léger servant à
    l'empreinte de cache : modifier une note invalide donc la session.
    """
    lignes = conn.execute(
        "SELECT * FROM watch_later "
        "ORDER BY note_sur_10 IS NULL, note_sur_10 DESC, ajoute_le DESC "
        "LIMIT ?",
        (int(limite),),
    ).fetchall()
    if not lignes:
        return "(aucune vidéo mise de côté)", []
    morceaux: list[str] = []
    instantane: list[dict] = []
    for ligne in lignes:
        note = (
            int(ligne["note_sur_10"]) if ligne["note_sur_10"] is not None else None
        )
        etiquette = "Vidéo" if ligne["type"] == "video" else "Chaîne"
        details = " · ".join(
            morceau
            for morceau in (str(ligne["sous_titre"] or ""), str(ligne["url"] or ""))
            if morceau
        )
        classement = f"classement {note}/10" if note is not None else "non notée"
        morceau = f"- [{etiquette}] « {ligne['titre']} » — {classement}"
        if details:
            morceau += f" · {details}"
        morceaux.append(morceau)
        instantane.append(
            {
                "element_id": str(ligne["element_id"]),
                "type": str(ligne["type"]),
                "titre": str(ligne["titre"]),
                "note_sur_10": note,
            }
        )
    return "\n".join(morceaux), instantane


def retirer(racine: Path, element_id: str, type: str) -> bool:
    """Retire une entrée. Renvoie True si une ligne a réellement disparu."""
    try:
        with _base(racine) as conn:
            curseur = conn.execute(
                "DELETE FROM watch_later WHERE element_id = ? AND type = ?",
                (str(element_id), type),
            )
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurPlaylist(f"Retrait impossible : {probleme}") from probleme


def vider(racine: Path) -> int:
    """Vide la playlist et renvoie le nombre d'entrées supprimées."""
    try:
        with _base(racine) as conn:
            curseur = conn.execute("DELETE FROM watch_later")
            conn.commit()
            return int(curseur.rowcount)
    except sqlite3.Error as probleme:
        raise ErreurPlaylist(f"Suppression impossible : {probleme}") from probleme


def contient(racine: Path, element_id: str, type: str) -> bool:
    """Vrai si l'élément est déjà dans la playlist (sans lever d'exception)."""
    try:
        with _base(racine) as conn:
            ligne = conn.execute(
                "SELECT 1 FROM watch_later WHERE element_id = ? AND type = ?",
                (str(element_id), type),
            ).fetchone()
            return ligne is not None
    except sqlite3.Error:
        return False
