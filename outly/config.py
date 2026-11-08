"""Chargement et validation de la configuration.

Deux sources, volontairement séparées :
  * `.env`        : secrets et points d'accès (clé YouTube, base_url/modèle/clé LLM) ;
  * `config.yaml` : comportement (seuils, budgets, fenêtres, sorties).

Toute erreur de configuration lève `ErreurConfig` avec un message en français,
exploitable directement par la CLI.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv


class ErreurConfig(Exception):
    """Configuration absente, illisible ou incohérente."""


FAUX = {"false", "0", "non", "no", ""}
# Heure locale au format 24 h, utilisée par la planification des e-mails.
_HEURE_VALIDE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _booleen(valeur: Any, defaut: bool, chemin: str) -> bool:
    if valeur is None:
        return defaut
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, (int, float)):
        return bool(valeur)
    texte = str(valeur).strip().lower()
    if texte in ("true", "1", "oui", "yes", "vrai"):
        return True
    if texte in FAUX:
        return False
    raise ErreurConfig(f"Valeur booléenne invalide pour {chemin} : {valeur!r}")


def _entier(valeur: Any, defaut: int, chemin: str, mini: int, maxi: int) -> int:
    if valeur is None:
        return defaut
    try:
        resultat = int(valeur)
    except (TypeError, ValueError) as erreur:
        raise ErreurConfig(f"Valeur entière invalide pour {chemin} : {valeur!r}") from erreur
    if not mini <= resultat <= maxi:
        raise ErreurConfig(
            f"{chemin} doit être compris entre {mini} et {maxi} (valeur lue : {resultat})."
        )
    return resultat


def _liste_nombres(valeur: Any, defaut: list[int], chemin: str) -> list[int]:
    if valeur is None:
        return list(defaut)
    if not isinstance(valeur, list):
        raise ErreurConfig(f"{chemin} doit être une liste d'entiers.")
    resultat = []
    for element in valeur:
        try:
            resultat.append(int(element))
        except (TypeError, ValueError) as erreur:
            raise ErreurConfig(f"Élément non entier dans {chemin} : {element!r}") from erreur
    if not resultat:
        raise ErreurConfig(f"{chemin} ne peut pas être vide.")
    return resultat


def _section(brut: dict, nom: str, avertissements: list[str]) -> dict:
    """Renvoie une section du YAML et signale les clés inconnues (fautes de frappe)."""
    valeur = brut.get(nom)
    if valeur is None:
        return {}
    if not isinstance(valeur, dict):
        raise ErreurConfig(f"La section « {nom} » doit être un objet YAML.")
    return valeur


@dataclass
class ConfigYoutube:
    region_code: str = "FR"
    relevance_language: str = "fr"
    batch_size: int = 50
    videos_per_channel: int = 30
    max_search_calls_per_run: int = 20
    max_search_calls_per_day: int = 90
    max_units_per_run: int = 2000
    max_units_per_day: int = 8000
    subscriber_min: int = 1000
    subscriber_max: int = 5000000
    min_channel_age_days: int = 180
    max_candidates_per_run: int = 400
    analysis_limit: int = 60


@dataclass
class ConfigDecouverte:
    use_channel_sections: bool = True
    use_description_links: bool = True
    require_keyword_hit: bool = False


@dataclass
class ConfigMoi:
    """Point de départ « ma chaîne » et portée géographique des recherches."""

    chaine: str = ""
    recherche_internationale: bool = True


@dataclass
class ConfigCache:
    ttl_channel_days: int = 7
    ttl_videos_days: int = 1
    ttl_search_days: int = 3
    max_ttl_days: int = 30


@dataclass
class ConfigMetriques:
    short_max_seconds: int = 180
    min_videos_for_metrics: int = 3


@dataclass
class ConfigLlm:
    max_channels_per_run: int = 30
    temperature: float = 0.0
    timeout_s: int = 60
    max_retries: int = 2
    requests_per_minute: int = 8
    json_mode: str = "auto"
    min_score_keep: int = 50
    fallback_enabled: bool = True


@dataclass
class ConfigClassement:
    """Règles de rétention du rapport, y compris le repli sans LLM."""

    min_retenues: int = 5
    seuil_local: int = 40


@dataclass
class ConfigOutliers:
    max_channels: int = 3
    per_channel: int = 3
    max_chars_transcription: int = 12000


@dataclass
class ConfigTranscriptions:
    enabled: bool = False
    languages: list[str] = field(default_factory=lambda: ["fr", "en"])


@dataclass
class ConfigHooks:
    """Génération d'accroches à partir des intros des vidéos outliers.

    `enabled` contrôle la génération automatique en fin d'exécution complète.
    Le sujet reste vide tant que l'utilisateur ne l'a pas renseigné : aucun
    appel LLM n'est alors fait.
    """

    enabled: bool = False
    sujet: str = ""
    variants: int = 6
    max_videos: int = 6
    max_chars_transcription: int = 12000


@dataclass
class ConfigSortie:
    dossier: str = "out"
    csv_separateur: str = ";"
    csv_encodage: str = "utf-8-sig"
    report_top: int = 10


@dataclass
class ConfigEmail:
    """Rapports envoyés par e-mail (le mot de passe reste dans `.env`)."""

    destinataire: str = ""
    # Fixés par l'application ; conservés pour relire d'anciens config.yaml.
    expediteur: str = "outly.analyze@gmail.com"
    smtp_hote: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_ssl: bool = False
    frequence: str = "jamais"
    heure: str = "08:00"
    jour_semaine: int = 1
    jour_mois: int = 1
    joindre_csv: bool = True


@dataclass
class Config:
    racine: Path
    fichier_db: Path
    dossier_sortie: Path
    youtube: ConfigYoutube = field(default_factory=ConfigYoutube)
    moi: ConfigMoi = field(default_factory=ConfigMoi)
    decouverte: ConfigDecouverte = field(default_factory=ConfigDecouverte)
    cache: ConfigCache = field(default_factory=ConfigCache)
    metriques: ConfigMetriques = field(default_factory=ConfigMetriques)
    llm: ConfigLlm = field(default_factory=ConfigLlm)
    classement: ConfigClassement = field(default_factory=ConfigClassement)
    outliers: ConfigOutliers = field(default_factory=ConfigOutliers)
    transcriptions: ConfigTranscriptions = field(default_factory=ConfigTranscriptions)
    hooks: ConfigHooks = field(default_factory=ConfigHooks)
    sortie: ConfigSortie = field(default_factory=ConfigSortie)
    email: ConfigEmail = field(default_factory=ConfigEmail)
    avertissements: list[str] = field(default_factory=list)

    # --- Secrets (lus depuis .env, jamais journalisés) ----------------------
    cle_youtube: str | None = None
    llm_base_url: str | None = None
    llm_modele: str | None = None
    llm_cle: str | None = None
    llm_secours_base_url: str | None = None
    llm_secours_modele: str | None = None
    llm_secours_cle: str | None = None
    exa_cle: str | None = None
    smtp_mot_de_passe: str | None = None

    @property
    def chemin_positioning(self) -> Path:
        return self.racine / "positioning.md"

    @property
    def chemin_seeds(self) -> Path:
        return self.racine / "seeds.txt"

    @property
    def chemin_keywords(self) -> Path:
        return self.racine / "keywords.txt"


def _charge_yaml(chemin: Path) -> dict:
    if not chemin.exists():
        raise ErreurConfig(
            f"Fichier de configuration introuvable : {chemin}\n"
            "Lancez d'abord : python -m outly init"
        )
    try:
        contenu = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except yaml.YAMLError as erreur:
        raise ErreurConfig(f"YAML invalide dans {chemin.name} : {erreur}") from erreur
    if contenu is None:
        return {}
    if not isinstance(contenu, dict):
        raise ErreurConfig(f"{chemin.name} doit contenir un objet YAML à la racine.")
    return contenu


def est_empaquetee() -> bool:
    """Vrai lorsque le code tourne depuis l'exécutable construit."""
    return bool(getattr(sys, "frozen", False))


