"""Score local « quelle chaîne suivre en priorité ».

Aucun appel réseau, aucun LLM : le score ne fait que résumer des mesures déjà
présentes dans le rapport. Il répond à une question différente du score de
proximité : parmi les chaînes proches, laquelle offre le meilleur modèle à
étudier maintenant (formats qui percent, rythme tenu, audience réelle) ?

Les poids sont fixes et chaque point est justifié par une valeur affichable.
Une donnée absente n'est pas inventée : elle réduit la confiance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

POIDS = {
    "proximite": 0.35,
    "outlier": 0.25,
    "cadence": 0.15,
    "fraicheur": 0.10,
    "audience": 0.15,
}


@dataclass
class ScoreSuivi:
    channel_id: str
    titre: str
    score: int
    confiance: str
    raisons: list[str] = field(default_factory=list)
    manques: list[str] = field(default_factory=list)
    rang: int = 0


def _nombre(valeur: float | int | None, decimales: int = 0) -> str:
    if valeur is None:
        return "—"
    if decimales:
        return f"{valeur:,.{decimales}f}".replace(",", " ").replace(".", ",")
    return f"{int(round(valeur)):,}".replace(",", " ")


def _metrique(ligne: Any, nom: str):
    metriques = getattr(ligne, "metriques", None)
    return getattr(metriques, nom, None) if metriques is not None else None


def _mediane(ligne: Any) -> float | None:
    longue = _metrique(ligne, "mediane_vues_longues")
    courte = _metrique(ligne, "mediane_vues_short")
    if longue is not None and longue > 0:
        return float(longue)
    if courte is not None and courte > 0:
        return float(courte)
    return None


def _points_proximite(ligne: Any) -> tuple[float | None, str | None]:
    score = getattr(ligne, "score_affiche", None)
    if score is None:
        return None, None
    return max(0.0, min(1.0, float(score) / 100.0)), f"proximité {int(score)}/100"


def _points_outlier(ligne: Any) -> tuple[float | None, str | None]:
    outlier = _metrique(ligne, "meilleur_outlier")
    if outlier is None:
        return None, None
    # 1× la médiane = aucun signal ; 4× = format qui perce nettement.
    valeur = max(0.0, min(1.0, (float(outlier) - 1.0) / 3.0))
    return valeur, f"meilleur outlier ×{_nombre(outlier, 1)}"


def _points_cadence(ligne: Any) -> tuple[float | None, str | None]:
    cadence = _metrique(ligne, "cadence_semaine")
    if cadence is None:
        return None, None
    # Deux publications par semaine suffisent à prouver un rythme tenable.
    return max(0.0, min(1.0, float(cadence) / 2.0)), f"{_nombre(cadence, 1)} vidéos/semaine"


def _points_fraicheur(ligne: Any) -> tuple[float | None, str | None]:
    jours = _metrique(ligne, "jours_depuis_publication")
    if jours is None:
        return None, None
    jours = max(0.0, float(jours))
    if jours <= 7:
        valeur = 1.0
    elif jours <= 14:
        valeur = 0.75
    elif jours <= 30:
        valeur = 0.45
    elif jours <= 60:
        valeur = 0.15
    else:
        valeur = 0.0
    return valeur, f"dernière vidéo il y a {_nombre(jours)} j"


def classer(lignes: list[Any]) -> list[ScoreSuivi]:
    """Classe les chaînes par priorité de suivi, de la plus forte à la plus faible."""
    candidates = [ligne for ligne in lignes if getattr(ligne, "channel_id", "")]
    if not candidates:
        return []
    medianes = [valeur for ligne in candidates if (valeur := _mediane(ligne))]
    plafond = max(medianes) if medianes else None

    scores: list[ScoreSuivi] = []
    for ligne in candidates:
        composantes: list[tuple[str, float, float]] = []
        raisons: list[str] = []
        manques: list[str] = []

        proximite, raison = _points_proximite(ligne)
        if proximite is None:
            manques.append("score de proximité absent")
        else:
            composantes.append(("proximite", POIDS["proximite"], proximite))
            raisons.append(raison or "")

        outlier, raison = _points_outlier(ligne)
        if outlier is None:
            manques.append("outlier inconnu")
        else:
            composantes.append(("outlier", POIDS["outlier"], outlier))
            raisons.append(raison or "")

        cadence, raison = _points_cadence(ligne)
        if cadence is None:
            manques.append("cadence inconnue")
        else:
            composantes.append(("cadence", POIDS["cadence"], cadence))
            raisons.append(raison or "")

        fraicheur, raison = _points_fraicheur(ligne)
        if fraicheur is None:
            manques.append("date de publication inconnue")
        else:
            composantes.append(("fraicheur", POIDS["fraicheur"], fraicheur))
            raisons.append(raison or "")

        mediane = _mediane(ligne)
        if mediane is None or not plafond:
            manques.append("médiane de vues inconnue")
        else:
            audience = math.log1p(mediane) / math.log1p(plafond) if plafond > 1 else 1.0
            composantes.append(("audience", POIDS["audience"], max(0.0, min(1.0, audience))))
            # La preuve d'audience complète la raison « outlier » : quatre
            # preuves maximum restent lisibles dans la carte de verdict.
            for index, raison in enumerate(raisons):
                if raison.startswith("meilleur outlier"):
                    raisons[index] = raison + f" · médiane {_nombre(mediane)} vues"
                    break
            else:
                raisons.append(f"médiane {_nombre(mediane)} vues")

        poids_disponible = sum(poids for _nom, poids, _valeur in composantes)
        if not composantes or poids_disponible <= 0:
            score = 0
        else:
            score = round(
                100
                * sum(poids * valeur for _nom, poids, valeur in composantes)
                / poids_disponible
            )
        couverture = poids_disponible / sum(POIDS.values())
        confiance = "élevée" if couverture >= 0.9 else ("moyenne" if couverture >= 0.6 else "faible")
        scores.append(
            ScoreSuivi(
                channel_id=str(getattr(ligne, "channel_id", "")),
                titre=" ".join(str(getattr(ligne, "titre", "") or "Sans titre").split()),
                score=max(0, min(100, int(score))),
                confiance=confiance,
                raisons=[raison for raison in raisons if raison][:4],
                manques=manques[:4],
            )
        )

    scores.sort(key=lambda score: (-score.score, score.titre.casefold()))
    for rang, score in enumerate(scores, start=1):
        score.rang = rang
    return scores


def recommandation(scores: list[ScoreSuivi]) -> str:
    """Phrase de synthèse, sans désigner un « copier-coller » de contenu."""
    if not scores:
        return "Sélectionnez au moins deux chaînes pour obtenir une priorité de suivi."
    meilleur = scores[0]
    if len(scores) > 1 and meilleur.score - scores[1].score <= 2:
        return (
            f"{meilleur.titre} et {scores[1].titre} se valent : étudiez leurs mécaniques "
            "respectives avant de trancher."
        )
    return (
        f"{meilleur.titre} arrive en tête ({meilleur.score}/100). "
        "Copiez ses mécaniques de format, jamais son contenu."
    )


__all__ = ["POIDS", "ScoreSuivi", "classer", "recommandation"]
