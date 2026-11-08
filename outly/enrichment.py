"""Enrichissement des chaînes retenues : uploads, vidéos et métriques.

Deux appels par chaîne, jamais plus :
  1. `playlistItems.list` sur la playlist « uploads » (1 unité, 50 vidéos max) ;
  2. `videos.list` sur les identifiants obtenus (1 unité pour 50 vidéos).

`search.list` n'est jamais utilisé pour lister les vidéos d'une chaîne : la
documentation officielle le déconseille explicitement (indexation retardée).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .config import Config
from .db import (
    enregistrer_chaine,
    enregistrer_metriques,
    enregistrer_videos,
    lire_chaine,
    marquer_statut,
)
from .metrics import calculer_metriques, est_short
from .models import Chaine, Video, duree_iso_en_secondes, entier_ou_none
from .youtube_api import ClientYoutube, ErreurApi, ErreurIntrouvable


@dataclass
class ResumeEnrichissement:
    chaines_traitees: int = 0
    videos_ajoutees: int = 0
    erreurs: int = 0
    messages: list[str] = field(default_factory=list)

    def resume(self) -> str:
        lignes = [
            f"Chaînes enrichies : {self.chaines_traitees}",
            f"Vidéos enregistrées : {self.videos_ajoutees}",
            f"Erreurs ignorées : {self.erreurs}",
        ]
        lignes += [f"  ! {message}" for message in self.messages]
        return "\n".join(lignes)


def assurer_details(conn: sqlite3.Connection, client: ClientYoutube, config: Config) -> int:
    """Complète les fiches incomplètes (abonnés, playlist uploads) par lots."""
    lignes = conn.execute(
        "SELECT channel_id FROM channels WHERE playlist_uploads IS NULL OR abonnes IS NULL"
    ).fetchall()
    identifiants = [ligne["channel_id"] for ligne in lignes]
    if not identifiants:
        return 0
    chaines = client.chaines_par_ids(identifiants)
    for chaine in chaines:
        enregistrer_chaine(conn, chaine)
    return len(chaines)


def _chaines_a_traiter(conn: sqlite3.Connection, config: Config, limite: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT channel_id, titre FROM channels
        WHERE statut = 'candidate'
        ORDER BY est_seed DESC, COALESCE(score_heuristique, 0) DESC,
                 COALESCE(abonnes, 0) DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()


def _construire_videos(
    chaine: Chaine,
    elements: list[dict],
    infos: dict[str, dict],
    config: Config,
) -> list[Video]:
    """Fusionne les données de la playlist (titres, dates) et de `videos.list`."""
    seuil = config.metriques.short_max_seconds
    videos: list[Video] = []

    for rang, element in enumerate(elements):
        snippet = element.get("snippet") or {}
        identifiant = (snippet.get("resourceId") or {}).get("videoId")
        if not identifiant:
            continue
        detail = infos.get(identifiant) or {}
        detail_snippet = detail.get("snippet") or {}
        statistiques = detail.get("statistics") or {}
        contenu = detail.get("contentDetails") or {}
        direct = detail_snippet.get("liveBroadcastContent") or "none"

        duree = duree_iso_en_secondes(contenu.get("duration"))
        videos.append(
            Video(
                video_id=identifiant,
                channel_id=chaine.channel_id,
                titre=(snippet.get("title") or detail_snippet.get("title") or "(sans titre)"),
                description=(snippet.get("description") or detail_snippet.get("description")),
                publie_le=(snippet.get("publishedAt") or detail_snippet.get("publishedAt")),
                duree_s=duree,
                vues=entier_ou_none(statistiques.get("viewCount")),
                likes=entier_ou_none(statistiques.get("likeCount")),
                commentaires=entier_ou_none(statistiques.get("commentCount")),
                est_short=est_short(duree, seuil, direct),
                live=direct,
                rang_uploads=rang,
            )
        )
    return videos


def _enrichir_une(
    conn: sqlite3.Connection,
    client: ClientYoutube,
    config: Config,
    channel_id: str,
    resume: ResumeEnrichissement,
) -> None:
    chaine = lire_chaine(conn, channel_id)
    if chaine is None:
        return

    if not chaine.playlist_uploads:
        for complete in client.chaines_par_ids([channel_id]):
            enregistrer_chaine(conn, complete)
        chaine = lire_chaine(conn, channel_id) or chaine

    if not chaine.playlist_uploads:
        marquer_statut(conn, channel_id, "ecartee", "playlist « uploads » introuvable")
        resume.messages.append(
            f"{chaine.titre} : aucune playlist uploads accessible, chaîne écartée."
        )
        return

    elements = client.elements_playlist(
        chaine.playlist_uploads, config.youtube.videos_per_channel
    )
    if not elements:
        marquer_statut(conn, channel_id, "ecartee", "aucune vidéo publique")
        resume.messages.append(f"{chaine.titre} : aucune vidéo publique, chaîne écartée.")
        return

    identifiants = [
        (element.get("snippet", {}).get("resourceId") or {}).get("videoId") for element in elements
    ]
    infos = client.videos_infos([identifiant for identifiant in identifiants if identifiant])
    videos = _construire_videos(chaine, elements, infos, config)

    resume.videos_ajoutees += enregistrer_videos(conn, videos)
    metriques = calculer_metriques(
        channel_id,
        videos,
        short_max_seconds=config.metriques.short_max_seconds,
        fenetre=config.youtube.videos_per_channel,
    )
    enregistrer_metriques(conn, metriques)
    marquer_statut(conn, channel_id, "enrichie")
    resume.chaines_traitees += 1


def rafraichir_recentes(
    conn: sqlite3.Connection,
    client: ClientYoutube,
    config: Config,
    limite: int | None = None,
) -> ResumeEnrichissement:
    """Recharge les dernières uploads des chaînes déjà évaluées.

    Une veille qui ne découvre que de nouvelles chaînes renvoie toujours le
    même rapport : les concurrents suivis publient pourtant entre deux envois.
    Ce passage reprend leurs uploads, en commençant par les chaînes qui ont
    publié le plus récemment — c'est là que se trouvent les nouveautés.
    """
    resume = ResumeEnrichissement()
    plafond = min(limite or config.youtube.analysis_limit, config.youtube.analysis_limit)
    lignes = conn.execute(
        """
        SELECT c.channel_id, c.titre
        FROM channels c
        LEFT JOIN channel_metrics m ON m.channel_id = c.channel_id
        WHERE c.statut IN ('enrichie', 'scored')
        ORDER BY COALESCE(m.jours_depuis_publication, 9999) ASC,
                 COALESCE(c.score_heuristique, 0) DESC,
                 COALESCE(c.abonnes, 0) DESC
        LIMIT ?
        """,
        (plafond,),
    ).fetchall()

    for ligne in lignes:
        try:
            _enrichir_une(conn, client, config, ligne["channel_id"], resume)
        except ErreurIntrouvable:
            resume.erreurs += 1
            resume.messages.append(f"{ligne['titre']} : chaîne introuvable ou supprimée")
        except ErreurApi as erreur:
            resume.erreurs += 1
            resume.messages.append(f"{ligne['titre']} : {erreur}")
    return resume


def enrichir(
    conn: sqlite3.Connection,
    client: ClientYoutube,
    config: Config,
    limite: int | None = None,
) -> ResumeEnrichissement:
    """Enrichit les prochaines chaînes candidates, par ordre de priorité."""
    resume = ResumeEnrichissement()
    plafond = min(limite or config.youtube.analysis_limit, config.youtube.analysis_limit)

    try:
        assurer_details(conn, client, config)
    except ErreurApi as erreur:
        resume.messages.append(f"complément des fiches : {erreur}")

    for ligne in _chaines_a_traiter(conn, config, plafond):
        try:
            _enrichir_une(conn, client, config, ligne["channel_id"], resume)
        except ErreurIntrouvable:
            resume.erreurs += 1
            marquer_statut(conn, ligne["channel_id"], "ecartee", "chaîne introuvable ou supprimée")
            resume.messages.append(f"{ligne['titre']} : chaîne introuvable, écartée.")
        except ErreurApi as erreur:
            # Une chaîne en erreur ne doit jamais interrompre tout le lot.
            resume.erreurs += 1
            resume.messages.append(f"{ligne['titre']} : {erreur}")

    return resume


def enrichir_ma_chaine(
    conn: sqlite3.Connection,
    client: ClientYoutube,
    config: Config,
) -> ResumeEnrichissement:
    """Récupère les vidéos et métriques de ma chaîne sans la sortir de « moi ».

    `_enrichir_une` marque normalement la chaîne « enrichie » pour qu'elle
    entre dans le paysage concurrentiel. Ma chaîne, elle, doit rester la
    référence de l'audit : son statut est restauré dans tous les cas, même si
    l'API échoue en cours de route.
    """
    resume = ResumeEnrichissement()
    ligne = conn.execute(
        "SELECT channel_id, titre FROM channels WHERE statut = 'moi' "
        "ORDER BY maj_le DESC LIMIT 1"
    ).fetchone()
    if ligne is None:
        valeur = (config.moi.chaine or "").strip().lstrip("@")
        if valeur:
            ligne = conn.execute(
                "SELECT channel_id, titre FROM channels WHERE handle = ? OR channel_id = ? LIMIT 1",
                (valeur, valeur),
            ).fetchone()
    if ligne is None:
        resume.messages.append(
            "Ma chaîne n'est pas encore connue : lancez d'abord une veille pour "
            "récupérer son identité."
        )
        return resume

    channel_id = ligne["channel_id"]
    try:
        _enrichir_une(conn, client, config, channel_id, resume)
    except ErreurIntrouvable:
        resume.erreurs += 1
        resume.messages.append(f"{ligne['titre']} : chaîne introuvable ou supprimée")
    except ErreurApi as erreur:
        resume.erreurs += 1
        resume.messages.append(f"{ligne['titre']} : {erreur}")
    finally:
        conn.execute(
            "UPDATE channels SET statut = 'moi' WHERE channel_id = ?", (channel_id,)
        )
        conn.commit()
    return resume