def url_locale(base_url: str | None) -> bool:
    """Vrai pour un serveur LLM local, qui n'exige aucune clé.

    Ollama installé sur le poste écoute sur `localhost:11434` et ignore la clé.
    Exiger `LLM_API_KEY` pour ce cas rendait le fournisseur local inutilisable.
    Seules les adresses de bouclage sont reconnues : un serveur distant sans
    authentification reste refusé, ce qui évite d'envoyer des prompts à un
    service ouvert par erreur.
    """
    if not base_url:
        return False
    try:
        hote = (urlparse(str(base_url).strip()).hostname or "").lower()
    except ValueError:
        return False
    return hote in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@lru_cache(maxsize=1)
def dossier_executable() -> Path:
    """Dossier qui contient `Outly.exe` (empaquetée uniquement)."""
    return Path(sys.executable).resolve().parent


@lru_cache(maxsize=1)
def dossier_donnees() -> Path:
    """Dossier des fichiers de l'utilisateur pour l'exécutable.

    Portatif d'abord : le dossier de l'exécutable, pour que la configuration,
    la base et les rapports restent à côté de l'application et suivent un
    simple déplacement du dossier. S'il n'est pas inscriptible (Program Files,
    support en lecture seule), on bascule sur `%LOCALAPPDATA%/Outly`.
    """
    dossier = dossier_executable()
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        sonde = dossier / ".outly-ecriture"
        sonde.touch()
        sonde.unlink()
        return dossier
    except OSError:
        base = (os.getenv("LOCALAPPDATA") or "").strip()
        cible = (
            Path(base) if base else Path.home() / "AppData" / "Local"
        ) / "Outly"
        cible.mkdir(parents=True, exist_ok=True)
        return cible


