"""Scoring de proximité des chaînes par le LLM.

Points clés :
  * un re-run ne rejoue JAMAIS un appel LLM pour une chaîne inchangée
    (empreinte du prompt + version stockées en base) ;
  * une réponse invalide déclenche un second essai avec l'erreur injectée,
    puis un échec propre et consigné — jamais une exception non gérée ;
  * le rapport ne dépend pas du LLM : une chaîne non scorée reste exploitable.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..db import enregistrer_score, lire_chaine, lire_metriques, lire_videos, marquer_statut
from ..models import Chaine, Metriques, ScoreChaine, Video
from . import prompts
from .client import ErreurLlm, ReponseLlm, ServiceLlm

TENTATIVES_JSON = 2

# Le positionnement est renvoyé au LLM pour CHAQUE chaîne. Sans plafond, un PDF
# de 40 pages importé dans l'interface ferait exploser le coût et la context
# window. On garde le début du document, coupé sur une frontière lisible.
MAX_POSITIONNEMENT_PROMPT = 12_000


@dataclass
class ResumeScoring:
    examinees: int = 0
    valides: int = 0
    invalides: int = 0
    deja_scorees: int = 0
    appels: int = 0
    messages: list[str] = field(default_factory=list)

    def resume(self) -> str:
        lignes = [
            f"Chaînes examinées  : {self.examinees}",
            f"Scores valides     : {self.valides}",
            f"Réponses invalides : {self.invalides}",
            f"Déjà scorées       : {self.deja_scorees} (aucun appel LLM)",
            f"Appels LLM         : {self.appels}",
        ]
        lignes += [f"  ! {message}" for message in self.messages]
        return "\n".join(lignes)


def lire_positionnement(chemin: Path) -> str:
    if not chemin.exists():
        return ""
    return chemin.read_text(encoding="utf-8").strip()


def positionnement_pour_prompt(chemin: Path) -> str:
    """Positionnement borné pour le LLM ; le fichier, lui, reste intact."""
    texte = lire_positionnement(chemin)
    if len(texte) <= MAX_POSITIONNEMENT_PROMPT:
        return texte
    coupe = texte[:MAX_POSITIONNEMENT_PROMPT]
    for separateur in ("\n\n", "\n", ". "):
        position = coupe.rfind(separateur)
        if position >= MAX_POSITIONNEMENT_PROMPT // 2:
            return coupe[:position].rstrip() + "\n\n[…]"
    return coupe.rstrip() + " […]"


def _deja_scoree(conn: sqlite3.Connection, channel_id: str, version: str, empreinte: str) -> bool:
    ligne = conn.execute(
        "SELECT 1 FROM channel_scores WHERE channel_id = ? AND prompt_version = ? "
        "AND empreinte_prompt = ? AND valide = 1",
        (channel_id, version, empreinte),
    ).fetchone()
    return ligne is not None


def _candidats(conn: sqlite3.Connection, config: Config, limite: int) -> list[str]:
    lignes = conn.execute(
        """
        SELECT c.channel_id
        FROM channels c
        JOIN channel_metrics m ON m.channel_id = c.channel_id
        WHERE c.statut IN ('enrichie', 'scored')
          AND m.n_videos >= ?
        ORDER BY COALESCE(c.score_heuristique, 0) DESC, COALESCE(c.abonnes, 0) DESC
        LIMIT ?
        """,
        (config.metriques.min_videos_for_metrics, limite),
    ).fetchall()
    return [ligne["channel_id"] for ligne in lignes]


def demander_json(
    service: ServiceLlm,
    messages: list[dict],
    schema: dict,
) -> tuple[dict | None, str | None, int, str | None]:
    """Demande une réponse valide, avec une seconde chance si le JSON est mauvais."""
    base = list(messages[:2])
    conversation = list(messages)
    brut: str | None = None
    erreur: str | None = None

    for tentative in range(1, TENTATIVES_JSON + 1):
        reponse: ReponseLlm = service.completer(conversation)
        brut = reponse.texte
        donnees = prompts.extraire_json(brut)
        if donnees is None:
            erreur = "la réponse ne contient aucun objet JSON exploitable"
        else:
            erreurs = prompts.valider(donnees, schema)
            if not erreurs:
                return donnees, brut, tentative, None
            erreur = "schéma non respecté : " + " ; ".join(erreurs[:5])

        if tentative < TENTATIVES_JSON:
            conversation = base + [
                {"role": "assistant", "content": (brut or "")[:4000]},
                {
                    "role": "user",
                    "content": (
                        f"Ta réponse précédente est invalide ({erreur}). "
                        "Réponds de nouveau uniquement avec l'objet JSON conforme au schéma, "
                        "sans texte avant ni après, sans balise Markdown."
                    ),
                },
            ]

    return None, brut, TENTATIVES_JSON, erreur


def scorer(
    conn: sqlite3.Connection,
    config: Config,
    service: ServiceLlm,
    limite: int | None = None,
    run_id: int | None = None,
) -> ResumeScoring:
    resume = ResumeScoring()
    positionnement = positionnement_pour_prompt(config.chemin_positioning)
    if not positionnement:
        resume.messages.append(
            "positioning.md est vide ou absent : le score de proximité n'a aucune référence. "
            "Remplissez-le avant de relancer le scoring."
        )
        return resume

    schema = prompts.charger_schema("channel_score.schema.json")
    plafond = min(limite or config.llm.max_channels_per_run, config.llm.max_channels_per_run)
    echecs_consecutifs = 0

    for channel_id in _candidats(conn, config, plafond):
        resume.examinees += 1
        chaine = lire_chaine(conn, channel_id)
        metriques = lire_metriques(conn, channel_id)
        if chaine is None or metriques is None:
            continue
        videos = lire_videos(conn, channel_id, config.youtube.videos_per_channel)
        empreinte = prompts.empreinte_chaine(
            prompts.PROMPT_VERSION, positionnement, chaine, videos, metriques
        )

        if _deja_scoree(conn, channel_id, prompts.PROMPT_VERSION, empreinte):
            resume.deja_scorees += 1
            continue

        if echecs_consecutifs >= 2:
            resume.messages.append(
                "Deux échecs LLM consécutifs : arrêt du scoring pour cette exécution "
                "(le rapport utilisera le classement heuristique)."
            )
            break

        messages = [
            {"role": "system", "content": prompts.PROMPT_SYSTEME},
            {
                "role": "user",
                "content": prompts.construire_prompt(positionnement, chaine, videos, metriques),
            },
        ]

        try:
            donnees, brut, tentatives, erreur = demander_json(service, messages, schema)
        except ErreurLlm as probleme:
            echecs_consecutifs += 1
            resume.invalides += 1
            resume.messages.append(f"{chaine.titre} : {probleme}")
            enregistrer_score(
                conn,
                channel_id,
                run_id,
                empreinte,
                prompts.PROMPT_VERSION,
                None,
                None,
                None,
                None,
                0,
                str(probleme),
                False,
            )
            continue

        if donnees is None:
            resume.invalides += 1
            enregistrer_score(
                conn,
                channel_id,
                run_id,
                empreinte,
                prompts.PROMPT_VERSION,
                None,
                None,
                None,
                brut,
                tentatives,
                erreur,
                False,
            )
            resume.messages.append(f"{chaine.titre} : réponse LLM invalide ({erreur})")
            continue

        echecs_consecutifs = 0
        score = prompts.transformer_score(donnees)
        enregistrer_score(
            conn,
            channel_id,
            run_id,
            empreinte,
            prompts.PROMPT_VERSION,
            service.nom_fournisseur,
            service.nom_modele,
            score,
            brut,
            tentatives,
            None,
            True,
        )
        marquer_statut(conn, channel_id, "scored")
        resume.valides += 1

        if not prompts.deux_phrases(score.justification):
            resume.messages.append(
                f"{chaine.titre} : la justification ne contient pas deux phrases "
                "(score conservé, contrôle de style seulement)."
            )

    resume.appels = service.appels
    return resume


__all__ = [
    "ResumeScoring",
    "ScoreChaine",
    "Chaine",
    "Video",
    "Metriques",
    "demander_json",
    "lire_positionnement",
    "positionnement_pour_prompt",
    "scorer",
]
