"""Lecture de l'état local pour l'interface.

Tout est en lecture seule : la base, les fichiers d'entrée et le journal de
quota. Aucun appel réseau n'est fait ici, donc rafraîchir l'interface ne coûte
jamais une unité de quota.

Note sur `load_dotenv` : `config.charger()` injecte les valeurs de `.env` dans
`os.environ` (avec `override=False`). L'interface relit `.env` après chaque
sauvegarde ; sans isolation, la première lecture figerait les secrets pour
toute la session et une nouvelle clé ne serait jamais prise en compte dans le
processus courant. `_env_neutre()` rétablit l'environnement après lecture.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import (
    audit,
    cerveau,
    db,
    discovery,
    emailing,
    historique,
    hooks,
    idees,
    production,
    recherche,
    report,
    strategie,
)
from ..config import Config, ErreurConfig, charger, url_locale
from ..quota import CompteurQuota
from .settings import lire_env

NOMS_ENV = (
    "YOUTUBE_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_FALLBACK_BASE_URL",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_API_KEY",
    "EXA_API_KEY",
    "YOUTUBE_OAUTH_CLIENT_ID",
    "YOUTUBE_OAUTH_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "SMTP_PASSWORD",
)


@contextmanager
def _env_neutre():
    """Rend à `os.environ` son état d'avant l'appel à `charger()`."""
    avant = {nom: os.environ.get(nom) for nom in NOMS_ENV}
    try:
        yield
    finally:
        for nom, valeur in avant.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur


def charger_config(racine: Path, exiger_cles: bool = False) -> Config:
    """Charge la configuration sans laisser de secrets dans l'environnement."""
    with _env_neutre():
        return charger(racine, exiger_cles=exiger_cles)


def _valeur_utile(valeur: str | None) -> bool:
    if not valeur:
        return False
    return not valeur.strip().lower().startswith("collez_ici")


@dataclass
class Execution:
    identifiant: int
    mode: str
    debut: str
    fin: str | None
    statut: str
    unites: int
    recherches: int
    appels_llm: int

    @property
    def libelle_statut(self) -> str:
        return {
            "running": "en cours",
            "done": "terminée",
            "budget_atteint": "arrêtée (budget)",
            "erreur": "en erreur",
        }.get(self.statut, self.statut)


@dataclass
class EtatEmail:
    """Réglage d'envoi et dates utiles à la page « Rapports e-mail »."""

    frequence: str = "jamais"
    destinataire: str = ""
    mot_de_passe: bool = False
    heure: str = "08:00"
    dernier: datetime | None = None
    prochain: datetime | None = None
    en_attente: bool = False
    problemes: list[str] = field(default_factory=list)

    @property
    def actif(self) -> bool:
        return self.frequence != "jamais"


@dataclass
class Instantane:
    """Photo de l'état local, affichée sur le tableau de bord."""

    racine: Path
    erreur_config: str | None = None
    erreur_base: str | None = None
    cle_youtube: bool = False
    llm_complet: bool = False
    llm_modele: str = ""
    llm_base_url: str = ""
    llm_secours: bool = False
    positionnement_caracteres: int = 0
    seeds: int = 0
    mots_cles: int = 0
    ma_chaine: str = ""
    international: bool = True
    budget_unites: int = 8000
    budget_recherches: int = 90
    max_recherches_run: int = 20
    unites_jour: int = 0
    recherches_jour: int = 0
    nb_chaines: int = 0
    nb_retenues: int = 0
    nb_outliers: int = 0
    derniere_execution: Execution | None = None
    rapport: Path | None = None
    rapport_ecrit_le: datetime | None = None

    @property
    def problemes(self) -> list[str]:
        """Ce qui empêche encore une exécution complète, en clair."""
        manques = []
        if self.erreur_config:
            manques.append("config.yaml invalide")
        if self.erreur_base:
            manques.append(f"base SQLite illisible : {self.erreur_base}")
        if not self.cle_youtube:
            manques.append("clé YouTube absente")
        if not self.llm_complet:
            manques.append("fournisseur LLM incomplet")
        if self.positionnement_caracteres < 200:
            manques.append("positioning.md trop court")
        if not self.seeds and not self.mots_cles and not self.ma_chaine:
            manques.append("aucune source : ni seed, ni mot-clé, ni ma chaîne")
        return manques

    @property
    def pret(self) -> bool:
        return not self.problemes