def racine_projet() -> Path:
    """Dossier de travail d'Outly.

    En développement, le dossier qui contient le paquet. Empaquetée, le
    dossier de l'exécutable (voir `dossier_donnees`). `OUTLY_ROOT` permet de
    cibler un autre projet : c'est ainsi que l'interface lance la CLI sur le
    dossier qu'elle édite, quel que soit le répertoire courant du processus.
    """
    forcee = (os.getenv("OUTLY_ROOT") or "").strip()
    if forcee:
        return Path(forcee).expanduser().resolve()
    if est_empaquetee():
        return dossier_donnees()
    return Path(__file__).resolve().parent.parent


def charger(racine: Path | None = None, exiger_cles: bool = False) -> Config:
    """Charge `.env` + `config.yaml` et valide l'ensemble.

    `exiger_cles=False` permet à `doctor --offline` de fonctionner sans clé API.
    """
    racine = (racine or racine_projet()).resolve()
    load_dotenv(racine / ".env", override=False)

    brut = _charge_yaml(racine / "config.yaml")
    config = Config(
        racine=racine,
        fichier_db=racine / "data" / "radar.sqlite3",
        dossier_sortie=racine / "out",
    )
    avertissements: list[str] = []

    # --- youtube ------------------------------------------------------------
    yt = _section(brut, "youtube", avertissements)
    config.youtube = ConfigYoutube(
        region_code=str(yt.get("region_code", "FR")).upper(),
        relevance_language=str(yt.get("relevance_language", "fr")).lower(),
        batch_size=_entier(yt.get("batch_size"), 50, "youtube.batch_size", 1, 50),
        videos_per_channel=_entier(yt.get("videos_per_channel"), 30, "youtube.videos_per_channel", 1, 50),
        max_search_calls_per_run=_entier(
            yt.get("max_search_calls_per_run"), 20, "youtube.max_search_calls_per_run", 0, 100
        ),
        max_search_calls_per_day=_entier(
            yt.get("max_search_calls_per_day"), 90, "youtube.max_search_calls_per_day", 0, 100
        ),
        max_units_per_run=_entier(yt.get("max_units_per_run"), 2000, "youtube.max_units_per_run", 0, 10000),
        max_units_per_day=_entier(yt.get("max_units_per_day"), 8000, "youtube.max_units_per_day", 0, 10000),
        subscriber_min=_entier(yt.get("subscriber_min"), 1000, "youtube.subscriber_min", 0, 10**9),
        subscriber_max=_entier(yt.get("subscriber_max"), 5_000_000, "youtube.subscriber_max", 0, 10**10),
        min_channel_age_days=_entier(
            yt.get("min_channel_age_days"), 180, "youtube.min_channel_age_days", 0, 3650
        ),
        max_candidates_per_run=_entier(
            yt.get("max_candidates_per_run"), 400, "youtube.max_candidates_per_run", 1, 5000
        ),
        analysis_limit=_entier(yt.get("analysis_limit"), 60, "youtube.analysis_limit", 1, 1000),
    )
    if config.youtube.subscriber_min > config.youtube.subscriber_max:
        raise ErreurConfig("youtube.subscriber_min dépasse youtube.subscriber_max.")
    if config.youtube.max_search_calls_per_run > config.youtube.max_search_calls_per_day:
        raise ErreurConfig(
            "youtube.max_search_calls_per_run dépasse max_search_calls_per_day : "
            "une exécution ne peut pas consommer plus que le budget journalier."
        )
    if config.youtube.max_units_per_run > config.youtube.max_units_per_day:
        raise ErreurConfig(
            "youtube.max_units_per_run dépasse max_units_per_day : "
            "une exécution ne peut pas consommer plus que le budget journalier."
        )

    # --- discovery ----------------------------------------------------------
    dec = _section(brut, "discovery", avertissements)
    config.decouverte = ConfigDecouverte(
        use_channel_sections=_booleen(dec.get("use_channel_sections"), True, "discovery.use_channel_sections"),
        use_description_links=_booleen(
            dec.get("use_description_links"), True, "discovery.use_description_links"
        ),
        require_keyword_hit=_booleen(
            dec.get("require_keyword_hit"), False, "discovery.require_keyword_hit"
        ),
    )

    # --- moi -----------------------------------------------------------------
    moi = _section(brut, "moi", avertissements)
    chaine_moi = str(moi.get("chaine", "") or "").strip()
    if chaine_moi.lower().startswith("collez_ici"):
        chaine_moi = ""
    config.moi = ConfigMoi(
        chaine=chaine_moi,
        recherche_internationale=_booleen(
            moi.get("recherche_internationale"), True, "moi.recherche_internationale"
        ),
    )

    # --- cache --------------------------------------------------------------
    ca = _section(brut, "cache", avertissements)
    config.cache = ConfigCache(
        ttl_channel_days=_entier(ca.get("ttl_channel_days"), 7, "cache.ttl_channel_days", 1, 365),
        ttl_videos_days=_entier(ca.get("ttl_videos_days"), 1, "cache.ttl_videos_days", 1, 365),
        ttl_search_days=_entier(ca.get("ttl_search_days"), 3, "cache.ttl_search_days", 1, 365),
        max_ttl_days=_entier(ca.get("max_ttl_days"), 30, "cache.max_ttl_days", 1, 30),
    )
    plafond = config.cache.max_ttl_days
    for nom in ("ttl_channel_days", "ttl_videos_days", "ttl_search_days"):
        valeur = getattr(config.cache, nom)
        if valeur > plafond:
            setattr(config.cache, nom, plafond)
            avertissements.append(
                f"cache.{nom} = {valeur} j ramené à {plafond} j : les Developer Policies YouTube "
                "imposent de rafraîchir ou supprimer les données API sous 30 jours."
            )

    # --- metrics ------------------------------------------------------------
    me = _section(brut, "metrics", avertissements)
    config.metriques = ConfigMetriques(
        short_max_seconds=_entier(
            me.get("short_max_seconds"), 180, "metrics.short_max_seconds", 1, 3600
        ),
        min_videos_for_metrics=_entier(
            me.get("min_videos_for_metrics"), 3, "metrics.min_videos_for_metrics", 1, 50
        ),
    )

    # --- llm ----------------------------------------------------------------
    ll = _section(brut, "llm", avertissements)
    mode_json = str(ll.get("json_mode", "auto")).lower()
    if mode_json not in ("auto", "json_object", "json_schema", "none"):
        raise ErreurConfig(
            "llm.json_mode doit valoir auto, json_object, json_schema ou none "
            f"(valeur lue : {mode_json!r})."
        )
    try:
        temperature = float(ll.get("temperature", 0))
    except (TypeError, ValueError) as erreur:
        raise ErreurConfig("llm.temperature doit être un nombre entre 0 et 2.") from erreur
    if not 0 <= temperature <= 2:
        raise ErreurConfig("llm.temperature doit être comprise entre 0 et 2.")
    config.llm = ConfigLlm(
        max_channels_per_run=_entier(
            ll.get("max_channels_per_run"), 30, "llm.max_channels_per_run", 1, 1000
        ),
        temperature=temperature,
        timeout_s=_entier(ll.get("timeout_s"), 60, "llm.timeout_s", 5, 600),
        max_retries=_entier(ll.get("max_retries"), 2, "llm.max_retries", 0, 6),
        requests_per_minute=_entier(
            ll.get("requests_per_minute"), 8, "llm.requests_per_minute", 1, 600
        ),
        json_mode=mode_json,
        min_score_keep=_entier(ll.get("min_score_keep"), 50, "llm.min_score_keep", 0, 100),
        fallback_enabled=_booleen(ll.get("fallback_enabled"), True, "llm.fallback_enabled"),
    )

    # --- classement ---------------------------------------------------------
    cl = _section(brut, "classement", avertissements)
    config.classement = ConfigClassement(
        min_retenues=_entier(
            cl.get("min_retenues"), 5, "classement.min_retenues", 0, 50
        ),
        seuil_local=_entier(
            cl.get("seuil_local"), 40, "classement.seuil_local", 0, 100
        ),
    )

    # --- outliers -----------------------------------------------------------
    ol = _section(brut, "outliers", avertissements)
    config.outliers = ConfigOutliers(
        max_channels=_entier(ol.get("max_channels"), 3, "outliers.max_channels", 1, 100),
        per_channel=_entier(ol.get("per_channel"), 3, "outliers.per_channel", 1, 10),
        max_chars_transcription=_entier(
            ol.get("max_chars_transcription"), 12000, "outliers.max_chars_transcription", 500, 100000
        ),
    )

    # --- transcripts --------------------------------------------------------
    tr = _section(brut, "transcripts", avertissements)
    langues = tr.get("languages") or ["fr", "en"]
    if not isinstance(langues, list) or not all(isinstance(langue, str) for langue in langues):
        raise ErreurConfig("transcripts.languages doit être une liste de codes de langue.")
    config.transcriptions = ConfigTranscriptions(
        enabled=_booleen(tr.get("enabled"), False, "transcripts.enabled"),
        languages=[langue.lower() for langue in langues],
    )

    # --- hooks --------------------------------------------------------------
    hk = _section(brut, "hooks", avertissements)
    config.hooks = ConfigHooks(
        enabled=_booleen(hk.get("enabled"), False, "hooks.enabled"),
        sujet=str(hk.get("sujet", "") or "").strip()[:400],
        variants=_entier(hk.get("variants"), 6, "hooks.variants", 1, 12),
        max_videos=_entier(hk.get("max_videos"), 6, "hooks.max_videos", 1, 20),
        max_chars_transcription=_entier(
            hk.get("max_chars_transcription"),
            12000,
            "hooks.max_chars_transcription",
            1000,
            100000,
        ),
    )

    # --- output -------------------------------------------------------------
    so = _section(brut, "output", avertissements)
    separateur = str(so.get("csv_separator", ";"))
    if separateur not in (";", ","):
        raise ErreurConfig("output.csv_separator doit valoir « ; » ou « , ».")
    encodage = str(so.get("csv_encoding", "utf-8-sig")).lower()
    if encodage not in ("utf-8-sig", "utf-8"):
        raise ErreurConfig("output.csv_encoding doit valoir utf-8-sig ou utf-8.")
    config.sortie = ConfigSortie(
        dossier=str(so.get("dir", "out")),
        csv_separateur=separateur,
        csv_encodage=encodage,
        report_top=_entier(so.get("report_top"), 10, "output.report_top", 1, 200),
    )
    config.dossier_sortie = racine / config.sortie.dossier

    # --- email --------------------------------------------------------------
    em = _section(brut, "email", avertissements)
    frequence = str(em.get("frequence", "jamais") or "jamais").strip().lower()
    if frequence not in ("jamais", "quotidien", "hebdomadaire", "mensuel"):
        raise ErreurConfig(
            "email.frequence doit valoir jamais, quotidien, hebdomadaire ou mensuel "
            f"(valeur lue : {frequence!r})."
        )
    heure = str(em.get("heure", "08:00") or "08:00").strip()
    if not _HEURE_VALIDE.match(heure):
        raise ErreurConfig(f"email.heure doit être de la forme HH:MM (valeur lue : {heure!r}).")
    config.email = ConfigEmail(
        destinataire=str(em.get("destinataire", "") or "").strip(),
        expediteur=str(em.get("expediteur", "outly.analyze@gmail.com") or "").strip(),
        smtp_hote=str(em.get("smtp_hote", "smtp.gmail.com") or "").strip(),
        smtp_port=_entier(em.get("smtp_port"), 587, "email.smtp_port", 1, 65535),
        smtp_ssl=_booleen(em.get("smtp_ssl"), False, "email.smtp_ssl"),
        frequence=frequence,
        heure=heure,
        jour_semaine=_entier(em.get("jour_semaine"), 1, "email.jour_semaine", 1, 7),
        jour_mois=_entier(em.get("jour_mois"), 1, "email.jour_mois", 1, 28),
        joindre_csv=_booleen(em.get("joindre_csv"), True, "email.joindre_csv"),
    )

    # --- clés inconnues -----------------------------------------------------
    connues = {
        "youtube", "moi", "discovery", "cache", "metrics", "llm", "outliers",
        "transcripts", "hooks", "output", "email",
    }
    for cle in brut:
        if cle not in connues:
            avertissements.append(f"Section inconnue dans config.yaml : « {cle} » (ignorée).")

    # --- secrets ------------------------------------------------------------
    def _secret(nom: str) -> str | None:
        """Lit une variable en refusant les valeurs de remplissage du modèle."""
        valeur = (os.getenv(nom) or "").strip()
        if not valeur or valeur.lower().startswith("collez_ici"):
            return None
        return valeur

    config.cle_youtube = _secret("YOUTUBE_API_KEY")
    config.llm_base_url = _secret("LLM_BASE_URL")
    config.llm_modele = _secret("LLM_MODEL")
    config.llm_cle = _secret("LLM_API_KEY")
    config.llm_secours_base_url = _secret("LLM_FALLBACK_BASE_URL")
    config.llm_secours_modele = _secret("LLM_FALLBACK_MODEL")
    config.llm_secours_cle = _secret("LLM_FALLBACK_API_KEY")
    config.exa_cle = _secret("EXA_API_KEY")
    config.smtp_mot_de_passe = _secret("SMTP_PASSWORD")

    config.avertissements = avertissements

    if exiger_cles:
        manquantes = []
        if not config.cle_youtube:
            manquantes.append("YOUTUBE_API_KEY")
        if not config.llm_base_url:
            manquantes.append("LLM_BASE_URL")
        if not config.llm_modele:
            manquantes.append("LLM_MODEL")
        # Ollama local (http://localhost:11434/v1) n'a pas de clé : on ne
        # réclame LLM_API_KEY que pour un fournisseur distant.
        if not config.llm_cle and not url_locale(config.llm_base_url):
            manquantes.append("LLM_API_KEY")
        if manquantes:
            raise ErreurConfig(
                "Variables manquantes dans .env : " + ", ".join(manquantes) + ".\n"
                "Copiez .env.example en .env puis renseignez-les."
            )

    return config


