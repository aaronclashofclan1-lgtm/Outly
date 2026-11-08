"""Structures de données échangées entre les couches (API, base, LLM, rapport).

Aucune dépendance externe : uniquement des dataclasses et des analyseurs de
formats renvoyés par l'API YouTube (durées ISO 8601, compteurs sous forme de
chaînes, dates RFC 3339).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Exemple renvoyé par l'API : "PT1H2M3S", "PT45S", "P1DT2H"
_MOTIF_DUREE = re.compile(
    r"^P(?:(?P<jours>\d+)D)?(?:T(?:(?P<heures>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<secondes>\d+)S)?)?$"
)


def entier_ou_none(valeur: object) -> int | None:
    """Convertit un compteur de l'API en entier, sans jamais lever d'exception.

    L'API renvoie les statistiques sous forme de chaînes ("1234") et omet
    parfois le champ quand la valeur est masquée.
    """
    if valeur is None:
        return None
    try:
        return int(str(valeur))
    except (TypeError, ValueError):
        return None


def duree_iso_en_secondes(valeur: str | None) -> int | None:
    """Traduit une durée ISO 8601 de l'API ("PT1H2M3S") en secondes."""
    if not valeur:
        return None
    correspondance = _MOTIF_DUREE.match(valeur.strip())
    if not correspondance:
        return None
    morceaux = {cle: int(val or 0) for cle, val in correspondance.groupdict().items()}
    return (
        morceaux["jours"] * 86400
        + morceaux["heures"] * 3600
        + morceaux["minutes"] * 60
        + morceaux["secondes"]
    )


def date_iso(valeur: str | None) -> datetime | None:
    """Analyse une date RFC 3339 de l'API et la renvoie en UTC."""
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_utc(quand: datetime | None = None) -> str:
    """Horodatage ISO 8601 en UTC, tel qu'il est stocké en base."""
    return (quand or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Chaine:
    """Une chaîne candidate ou suivie."""

    channel_id: str
    titre: str
    handle: str | None = None
    description: str | None = None
    pays: str | None = None
    cree_le: str | None = None
    abonnes: int | None = None
    abonnes_masques: bool = False
    nb_videos: int | None = None
    vues_totales: int | None = None
    playlist_uploads: str | None = None
    mots_cles_chaine: str | None = None
    sujets: list[str] = field(default_factory=list)
    est_seed: bool = False
    source: str | None = None
    score_heuristique: float | None = None
    langue_estimee: str | None = None

    @classmethod
    def depuis_api(cls, item: dict, source: str | None = None, est_seed: bool = False) -> "Chaine":
        """Construit une chaîne depuis un « channel resource » de l'API."""
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        details = item.get("contentDetails") or {}
        uploads = ((details.get("relatedPlaylists") or {}).get("uploads")) or None
        marque = bool(stats.get("hiddenSubscriberCount"))

        return cls(
            channel_id=item.get("id", ""),
            titre=(snippet.get("title") or "(sans titre)").strip(),
            handle=(snippet.get("customUrl") or None),
            description=snippet.get("description") or None,
            pays=snippet.get("country") or None,
            cree_le=snippet.get("publishedAt") or None,
            abonnes=entier_ou_none(stats.get("subscriberCount")),
            abonnes_masques=marque,
            nb_videos=entier_ou_none(stats.get("videoCount")),
            vues_totales=entier_ou_none(stats.get("viewCount")),
            playlist_uploads=uploads,
            mots_cles_chaine=((item.get("brandingSettings") or {}).get("channel") or {}).get("keywords"),
            sujets=(item.get("topicDetails") or {}).get("topicCategories") or [],
            est_seed=est_seed,
            source=source,
        )

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/channel/{self.channel_id}"


@dataclass
class Video:
    """Une vidéo rattachée à une chaîne."""

    video_id: str
    channel_id: str
    titre: str
    description: str | None = None
    publie_le: str | None = None
    duree_s: int | None = None
    vues: int | None = None
    likes: int | None = None
    commentaires: int | None = None
    est_short: int | None = None
    live: str = "none"
    rang_uploads: int | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class Metriques:
    """Indicateurs calculés sans LLM pour une chaîne."""

    channel_id: str
    fenetre: int
    n_videos: int
    n_shorts: int
    n_longues: int
    mediane_vues_short: float | None
    mediane_vues_longues: float | None
    cadence_semaine: float | None
    jours_depuis_publication: int | None
    meilleur_outlier: float | None
    mix_format: str

    @property
    def n_inconnues(self) -> int:
        return max(0, self.n_videos - self.n_shorts - self.n_longues)

    @property
    def part_shorts(self) -> float | None:
        if self.n_videos <= 0:
            return None
        return round(self.n_shorts / self.n_videos, 3)


@dataclass
class ScoreChaine:
    """Réponse validée du LLM pour une chaîne."""

    score_proximite: int
    recouvrement_audience: str
    format_detecte: str
    langue: str
    justification: str
    risques: list[str]
