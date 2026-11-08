"""Découverte des chaînes concurrentes.

Trois sources, toutes documentées par Google :
  1. `seeds.txt` (résolution par @handle, ID ou ancien /user/) ;
  2. `keywords.txt` via `search.list?type=channel` (1 appel = jusqu'à 50 chaînes) ;
  3. expansion : chaînes mises en avant (`channelSections.list`) et liens
     `@handle` trouvés dans les descriptions de vidéos déjà en base.

Aucune méthode non documentée n'est utilisée. Le pré-filtrage se fait sans quota.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .db import enregistrer_chaine, enregistrer_reference
from .models import Chaine, date_iso, iso_utc
from .youtube_api import ClientYoutube, ErreurApi, ErreurIntrouvable

MOTIF_ID = re.compile(r"(UC[A-Za-z0-9_\-]{22})")
# \w est Unicode : les handles accentués (@théo) sont acceptés.
MOTIF_USER = re.compile(r"youtube\.com/user/([\w.\-]+)", re.IGNORECASE)
MOTIF_C = re.compile(r"youtube\.com/c/([\w.\-]+)", re.IGNORECASE)
MOTIF_HANDLE_URL = re.compile(r"youtube\.com/@([\w.\-]+)", re.IGNORECASE)
# Un @handle ne doit pas être confondu avec une adresse e-mail (contact@site.com).
MOTIF_HANDLE_TEXTE = re.compile(r"(?<![\w.])@([\w.\-]{3,})")
MOTIF_HANDLE_BRUT = re.compile(r"^@?([\w.\-]{3,})$")


# ---------------------------------------------------------------------------
# Fichiers d'entrée
# ---------------------------------------------------------------------------


def lire_lignes(chemin: Path) -> list[str]:
    """Lit un fichier d'entrée : une valeur par ligne, `#` pour commenter."""
    if not chemin.exists():
        return []
    valeurs = []
    for ligne in chemin.read_text(encoding="utf-8-sig").splitlines():
        utile = ligne.split("#", 1)[0].strip()
        if utile:
            valeurs.append(utile)
    return valeurs


def classifier_seed(valeur: str) -> tuple[str, str]:
    """Classe une entrée de seeds.txt : (type, valeur normalisée).

    Types : id, handle, username, c (URL /c/ non résolvable), inconnu.
    """
    texte = valeur.strip().strip('"').strip("'")
    if not texte:
        return "inconnu", texte
    minuscule = texte.lower()

    if "youtube.com" in minuscule or "youtu.be" in minuscule:
        correspondance = MOTIF_ID.search(texte)
        if correspondance and "/channel/" in minuscule:
            return "id", correspondance.group(1)
        correspondance = MOTIF_USER.search(texte)
        if correspondance:
            return "username", correspondance.group(1)
        correspondance = MOTIF_HANDLE_URL.search(texte)
        if correspondance:
            return "handle", correspondance.group(1).rstrip(".-")
        correspondance = MOTIF_C.search(texte)
        if correspondance:
            return "c", correspondance.group(1)
        if correspondance_id := MOTIF_ID.search(texte):
            return "id", correspondance_id.group(1)
        return "inconnu", texte

    if MOTIF_ID.fullmatch(texte):
        return "id", texte
    correspondance = MOTIF_HANDLE_BRUT.match(texte)
    if correspondance:
        return "handle", correspondance.group(1).rstrip(".-")
    return "inconnu", texte


