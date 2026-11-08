"""Interface en ligne de commande d'Outly.

Codes de sortie :
  0  succès
  2  configuration invalide ou clé refusée
  3  budget de quota atteint (arrêt propre)
  4  LLM indisponible
  5  aucune donnée exploitable
  6  envoi du rapport par e-mail impossible
130  interruption clavier
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import audit, cerveau, db, discovery, emailing, enrichment, hooks, idees, outliers, plan, production, proximite, recherche, report, strategie
from .cache import Cache
from .config import ErreurConfig, charger, url_locale
from .config import resume as resume_config
from .llm import scoring
from .llm.client import ErreurLlm, construire_service
from .quota import BudgetDepasse, CompteurQuota, ErreurJournalQuota
from .youtube_api import ClientYoutube, ErreurApi, ErreurCle, ErreurQuota

VERSION = "1.0.0"
DOSSIER_MODELES = Path(__file__).resolve().parent / "modeles"


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------


def dire(message: str = "") -> None:
    print(message)


def avertir(message: str) -> None:
    print(f"! {message}", file=sys.stderr)


def erreur(message: str) -> None:
    print(f"Erreur : {message}", file=sys.stderr)


def _titre(texte: str) -> None:
    dire()
    dire(texte)
    dire("=" * len(texte))


# ---------------------------------------------------------------------------
# Contexte d'exécution
# ---------------------------------------------------------------------------


def _connexion(config) -> sqlite3.Connection:
    conn = db.connexion(config.fichier_db)
    db.migrer(conn)
    return conn


def _compteur(config, conn, run_id: int | None) -> CompteurQuota:
    return CompteurQuota(
        conn,
        run_id,
        budget_unites_jour=config.youtube.max_units_per_day,
        budget_recherches_jour=config.youtube.max_search_calls_per_day,
        budget_unites_run=config.youtube.max_units_per_run,
        budget_recherches_run=config.youtube.max_search_calls_per_run,
    )


def _client(config, conn, run_id: int | None) -> tuple[ClientYoutube, CompteurQuota]:
    compteur = _compteur(config, conn, run_id)
    cache = Cache(conn, plafond_jours=config.cache.max_ttl_days)
    client = ClientYoutube(config.cle_youtube or "", compteur, cache, config)
    return client, compteur


def _run_id(config, conn, mode: str, args: argparse.Namespace) -> int:
    donnees = {cle: valeur for cle, valeur in vars(args).items() if cle != "fonction"}
    return db.demarrer_run(conn, mode, donnees)


def _afficher_avertissements(config) -> None:
    for message in config.avertissements:
        avertir(message)


def _resume_quota(compteur: CompteurQuota) -> None:
    dire()
    dire(compteur.rapport())


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def preparer_dossier(racine: Path) -> tuple[list[str], list[str]]:
    """Crée les fichiers de départ absents. Renvoie (créés, conservés).

    Utilisé par `init` et, sans bruit, au premier lancement de l'exécutable :
    l'application empaquetée ne peut pas demander à l'utilisateur de taper
    `python -m outly init`.
    """
    racine.mkdir(parents=True, exist_ok=True)
    crees: list[str] = []
    conserves: list[str] = []
    for nom in ("config.yaml", "positioning.md", "seeds.txt", "keywords.txt"):
        source = DOSSIER_MODELES / nom
        destination = racine / nom
        if not source.exists():
            continue
        if db.copier_si_absent(source, destination):
            crees.append(nom)
        else:
            conserves.append(nom)

    env = racine / ".env"
    if not env.exists():
        # Le gabarit fourni avec l'exécutable porte un autre nom : `.env`
        # commencerait par un point et serait ignoré par la construction.
        for modele in (racine / ".env.example", DOSSIER_MODELES / "env.modele"):
            if modele.is_file() and db.copier_si_absent(modele, env):
                crees.append(".env (à remplir)")
                break

    (racine / "data").mkdir(exist_ok=True)
    (racine / "out").mkdir(exist_ok=True)
    return crees, conserves


def cmd_init(args: argparse.Namespace) -> int:
    racine = Path(args.racine or ".").resolve()
    crees, conserves = preparer_dossier(racine)

    _titre("Initialisation")
    dire(f"Dossier : {racine}")
    for nom in crees:
        dire(f"  + {nom}")
    for nom in conserves:
        dire(f"  = {nom} (déjà présent, non modifié)")
    dire()
    dire("Étapes suivantes :")
    dire("  1. Remplissez .env (clé YouTube + clé LLM).")
    dire("  2. Remplissez positioning.md, seeds.txt et keywords.txt.")
    dire("  3. Vérifiez la configuration : python -m outly doctor --probe")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    resultats: list[tuple[str, str, str]] = []
    if getattr(args, "offline", False):
        args.probe = False

    def ajouter(niveau: str, libelle: str, detail: str = "") -> None:
        resultats.append((niveau, libelle, detail))

    try:
        config = charger(exiger_cles=False)
        ajouter("OK", "config.yaml", str(config.racine / "config.yaml"))
    except ErreurConfig as probleme:
        erreur(str(probleme))
        return 2

    ajouter("OK", "Python", sys.version.split()[0])

    for module in ("requests", "yaml", "jsonschema", "dotenv", "youtube_transcript_api"):
        try:
            __import__(module)
            ajouter("OK", f"dépendance {module}", "installée")
        except ImportError:
            ajouter("ERREUR", f"dépendance {module}", "manquante : pip install -r requirements.txt")

    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/Los_Angeles")
        ajouter("OK", "fuseau Pacifique", "base de fuseaux disponible")
    except Exception:
        ajouter("ERREUR", "fuseau Pacifique", "installez tzdata : pip install tzdata")

    positionnement = scoring.lire_positionnement(config.chemin_positioning)
    if len(positionnement) < 200:
        ajouter("ATTENTION", "positioning.md", "absent ou trop court (< 200 caractères)")
    else:
        ajouter("OK", "positionnement", f"{len(positionnement)} caractères")

    seeds = discovery.lire_lignes(config.chemin_seeds)
    mots_cles = discovery.lire_lignes(config.chemin_keywords)
    ajouter(
        "OK" if seeds else "ATTENTION",
        "seeds.txt",
        f"{len(seeds)} entrée(s)"
        if seeds
        else "vide : la découverte partira des mots-clés, de ma chaîne ou du LLM",
    )
    if mots_cles:
        message_mots_cles = f"{len(mots_cles)} mot(s)-clé(s)"
    elif config.moi.chaine or len(positionnement) >= 200:
        message_mots_cles = "vide : requêtes issues de ma chaîne et du positionnement (LLM)"
    else:
        message_mots_cles = "vide : aucune requête possible (ni mot-clé, ni ma chaîne, ni positionnement)"
    ajouter(
        "OK" if mots_cles else "ATTENTION",
        "keywords.txt",
        message_mots_cles,
    )

    try:
        conn = _connexion(config)
        db.lire_meta(conn, "schema_version")
        ajouter("OK", "base SQLite", str(config.fichier_db))
    except Exception as probleme:  # disque plein, droits insuffisants…
        ajouter("ERREUR", "base SQLite", str(probleme))
        conn = None

    ajouter(
        "OK" if config.cle_youtube else "ATTENTION",
        "clé YouTube",
        "présente" if config.cle_youtube else "absente de .env",
    )
    llm_pret = bool(
        config.llm_modele
        and config.llm_base_url
        and (config.llm_cle or url_locale(config.llm_base_url))
    )
    ajouter(
        "OK" if llm_pret else "ATTENTION",
        "clé LLM",
        (
            f"{config.llm_modele} via {config.llm_base_url}"
            + (" (sans clé, serveur local)" if not config.llm_cle else "")
        )
        if llm_pret
        else "incomplète dans .env",
    )

    # E-mail : on vérifie la cohérence des réglages, sans jamais se connecter.
    if config.email.frequence == "jamais":
        ajouter("OK", "rapports e-mail", "désactivés (frequence: jamais)")
    else:
        problemes_email = emailing.valider(emailing.reglage(config))
        if problemes_email:
            ajouter("ATTENTION", "rapports e-mail", "; ".join(problemes_email))
        else:
            ajouter(
                "OK",
                "rapports e-mail",
                f"{config.email.frequence} à {config.email.heure} vers {config.email.destinataire}",
            )
        if conn is not None:
            dernier = emailing.dernier_envoi(conn)
            ajouter(
                "OK" if dernier else "ATTENTION",
                "dernier envoi e-mail",
                dernier.strftime("%d/%m/%Y à %H:%M")
                if dernier
                else "aucun envoi enregistré",
            )

    if args.probe:
        if conn is not None and config.cle_youtube:
            dire("Sonde YouTube : 1 appel prévu (1 unité).")
            try:
                client, compteur = _client(config, conn, None)
                chaine = client.chaine_par_handle("YouTube")
                if chaine is None:
                    ajouter("ATTENTION", "sonde YouTube", "handle de test introuvable (inattendu)")
                else:
                    ajouter("OK", "sonde YouTube", f"réponse reçue pour « {chaine.titre} »")
                totaux = compteur.totaux_jour()
                ajouter("OK", "quota après sonde", f"{totaux.unites_principal} unité(s) aujourd'hui")
            except ErreurApi as probleme:
                ajouter("ERREUR", "sonde YouTube", str(probleme))
        else:
            ajouter("ATTENTION", "sonde YouTube", "ignorée : clé YouTube absente")

        if config.llm_cle or url_locale(config.llm_base_url):
            dire("Sonde LLM : une requête minimale (quelques jetons), puis la liste des modèles.")
            try:
                service = construire_service(config)
                reponse = service.completer(
                    [{"role": "user", "content": "Réponds uniquement : OK"}]
                )
                ajouter(
                    "OK",
                    "sonde LLM",
                    f"réponse reçue de « {reponse.modele} » ({len(reponse.texte)} caractères)",
                )
            except ErreurLlm as probleme:
                ajouter("ERREUR", "sonde LLM", str(probleme))
            else:
                try:
                    modeles = service.lister_modeles()
                    if config.llm_modele in modeles:
                        ajouter(
                            "OK",
                            "modèles LLM",
                            f"modèle « {config.llm_modele} » disponible",
                        )
                    else:
                        apercu = ", ".join(modeles[:8]) or "(liste vide)"
                        ajouter(
                            "ATTENTION",
                            "modèles LLM",
                            f"modèle « {config.llm_modele} » absent de la liste. "
                            f"Disponibles : {apercu}",
                        )
                except ErreurLlm as probleme:
                    ajouter(
                        "ATTENTION",
                        "modèles LLM",
                        f"liste indisponible ({probleme}) ; la requête de test a réussi",
                    )
        else:
            ajouter("ATTENTION", "sonde LLM", "ignorée : clé LLM absente")

    _titre("Diagnostic")
    for niveau, libelle, detail in resultats:
        marque = {"OK": "[OK]     ", "ATTENTION": "[ATTENTION]", "ERREUR": "[ERREUR] "}[niveau]
        dire(f"{marque} {libelle:<22} {detail}")

    _titre("Configuration effective")
    dire(resume_config(config))
    _afficher_avertissements(config)

    bloquantes = sum(1 for niveau, _, _ in resultats if niveau == "ERREUR")
    if bloquantes:
        dire()
        erreur(f"{bloquantes} problème(s) bloquant(s). Corrigez-les avant de continuer.")
        return 2
    return 0


# ---------------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------------


def _estimation_decouverte(config) -> dict:
    seeds = discovery.lire_lignes(config.chemin_seeds)
    mots_cles = discovery.lire_lignes(config.chemin_keywords)
    types = [discovery.classifier_seed(valeur) for valeur in seeds]
    handles = sum(1 for type_seed, _ in types if type_seed in ("handle", "username"))
    ids = sum(1 for type_seed, _ in types if type_seed == "id")
    passes = 2 if config.moi.recherche_internationale else 1
    capacite = max(0, config.youtube.max_search_calls_per_run // passes)
    # Le LLM peut ajouter jusqu'à 12 requêtes ; l'estimation est volontairement large.
    requetes = min(capacite, len(mots_cles) + (12 if config.moi.chaine else 0))
    recherches = requetes * passes
    ma_chaine = bool(config.moi.chaine)
    sections = (len(seeds) + (1 if ma_chaine else 0)) if config.decouverte.use_channel_sections else 0
    candidats = recherches * 50
    lots_stats = math.ceil(candidats / config.youtube.batch_size) if candidats else 0
    return {
        "handles": handles,
        "ids": ids,
        "recherches": recherches,
        "requetes": requetes,
        "passes": passes,
        "sections": sections,
        "lots_stats": lots_stats,
        "ma_chaine": ma_chaine,
        "unites": handles + (1 if ids else 0) + (1 if ma_chaine else 0)
        + (2 if ma_chaine else 0) + sections + lots_stats,
        "mots_cles": len(mots_cles),
        "seeds": len(seeds),
    }


def cmd_discover(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=not args.dry_run)
    _afficher_avertissements(config)
    estimation = _estimation_decouverte(config)

    _titre("Plan de découverte (aucune dépense)")
    dire(f"  ma chaîne            : {config.moi.chaine or '(non renseignée)'}")
    dire(f"  portée               : {'France + international' if estimation['passes'] > 1 else 'France seule'}")
    dire(f"  seeds.txt            : {estimation['seeds']} entrée(s)")
    dire(f"  keywords.txt         : {estimation['mots_cles']} mot(s)-clé(s)")
    dire(f"  requêtes prévues     : ~{estimation['requetes']} "
         f"(x{estimation['passes']} = {estimation['recherches']} appels de recherche)")
    dire(f"  résolution des seeds : {estimation['handles']} appel(s) par handle"
         f" + {1 if estimation['ids'] else 0} lot(s) d'identifiants")
    if estimation['ma_chaine']:
        dire("  ma chaîne            : 1 appel + 2 pour ses vidéos récentes")
    dire(f"  chaînes mises en avant : {estimation['sections']} appel(s)")
    dire(f"  stats des candidats  : ~{estimation['lots_stats']} lot(s) de 50")
    dire()
    dire(f"  Estimation : ~{estimation['unites']} unités du pool principal "
         f"+ {estimation['recherches']} appels de recherche (compartiment dédié).")
    dire("  Les requêtes générées par le LLM ne coûtent aucune unité YouTube.")
    dire("  Les appels servis par le cache coûtent 0 unité.")

    if args.dry_run:
        dire()
        dire("Mode --dry-run : rien n'a été dépensé. Relancez sans l'option pour exécuter.")
        return 0

    conn = _connexion(config)
    run_id = _run_id(config, conn, "discover", args)
    client, compteur = _client(config, conn, run_id)
    statut = "done"
    service = None
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        avertir(f"Requêtes LLM indisponibles ({probleme}) : repli sur les mots-clés.")
    try:
        resume = discovery.decouvrir(
            conn, client, config, args.max_searches, service=service
        )
        _titre("Découverte")
        dire(resume.resume())
    except (BudgetDepasse, ErreurQuota) as probleme:
        statut = "budget_atteint"
        avertir(str(probleme))
    finally:
        totaux = compteur.totaux_run()
        db.terminer_run(conn, run_id, statut, totaux.unites_principal, totaux.unites_recherche)
        _resume_quota(compteur)
    return 0 if statut == "done" else 3


# ---------------------------------------------------------------------------
# Enrich
# ---------------------------------------------------------------------------


def cmd_enrich(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=True)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "enrich", args)
    client, compteur = _client(config, conn, run_id)
    statut = "done"
    try:
        resume = enrichment.enrichir(conn, client, config, args.limit)
        _titre("Enrichissement")
        dire(resume.resume())
    except (BudgetDepasse, ErreurQuota) as probleme:
        statut = "budget_atteint"
        avertir(str(probleme))
    finally:
        totaux = compteur.totaux_run()
        db.terminer_run(conn, run_id, statut, totaux.unites_principal, totaux.unites_recherche)
        _resume_quota(compteur)
    return 0 if statut == "done" else 3


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------


def cmd_score(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "score", args)
    statut = "done"

    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        avertir(f"LLM indisponible ({probleme}) : score de proximité local.")
        scores = proximite.scorer(conn, config)
        _titre("Scoring de proximité (local, sans LLM)")
        if not scores:
            db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
            erreur(
                "Score local impossible : ma chaîne n'est pas connue. "
                "Renseignez moi.chaine dans config.yaml puis relancez discover."
            )
            return 5
        au_dessus = sum(
            1 for score in scores.values() if score.score >= config.classement.seuil_local
        )
        dire(f"Chaînes scorées    : {len(scores)}")
        dire(
            f"Au-dessus du seuil : {au_dessus} "
            f"(seuil local {config.classement.seuil_local}/100)"
        )
        db.terminer_run(conn, run_id, "done", 0, 0, 0)
        return 0

    resume = scoring.scorer(conn, config, service, args.limit, run_id)
    _titre("Scoring de proximité")
    dire(resume.resume())
    if resume.valides == 0 and resume.examinees > 0:
        avertir(
            "Aucun score LLM valide : le rapport utilisera le score de proximité "
            "local, calculé hors ligne."
        )
        scores = proximite.scorer(conn, config)
        if scores:
            au_dessus = sum(
                1
                for score in scores.values()
                if score.score >= config.classement.seuil_local
            )
            dire(
                f"Score local : {len(scores)} chaîne(s), {au_dessus} au-dessus du "
                f"seuil {config.classement.seuil_local}/100."
            )
            db.terminer_run(conn, run_id, "done", 0, 0, resume.appels)
            return 0
        statut = "erreur"
        db.terminer_run(conn, run_id, statut, 0, 0, resume.appels)
        erreur(
            "Aucun score valide et score local impossible : renseignez moi.chaine, "
            "puis vérifiez le fournisseur LLM avec doctor --probe."
        )
        return 4

    db.terminer_run(conn, run_id, statut, 0, 0, resume.appels)
    return 0


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def cmd_hooks(args: argparse.Namespace) -> int:
    """Analyse les intros des outliers puis écrit des accroches pour un sujet."""
    config = charger(exiger_cles=True)
    if args.transcripts:
        config.transcriptions.enabled = True
    sujet = " ".join((args.sujet or config.hooks.sujet or "").split())
    if not sujet:
        erreur(
            "aucun sujet renseigné. Passez --sujet \"mon titre de vidéo\" ou "
            "renseignez hooks.sujet dans config.yaml."
        )
        return 2
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "hooks", args)
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 4

    resume = hooks.generer(
        conn, config, service, sujet=sujet, run_id=run_id, forcer=args.force
    )
    _titre("Hooks")
    dire(resume.resume())
    db.terminer_run(
        conn, run_id, "done" if resume.reussie else "erreur", 0, 0, service.appels
    )
    return 0 if resume.reussie else 5


# ---------------------------------------------------------------------------
# Cerveau IA
# ---------------------------------------------------------------------------


def cmd_brain(args: argparse.Namespace) -> int:
    """Relie les notes et les outliers pour proposer angles, sujets et hooks."""
    config = charger(exiger_cles=True)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "brain", args)
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 4

    resume = cerveau.generer(
        conn,
        config,
        service,
        question=args.question or "",
        inclure_archivees=args.inclure_archives,
        run_id=run_id,
        forcer=args.force,
    )
    _titre("Cerveau IA")
    dire(resume.resume())
    db.terminer_run(
        conn, run_id, "done" if resume.reussie else "erreur", 0, 0, service.appels
    )
    return 0 if resume.reussie else 5


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------


def cmd_idees(args: argparse.Namespace) -> int:
    """Propose des idées de vidéos, avec trois titres à angles différents."""
    config = charger(exiger_cles=True)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "idees", args)
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 4

    resume = idees.generer(
        conn,
        config,
        service,
        question=args.question or "",
        nombre=args.nombre,
        run_id=run_id,
        forcer=args.force,
    )
    _titre("Idées de vidéos")
    dire(resume.resume())
    db.terminer_run(
        conn, run_id, "done" if resume.reussie else "erreur", 0, 0, service.appels
    )
    return 0 if resume.reussie else 5


def cmd_recherche(args: argparse.Namespace) -> int:
    """Recherche documentaire : Wikipédia seule, ou Wikipédia + web avec IA."""
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    mode = "avec_ia" if args.ia else "sans_ia"
    service = None
    if mode == "avec_ia":
        if not config.exa_cle:
            erreur(
                "La recherche avec IA nécessite EXA_API_KEY. "
                "Ajoutez-la dans .env, ou utilisez --sans-ia."
            )
            return 2
        try:
            service = construire_service(config)
        except ErreurLlm as probleme:
            erreur(str(probleme))
            return 4

    conn = _connexion(config)
    run_id = _run_id(config, conn, "recherche", args)
    try:
        resume = recherche.generer(
            conn,
            config,
            titre=args.titre,
            mots_cles=args.mots_cles,
            mode=mode,
            service=service,
            run_id=run_id,
            forcer=args.force,
        )
    except recherche.ErreurRecherche as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 2

    _titre("Recherche documentaire")
    dire(resume.resume())
    db.terminer_run(
        conn,
        run_id,
        "done" if resume.reussie else "erreur",
        0,
        0,
        resume.appels_llm,
    )
    return 0 if resume.reussie else 5


def cmd_audit(args: argparse.Namespace) -> int:
    """Audite ma chaîne, le secteur, les concurrents, puis propose une stratégie."""
    config = charger(exiger_cles=True)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "audit", args)
    if args.recuperation and config.cle_youtube:
        try:
            client, _compteur = _client(config, conn, run_id)
            enrichissement = enrichment.enrichir_ma_chaine(conn, client, config)
            for message in enrichissement.messages:
                avertir(message)
        except (ErreurCle, ErreurQuota, ErreurApi) as probleme:
            avertir(f"récupération de ma chaîne ignorée : {probleme}")
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 4

    resume = audit.generer(conn, config, service, run_id=run_id, forcer=args.force)
    _titre("Audit de ma chaîne")
    dire(resume.resume())
    db.terminer_run(
        conn, run_id, "done" if resume.reussie else "erreur", 0, 0, service.appels
    )
    return 0 if resume.reussie else 5


def cmd_strategie(args: argparse.Namespace) -> int:
    """Analyse les dernières vidéos et ajuste la stratégie de contenu."""
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "strategie", args)
    service = None
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        avertir(f"LLM indisponible ({probleme}) : analyse locale seule.")
    racine = Path(config.chemin_positioning).parent
    resume = strategie.generer(
        conn,
        config,
        service,
        racine=racine,
        jours=args.jours,
        forcer=args.force,
        run_id=run_id,
    )
    _titre("Diagnostic des dernières vidéos")
    dire(resume.resume())
    db.terminer_run(
        conn,
        run_id,
        "done" if resume.reussie else "erreur",
        0,
        0,
        getattr(service, "appels", 0),
    )
    return 0 if resume.reussie else 5


def cmd_production(args: argparse.Namespace) -> int:
    """Estime le temps, le matériel et les ressources d'une fiche du plan."""
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    racine = Path(config.chemin_positioning).parent
    fiche = next(
        (item for item in plan.lister(racine) if item.identifiant == int(args.plan)),
        None,
    )
    if fiche is None:
        erreur(f"Fiche de plan introuvable : {args.plan}.")
        return 5
    conn = _connexion(config)
    run_id = _run_id(config, conn, "production", args)
    service = None
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        avertir(f"LLM indisponible ({probleme}) : estimation locale.")
    resume = production.generer(
        conn,
        config,
        service,
        fiche,
        forcer=args.force,
        run_id=run_id,
    )
    _titre("Estimation de production")
    dire(resume.resume())
    db.terminer_run(
        conn,
        run_id,
        "done" if resume.reussie else "erreur",
        0,
        0,
        getattr(service, "appels", 0) if service else 0,
    )
    return 0 if resume.reussie else 5


