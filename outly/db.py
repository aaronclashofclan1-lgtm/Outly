"""Accès SQLite : connexion, création du schéma et écritures idempotentes."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from .models import Chaine, Metriques, ScoreChaine, Video, iso_utc

SCHEMA_VERSION = "10"
CHEMIN_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def connexion(chemin_db: Path) -> sqlite3.Connection:
    """Ouvre (et crée au besoin) la base, en mode WAL.

    Le dossier peut vivre sur un SSD USB externe (exFAT chez certains
    utilisateurs) : on réduit les fsync et on attend plus longtemps les verrous.
    """
    chemin_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(chemin_db), timeout=30)
    conn.row_factory = sqlite3.Row
    # busy_timeout doit être posé avant le changement de journal : une autre
    # instance peut être en train d'écrire au moment de l'ouverture.
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL est le réglage recommandé en WAL : moins d'écritures physiques,
    # sans perdre la cohérence de la base en cas d'arrêt brutal.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -20000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA wal_autocheckpoint = 1000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrer(conn: sqlite3.Connection) -> None:
    """Applique le schéma. Idempotent : relançable à chaque commande."""
    conn.executescript(CHEMIN_SCHEMA.read_text(encoding="utf-8"))
    # Les bases antérieures à la version 10 n'ont pas le cache des mentions
    # trouvées dans les descriptions de vidéos.
    colonnes_videos = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(videos)")}
    for colonne in ("mentions_json", "mentions_scannees_le"):
        if colonne not in colonnes_videos:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {colonne} TEXT")
    # L'index et le trigger dépendent des colonnes ci-dessus : ils sont créés
    # après l'ALTER, sinon une base existante échouerait au premier lancement.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_videos_mentions "
        "ON videos(mentions_scannees_le) WHERE mentions_scannees_le IS NULL"
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS videos_invalidate_mentions
        AFTER UPDATE OF description ON videos
        WHEN OLD.description IS NOT NEW.description
        BEGIN
            UPDATE videos
            SET mentions_json = NULL, mentions_scannees_le = NULL
            WHERE video_id = NEW.video_id;
        END
        """
    )
    # Les bases créées avant la version 2 n'ont pas les repères temporels des
    # transcriptions : CREATE TABLE IF NOT EXISTS ne les ajoute donc pas.
    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(transcripts)")}
    if "segments_json" not in colonnes:
        conn.execute("ALTER TABLE transcripts ADD COLUMN segments_json TEXT")
    # Les bases créées avant la version 5 n'ont pas le classement sur 10 :
    # CREATE TABLE IF NOT EXISTS ne ajoute donc pas ces colonnes.
    for table in ("notes", "watch_later"):
        colonnes = {ligne["name"] for ligne in conn.execute(f"PRAGMA table_info({table})")}
        if "note_sur_10" not in colonnes:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN note_sur_10 INTEGER")
    conn.execute(
        "INSERT INTO meta (cle, valeur) VALUES ('schema_version', ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def lire_meta(conn: sqlite3.Connection, cle: str) -> str | None:
    ligne = conn.execute("SELECT valeur FROM meta WHERE cle = ?", (cle,)).fetchone()
    return ligne["valeur"] if ligne else None


def ecrire_meta(conn: sqlite3.Connection, cle: str, valeur: str) -> None:
    conn.execute(
        "INSERT INTO meta (cle, valeur) VALUES (?, ?) "
        "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
        (cle, valeur),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Exécutions
# ---------------------------------------------------------------------------


def demarrer_run(conn: sqlite3.Connection, mode: str, args: dict) -> int:
    curseur = conn.execute(
        "INSERT INTO runs (started_at, mode, args_json, statut) VALUES (?, ?, ?, 'running')",
        (iso_utc(), mode, json.dumps(args, ensure_ascii=False, sort_keys=True)),
    )
    conn.commit()
    return int(curseur.lastrowid)


def terminer_run(
    conn: sqlite3.Connection,
    run_id: int,
    statut: str,
    unites: int = 0,
    recherches: int = 0,
    appels_llm: int = 0,
) -> None:
    """Clôt une exécution, avec reprise sur une coupure USB transitoire."""
    for tentative in range(3):
        try:
            conn.execute(
                "UPDATE runs SET finished_at = ?, statut = ?, unites_utilisees = ?, "
                "recherches_utilisees = ?, appels_llm = ? WHERE id = ?",
                (iso_utc(), statut, unites, recherches, appels_llm, run_id),
            )
            conn.commit()
            return
        except (sqlite3.OperationalError, OSError):
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            if tentative == 2:
                raise
            time.sleep(0.25 * (tentative + 1))


# ---------------------------------------------------------------------------
# Écritures
# ---------------------------------------------------------------------------


def enregistrer_chaine(conn: sqlite3.Connection, chaine: Chaine) -> None:
    """Insère ou met à jour une chaîne sans écraser les données déjà présentes.

    Les champs optionnels utilisent COALESCE : une réponse partielle de l'API
    (par exemple un simple résultat de recherche) ne doit pas effacer une
    description déjà connue. Le statut ne régresse jamais.
    """
    maintenant = iso_utc()
    conn.execute(
        """
        INSERT INTO channels (
            channel_id, handle, titre, description, pays, cree_le, abonnes,
            abonnes_masques, nb_videos, vues_totales, playlist_uploads,
            mots_cles_chaine, sujets_json, est_seed, source, score_heuristique,
            langue_estimee, statut, recupere_le, ajoute_le, maj_le
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            handle            = COALESCE(excluded.handle, channels.handle),
            titre             = excluded.titre,
            description       = COALESCE(excluded.description, channels.description),
            pays              = COALESCE(excluded.pays, channels.pays),
            cree_le           = COALESCE(excluded.cree_le, channels.cree_le),
            abonnes           = COALESCE(excluded.abonnes, channels.abonnes),
            abonnes_masques   = excluded.abonnes_masques,
            nb_videos         = COALESCE(excluded.nb_videos, channels.nb_videos),
            vues_totales      = COALESCE(excluded.vues_totales, channels.vues_totales),
            playlist_uploads  = COALESCE(excluded.playlist_uploads, channels.playlist_uploads),
            mots_cles_chaine  = COALESCE(excluded.mots_cles_chaine, channels.mots_cles_chaine),
            sujets_json       = COALESCE(excluded.sujets_json, channels.sujets_json),
            est_seed          = MAX(channels.est_seed, excluded.est_seed),
            source            = COALESCE(channels.source, excluded.source),
            score_heuristique = COALESCE(excluded.score_heuristique, channels.score_heuristique),
            langue_estimee    = COALESCE(excluded.langue_estimee, channels.langue_estimee),
            statut            = CASE
                                    WHEN channels.statut IN ('enrichie', 'scored') THEN channels.statut
                                    ELSE excluded.statut
                                END,
            maj_le            = excluded.maj_le
        """,
        (
            chaine.channel_id,
            chaine.handle,
            chaine.titre,
            chaine.description,
            chaine.pays,
            chaine.cree_le,
            chaine.abonnes,
            int(chaine.abonnes_masques),
            chaine.nb_videos,
            chaine.vues_totales,
            chaine.playlist_uploads,
            chaine.mots_cles_chaine,
            json.dumps(chaine.sujets, ensure_ascii=False) if chaine.sujets else None,
            int(chaine.est_seed),
            chaine.source,
            chaine.score_heuristique,
            chaine.langue_estimee,
            maintenant,
            maintenant,
            maintenant,
        ),
    )
    conn.commit()


def enregistrer_reference(
    conn: sqlite3.Connection,
    chaine: Chaine,
    titres_videos: Iterable[str],
) -> None:
    """Mémorise le profil de ma chaîne pour le score de proximité local.

    Les titres viennent de la même lecture que celle déjà faite pour les
    requêtes de découverte : aucun appel API supplémentaire.
    """
    conn.execute(
        """
        INSERT INTO channel_reference (channel_id, titre, mots_cles, description,
                                       titres_videos, maj_le)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            titre         = excluded.titre,
            mots_cles     = excluded.mots_cles,
            description   = excluded.description,
            titres_videos = excluded.titres_videos,
            maj_le        = excluded.maj_le
        """,
        (
            chaine.channel_id,
            chaine.titre,
            chaine.mots_cles_chaine,
            chaine.description,
            json.dumps(list(titres_videos), ensure_ascii=False),
            iso_utc(),
        ),
    )
    conn.commit()


def enregistrer_videos(conn: sqlite3.Connection, videos: Iterable[Video]) -> int:
    """Insère ou met à jour des vidéos. Renvoie le nombre de lignes traitées."""
    maintenant = iso_utc()
    lignes = [
        (
            video.video_id,
            video.channel_id,
            video.titre,
            video.description,
            video.publie_le,
            video.duree_s,
            video.vues,
            video.likes,
            video.commentaires,
            video.est_short,
            video.live,
            video.rang_uploads,
            maintenant,
        )
        for video in videos
    ]
    if not lignes:
        return 0
    conn.executemany(
        """
        INSERT INTO videos (video_id, channel_id, titre, description, publie_le,
                            duree_s, vues, likes, commentaires, est_short, live,
                            rang_uploads, recupere_le)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            titre        = excluded.titre,
            description  = COALESCE(excluded.description, videos.description),
            publie_le    = COALESCE(excluded.publie_le, videos.publie_le),
            duree_s      = COALESCE(excluded.duree_s, videos.duree_s),
            vues         = COALESCE(excluded.vues, videos.vues),
            likes        = COALESCE(excluded.likes, videos.likes),
            commentaires = COALESCE(excluded.commentaires, videos.commentaires),
            est_short    = COALESCE(excluded.est_short, videos.est_short),
            live         = excluded.live,
            rang_uploads = COALESCE(excluded.rang_uploads, videos.rang_uploads),
            recupere_le  = excluded.recupere_le
        """,
        lignes,
    )
    conn.commit()
    return len(lignes)


def enregistrer_metriques(conn: sqlite3.Connection, metriques: Metriques) -> None:
    conn.execute(
        """
        INSERT INTO channel_metrics (channel_id, calcule_le, fenetre, n_videos, n_shorts,
                                     n_longues, mediane_vues_short, mediane_vues_longues,
                                     cadence_semaine, jours_depuis_publication,
                                     meilleur_outlier, mix_format, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            calcule_le               = excluded.calcule_le,
            fenetre                  = excluded.fenetre,
            n_videos                 = excluded.n_videos,
            n_shorts                 = excluded.n_shorts,
            n_longues                = excluded.n_longues,
            mediane_vues_short       = excluded.mediane_vues_short,
            mediane_vues_longues     = excluded.mediane_vues_longues,
            cadence_semaine          = excluded.cadence_semaine,
            jours_depuis_publication = excluded.jours_depuis_publication,
            meilleur_outlier         = excluded.meilleur_outlier,
            mix_format               = excluded.mix_format,
            detail_json              = excluded.detail_json
        """,
        (
            metriques.channel_id,
            iso_utc(),
            metriques.fenetre,
            metriques.n_videos,
            metriques.n_shorts,
            metriques.n_longues,
            metriques.mediane_vues_short,
            metriques.mediane_vues_longues,
            metriques.cadence_semaine,
            metriques.jours_depuis_publication,
            metriques.meilleur_outlier,
            metriques.mix_format,
            None,
        ),
    )
    conn.commit()


def enregistrer_score(
    conn: sqlite3.Connection,
    channel_id: str,
    run_id: int | None,
    empreinte_prompt: str,
    prompt_version: str,
    fournisseur: str | None,
    modele: str | None,
    score: ScoreChaine | None,
    reponse_brute: str | None,
    tentatives: int,
    erreur: str | None,
    valide: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO channel_scores (channel_id, run_id, empreinte_prompt, prompt_version,
                                    fournisseur, modele, score_proximite, recouvrement_audience,
                                    format_detecte, langue, justification, risques_json,
                                    reponse_brute, tentatives, valide, erreur, cree_le)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, prompt_version, empreinte_prompt) DO UPDATE SET
            run_id       = excluded.run_id,
            fournisseur  = excluded.fournisseur,
            modele       = excluded.modele,
            score_proximite       = excluded.score_proximite,
            recouvrement_audience = excluded.recouvrement_audience,
            format_detecte        = excluded.format_detecte,
            langue                = excluded.langue,
            justification         = excluded.justification,
            risques_json          = excluded.risques_json,
            reponse_brute         = excluded.reponse_brute,
            tentatives            = excluded.tentatives,
            valide                = excluded.valide,
            erreur                = excluded.erreur,
            cree_le               = excluded.cree_le
        """,
        (
            channel_id,
            run_id,
            empreinte_prompt,
            prompt_version,
            fournisseur,
            modele,
            score.score_proximite if score else None,
            score.recouvrement_audience if score else None,
            score.format_detecte if score else None,
            score.langue if score else None,
            score.justification if score else None,
            json.dumps(score.risques, ensure_ascii=False) if score else None,
            reponse_brute,
            tentatives,
            int(valide),
            erreur,
            iso_utc(),
        ),
    )
    conn.commit()


def marquer_statut(
    conn: sqlite3.Connection, channel_id: str, statut: str, raison: str | None = None
) -> None:
    conn.execute(
        "UPDATE channels SET statut = ?, raison_ecart = ?, maj_le = ? WHERE channel_id = ?",
        (statut, raison, iso_utc(), channel_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Lectures utilitaires
# ---------------------------------------------------------------------------


def chaine_existe(conn: sqlite3.Connection, channel_id: str) -> bool:
    return conn.execute("SELECT 1 FROM channels WHERE channel_id = ?", (channel_id,)).fetchone() is not None


def compter(conn: sqlite3.Connection, table: str, condition: str = "1=1", params: tuple = ()) -> int:
    # `table` et `condition` proviennent toujours du code, jamais d'une entrée utilisateur.
    ligne = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {condition}", params).fetchone()
    return int(ligne["n"]) if ligne else 0


def copier_si_absent(source: Path, destination: Path) -> bool:
    """Copie un fichier modèle sans jamais écraser l'existant.

    La copie passe par un fichier temporaire puis un lien exclusif : un arrêt
    brutal ne peut pas laisser un ``config.yaml`` tronqué au premier lancement.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporaire = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        shutil.copyfile(source, temporaire)
        try:
            os.link(temporaire, destination)
        except FileExistsError:
            return False
        except OSError:
            # Certains supports (FAT/exFAT, partages réseau) refusent les liens
            # durs. On garde alors le remplacement atomique, après une dernière
            # vérification pour ne pas écraser un fichier créé entre-temps.
            if destination.exists():
                return False
            os.replace(temporaire, destination)
        return True
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass


def lire_chaine(conn: sqlite3.Connection, channel_id: str) -> Chaine | None:
    """Relit une chaîne complète depuis la base."""
    ligne = conn.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
    if ligne is None:
        return None
    return Chaine(
        channel_id=ligne["channel_id"],
        titre=ligne["titre"],
        handle=ligne["handle"],
        description=ligne["description"],
        pays=ligne["pays"],
        cree_le=ligne["cree_le"],
        abonnes=ligne["abonnes"],
        abonnes_masques=bool(ligne["abonnes_masques"]),
        nb_videos=ligne["nb_videos"],
        vues_totales=ligne["vues_totales"],
        playlist_uploads=ligne["playlist_uploads"],
        mots_cles_chaine=ligne["mots_cles_chaine"],
        sujets=json.loads(ligne["sujets_json"]) if ligne["sujets_json"] else [],
        est_seed=bool(ligne["est_seed"]),
        source=ligne["source"],
        score_heuristique=ligne["score_heuristique"],
        langue_estimee=ligne["langue_estimee"],
    )


def lire_videos(conn: sqlite3.Connection, channel_id: str, limite: int = 50) -> list[Video]:
    """Vidéos d'une chaîne, de la plus récente à la plus ancienne."""
    lignes = conn.execute(
        "SELECT * FROM videos WHERE channel_id = ? "
        "ORDER BY COALESCE(publie_le, '') DESC LIMIT ?",
        (channel_id, limite),
    ).fetchall()
    return [
        Video(
            video_id=ligne["video_id"],
            channel_id=ligne["channel_id"],
            titre=ligne["titre"],
            description=ligne["description"],
            publie_le=ligne["publie_le"],
            duree_s=ligne["duree_s"],
            vues=ligne["vues"],
            likes=ligne["likes"],
            commentaires=ligne["commentaires"],
            est_short=ligne["est_short"],
            live=ligne["live"],
            rang_uploads=ligne["rang_uploads"],
        )
        for ligne in lignes
    ]


def enregistrer_analyse_outlier(
    conn: sqlite3.Connection,
    video_id: str,
    run_id: int | None,
    prompt_version: str,
    fournisseur: str | None,
    modele: str | None,
    donnees: dict | None,
    erreur: str | None,
) -> None:
    """Enregistre (ou remplace) l'analyse LLM d'une vidéo outlier."""
    conn.execute(
        "INSERT INTO analyses_outliers (video_id, run_id, prompt_version, fournisseur, modele, "
        "accroche, structure_json, explication, reutilisable, valide, erreur, cree_le) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(video_id, prompt_version) DO UPDATE SET run_id = excluded.run_id, "
        "fournisseur = excluded.fournisseur, modele = excluded.modele, "
        "accroche = excluded.accroche, structure_json = excluded.structure_json, "
        "explication = excluded.explication, reutilisable = excluded.reutilisable, "
        "valide = excluded.valide, erreur = excluded.erreur, cree_le = excluded.cree_le",
        (
            video_id,
            run_id,
            prompt_version,
            fournisseur,
            modele,
            (donnees or {}).get("accroche"),
            json.dumps((donnees or {}).get("structure"), ensure_ascii=False)
            if donnees
            else None,
            (donnees or {}).get("explication"),
            json.dumps((donnees or {}).get("reutilisable") or [], ensure_ascii=False)
            if donnees
            else None,
            int(donnees is not None),
            erreur,
            iso_utc(),
        ),
    )
    conn.commit()


def lire_metriques(conn: sqlite3.Connection, channel_id: str) -> Metriques | None:
    ligne = conn.execute(
        "SELECT * FROM channel_metrics WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    if ligne is None:
        return None
    return Metriques(
        channel_id=ligne["channel_id"],
        fenetre=ligne["fenetre"],
        n_videos=ligne["n_videos"],
        n_shorts=ligne["n_shorts"],
        n_longues=ligne["n_longues"],
        mediane_vues_short=ligne["mediane_vues_short"],
        mediane_vues_longues=ligne["mediane_vues_longues"],
        cadence_semaine=ligne["cadence_semaine"],
        jours_depuis_publication=ligne["jours_depuis_publication"],
        meilleur_outlier=ligne["meilleur_outlier"],
        mix_format=ligne["mix_format"],
    )
