"""Ordre personnalisable des blocs de page.

Chaque page expose une ou plusieurs zones (les deux colonnes de l'accueil, la
zone unique de l'audit). Les cartes y sont repérées par un identifiant stable,
indépendant de leur titre affiché. L'ordre est enregistré dans
``data/preferences.json`` sous la clé ``disposition``.

Une disposition absente, partielle ou invalide ne doit jamais empêcher une page
de s'afficher : les identifiants inconnus sont ignorés et les blocs manquants
sont ajoutés à la fin, dans l'ordre où la page les a construits.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import settings

# Ordre par défaut. Il sert aussi de documentation des blocs disponibles.
PAGES: dict[str, dict[str, list[str]]] = {
    "accueil": {
        "gauche": ["infos", "veille"],
        "droite": ["audit", "quota", "execution", "rapport"],
    },
    "audit": {
        "principal": [
            "entete",
            "diagnostic",
            "concurrents",
            "strategie",
            "plan",
            "kpis",
            "idees",
            "limites",
            "vide",
        ],
    },
}

ZONES: dict[tuple[str, str], str] = {
    ("accueil", "gauche"): "Colonne gauche",
    ("accueil", "droite"): "Colonne droite",
    ("audit", "principal"): "Sections de l'audit",
}


def est_inverse(racine: Path, page: str) -> bool:
    """Vrai si l'utilisateur a inversé les colonnes de cette page."""
    valeur = settings.lire_preferences(racine).get("inversions")
    return bool(isinstance(valeur, dict) and valeur.get(page))


def inverser(racine: Path, page: str) -> bool:
    """Bascule l'ordre des colonnes d'une page éditeur."""
    valeurs = settings.lire_preferences(racine).get("inversions")
    courant = dict(valeurs) if isinstance(valeurs, dict) else {}
    courant[page] = not bool(courant.get(page))
    settings.ecrire_preferences(racine, inversions=courant)
    return bool(courant[page])


def defaut(page: str) -> dict[str, list[str]]:
    """Copie de l'ordre par défaut d'une page."""
    return deepcopy(PAGES.get(page, {}))


def _lire_brut(racine: Path) -> dict:
    valeur = settings.lire_preferences(racine).get("disposition")
    return valeur if isinstance(valeur, dict) else {}


def ordre(racine: Path, page: str, zone: str, disponibles) -> list[str]:
    """Ordre à appliquer pour une zone, limité aux blocs réellement présents."""
    page_dispo = _lire_brut(racine).get(page)
    sauve: list[str] = []
    if isinstance(page_dispo, dict):
        valeur = page_dispo.get(zone)
        if isinstance(valeur, list):
            sauve = [identifiant for identifiant in valeur if isinstance(identifiant, str)]

    connus = [str(identifiant) for identifiant in disponibles]
    # Les identifiants qui ne font pas partie des blocs connus de la page sont
    # ignorés : une ancienne disposition ne doit jamais ressusciter un bloc
    # supprimé ou en injecter un arbitraire. La validation se fait au niveau de
    # la page, pas de la zone : un bloc déplacé d'une colonne à l'autre doit
    # rester reconnu dans sa nouvelle zone.
    connus_page = [
        identifiant
        for valeurs in PAGES.get(page, {}).values()
        for identifiant in valeurs
    ]
    if connus_page:
        connus = [identifiant for identifiant in connus if identifiant in connus_page]
    resultat: list[str] = []
    for identifiant in sauve + connus:
        if identifiant in connus and identifiant not in resultat:
            resultat.append(identifiant)
    return resultat


def zone(racine: Path, page: str, identifiant: str, defaut: str) -> str:
    """Colonne/zone enregistrée pour un bloc, ou zone par défaut."""
    page_dispo = _lire_brut(racine).get(page)
    if isinstance(page_dispo, dict):
        for nom, valeurs in page_dispo.items():
            if isinstance(valeurs, list) and identifiant in valeurs:
                return nom
    return defaut


def enregistrer_zones(racine: Path, page: str, zones_ordre: dict[str, list[str]]) -> None:
    """Enregistre l'ordre de plusieurs zones sans toucher aux autres pages."""
    disposition = deepcopy(_lire_brut(racine))
    ancienne_page = disposition.get(page)
    if not isinstance(ancienne_page, dict):
        ancienne_page = {}

    propre: dict[str, list[str]] = {}
    for zone, valeurs in zones_ordre.items():
        vus: list[str] = []
        for identifiant in valeurs:
            if isinstance(identifiant, str) and identifiant and identifiant not in vus:
                vus.append(identifiant)
        propre[str(zone)] = vus[:60]

    # Les zones non transmises gardent leur ordre enregistré.
    for zone, valeurs in ancienne_page.items():
        if zone in propre or not isinstance(zone, str) or not isinstance(valeurs, list):
            continue
        propre[zone] = [str(i) for i in valeurs if isinstance(i, str)][:60]

    disposition[page] = propre
    settings.ecrire_preferences(racine, disposition=disposition)


def reinitialiser(racine: Path, page: str | None = None) -> None:
    """Supprime la disposition d'une page, ou de toutes les pages."""
    disposition = deepcopy(_lire_brut(racine))
    if page is None:
        disposition = {}
    else:
        disposition.pop(page, None)
    settings.ecrire_preferences(racine, disposition=disposition)


__all__ = [
    "PAGES",
    "ZONES",
    "defaut",
    "est_inverse",
    "inverser",
    "ordre",
    "zone",
    "enregistrer_zones",
    "reinitialiser",
]