def resume(config: Config) -> str:
    """Résumé lisible de la configuration effective (aucun secret affiché)."""
    yt = config.youtube
    lignes = [
        f"Racine            : {config.racine}",
        f"Base SQLite       : {config.fichier_db}",
        f"Sorties           : {config.dossier_sortie}",
        f"Pays / langue     : {yt.region_code} / {yt.relevance_language}"
        + (" + international" if config.moi.recherche_internationale else " (France seule)"),
        f"Ma chaîne         : {config.moi.chaine or '(non renseignée)'}",
        f"Recherches        : {yt.max_search_calls_per_run}/run, {yt.max_search_calls_per_day}/jour",
        f"Budget unités     : {yt.max_units_per_run}/run, {yt.max_units_per_day}/jour",
        f"Vidéos par chaîne : {yt.videos_per_channel} (fenêtre des métriques)",
        f"Seuil Short       : {config.metriques.short_max_seconds} s",
        f"Cache             : chaînes {config.cache.ttl_channel_days} j, "
        f"vidéos {config.cache.ttl_videos_days} j, recherches {config.cache.ttl_search_days} j",
        f"LLM               : {config.llm_modele or '(non configuré)'} "
        f"via {config.llm_base_url or '(non configuré)'}, "
        f"{config.llm.max_channels_per_run} chaînes/run, {config.llm.requests_per_minute} req/min",
        "Hooks             : "
        + (
            f"activés, {config.hooks.variants} variante(s), sujet="
            f"{config.hooks.sujet or '(à renseigner)'}"
            if config.hooks.enabled
            else "désactivés"
        ),
        "E-mail            : "
        + (
            "désactivé"
            if config.email.frequence == "jamais"
            else f"{config.email.frequence} à {config.email.heure} "
            f"vers {config.email.destinataire or '(destinataire manquant)'} "
            "depuis outly.analyze@gmail.com"
        ),
        f"Clé YouTube       : {'présente' if config.cle_youtube else 'absente'}",
        f"Clé Exa           : {'présente' if config.exa_cle else 'absente'}",
        "Clé LLM           : "
        + (
            "présente"
            if config.llm_cle
            else ("non requise (serveur local)" if url_locale(config.llm_base_url) else "absente")
        ),
        f"Mot de passe Gmail : {'présent' if config.smtp_mot_de_passe else 'absent'}",
    ]
    return "\n".join(lignes)