# ---------------------------------------------------------------------------
# Normalisation et pré-filtre
# ---------------------------------------------------------------------------


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents, ponctuation remplacée par des espaces."""
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFD", texte.lower())
    sans_accents = "".join(c for c in decompose if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sans_accents).strip()


def score_heuristique(chaine: Chaine, mots_cles: list[str]) -> float:
    """Part des mots-clés de la niche présents dans le texte de la chaîne.

    Grossier mais gratuit : sert uniquement à ordonner les candidats avant de
    dépenser du quota d'enrichissement.
    """
    if not mots_cles:
        return 0.0
    texte = normaliser(" ".join(filter(None, [chaine.titre, chaine.description, chaine.mots_cles_chaine])))
    if not texte:
        return 0.0
    trouves = sum(1 for mot in mots_cles if normaliser(mot) and normaliser(mot) in texte)
    return round(trouves / len(mots_cles), 3)


def extraire_mentions(texte: str | None) -> list[str]:
    """Extrait les @handles et IDs de chaînes cités dans un texte libre."""
    if not texte:
        return []
    trouves = [f"@{m.rstrip('.-')}" for m in MOTIF_HANDLE_TEXTE.findall(texte)]
    trouves += [f"https://www.youtube.com/channel/{m}" for m in MOTIF_ID.findall(texte)]
    return list(dict.fromkeys(trouves))


def filtrer_et_classer(
    candidats: list[Chaine],
    config: Config,
    mots_cles: list[str],
    maintenant: datetime | None = None,
) -> list[Chaine]:
    """Applique les filtres bon marché et renvoie les candidats classés.

    Les seeds échappent aux filtres : ce sont des concurrents connus, on ne veut
    pas les perdre parce qu'ils sont petits ou récents.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    yt = config.youtube
    retenus: list[Chaine] = []

    for chaine in candidats:
        chaine.score_heuristique = score_heuristique(chaine, mots_cles)
        if chaine.est_seed:
            retenus.append(chaine)
            continue

        if chaine.abonnes is not None and not chaine.abonnes_masques:
            if chaine.abonnes < yt.subscriber_min or chaine.abonnes > yt.subscriber_max:
                continue

        creation = date_iso(chaine.cree_le)
        if creation is not None and creation > maintenant - timedelta(days=yt.min_channel_age_days):
            continue

        if config.decouverte.require_keyword_hit and chaine.score_heuristique == 0:
            continue

        retenus.append(chaine)

    retenus.sort(
        key=lambda c: (
            0 if c.est_seed else 1,
            -(c.score_heuristique or 0.0),
            -(c.abonnes or 0),
        )
    )
    return retenus[: yt.max_candidates_per_run]


# ---------------------------------------------------------------------------
# Résolution
# ---------------------------------------------------------------------------


@dataclass
class ResumeDecouverte:
    seeds_resolus: int = 0
    candidats_ajoutes: int = 0
    candidats_analysables: int = 0
    recherches: int = 0
    requetes_utilisees: int = 0
    passes_recherche: int = 1
    ma_chaine: str | None = None
    messages: list[str] = field(default_factory=list)

    def resume(self) -> str:
        lignes = [
            f"Ma chaîne             : {self.ma_chaine or '(non renseignée)'}",
            f"Seeds résolus         : {self.seeds_resolus}",
            f"Nouveaux candidats    : {self.candidats_ajoutes}",
            f"Candidats analysables : {self.candidats_analysables}",
            f"Requêtes utilisées    : {self.requetes_utilisees} "
            f"({'France + international' if self.passes_recherche > 1 else 'France seule'})",
            f"Appels de recherche   : {self.recherches}",
        ]
        lignes += [f"  ! {message}" for message in self.messages]
        return "\n".join(lignes)


def resoudre_seeds(client: ClientYoutube, lignes: list[str], resume: ResumeDecouverte) -> list[Chaine]:
    """Résout seeds.txt en chaînes réelles, en minimisant le nombre d'appels."""
    ids: list[str] = []
    chaines: list[Chaine] = []

    for valeur in lignes:
        type_seed, propre = classifier_seed(valeur)
        if type_seed == "id":
            ids.append(propre)
        elif type_seed == "handle":
            try:
                chaine = client.chaine_par_handle(propre)
            except ErreurIntrouvable:
                resume.messages.append(f"handle introuvable : @{propre}")
                continue
            except ErreurApi as erreur:
                resume.messages.append(f"@{propre} : {erreur}")
                continue
            if chaine is None:
                resume.messages.append(f"handle introuvable : @{propre}")
            else:
                chaine.est_seed = True
                chaine.source = "seed"
                chaines.append(chaine)
        elif type_seed == "username":
            try:
                chaine = client.chaine_par_username(propre)
            except ErreurIntrouvable:
                resume.messages.append(f"ancien nom d'utilisateur introuvable : {propre}")
                continue
            except ErreurApi as erreur:
                resume.messages.append(f"{propre} : {erreur}")
                continue
            if chaine is None:
                resume.messages.append(f"ancien nom d'utilisateur introuvable : {propre}")
            else:
                chaine.est_seed = True
                chaine.source = "seed"
                chaines.append(chaine)
        elif type_seed == "c":
            resume.messages.append(
                f"URL /c/{propre} non résolvable : l'API n'expose pas les URL personnalisées. "
                "Remplacez-la par le @handle ou l'identifiant UC… dans seeds.txt."
            )
        else:
            resume.messages.append(f"entrée de seeds.txt ignorée (format inconnu) : {valeur}")

    if ids:
        try:
            for chaine in client.chaines_par_ids(ids):
                chaine.est_seed = True
                chaine.source = "seed"
                chaines.append(chaine)
        except ErreurApi as erreur:
            resume.messages.append(f"résolution des identifiants : {erreur}")

    resume.seeds_resolus = len(chaines)
    return chaines