def _lire_compte_exploitation(conn: sqlite3.Connection) -> tuple[int, int]:
    statuts = "('enrichie', 'scored', 'ecartee')"
    chaines = conn.execute(f"SELECT COUNT(*) FROM channels WHERE statut IN {statuts}").fetchone()[0]
    outliers = conn.execute(
        "SELECT COUNT(*) FROM v_outliers v JOIN channels c ON c.channel_id = v.channel_id "
        f"WHERE v.score_outlier > 1.0 AND c.statut IN {statuts}"
    ).fetchone()[0]
    return int(chaines), int(outliers)


def _lire_derniere_execution(conn: sqlite3.Connection) -> Execution | None:
    ligne = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if ligne is None:
        return None
    return Execution(
        identifiant=int(ligne["id"]),
        mode=str(ligne["mode"]),
        debut=str(ligne["started_at"]),
        fin=ligne["finished_at"],
        statut=str(ligne["statut"]),
        unites=int(ligne["unites_utilisees"] or 0),
        recherches=int(ligne["recherches_utilisees"] or 0),
        appels_llm=int(ligne["appels_llm"] or 0),
    )


def instantane(racine: Path) -> Instantane:
    """État complet, tolérant aux fichiers absents ou à une config cassée."""
    resultat = Instantane(racine=racine)

    # 1. Fichiers d'entrée : lisibles même si config.yaml est invalide.
    texte_positionnement = ""
    chemin = racine / "positioning.md"
    if chemin.exists():
        try:
            texte_positionnement = chemin.read_text(encoding="utf-8-sig").strip()
        except OSError:
            texte_positionnement = ""
    resultat.positionnement_caracteres = len(texte_positionnement)
    try:
        resultat.seeds = len(discovery.lire_lignes(racine / "seeds.txt"))
        resultat.mots_cles = len(discovery.lire_lignes(racine / "keywords.txt"))
    except OSError:
        pass

    # 2. Présence des clés, lue dans le fichier (jamais affichée).
    env = lire_env(racine / ".env")
    resultat.cle_youtube = _valeur_utile(env.get("YOUTUBE_API_KEY"))
    base_url = env.get("LLM_BASE_URL", "")
    resultat.llm_complet = bool(
        _valeur_utile(base_url)
        and _valeur_utile(env.get("LLM_MODEL"))
        and (_valeur_utile(env.get("LLM_API_KEY")) or url_locale(base_url))
    )
    resultat.llm_modele = env.get("LLM_MODEL", "")
    resultat.llm_base_url = env.get("LLM_BASE_URL", "")
    base_secours = env.get("LLM_FALLBACK_BASE_URL", "")
    resultat.llm_secours = bool(
        _valeur_utile(base_secours)
        and _valeur_utile(env.get("LLM_FALLBACK_MODEL"))
        and (
            _valeur_utile(env.get("LLM_FALLBACK_API_KEY"))
            or url_locale(base_secours)
        )
    )

    # 3. Configuration.
    fichier_db = racine / "data" / "radar.sqlite3"
    try:
        config = charger_config(racine)
    except ErreurConfig as probleme:
        resultat.erreur_config = str(probleme)
        config = None
    if config is not None:
        resultat.cle_youtube = resultat.cle_youtube or bool(config.cle_youtube)
        resultat.llm_complet = resultat.llm_complet or bool(
            config.llm_modele
            and config.llm_base_url
            and (config.llm_cle or url_locale(config.llm_base_url))
        )
        resultat.budget_unites = config.youtube.max_units_per_day
        resultat.budget_recherches = config.youtube.max_search_calls_per_day
        resultat.max_recherches_run = config.youtube.max_search_calls_per_run
        resultat.ma_chaine = config.moi.chaine
        resultat.international = config.moi.recherche_internationale
        fichier_db = config.fichier_db

    # 4. Base et quota.
    if fichier_db.exists():
        try:
            conn = db.connexion(fichier_db)
        except sqlite3.Error as probleme:
            resultat.erreur_base = str(probleme)
            conn = None
        if conn is not None:
            try:
                db.migrer(conn)
                compteur = CompteurQuota(
                    conn,
                    None,
                    budget_unites_jour=resultat.budget_unites,
                    budget_recherches_jour=resultat.budget_recherches,
                )
                totaux = compteur.totaux_jour()
                resultat.unites_jour = totaux.unites_principal
                resultat.recherches_jour = totaux.unites_recherche
                resultat.nb_chaines, resultat.nb_outliers = _lire_compte_exploitation(conn)
                resultat.derniere_execution = _lire_derniere_execution(conn)
                if config is not None:
                    try:
                        resultat.nb_retenues = sum(
                            1 for ligne in report.charger_lignes(conn, config)
                            if ligne.statut == "retenue"
                        )
                    except sqlite3.Error:
                        resultat.nb_retenues = 0
            except sqlite3.Error as probleme:
                resultat.erreur_base = str(probleme)
            finally:
                conn.close()

    # 5. Rapport.
    if config is not None:
        chemin_rapport = config.dossier_sortie / "report.md"
        if chemin_rapport.exists():
            resultat.rapport = chemin_rapport
            try:
                resultat.rapport_ecrit_le = datetime.fromtimestamp(chemin_rapport.stat().st_mtime)
            except OSError:
                resultat.rapport_ecrit_le = None

    return resultat


