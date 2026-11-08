"""Score de proximité calculé en local, sans aucun appel réseau.

Rôle : garantir un classement exploitable quand le fournisseur LLM est
indisponible (clé refusée, quota épuisé, coupure réseau). Le score compare le
profil de « ma chaîne » — description, mots-clés, positionnement et titres de
ses vidéos récentes — au profil de chaque candidat : description, mots-clés de
la chaîne et titres de ses dernières vidéos.

Deux mesures complémentaires, toutes deux normalisées entre 0 et 1 :

  * la **couverture des expressions** de ma niche (mots-clés de ma chaîne et
    `keywords.txt`), pondérée par leur rareté chez les candidats : une
    expression partagée par tout le corpus pèse moins qu'une expression
    distinctive ;
  * le **recouvrement lexical** des termes les plus caractéristiques de mon
    profil (TF-IDF), qui attrape les reformulations absent des expressions.

Le résultat est un entier de 0 à 100. Il n'a pas la finesse d'un jugement LLM,
mais il est déterministe, gratuit et suffisant pour classer les concurrents.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .discovery import lire_lignes, mots_cles_chaines

VERSION = "local-v1"
MAX_TERMES_CARACTERISTIQUES = 60
MAX_TITRES_REFERENCE = 30
MAX_TITRES_CANDIDAT = 30

# Mots vides français et anglais, plus le vocabulaire de YouTube lui-même :
# « vidéo », « chaîne » ou « abonné » ne disent rien de la niche.
MOTS_VIDES = frozenset(
    """
    les des une uns unes aux avec dans pour par sur sous entre chez vers sans
    plus moins tres tout tous toute toutes autre autres meme memes aussi mais
    donc alors comme quand comment pourquoi quoi qui que quel quelle quels
    quelles dont ou et est sont etre ete etait avoir avons avez ont avait
    etaient sera seront fait faire fais font va vont peut peuvent dois doit
    votre vos notre nos leur leurs son sa ses mon ma mes ton ta tes vous nous
    ils elles elle lui eux moi toi je tu on ce cet cette ces cela celui celle
    ceux celles ne pas non oui si the and for with from that this these those
    you your our their they them have has had was were are will can could
    would should not but about into over under more most very just all any
    each other some such only own same than too also now new video videos
    chaine youtube regarder abonne abonnes nouvelle nouveau nouvelles nouveaux
    aujourd hui depuis encore bien ici la le un du de d l c est
    avant vraiment marche produit astuce conseil solution confiance prix
    """.split()
)


@dataclass
class ProfilLocal:
    """Ce à quoi les candidats sont comparés."""

    titre: str
    phrases: list[str]
    sac: Counter[str] = field(default_factory=Counter)
    texte: str = ""
    titres_videos: list[str] = field(default_factory=list)


@dataclass
class ScoreLocal:
    score: int
    couverture: float
    recouvrement: float
    termes_communs: list[str]

    def justification(self) -> str:
        if not self.termes_communs:
            return "Aucun thème commun avec ma chaîne n'a été détecté."
        return (
            "Thèmes partagés avec ma chaîne : "
            + ", ".join(self.termes_communs[:6])
            + "."
        )


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents, ponctuation remplacée par des espaces."""
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFD", texte.lower())
    sans_accents = "".join(c for c in decompose if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sans_accents).strip()


def jetons(texte: str | None) -> list[str]:
    """Termes significatifs, avec un pluriel simplifié (« souris » intact)."""
    resultat: list[str] = []
    for mot in normaliser(texte).split():
        if len(mot) < 3 or mot in MOTS_VIDES:
            continue
        # Pluriel simplifié, sans casser « souris », « cas » ni « processus ».
        if len(mot) > 4 and mot.endswith("s") and not mot.endswith(("ss", "is", "us", "os", "as")):
            mot = mot[:-1]
        if mot not in MOTS_VIDES:
            resultat.append(mot)
    return resultat


def sac_termes(parties: list[tuple[str | None, float]]) -> Counter[str]:
    """Compteur pondéré des termes d'un texte découpé en champs."""
    sac: Counter[str] = Counter()
    for texte, poids in parties:
        for mot in jetons(texte):
            sac[mot] += poids
    return sac


def _lire_positionnement(config: Config) -> str:
    chemin: Path = config.chemin_positioning
    if not chemin.exists():
        return ""
    try:
        return chemin.read_text(encoding="utf-8")
    except OSError:
        return ""


def _ma_chaine(conn: sqlite3.Connection, config: Config):
    """Ligne de ma chaîne : statut `moi`, sinon le handle configuré."""
    ligne = conn.execute(
        "SELECT * FROM channels WHERE statut = 'moi' ORDER BY maj_le DESC LIMIT 1"
    ).fetchone()
    if ligne is not None:
        return ligne
    valeur = (config.moi.chaine or "").strip().lstrip("@")
    if not valeur:
        return None
    return conn.execute(
        "SELECT * FROM channels WHERE handle = ? OR channel_id = ? LIMIT 1",
        (valeur, valeur),
    ).fetchone()


def _titres_reference(conn: sqlite3.Connection, channel_id: str) -> list[str]:
    ligne = conn.execute(
        "SELECT titres_videos FROM channel_reference WHERE channel_id = ?",
        (channel_id,),
    ).fetchone()
    if ligne is None or not ligne["titres_videos"]:
        return []
    try:
        titres = json.loads(ligne["titres_videos"])
    except (TypeError, ValueError):
        return []
    if not isinstance(titres, list):
        return []
    return [str(titre) for titre in titres if str(titre).strip()][:MAX_TITRES_REFERENCE]


