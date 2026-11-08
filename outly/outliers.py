"""Analyse approfondie des meilleurs outliers (étape optionnelle).

Pour chaque chaîne retenue, on prend ses N meilleures vidéos au sens du score
d'outlier (vues ÷ médiane du même format), on tente — si l'option est activée —
de récupérer la transcription, puis on demande au LLM l'accroche, la structure
et ce qui explique la performance.

Si la transcription est indisponible, l'analyse se fait sur le titre et les
chiffres, et le prompt impose de le dire. Rien ne plante.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from . import transcripts
from .config import Config
from .db import enregistrer_analyse_outlier, lire_metriques
from .llm import prompts
from .llm.client import ErreurLlm, ServiceLlm
from .llm.scoring import demander_json
from .models import Video


@dataclass
class ResumeOutliers:
    chaines: int = 0
    videos: int = 0
    valides: int = 0
    invalides: int = 0
    deja_analysees: int = 0
    transcriptions_ok: int = 0
    messages: list[str] = field(default_factory=list)

    def resume(self) -> str:
        lignes = [
            f"Chaînes traitées     : {self.chaines}",
            f"Vidéos examinées     : {self.videos}",
            f"Analyses valides     : {self.valides}",
            f"Analyses invalides   : {self.invalides}",
            f"Déjà analysées       : {self.deja_analysees}",
            f"Transcriptions lues  : {self.transcriptions_ok}",
        ]
        lignes += [f"  ! {message}" for message in self.messages]
        return "\n".join(lignes)


def _classement_chaines(conn: sqlite3.Connection, config: Config, limite: int) -> list[sqlite3.Row]:
    lignes = conn.execute(
        """
        SELECT c.channel_id, c.titre,
               (SELECT s.score_proximite FROM channel_scores s
                 WHERE s.channel_id = c.channel_id AND s.valide = 1
                 ORDER BY s.id DESC LIMIT 1) AS score
        FROM channels c
        WHERE c.statut IN ('scored', 'enrichie')
        """
    ).fetchall()
    return sorted(
        lignes,
        key=lambda ligne: (ligne["score"] is None, -(ligne["score"] or 0)),
    )[:limite]


def _videos_outliers(conn: sqlite3.Connection, channel_id: str, limite: int) -> list[sqlite3.Row]:
    """Vidéos réellement au-dessus de la médiane de leur format."""
    return conn.execute(
        "SELECT * FROM v_outliers WHERE channel_id = ? AND score_outlier > 1.0 "
        "ORDER BY score_outlier DESC LIMIT ?",
        (channel_id, limite),
    ).fetchall()


def _deja_analyses(conn: sqlite3.Connection, video_id: str, version: str) -> bool:
    ligne = conn.execute(
        "SELECT 1 FROM analyses_outliers WHERE video_id = ? AND prompt_version = ? AND valide = 1",
        (video_id, version),
    ).fetchone()
    return ligne is not None


def analyser(
    conn: sqlite3.Connection,
    config: Config,
    service: ServiceLlm,
    limite_chaines: int | None = None,
    run_id: int | None = None,
) -> ResumeOutliers:
    resume = ResumeOutliers()
    schema = prompts.charger_schema("outlier_analysis.schema.json")
    plafond = limite_chaines or config.outliers.max_channels
    blocages_consecutifs = 0

    if not config.transcriptions.enabled:
        resume.messages.append(
            "Transcriptions désactivées (transcripts.enabled = false) : "
            "les analyses s'appuient sur les titres et les chiffres."
        )

    for ligne_chaine in _classement_chaines(conn, config, plafond):
        resume.chaines += 1
        metriques = lire_metriques(conn, ligne_chaine["channel_id"])
        for ligne in _videos_outliers(conn, ligne_chaine["channel_id"], config.outliers.per_channel):
            resume.videos += 1
            video_id = ligne["video_id"]
            if _deja_analyses(conn, video_id, prompts.PROMPT_OUTLIER_VERSION):
                resume.deja_analysees += 1
                continue

            video = Video(
                video_id=video_id,
                channel_id=ligne["channel_id"],
                titre=ligne["titre"],
                publie_le=ligne["publie_le"],
                duree_s=ligne["duree_s"],
                vues=ligne["vues"],
                est_short=ligne["est_short"],
            )

            texte: str | None = None
            if config.transcriptions.enabled and blocages_consecutifs < 2:
                en_cache = transcripts.depuis_cache(conn, video_id)
                if en_cache is not None:
                    texte = en_cache.texte
                    resume.transcriptions_ok += 1
                else:
                    resultat = transcripts.recuperer(
                        video_id,
                        config.transcriptions.languages,
                        config.outliers.max_chars_transcription,
                    )
                    resultat.langue = resultat.langue or ",".join(config.transcriptions.languages)
                    transcripts.enregistrer(conn, resultat)
                    if resultat.statut == "ok":
                        texte = resultat.texte
                        resume.transcriptions_ok += 1
                        blocages_consecutifs = 0
                    elif resultat.statut == "bloque":
                        blocages_consecutifs += 1
                        if blocages_consecutifs == 2:
                            resume.messages.append(
                                "YouTube bloque la lecture des sous-titres : "
                                "transcriptions abandonnées pour cette exécution."
                            )

            mediane = None
            if metriques is not None:
                mediane = (
                    metriques.mediane_vues_short
                    if video.est_short == 1
                    else metriques.mediane_vues_longues
                )
            ratio = (
                round(video.vues / mediane, 2)
                if (video.vues is not None and mediane)
                else None
            )

            messages = [
                {"role": "system", "content": prompts.PROMPT_SYSTEME_OUTLIER},
                {
                    "role": "user",
                    "content": prompts.construire_prompt_outlier(
                        ligne_chaine["titre"], video, mediane, ratio, texte
                    ),
                },
            ]

            try:
                donnees, _brut, _tentatives, erreur = demander_json(service, messages, schema)
            except ErreurLlm as probleme:
                resume.invalides += 1
                enregistrer_analyse_outlier(
                    conn, video_id, run_id, prompts.PROMPT_OUTLIER_VERSION, None, None, None, str(probleme)
                )
                resume.messages.append(f"{video.titre} : {probleme}")
                continue

            if donnees is None:
                resume.invalides += 1
                enregistrer_analyse_outlier(
                    conn,
                    video_id,
                    run_id,
                    prompts.PROMPT_OUTLIER_VERSION,
                    service.nom_fournisseur,
                    service.nom_modele,
                    None,
                    erreur,
                )
                resume.messages.append(f"{video.titre} : analyse invalide ({erreur})")
            else:
                resume.valides += 1
                enregistrer_analyse_outlier(
                    conn,
                    video_id,
                    run_id,
                    prompts.PROMPT_OUTLIER_VERSION,
                    service.nom_fournisseur,
                    service.nom_modele,
                    donnees,
                    None,
                )

    return resume


def extraire_analyses(conn: sqlite3.Connection, channel_id: str) -> dict[str, dict]:
    """Analyses valides d'une chaîne, indexées par identifiant de vidéo."""
    lignes = conn.execute(
        "SELECT a.* FROM analyses_outliers a "
        "JOIN videos v ON v.video_id = a.video_id "
        "WHERE v.channel_id = ? AND a.valide = 1",
        (channel_id,),
    ).fetchall()
    resultat: dict[str, dict] = {}
    for ligne in lignes:
        resultat[ligne["video_id"]] = {
            "accroche": ligne["accroche"],
            "structure": json.loads(ligne["structure_json"]) if ligne["structure_json"] else [],
            "explication": ligne["explication"],
            "reutilisable": json.loads(ligne["reutilisable"]) if ligne["reutilisable"] else [],
        }
    return resultat