def etat_email(racine: Path) -> EtatEmail:
    """État de l'envoi automatique, sans réseau et sans révéler de secret."""
    try:
        config = charger_config(racine)
    except ErreurConfig as probleme:
        return EtatEmail(problemes=[str(probleme)])

    reglage = emailing.reglage(config)
    resultat = EtatEmail(
        frequence=reglage.frequence,
        destinataire=reglage.destinataire,
        mot_de_passe=bool(reglage.mot_de_passe),
        heure=reglage.heure,
        problemes=emailing.valider(reglage) if reglage.frequence != "jamais" else [],
    )
    if config.fichier_db.exists():
        try:
            conn = db.connexion(config.fichier_db)
        except sqlite3.Error:
            conn = None
        if conn is not None:
            try:
                db.migrer(conn)
                resultat.dernier = emailing.dernier_envoi(conn)
                resultat.en_attente = emailing.est_du(reglage, resultat.dernier)
            except sqlite3.Error:
                pass
            finally:
                conn.close()
    resultat.prochain = emailing.prochaine_echeance(
        reglage.frequence,
        datetime.now(),
        reglage.heure,
        reglage.jour_semaine,
        reglage.jour_mois,
    )
    return resultat


def resultats(racine: Path) -> list[report.LigneRapport]:
    """Chaînes évaluées, triées exactement comme dans report.md."""
    try:
        config = charger_config(racine)
    except ErreurConfig:
        return []
    if not config.fichier_db.exists():
        return []
    try:
        conn = db.connexion(config.fichier_db)
    except sqlite3.Error:
        return []
    try:
        db.migrer(conn)
        return report.charger_lignes(conn, config)
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def derniere_session_cerveau(racine: Path) -> dict | None:
    """Dernière analyse du cerveau IA, ou None si elle n'existe pas encore.

    Une configuration invalide ne doit pas cacher les notes déjà enregistrées :
    on retombe alors sur l'emplacement standard de la base.
    """
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return cerveau.derniere_session(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def dernieres_idees(racine: Path) -> dict | None:
    """Dernier jeu d'idées de vidéos, ou None s'il n'y en a pas encore."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return idees.derniere_session(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def matiere_cerveau(racine: Path) -> dict[str, list[dict]]:
    """Recherches et idées réellement envoyées au cerveau IA.

    L'interface les affiche pour que l'utilisateur voie que les pages Recherche
    et Idées alimentent l'analyse : trois dossiers et deux jeux au maximum,
    exactement comme `cerveau.generer`.
    """
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return {"recherches": [], "idees": []}
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return {"recherches": [], "idees": []}
    try:
        db.migrer(conn)
        recherches: list[dict] = []
        for session in recherche.sessions_recentes(conn, 3):
            dossier = session.get("dossier") or {}
            sources = [
                source
                for source in dossier.get("sources") or []
                if isinstance(source, dict)
            ]
            faits = [
                fait
                for fait in dossier.get("faits") or []
                if isinstance(fait, dict) and fait.get("statut") == "recoupe"
            ]
            domaines: list[str] = []
            for source in sources:
                domaine = str(source.get("domaine") or "").strip()
                if domaine and domaine not in domaines:
                    domaines.append(domaine)
            recherches.append(
                {
                    "id": session.get("id"),
                    "titre": session.get("titre") or "Sans titre",
                    "mode": session.get("mode") or "sans_ia",
                    "cree_le": session.get("cree_le"),
                    "nb_sources": len(sources),
                    "nb_faits": len(faits),
                    "domaines": domaines[:5],
                }
            )
        jeux: list[dict] = []
        for session in idees.sessions_recentes(conn, 2):
            contenu = session.get("idees") or []
            sujets = [
                str(idee.get("sujet") or "").strip()
                for idee in contenu
                if isinstance(idee, dict) and str(idee.get("sujet") or "").strip()
            ]
            jeux.append(
                {
                    "id": session.get("id"),
                    "cree_le": session.get("cree_le"),
                    "question": session.get("question") or "",
                    "introduction": session.get("introduction") or "",
                    "nb_idees": len(contenu),
                    "sujets": sujets[:5],
                }
            )
        return {"recherches": recherches, "idees": jeux}
    except sqlite3.Error:
        return {"recherches": [], "idees": []}
    finally:
        conn.close()


def derniere_recherche(racine: Path, mode: str | None = None) -> dict | None:
    """Dernier dossier de recherche valide, ou None s'il n'y en a pas encore."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return recherche.derniere_session(conn, mode)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def derniere_audit(racine: Path) -> dict | None:
    """Dernier audit valide de ma chaîne, ou None s'il n'y en a pas encore."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return audit.derniere_session(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def derniere_strategie(racine: Path) -> dict | None:
    """Dernier diagnostic de stratégie valide, ou None."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return strategie.derniere_session(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def concurrents_historique(racine: Path) -> list[dict]:
    """Chaînes concurrentes disposant de vidéos pour l'onglet Concurrents."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return []
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return []
    try:
        db.migrer(conn)
        return historique.chaines(conn)
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def serie_historique(racine: Path, channel_id: str) -> dict | None:
    """Série cumulée des vues connues d'une chaîne concurrente."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists() or not channel_id:
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return historique.serie_chaine(conn, channel_id)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def contexte_strategie(racine: Path, jours: int = 10) -> dict | None:
    """Contexte local des dernières vidéos, sans appel réseau ni LLM."""
    try:
        config = charger_config(racine)
    except ErreurConfig:
        return None
    try:
        conn = db.connexion(config.fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return strategie.construire_contexte(racine, conn, config, jours=jours)
    except (sqlite3.Error, OSError):
        return None
    finally:
        conn.close()


def estimation_production(racine: Path, plan_id: int) -> dict | None:
    """Dernière estimation de production d'une fiche, ou None."""
    try:
        config = charger_config(racine)
        fichier_db = config.fichier_db
    except ErreurConfig:
        fichier_db = Path(racine) / "data" / "radar.sqlite3"
    if not fichier_db.exists():
        return None
    try:
        conn = db.connexion(fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        return production.derniere_estimation(conn, plan_id)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def jeu_hooks(racine: Path) -> dict | None:
    """Dernier jeu de variantes et les analyses d'intro qui l'ont nourri."""
    try:
        config = charger_config(racine)
    except ErreurConfig:
        return None
    if not config.fichier_db.exists():
        return None
    try:
        conn = db.connexion(config.fichier_db)
    except sqlite3.Error:
        return None
    try:
        db.migrer(conn)
        jeu = hooks.dernier_jeu(conn)
        if jeu is None:
            return None
        jeu["intros"] = hooks.intros_du_jeu(conn, jeu)
        return jeu
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def outliers_de(racine: Path, channel_id: str, limite: int = 3) -> list[dict]:
    """Meilleurs outliers d'une chaîne, avec l'analyse LLM s'il y en a une."""
    try:
        config = charger_config(racine)
    except ErreurConfig:
        return []
    if not config.fichier_db.exists():
        return []
    try:
        conn = db.connexion(config.fichier_db)
    except sqlite3.Error:
        return []
    try:
        lignes = conn.execute(
            """
            SELECT v.*, a.accroche, a.explication
            FROM v_outliers v
            LEFT JOIN analyses_outliers a
                   ON a.video_id = v.video_id AND a.valide = 1
            WHERE v.channel_id = ? AND v.score_outlier > 1.0
            ORDER BY v.score_outlier DESC
            LIMIT ?
            """,
            (channel_id, limite),
        ).fetchall()
        return [dict(ligne) for ligne in lignes]
    except sqlite3.Error:
        return []
    finally:
        conn.close()