def resoudre_ma_chaine(
    client: ClientYoutube, config: Config, resume: ResumeDecouverte
) -> Chaine | None:
    """Résout la chaîne de l'utilisateur (1 unité, puis cache)."""
    valeur = config.moi.chaine.strip()
    if not valeur:
        return None

    type_seed, propre = classifier_seed(valeur)
    try:
        if type_seed == "handle":
            chaine = client.chaine_par_handle(propre)
        elif type_seed == "username":
            chaine = client.chaine_par_username(propre)
        elif type_seed == "id":
            chaines = client.chaines_par_ids([propre])
            chaine = chaines[0] if chaines else None
        else:
            resume.messages.append(
                f"Ma chaîne « {valeur} » n'est pas reconnue : utilisez un @handle, une URL "
                "de chaîne ou un identifiant UC… (les URL /c/ ne sont pas résolvables)."
            )
            return None
    except ErreurIntrouvable:
        resume.messages.append(f"Ma chaîne est introuvable : {valeur}")
        return None
    except ErreurApi as erreur:
        resume.messages.append(f"Ma chaîne : {erreur}")
        return None

    if chaine is None:
        resume.messages.append(f"Ma chaîne est introuvable : {valeur}")
        return None
    chaine.source = "moi"
    chaine.est_seed = True
    resume.ma_chaine = chaine.titre
    return chaine


def _enregistrer_ma_chaine(conn, chaine: Chaine) -> None:
    """Stocke ma chaîne avec un statut inerte pour le reste du pipeline.

    `statut = 'moi'` la rend invisible de l'enrichissement, du scoring et du
    rapport : elle sert de point de départ, pas de concurrent à classer.
    """
    enregistrer_chaine(conn, chaine)
    conn.execute(
        "UPDATE channels SET statut = 'moi', source = 'moi' WHERE channel_id = ?",
        (chaine.channel_id,),
    )
    conn.commit()


def _mes_videos(
    client: ClientYoutube, config: Config, chaine: Chaine, resume: ResumeDecouverte
) -> tuple[list[str], list[str]]:
    """Titres et descriptions de mes vidéos récentes (2 unités, cache ensuite)."""
    if not chaine.playlist_uploads:
        return [], []
    try:
        elements = client.elements_playlist(
            chaine.playlist_uploads, config.youtube.videos_per_channel
        )
    except ErreurApi as erreur:
        resume.messages.append(f"vidéos de ma chaîne : {erreur}")
        return [], []

    titres: list[str] = []
    identifiants: list[str] = []
    for element in elements:
        snippet = element.get("snippet") or {}
        titre = (snippet.get("title") or "").strip()
        identifiant = (snippet.get("resourceId") or {}).get("videoId")
        if titre:
            titres.append(titre)
        if identifiant:
            identifiants.append(identifiant)

    descriptions: list[str] = []
    if identifiants:
        try:
            infos = client.videos_infos(identifiants)
        except ErreurApi as erreur:
            resume.messages.append(f"descriptions de ma chaîne : {erreur}")
            infos = {}
        for identifiant in identifiants:
            snippet = (infos.get(identifiant) or {}).get("snippet") or {}
            if snippet.get("description"):
                descriptions.append(snippet["description"])
    return titres, descriptions


def mots_cles_chaines(texte: str | None) -> list[str]:
    """Extrait les expressions entre guillemets des mots-clés d'une chaîne (sans doublon)."""
    if not texte:
        return []
    trouves = [element.strip() for element in re.findall(r'"([^"]+)"', texte)]
    return _dedoublonner([element for element in trouves if element])


