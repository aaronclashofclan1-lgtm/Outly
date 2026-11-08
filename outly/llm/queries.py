"""Requêtes de recherche dérivées du positionnement (et de la chaîne).

Un seul appel LLM, avant les recherches YouTube. Il transforme le
positionnement en requêtes courtes, en français et en anglais, pour trouver des
**chaînes** concurrentes — pas des vidéos. Cela permet de sortir du seul
vocabulaire de `keywords.txt` et d'atteindre des concurrents non francophones.

En cas d'échec (clé absente, JSON invalide, débit), la fonction renvoie une
liste vide : la découverte continue avec les mots-clés de la chaîne et
`keywords.txt`. Elle ne lève jamais d'exception.
"""

from __future__ import annotations

from ..config import Config
from ..models import Chaine
from . import prompts
from .client import ErreurLlm
from .scoring import demander_json, positionnement_pour_prompt

PROMPT_VERSION = "requetes-v1"

PROMPT_SYSTEME = (
    "Tu es un analyste de veille concurrentielle YouTube. "
    "Tu réponds uniquement avec un objet JSON, sans texte autour ni balise Markdown."
)


def construire_prompt(
    positionnement: str,
    chaine: Chaine | None,
    titres: list[str],
    nombre: int,
) -> str:
    """Prompt français : le LLM doit proposer des requêtes, pas analyser une chaîne."""
    bloc_chaine = ""
    if chaine is not None:
        extraits = "\n".join(f"- {titre[:100]}" for titre in titres[:12])
        bloc_chaine = (
            f"\nSa propre chaîne s'appelle « {chaine.titre} »."
            + (f"\nSes derniers titres :\n{extraits}" if extraits else "")
            + "\n"
        )

    return (
        "Tu prépares une veille de concurrence sur YouTube.\n\n"
        "Positionnement de l'utilisateur :\n"
        "---\n"
        f"{positionnement.strip()}\n"
        "---\n"
        f"{bloc_chaine}\n"
        f"Propose {nombre} requêtes de recherche courtes (2 à 6 mots) pour trouver des "
        "CHAÎNES concurrentes de la sienne.\n"
        "Règles :\n"
        "- environ 60 % en français, 40 % en anglais (ou une autre langue si le "
        "positionnement le justifie) ;\n"
        "- décris la niche, les sujets, le type de public ; jamais le nom d'une chaîne ;\n"
        "- pas de guillemets, de hashtags, de numérotation ni de ponctuation finale ;\n"
        "- des formulations réellement différentes, sans doublon.\n\n"
        'Réponds uniquement avec un objet JSON de la forme : {"requetes": ["...", "..."]}'
    )


def generer_requetes(
    service,
    config: Config,
    chaine: Chaine | None,
    titres: list[str],
    limite: int = 8,
) -> list[str]:
    """Renvoie au plus `limite` requêtes valides, ou une liste vide."""
    if limite <= 0:
        return []
    positionnement = positionnement_pour_prompt(config.chemin_positioning)
    if not positionnement:
        return []

    nombre = max(3, min(12, int(limite)))
    schema = prompts.charger_schema("search_queries.schema.json")
    messages = [
        {"role": "system", "content": PROMPT_SYSTEME},
        {"role": "user", "content": construire_prompt(positionnement, chaine, titres, nombre)},
    ]

    try:
        donnees, _brut, _tentatives, _erreur = demander_json(service, messages, schema)
    except ErreurLlm:
        return []
    if not donnees:
        return []

    resultat: list[str] = []
    vues: set[str] = set()
    for valeur in donnees.get("requetes") or []:
        texte = " ".join(str(valeur).split())
        if not 2 <= len(texte) <= 80:
            continue
        cle = texte.casefold()
        if cle in vues:
            continue
        vues.add(cle)
        resultat.append(texte)
    return resultat[:limite]