def cmd_outliers(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=True)
    if args.transcripts:
        config.transcriptions.enabled = True
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "outliers", args)
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        db.terminer_run(conn, run_id, "erreur", 0, 0, 0)
        erreur(str(probleme))
        return 4

    resume = outliers.analyser(conn, config, service, args.limit, run_id)
    _titre("Analyse des outliers")
    dire(resume.resume())
    db.terminer_run(conn, run_id, "done", 0, 0, service.appels)
    return 0


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    conn = _connexion(config)
    resultat = report.generer(conn, config)
    _titre("Rapport")
    dire(f"Chaînes évaluées : {resultat['evaluees']} (retenues : {resultat['retenues']})")
    dire(f"Outliers exportés : {resultat['nb_outliers']}")
    dire()
    dire(f"  CSV chaînes : {resultat['channels']}")
    dire(f"  CSV outliers: {resultat['outliers_csv']}")
    dire(f"  Rapport     : {resultat['rapport']}")
    if resultat["evaluees"] == 0:
        avertir("Aucune chaîne évaluée : lancez d'abord discover puis enrich.")
        return 5
    return 0


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


def cmd_mail(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    _afficher_avertissements(config)
    reglage = emailing.reglage(config)
    conn = _connexion(config)
    dernier = emailing.dernier_envoi(conn)

    if args.si_du:
        if not emailing.est_du(reglage, dernier):
            _titre("Rapport e-mail")
            dire("Aucun envoi dû pour le moment : rien n'a été fait.")
            prochain = emailing.prochaine_echeance(
                reglage.frequence,
                datetime.now(),
                reglage.heure,
                reglage.jour_semaine,
                reglage.jour_mois,
            )
            if prochain is not None:
                dire(f"Prochain créneau : {prochain:%d/%m/%Y à %H:%M}.")
            return 0

        # Un rapport construit sur la base existante se répète à l'identique.
        # Avant l'envoi, on relance donc une veille complète : elle commence
        # par reprendre les dernières vidéos des chaînes déjà suivies.
        if getattr(args, "veille", True) and not args.dry_run:
            _titre("Veille préalable au rapport" + (" (test)" if args.test else ""))
            try:
                code = cmd_run(
                    argparse.Namespace(
                        dry_run=False,
                        max_searches=None,
                        limit=None,
                        limit_score=None,
                        with_outliers=True,
                        transcripts=False,
                    )
                )
                dire(f"Veille terminée (code {code}).")
            except ErreurConfig as probleme:
                avertir(
                    f"Veille impossible ({probleme}) : le rapport existant sera envoyé."
                )
            except Exception as probleme:  # une veille ratée ne doit pas priver d'e-mail
                avertir(
                    f"Veille interrompue ({probleme}) : le rapport existant sera envoyé."
                )

    try:
        message, retenues = emailing.preparer(
            conn,
            config,
            test=args.test,
            depuis=None if args.test else dernier,
        )
    except emailing.ErreurEmail as probleme:
        erreur(str(probleme))
        return 6

    if args.dry_run:
        _titre("Rapport e-mail (simulation)")
        dire(f"De          : {reglage.expediteur}")
        dire(f"À           : {reglage.destinataire}")
        dire(f"Objet       : {message['Subject']}")
        dire(f"Taille      : {len(message.as_bytes()) // 1024} Ko")
        pieces = [part.get_filename() for part in message.iter_attachments()]
        dire(f"Pièces      : {', '.join(nom for nom in pieces if nom) or 'aucune'}")
        dire(f"Retenues    : {retenues}")
        dire(f"Fréquence   : {reglage.frequence} à {reglage.heure}")
        dire()
        dire("Simulation : aucun message n'a été envoyé, aucune date modifiée.")
        return 0

    try:
        emailing.envoyer(reglage, message)
    except emailing.ErreurEmail as probleme:
        erreur(str(probleme))
        return 6

    if not args.test:
        emailing.noter_envoi(conn, reglage.destinataire, retenues)
    _titre("Rapport e-mail")
    dire(f"Message envoyé à {reglage.destinataire} ({retenues} chaîne(s) retenue(s)).")
    if args.test:
        dire("Envoi de test : la planification n'a pas été modifiée.")
    return 0


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    if args.dry_run:
        return cmd_discover(argparse.Namespace(dry_run=True, max_searches=args.max_searches))

    config = charger(exiger_cles=True)
    if args.transcripts:
        config.transcriptions.enabled = True
    _afficher_avertissements(config)
    conn = _connexion(config)
    run_id = _run_id(config, conn, "run", args)
    client, compteur = _client(config, conn, run_id)
    statut = "done"
    appels_llm = 0

    # Le LLM sert d'abord à fabriquer les requêtes de découverte, puis au
    # scoring et aux outliers. Une panne ne bloque pas la collecte.
    service = None
    try:
        service = construire_service(config)
    except ErreurLlm as probleme:
        avertir(f"LLM indisponible ({probleme}) : requêtes et scoring de secours.")

    # Priorité aux dernières vidéos des chaînes déjà suivies : sans ce passage,
    # une veille ne parle que de nouvelles chaînes et le rapport ne change pas.
    try:
        _titre("0/4 · Dernières vidéos des chaînes suivies")
        dire(enrichment.rafraichir_recentes(conn, client, config, args.limit).resume())
    except (BudgetDepasse, ErreurQuota) as probleme:
        avertir(str(probleme))

    try:
        _titre("1/4 · Découverte")
        resume = discovery.decouvrir(conn, client, config, args.max_searches, service=service)
        dire(resume.resume())

        _titre("2/4 · Enrichissement")
        resume_enrich = enrichment.enrichir(conn, client, config, args.limit)
        dire(resume_enrich.resume())

        if config.decouverte.use_description_links:
            complement = discovery.ResumeDecouverte()
            ajoutes = discovery.expandre_depuis_descriptions(conn, client, config, complement)
            for message in complement.messages:
                avertir(message)
            if ajoutes:
                dire(f"Expansion par les descriptions : {ajoutes} nouvelle(s) chaîne(s).")
                dire(enrichment.enrichir(conn, client, config, args.limit).resume())

        _titre("3/4 · Scoring")
        llm_operationnel = service is not None
        if service is not None:
            resume_score = scoring.scorer(conn, config, service, args.limit_score, run_id)
            appels_llm = service.appels
            dire(resume_score.resume())
            for message in service.messages:
                avertir(message)
            llm_operationnel = resume_score.valides > 0 or resume_score.examinees == 0
            if resume_score.examinees and not resume_score.valides:
                avertir(
                    "Aucun score LLM valide : le rapport utilisera le score de proximité "
                    "local calculé hors ligne."
                )
        else:
            scores_locaux = proximite.scorer(conn, config)
            if scores_locaux:
                au_dessus = sum(
                    1
                    for score in scores_locaux.values()
                    if score.score >= config.classement.seuil_local
                )
                avertir(
                    f"Scoring local de secours : {len(scores_locaux)} chaîne(s) scorée(s) "
                    f"hors ligne, {au_dessus} au-dessus du seuil "
                    f"{config.classement.seuil_local}/100."
                )
            else:
                avertir(
                    "Scoring ignoré : aucun fournisseur LLM et ma chaîne est inconnue "
                    "(renseignez moi.chaine pour activer le score local)."
                )

        if args.with_outliers and llm_operationnel:
            _titre("3bis · Analyse des outliers")
            resume_outliers = outliers.analyser(conn, config, service, None, run_id)
            appels_llm = service.appels
            dire(resume_outliers.resume())
        elif args.with_outliers:
            avertir(
                "Analyse des outliers ignorée : le LLM n'a produit aucun score valide "
                "(pas d'appels supplémentaires inutiles)."
            )

        if config.hooks.enabled:
            if service is None:
                avertir("Hooks ignorés : aucun fournisseur LLM disponible.")
            elif not config.hooks.sujet.strip():
                avertir(
                    "Hooks ignorés : aucun sujet renseigné. Ouvrez la page Hooks de "
                    "l'interface ou renseignez hooks.sujet dans config.yaml."
                )
            else:
                _titre("3ter · Hooks")
                resume_hooks = hooks.generer(conn, config, service, run_id=run_id)
                appels_llm = service.appels
                dire(resume_hooks.resume())

        _titre("4/4 · Rapport")
        resultat = report.generer(conn, config)
        dire(f"Retenues : {resultat['retenues']} / {resultat['evaluees']}")
        dire(f"  {resultat['channels']}")
        dire(f"  {resultat['outliers_csv']}")
        dire(f"  {resultat['rapport']}")

    except (BudgetDepasse, ErreurQuota) as probleme:
        statut = "budget_atteint"
        avertir(str(probleme))
        avertir("Génération d'un rapport partiel avec les données déjà collectées.")
        try:
            report.generer(conn, config)
        except Exception as second:  # le rapport ne doit pas masquer la cause première
            avertir(f"Rapport partiel impossible : {second}")
    finally:
        try:
            totaux = compteur.totaux_run()
        except sqlite3.OperationalError as probleme:
            avertir(f"Quota illisible en fin d'exécution : {probleme}")
            totaux = None
        try:
            db.terminer_run(
                conn,
                run_id,
                statut,
                totaux.unites_principal if totaux else 0,
                totaux.unites_recherche if totaux else 0,
                appels_llm,
            )
        except sqlite3.OperationalError as probleme:
            avertir(f"Clôture de l'exécution impossible dans la base : {probleme}")
        try:
            _resume_quota(compteur)
        except sqlite3.OperationalError as probleme:
            avertir(f"Résumé de quota indisponible : {probleme}")

    return 0 if statut == "done" else 3


# ---------------------------------------------------------------------------
# Quota / show / cache
# ---------------------------------------------------------------------------


def cmd_quota(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    conn = _connexion(config)
    compteur = _compteur(config, conn, None)
    _titre("Quota YouTube")
    dire(compteur.rapport())
    dire()
    dire("Rappel : `search.list` a son propre compartiment (100 appels/jour) et ne")
    dire("consomme pas les 10 000 unités des autres endpoints.")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    conn = _connexion(config)
    cible = args.cible.strip()
    if cible.startswith("UC") and len(cible) == 24:
        ligne = conn.execute("SELECT channel_id FROM channels WHERE channel_id = ?", (cible,)).fetchone()
    else:
        propre = cible.lstrip("@")
        ligne = conn.execute(
            "SELECT channel_id FROM channels WHERE channel_id = ? "
            "OR handle = ? OR handle = ? OR titre LIKE ?",
            (cible, f"@{propre}", propre, f"%{propre}%"),
        ).fetchone()
    if ligne is None:
        erreur(f"chaîne introuvable en base : {cible}")
        return 5

    chaine = db.lire_chaine(conn, ligne["channel_id"])
    if chaine is None:
        erreur("fiche illisible en base.")
        return 5
    metriques = db.lire_metriques(conn, chaine.channel_id)

    _titre(chaine.titre)
    dire(f"ID        : {chaine.channel_id}")
    dire(f"Handle    : {chaine.handle or '—'}")
    dire(f"Abonnés   : {report._nombre(chaine.abonnes)}"
        + (" (masqués)" if chaine.abonnes_masques else ""))
    dire(f"Vidéos    : {report._nombre(chaine.nb_videos)}")
    dire(f"Créée le  : {report._date_courte(chaine.cree_le)}")
    dire(f"Statut    : {conn.execute('SELECT statut FROM channels WHERE channel_id = ?', (chaine.channel_id,)).fetchone()['statut']}")
    if metriques:
        dire()
        dire(f"Médiane longues : {report._nombre(metriques.mediane_vues_longues)}")
        dire(f"Médiane Shorts  : {report._nombre(metriques.mediane_vues_short)}")
        dire(f"Cadence         : {report._nombre(metriques.cadence_semaine)} vidéos/semaine")
        dire(f"Répartition     : {metriques.n_shorts} Shorts / {metriques.n_longues} longues "
             f"({metriques.mix_format})")
        dire(f"Dernière vidéo  : il y a {report._nombre(metriques.jours_depuis_publication)} jours")
        dire(f"Meilleur outlier: ×{report._nombre(metriques.meilleur_outlier)}")

    score = conn.execute(
        "SELECT * FROM channel_scores WHERE channel_id = ? AND valide = 1 ORDER BY id DESC LIMIT 1",
        (chaine.channel_id,),
    ).fetchone()
    if score:
        dire()
        dire(f"Score de proximité : {score['score_proximite']}/100 "
             f"({score['recouvrement_audience']}, {score['format_detecte']}, {score['langue']})")
        dire(f"Justification : {score['justification']}")
    else:
        dire()
        dire("Score de proximité : non évalué par le LLM.")

    videos = report._outliers(conn, chaine.channel_id, 5)
    if videos:
        dire()
        dire("Principaux outliers :")
        for video in videos:
            dire(f"  ×{report._nombre(video['score_outlier']):<6} "
                 f"{report._nombre(video['vues']):>10} vues  {video['titre'][:70]}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    config = charger(exiger_cles=False)
    conn = _connexion(config)
    cache = Cache(conn, plafond_jours=config.cache.max_ttl_days)
    if args.action == "clear":
        nombre = cache.vider()
        dire(f"Cache vidé : {nombre} entrée(s) supprimée(s).")
    else:
        nombre = cache.purger(args.older_than)
        dire(f"Cache purgé ({args.older_than} jours) : {nombre} entrée(s) supprimée(s).")
    dire(f"Entrées restantes : {cache.compter()}")
    return 0


# ---------------------------------------------------------------------------
# Analyseur de commandes
# ---------------------------------------------------------------------------


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        prog="outly",
        description="Veille concurrentielle YouTube locale : découverte, proximité, outliers.",
    )
    parseur.add_argument("--version", action="version", version=f"outly {VERSION}")
    sous = parseur.add_subparsers(dest="commande", required=True)

    p_init = sous.add_parser("init", help="crée les fichiers d'entrée et le .env")
    p_init.add_argument("--racine", default=None, help="dossier cible (défaut : projet courant)")
    p_init.set_defaults(fonction=cmd_init)

    p_doctor = sous.add_parser("doctor", help="vérifie la configuration et les accès")
    acces = p_doctor.add_mutually_exclusive_group()
    acces.add_argument("--probe", action="store_true", help="teste réellement YouTube et le LLM")
    acces.add_argument(
        "--offline",
        action="store_true",
        help="diagnostic local uniquement (comportement par défaut, conservé pour compatibilité)",
    )
    p_doctor.set_defaults(fonction=cmd_doctor)

    p_discover = sous.add_parser("discover", help="découvre des chaînes candidates")
    p_discover.add_argument("--dry-run", action="store_true", help="affiche le plan sans dépenser")
    p_discover.add_argument("--max-searches", type=int, default=None, help="plafond de recherches")
    p_discover.set_defaults(fonction=cmd_discover)

    p_enrich = sous.add_parser("enrich", help="récupère les vidéos et calcule les métriques")
    p_enrich.add_argument("--limit", type=int, default=None, help="nombre de chaînes à enrichir")
    p_enrich.set_defaults(fonction=cmd_enrich)

    p_score = sous.add_parser("score", help="évalue la proximité des chaînes par le LLM")
    p_score.add_argument("--limit", type=int, default=None, help="nombre de chaînes à scorer")
    p_score.set_defaults(fonction=cmd_score)

    p_outliers = sous.add_parser("analyze-outliers", help="analyse approfondie des meilleurs outliers")
    p_outliers.add_argument("--limit", type=int, default=None, help="nombre de chaînes à analyser")
    p_outliers.add_argument(
        "--transcripts", action="store_true", help="active la lecture des sous-titres (non officielle)"
    )
    p_outliers.set_defaults(fonction=cmd_outliers)

    p_hooks = sous.add_parser(
        "hooks", help="analyse les intros des outliers et écrit des accroches pour un sujet"
    )
    p_hooks.add_argument("--sujet", default=None, help="titre ou sujet précis de votre vidéo")
    p_hooks.add_argument(
        "--transcripts", action="store_true", help="active la lecture des sous-titres (non officielle)"
    )
    p_hooks.add_argument(
        "--force", action="store_true", help="régénère même si un jeu identique est en cache"
    )
    p_hooks.set_defaults(fonction=cmd_hooks)

    p_brain = sous.add_parser(
        "brain", help="relie vos notes et les outliers pour proposer des angles inédits"
    )
    p_brain.add_argument(
        "--question",
        default="",
        help="question ou contrainte facultative (ex. « quels angles pour la rentrée ? »)",
    )
    p_brain.add_argument(
        "--inclure-archives",
        action="store_true",
        help="utilise aussi les notes archivées",
    )
    p_brain.add_argument(
        "--force", action="store_true", help="régénère même si une session identique est en cache"
    )
    p_brain.set_defaults(fonction=cmd_brain)

    p_idees = sous.add_parser(
        "idees", help="propose des sujets de vidéos avec trois titres à angles différents"
    )
    p_idees.add_argument(
        "--question",
        default="",
        help="contrainte facultative (ex. « une série pour les débutants »)",
    )
    p_idees.add_argument(
        "--nombre", type=int, default=5, help="nombre d'idées à demander (1 à 10, défaut 5)"
    )
    p_idees.add_argument(
        "--force", action="store_true", help="régénère même si une recherche identique est en cache"
    )
    p_idees.set_defaults(fonction=cmd_idees)

    p_recherche = sous.add_parser(
        "recherche",
        help="cherche un sujet sur Wikipédia, ou sur Wikipédia et le web avec l'IA",
    )
    p_recherche.add_argument("--titre", required=True, help="titre ou sujet de la vidéo")
    p_recherche.add_argument(
        "--mots-cles",
        required=True,
        help="mots-clés séparés par des virgules ou des points-virgules",
    )
    p_recherche.add_argument(
        "--ia",
        dest="ia",
        action="store_true",
        help="ajoute Exa et une synthèse LLM (EXA_API_KEY requise)",
    )
    p_recherche.add_argument(
        "--sans-ia",
        dest="ia",
        action="store_false",
        help="Wikipédia uniquement (défaut)",
    )
    p_recherche.set_defaults(fonction=cmd_recherche, ia=False)
    p_recherche.add_argument(
        "--force", action="store_true", help="ignore un dossier identique déjà en cache"
    )

    p_audit = sous.add_parser(
        "audit", help="audite votre chaîne et propose une stratégie de contenu"
    )
    p_audit.add_argument(
        "--force", action="store_true", help="régénère même si un audit identique est en cache"
    )
    p_audit.add_argument(
        "--sans-recuperation",
        dest="recuperation",
        action="store_false",
        help="n'interroge pas YouTube pour compléter les vidéos de ma chaîne",
    )
    p_audit.set_defaults(fonction=cmd_audit, recuperation=True)

    p_strategie = sous.add_parser(
        "strategie", help="diagnostique les dernières vidéos et ajuste la stratégie"
    )
    p_strategie.add_argument(
        "--jours", type=int, default=10, help="nombre de vidéos analysées (défaut 10)"
    )
    p_strategie.add_argument(
        "--force", action="store_true", help="régénère même si un diagnostic identique est en cache"
    )
    p_strategie.set_defaults(fonction=cmd_strategie)

    p_production = sous.add_parser(
        "production", help="estime le temps et les ressources d'une fiche du plan"
    )
    p_production.add_argument("--plan", type=int, required=True, help="identifiant de la fiche")
    p_production.add_argument(
        "--force", action="store_true", help="régénère même si une estimation identique est en cache"
    )
    p_production.set_defaults(fonction=cmd_production)

    p_report = sous.add_parser("report", help="écrit channels.csv, outliers.csv et report.md")
    p_report.set_defaults(fonction=cmd_report)

    p_mail = sous.add_parser("mail", help="envoie le rapport par e-mail")
    p_mail.add_argument(
        "--si-du",
        action="store_true",
        help="n'envoie que si la fréquence configurée rend un envoi dû",
    )
    p_mail.add_argument("--test", action="store_true", help="message marqué [test], sans effet sur la date")
    p_mail.add_argument("--dry-run", action="store_true", help="compose le message sans l'envoyer")
    p_mail.add_argument(
        "--sans-veille",
        dest="veille",
        action="store_false",
        help="envoie le rapport existant sans relancer de veille",
    )
    p_mail.set_defaults(fonction=cmd_mail, veille=True)

    p_run = sous.add_parser("run", help="enchaîne découverte, enrichissement, scoring et rapport")
    p_run.add_argument("--dry-run", action="store_true", help="affiche le plan sans dépenser")
    p_run.add_argument("--max-searches", type=int, default=None)
    p_run.add_argument("--limit", type=int, default=None, help="chaînes enrichies par étape")
    p_run.add_argument("--limit-score", type=int, default=None, help="chaînes scorées par le LLM")
    p_run.add_argument("--with-outliers", action="store_true", help="ajoute l'analyse des outliers")
    p_run.add_argument("--transcripts", action="store_true", help="active les sous-titres")
    p_run.set_defaults(fonction=cmd_run)

    p_quota = sous.add_parser("quota", help="affiche le quota consommé aujourd'hui")
    p_quota.set_defaults(fonction=cmd_quota)

    p_show = sous.add_parser("show", help="détail d'une chaîne (ID, handle ou titre)")
    p_show.add_argument("cible", help="identifiant, @handle ou fragment de titre")
    p_show.set_defaults(fonction=cmd_show)

    p_cache = sous.add_parser("cache", help="gère le cache local")
    p_cache.add_argument("action", choices=["purge", "clear"], help="purge par ancienneté ou vidage")
    p_cache.add_argument("--older-than", type=int, default=30, help="jours à conserver (défaut 30)")
    p_cache.set_defaults(fonction=cmd_cache)

    return parseur


def _configurer_console() -> None:
    """Sorties UTF-8, y compris dans un terminal Windows historique.

    Sans cela, les accents français deviennent des « � » dès que la sortie est
    redirigée (pipe, fichier, capture de test).
    """
    if sys.platform == "win32" and sys.stdout.isatty():
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configurer_console()
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    try:
        return int(args.fonction(args))
    except ErreurConfig as probleme:
        erreur(str(probleme))
        return 2
    except ErreurCle as probleme:
        erreur(str(probleme))
        return 2
    except ErreurQuota as probleme:
        erreur(str(probleme))
        return 3
    except BudgetDepasse as probleme:
        avertir(str(probleme))
        return 3
    except ErreurLlm as probleme:
        erreur(str(probleme))
        return 4
    except ErreurApi as probleme:
        erreur(str(probleme))
        return 3
    except ErreurJournalQuota as probleme:
        erreur(str(probleme))
        return 2
    except sqlite3.OperationalError as probleme:
        erreur(
            "La base SQLite n'a pas pu être lue ou écrite (disque externe, "
            "verrou ou espace insuffisant). Fermez les autres instances d'Outly, "
            f"vérifiez le disque puis relancez. Détail : {probleme}"
        )
        return 2
    except KeyboardInterrupt:
        dire()
        avertir("Interrompu par l'utilisateur.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