def _dedoublonner(requetes: list[str], limite: int | None = None) -> list[str]:
    """Requêtes nettoyées, sans doublon (insensible à la casse), dans l'ordre."""
    uniques: list[str] = []
    vues: set[str] = set()
    for requete in requetes:
        propre = " ".join(requete.split())
        if len(propre) < 2 or len(propre) > 120:
            continue
        cle = propre.casefold()
        if cle in vues:
            continue
        vues.add(cle)
        uniques.append(propre)
    return uniques[:limite] if limite is not None else uniques


def passes_recherche(config: Config) -> list[tuple[str | None, str | None]]:
    """Couples (pays, langue) à lancer pour chaque requête.

    Le second passage, sans `regionCode` ni `relevanceLanguage`, est mondial :
    c'est lui qui fait entrer les concurrents non francophones. Le premier
    garde les chaînes françaises que le classement mondial diluerait.
    """
    passes: list[tuple[str | None, str | None]] = [
        (config.youtube.region_code, config.youtube.relevance_language)
    ]
    if config.moi.recherche_internationale:
        passes.append((None, None))
    return passes


def _requetes_recherche(
    service,
    config: Config,
    chaine: Chaine | None,
    titres: list[str],
    limite: int,
    resume: ResumeDecouverte,
) -> list[str]:
    """Requêtes issues du positionnement (LLM), de ma chaîne et de keywords.txt.

    Les sources explicites (mots-clés de ma chaîne et `keywords.txt`) gardent au
    moins la moitié de la capacité : elles sont plus fiables qu'une requête
    inventée. Le LLM complète avec des formulations françaises et anglaises
    dérivées du positionnement. S'il échoue, les sources explicites remplissent
    toute la capacité.
    """
    if limite <= 0:
        return []

    explicites: list[str] = []
    if chaine is not None:
        explicites.extend(mots_cles_chaines(chaine.mots_cles_chaine))
    explicites.extend(lire_lignes(config.chemin_keywords))
    explicites = _dedoublonner(explicites)

    requetes: list[str] = []
    if service is not None:
        try:
            from .llm.queries import generer_requetes

            part_llm = limite if not explicites else max(1, limite // 2)
            requetes.extend(generer_requetes(service, config, chaine, titres, part_llm))
        except Exception as probleme:  # le LLM ne doit jamais bloquer la découverte
            resume.messages.append(f"requêtes LLM indisponibles : {probleme}")

    requetes.extend(explicites)
    return _dedoublonner(requetes, limite)


def _ajouter_mentions(
    conn,
    client: ClientYoutube,
    config: Config,
    resume: ResumeDecouverte,
    mentions: list[str],
    source: str,
) -> int:
    """Résout des @handles/URLs cités et les ajoute comme candidats."""
    if not mentions:
        return 0
    connus = {
        ligne["channel_id"] for ligne in conn.execute("SELECT channel_id FROM channels").fetchall()
    }
    handles_connus = {
        (ligne["handle"] or "").strip().lstrip("@").casefold()
        for ligne in conn.execute("SELECT handle FROM channels WHERE handle IS NOT NULL")
        if (ligne["handle"] or "").strip()
    }
    ajoutes = 0
    for valeur in dict.fromkeys(mentions):
        type_mention, propre = classifier_seed(valeur)
        if type_mention == "handle" and propre.casefold() in handles_connus:
            continue
        if ajoutes >= config.youtube.max_candidates_per_run:
            resume.messages.append(f"plafond de candidats atteint pendant l'expansion ({source}).")
            break
        try:
            if type_mention == "id":
                if propre in connus:
                    continue
                chaines = client.chaines_par_ids([propre])
                chaine = chaines[0] if chaines else None
            elif type_mention == "handle":
                chaine = client.chaine_par_handle(propre)
            else:
                continue
        except ErreurIntrouvable:
            continue
        except ErreurApi as erreur:
            resume.messages.append(f"mention « {valeur} » : {erreur}")
            continue
        if chaine is None or chaine.channel_id in connus:
            continue
        chaine.source = source
        enregistrer_chaine(conn, chaine)
        connus.add(chaine.channel_id)
        if chaine.handle:
            handles_connus.add(chaine.handle.strip().lstrip("@").casefold())
        ajoutes += 1
    return ajoutes


def _enregistrer_lot(conn, chaines: list[Chaine], resume: ResumeDecouverte) -> None:
    for chaine in chaines:
        if not chaine.channel_id:
            continue
        enregistrer_chaine(conn, chaine)
        resume.candidats_ajoutes += 1


def decouvrir(
    conn,
    client: ClientYoutube,
    config: Config,
    max_recherches: int | None = None,
    service=None,
) -> ResumeDecouverte:
    """Exécute la découverte complète et écrit les candidats en base.

    Le mode « depuis ma chaîne » ajoute trois sources gratuites ou peu coûteuses :
    ses vidéos récentes (titres + descriptions), ses chaînes mises en avant et
    ses mots-clés. Le positionnement sert au LLM à fabriquer des requêtes de
    recherche françaises et anglaises. Chaque requête est ensuite lancée en
    France puis, si l'option est active, dans le monde entier.
    """
    resume = ResumeDecouverte()
    mots_cles = lire_lignes(config.chemin_keywords)
    limite = max_recherches if max_recherches is not None else config.youtube.max_search_calls_per_run
    passes = passes_recherche(config)
    resume.passes_recherche = len(passes)

    # --- 1. Seeds ---------------------------------------------------------
    seeds = resoudre_seeds(client, lire_lignes(config.chemin_seeds), resume)
    _enregistrer_lot(conn, seeds, resume)

    # --- 2. Ma chaîne -----------------------------------------------------
    trouves: dict[str, str] = {}
    ma_chaine = resoudre_ma_chaine(client, config, resume)
    titres: list[str] = []
    if ma_chaine is not None:
        _enregistrer_ma_chaine(conn, ma_chaine)
        titres, descriptions = _mes_videos(client, config, ma_chaine, resume)
        if titres:
            enregistrer_reference(conn, ma_chaine, titres)

        if config.decouverte.use_description_links:
            mentions: list[str] = []
            for description in descriptions:
                mentions.extend(extraire_mentions(description))
            resume.candidats_ajoutes += _ajouter_mentions(
                conn, client, config, resume, mentions, "ma-chaine"
            )

        if config.decouverte.use_channel_sections:
            try:
                for identifiant in client.sections_chaine(ma_chaine.channel_id):
                    if identifiant != ma_chaine.channel_id:
                        trouves.setdefault(identifiant, "ma-chaine")
            except ErreurApi as erreur:
                resume.messages.append(f"chaînes mises en avant de ma chaîne : {erreur}")

    # --- 3. Requêtes puis recherches --------------------------------------
    # Deux passes par requête : la capacité est donc divisée par deux.
    capacite = max(0, limite // len(passes))
    requetes = _requetes_recherche(service, config, ma_chaine, titres, capacite, resume)
    if not requetes and limite > 0:
        resume.messages.append(
            "Aucune requête de recherche : renseignez « ma chaîne », keywords.txt, "
            "ou vérifiez le fournisseur LLM."
        )

    requetes_utilisees: list[str] = []
    for requete in requetes:
        if resume.recherches >= limite:
            break
        utilisee = False
        for pays, langue in passes:
            if resume.recherches >= limite:
                break
            try:
                items = client.rechercher_chaines(requete, pays, langue)
            except ErreurApi as erreur:
                resume.messages.append(f"recherche « {requete} » : {erreur}")
                break
            resume.recherches += 1
            utilisee = True
            for item in items:
                identifiant = (item.get("id") or {}).get("channelId")
                if not identifiant:
                    continue
                if ma_chaine is not None and identifiant == ma_chaine.channel_id:
                    continue
                trouves.setdefault(identifiant, f"mot-cle:{requete}")
        if utilisee:
            requetes_utilisees.append(requete)
    resume.requetes_utilisees = len(requetes_utilisees)

    # --- 4. Chaînes mises en avant des seeds -----------------------------
    if config.decouverte.use_channel_sections:
        for seed in seeds:
            if ma_chaine is not None and seed.channel_id == ma_chaine.channel_id:
                continue
            try:
                for identifiant in client.sections_chaine(seed.channel_id):
                    if ma_chaine is not None and identifiant == ma_chaine.channel_id:
                        continue
                    trouves.setdefault(identifiant, "section")
            except ErreurApi as erreur:
                resume.messages.append(f"sections de « {seed.titre} » : {erreur}")

    if ma_chaine is not None:
        trouves.pop(ma_chaine.channel_id, None)

    # --- 5. Détails des identifiants inconnus ----------------------------
    connus = {
        ligne["channel_id"]
        for ligne in conn.execute("SELECT channel_id FROM channels").fetchall()
    }
    nouveaux = [identifiant for identifiant in trouves if identifiant not in connus]
    if nouveaux:
        try:
            chaines = client.chaines_par_ids(nouveaux)
        except ErreurApi as erreur:
            resume.messages.append(f"détails des candidats : {erreur}")
            chaines = []
        for chaine in chaines:
            if ma_chaine is not None and chaine.channel_id == ma_chaine.channel_id:
                continue
            chaine.source = trouves.get(chaine.channel_id)
            enregistrer_chaine(conn, chaine)
            resume.candidats_ajoutes += 1

    # Remarque : les identifiants déjà connus gardent leur fiche existante ;
    # on rafraîchit seulement leur source si elle était vide.
    for identifiant, source in trouves.items():
        conn.execute(
            "UPDATE channels SET source = COALESCE(source, ?) WHERE channel_id = ?",
            (source, identifiant),
        )
    conn.commit()

    # --- 6. Expansion par les descriptions déjà en base -------------------
    if config.decouverte.use_description_links:
        resume.candidats_ajoutes += expandre_depuis_descriptions(conn, client, config, resume)

    # --- 7. Pré-filtre ----------------------------------------------------
    lignes = conn.execute("SELECT * FROM channels WHERE statut <> 'moi'").fetchall()
    candidats = [_chaine_depuis_ligne(ligne) for ligne in lignes]
    retenus = filtrer_et_classer(candidats, config, mots_cles)
    gardes = {chaine.channel_id for chaine in retenus}
    for chaine in retenus:
        conn.execute(
            "UPDATE channels SET score_heuristique = ? WHERE channel_id = ?",
            (chaine.score_heuristique, chaine.channel_id),
        )

    # Les candidats qui ne passent pas le pré-filtre sortent du pipeline : sans
    # cela, ils seraient enrichis plus tard et consommeraient du quota inutilement.
    for ligne in conn.execute("SELECT channel_id FROM channels WHERE statut = 'candidate'"):
        if ligne["channel_id"] not in gardes:
            conn.execute(
                "UPDATE channels SET statut = 'filtree', raison_ecart = ? WHERE channel_id = ?",
                ("hors pré-filtre (taille, ancienneté ou mots-clés)", ligne["channel_id"]),
            )
    conn.commit()
    resume.candidats_analysables = len(retenus)
    return resume


def _chaine_depuis_ligne(ligne) -> Chaine:
    """Reconstruit une Chaine depuis une ligne SQL (lecture seule)."""
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
        est_seed=bool(ligne["est_seed"]),
        source=ligne["source"],
        score_heuristique=ligne["score_heuristique"],
    )


def expandre_depuis_descriptions(
    conn, client: ClientYoutube, config: Config, resume: ResumeDecouverte
) -> int:
    """Ajoute les chaînes citées dans les descriptions de vidéos déjà en base.

    Coût : uniquement la résolution des handles encore inconnus (1 unité par
    handle, gratuite ensuite via le cache). Aucune recherche consommée.
    """
    # Seules les descriptions jamais analysées (ou modifiées depuis) sont lues.
    # Sans ce cache, chaque run reparcourait toutes les descriptions de la base
    # et resollicitait les mêmes handles.
    textes = conn.execute(
        "SELECT video_id, description FROM videos "
        "WHERE description IS NOT NULL AND description <> '' "
        "AND mentions_scannees_le IS NULL"
    ).fetchall()
    mentions: list[str] = []
    analyses: list[tuple[str, str]] = []
    for ligne in textes:
        extraites = extraire_mentions(ligne["description"])
        mentions.extend(extraites)
        analyses.append((ligne["video_id"], json.dumps(extraites, ensure_ascii=False)))

    ajoutes = _ajouter_mentions(conn, client, config, resume, mentions, "description")
    if analyses:
        maintenant = iso_utc()
        conn.executemany(
            "UPDATE videos SET mentions_json = ?, mentions_scannees_le = ? WHERE video_id = ?",
            [(contenu, maintenant, video_id) for video_id, contenu in analyses],
        )
        conn.commit()
    return ajoutes