def construire_profil(conn: sqlite3.Connection, config: Config) -> ProfilLocal | None:
    """Assemble le profil de référence, ou None si ma chaîne est inconnue."""
    ligne = _ma_chaine(conn, config)
    if ligne is None:
        return None

    description = ligne["description"] or ""
    mots_cles = ligne["mots_cles_chaine"] or ""
    positioning = _lire_positionnement(config)
    titres = _titres_reference(conn, ligne["channel_id"])

    phrases = list(dict.fromkeys(mots_cles_chaines(mots_cles) + lire_lignes(config.chemin_keywords)))
    texte = "\n".join(filter(None, [ligne["titre"], description, mots_cles, positioning, " ".join(titres)]))

    sac = sac_termes(
        [
            (mots_cles, 3.0),
            (description, 2.0),
            (positioning, 1.0),
            (ligne["titre"], 1.0),
            *[(titre, 2.0) for titre in titres],
        ]
    )
    return ProfilLocal(
        titre=ligne["titre"],
        phrases=phrases,
        sac=sac,
        texte=texte,
        titres_videos=titres,
    )


def _texte_candidat(conn: sqlite3.Connection, channel_id: str, ligne) -> str:
    titres = [
        resultat["titre"]
        for resultat in conn.execute(
            "SELECT titre FROM videos WHERE channel_id = ? ORDER BY publie_le DESC LIMIT ?",
            (channel_id, MAX_TITRES_CANDIDAT),
        )
    ]
    return "\n".join(
        filter(None, [ligne["titre"], ligne["mots_cles_chaine"], ligne["description"], " ".join(titres)])
    )


def _correspondance(expression: str, texte_normalise: str, termes: set[str]) -> float:
    """1 si l'expression est présente, sinon part des termes présents."""
    attendus = jetons(expression)
    if not attendus:
        return 0.0
    if normaliser(expression) in texte_normalise:
        return 1.0
    return sum(1 for terme in attendus if terme in termes) / len(attendus)


def scorer(conn: sqlite3.Connection, config: Config) -> dict[str, ScoreLocal]:
    """Calcule le score local de chaque chaîne enrichie analysable."""
    profil = construire_profil(conn, config)
    if profil is None or not profil.sac:
        return {}

    lignes = conn.execute(
        """
        SELECT c.channel_id, c.titre, c.mots_cles_chaine, c.description
        FROM channels c
        JOIN channel_metrics m ON m.channel_id = c.channel_id
        WHERE c.statut IN ('enrichie', 'scored')
          AND m.n_videos >= ?
        """,
        (config.metriques.min_videos_for_metrics,),
    ).fetchall()
    if not lignes:
        return {}

    documents = []
    frequences: Counter[str] = Counter()
    for ligne in lignes:
        texte = _texte_candidat(conn, ligne["channel_id"], ligne)
        termes = set(jetons(texte))
        documents.append((ligne["channel_id"], normaliser(texte), termes))
        frequences.update(termes)

    nombre = len(documents)
    idf = {
        mot: math.log((nombre + 1) / (frequences.get(mot, 0) + 1)) + 1.0
        for mot in profil.sac
    }

    # Expressions de la niche, pondérées par leur rareté dans le corpus.
    expressions: list[tuple[str, float]] = []
    for expression in profil.phrases:
        proches = sum(
            1
            for _, texte, termes in documents
            if _correspondance(expression, texte, termes) >= 0.99
        )
        poids = math.log((nombre + 1) / (proches + 1)) + 1.0
        expressions.append((expression, poids))
    poids_expressions = sum(poids for _, poids in expressions) or 1.0

    # Termes les plus caractéristiques de mon profil.
    caracteristiques = sorted(
        profil.sac, key=lambda mot: profil.sac[mot] * idf.get(mot, 1.0), reverse=True
    )[:MAX_TERMES_CARACTERISTIQUES]
    poids_caracteristiques = sum(
        profil.sac[mot] * idf.get(mot, 1.0) for mot in caracteristiques
    ) or 1.0

    scores: dict[str, ScoreLocal] = {}
    for channel_id, texte, termes in documents:
        couverture = (
            sum(
                poids * _correspondance(expression, texte, termes)
                for expression, poids in expressions
            )
            / poids_expressions
        )
        recouvrement = (
            sum(
                profil.sac[mot] * idf.get(mot, 1.0)
                for mot in caracteristiques
                if mot in termes
            )
            / poids_caracteristiques
        )
        brut = 0.6 * couverture + 0.4 * recouvrement
        # Compression en racine carrée : un recouvrement partiel de 25 % vaut
        # 50/100, ce qui place le seuil de rétention (40) au niveau d'une
        # proximité réelle plutôt qu'au niveau d'une correspondance parfaite.
        score = round(100 * math.sqrt(max(0.0, min(1.0, brut))))
        communs = sorted(
            (
                (profil.sac[mot] * idf.get(mot, 1.0), mot)
                for mot in caracteristiques
                if mot in termes
            ),
            reverse=True,
        )[:6]
        scores[channel_id] = ScoreLocal(
            score=score,
            couverture=round(couverture, 3),
            recouvrement=round(recouvrement, 3),
            termes_communs=[mot for _, mot in communs],
        )
    return scores


__all__ = [
    "ProfilLocal",
    "ScoreLocal",
    "VERSION",
    "construire_profil",
    "jetons",
    "normaliser",
    "scorer",
]
