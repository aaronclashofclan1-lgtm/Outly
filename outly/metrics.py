"""Indicateurs calculés sans LLM.

Toutes les fonctions de ce module sont pures (aucune entrée/sortie, aucune
horloge implicite sauf `jours_depuis`) : c'est le cœur testable de l'outil.

Choix structurant : le score d'outlier compare une vidéo à la médiane des vidéos
du MÊME format. Sans cela, un Short à 500 000 vues écraserait la médiane des
vidéos longues et fabriquerait de faux outliers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Iterable, Sequence

from .models import Metriques, Video


def mediane(valeurs: Iterable[int | float | None]) -> float | None:
    """Médiane en ignorant les valeurs absentes. None si rien d'exploitable."""
    propres = [float(valeur) for valeur in valeurs if valeur is not None]
    if not propres:
        return None
    return float(median(propres))


def est_short(duree_s: int | None, seuil_s: int = 180, live: str = "none") -> int | None:
    """1 si Short, 0 si vidéo longue, None si indéterminable.

    L'API YouTube n'expose ni le format (vertical/horizontal) ni un drapeau
    « Short » : la seule règle disponible est la durée. Un direct ou une
    rediffusion à venir n'a pas de durée comparable et reste indéterminé.
    """
    if live and live != "none":
        return None
    if duree_s is None:
        return None
    return 1 if duree_s <= seuil_s else 0


def score_outlier(vues: int | None, mediane_format: float | None) -> float | None:
    """Vues ÷ médiane du format, arrondi à 2 décimales. None si incalculable."""
    if vues is None or mediane_format is None or mediane_format <= 0:
        return None
    return round(vues / mediane_format, 2)


def cadence_semaine(dates: Sequence[datetime]) -> float | None:
    """Publications par semaine sur l'amplitude couverte par la fenêtre."""
    if len(dates) < 2:
        return None
    debut, fin = min(dates), max(dates)
    jours = (fin - debut).total_seconds() / 86400
    if jours < 7:
        return None
    return round(len(dates) / (jours / 7), 2)


def jours_depuis(date: datetime | None, maintenant: datetime | None = None) -> int | None:
    if date is None:
        return None
    reference = maintenant or datetime.now(timezone.utc)
    return max(0, (reference - date).days)


def repartition(n_shorts: int, n_longues: int) -> str:
    if n_shorts and n_longues:
        return "mixte"
    if n_shorts:
        return "shorts"
    if n_longues:
        return "longues"
    return "indetermine"


def calculer_metriques(
    channel_id: str,
    videos: Sequence[Video],
    short_max_seconds: int = 180,
    fenetre: int | None = None,
    maintenant: datetime | None = None,
) -> Metriques:
    """Construit les métriques d'une chaîne à partir de ses vidéos.

    Les directs sont exclus des médianes ; les vidéos de durée inconnue sont
    comptées mais n'entrent dans aucune des deux populations.
    """
    fenetre = fenetre if fenetre is not None else len(videos)
    retenues = list(videos)[:fenetre]
    instant = maintenant or datetime.now(timezone.utc)

    shorts = [v for v in retenues if v.live == "none" and est_short(v.duree_s, short_max_seconds) == 1]
    longues = [v for v in retenues if v.live == "none" and est_short(v.duree_s, short_max_seconds) == 0]

    mediane_shorts = mediane(v.vues for v in shorts)
    mediane_longues = mediane(v.vues for v in longues)

    # Une vidéo dont le format est indéterminé (durée inconnue ou direct) n'a pas
    # de médiane de référence : lui attribuer celle de l'autre format produirait
    # des ratios absurdes. Elle est donc exclue du calcul.
    scores: list[float] = []
    for video in retenues:
        if video.live != "none":
            continue
        format_video = est_short(video.duree_s, short_max_seconds)
        if format_video is None:
            continue
        base_format = mediane_shorts if format_video == 1 else mediane_longues
        valeur = score_outlier(video.vues, base_format)
        if valeur is not None:
            scores.append(valeur)

    dates = [d for d in (date_publication(v) for v in retenues) if d is not None]
    derniere = max(dates) if dates else None

    return Metriques(
        channel_id=channel_id,
        fenetre=fenetre,
        n_videos=len(retenues),
        n_shorts=len(shorts),
        n_longues=len(longues),
        mediane_vues_short=mediane_shorts,
        mediane_vues_longues=mediane_longues,
        cadence_semaine=cadence_semaine(dates),
        jours_depuis_publication=jours_depuis(derniere, instant),
        meilleur_outlier=max(scores) if scores else None,
        mix_format=repartition(len(shorts), len(longues)),
    )


def date_publication(video: Video) -> datetime | None:
    """Analyse la date de publication stockée en texte ISO."""
    if not video.publie_le:
        return None
    try:
        return datetime.fromisoformat(video.publie_le.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
