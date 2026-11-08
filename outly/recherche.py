"""Recherche documentaire pour un titre et des mots-clés.

Deux modes, volontairement séparés :

* ``sans_ia`` : interroge uniquement l'API MediaWiki de Wikipédia, récupère les
  pages de chaque mot-clé et conserve leurs extraits ;
* ``avec_ia`` : demande d'abord au LLM le public attendu, l'intention du
  créateur et des requêtes, puis interroge Wikipédia **et** Exa. La synthèse
  finale ne peut citer que les sources réellement récupérées.

Une affirmation n'est marquée « recoupée » que si elle cite au moins trois
domaines *web* indépendants et reconnus. Wikipédia reste une porte d'entrée
documentaire : elle ne compte pas dans ces trois sources. Tout le reste est
affiché « à vérifier », jamais présenté comme un fait établi.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .config import Config
from .llm import prompts, scoring
from .llm.client import ErreurLlm, ServiceLlm

PROMPT_VERSION = "recherche-v1"
URL_WIKIPEDIA = "https://fr.wikipedia.org/w/api.php"
URL_WIKIPEDIA_EN = "https://en.wikipedia.org/w/api.php"
URL_EXA = "https://api.exa.ai/search"
USER_AGENT = "Outly/1.0 (recherche documentaire locale)"
MAX_MOTS_CLES = 8
MAX_PAGES_WIKI = 3
MAX_EXTRAIT_WIKI = 2600
MAX_REQUETES_WEB = 8
MAX_RESULTATS_EXA = 4
MAX_SOURCES_WEB = 18
MAX_EXTRAIT_WEB = 1200
SOURCES_POUR_RECOUPER = 3

# Domaines de référence utilisés pour le badge « source connue ». La liste
# n'est pas une garantie de vérité : elle sert uniquement à distinguer une
# source éditoriale identifiable d'un blog anonyme. Le recoupement réel reste
# la confrontation de trois publications distinctes.
DOMAINES_REFERENCE = {
    "wikipedia.org",
    "britannica.com",
    "universalis.fr",
    "larousse.fr",
    "lemonde.fr",
    "lefigaro.fr",
    "lesechos.fr",
    "francetvinfo.fr",
    "radiofrance.fr",
    "arte.tv",
    "bbc.com",
    "bbc.co.uk",
    "reuters.com",
    "apnews.com",
    "nytimes.com",
    "theguardian.com",
    "economist.com",
    "hbr.org",
    "mckinsey.com",
    "weforum.org",
    "ourworldindata.org",
    "insee.fr",
    "ined.fr",
    "banque-france.fr",
    "vie-publique.fr",
    "legifrance.gouv.fr",
    "service-public.fr",
    "data.gouv.fr",
    "education.gouv.fr",
    "sante.gouv.fr",
    "economie.gouv.fr",
    "culture.gouv.fr",
    "has-sante.fr",
    "cnrs.fr",
    "inserm.fr",
    "europa.eu",
    "ec.europa.eu",
    "eurostat.ec.europa.eu",
    "who.int",
    "un.org",
    "unesco.org",
    "unicef.org",
    "worldbank.org",
    "oecd.org",
    "nature.com",
    "science.org",
    "thelancet.com",
    "nejm.org",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "nih.gov",
    "cdc.gov",
    "nasa.gov",
    "noaa.gov",
    "arxiv.org",
    "sciencedirect.com",
    "springer.com",
    "jstor.org",
    "cairn.info",
    "persee.fr",
    "openstax.org",
    "statista.com",
    "gouv.fr",
    "gov.uk",
    "canada.ca",
}

SUFFIXES_PUBLICS = {
    "co.uk",
    "ac.uk",
    "org.uk",
    "com.au",
    "co.jp",
    "com.br",
    "co.nz",
    "gouv.fr",
}


class ErreurRecherche(Exception):
    """Titre ou mots-clés invalides, ou service de recherche indisponible."""


@dataclass
class ReponseHttp:
    """Réponse minimale d'un transport HTTP injectable (utile aux tests)."""

    status: int
    charge: dict[str, Any]
    entetes: dict[str, str] = field(default_factory=dict)


@dataclass
class ResumeRecherche:
    titre: str
    mots_cles: list[str]
    mode: str
    dossier: dict[str, Any] | None = None
    depuis_cache: bool = False
    appels_llm: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def reussie(self) -> bool:
        return self.dossier is not None

    @property
    def libelle_mode(self) -> str:
        return "Wikipédia + web (IA)" if self.mode == "avec_ia" else "Wikipédia seule"

    def resume(self) -> str:
        lignes = [
            f"Titre              : {self.titre}",
            f"Mots-clés          : {', '.join(self.mots_cles)}",
            f"Mode               : {self.libelle_mode}",
        ]
        if self.depuis_cache:
            lignes.append("Cache              : dossier identique réutilisé")
        if self.dossier:
            sources = self.dossier.get("sources") or []
            faits = self.dossier.get("faits") or []
            recoupes = sum(1 for fait in faits if fait.get("statut") == "recoupe")
            lignes.extend(
                [
                    f"Sources récupérées  : {len(sources)}",
                    f"Faits recoupés      : {recoupes}/{len(faits)}",
                    f"Angles proposés     : {len(self.dossier.get('angles') or [])}",
                ]
            )
            if self.dossier.get("resume"):
                lignes.extend(["", str(self.dossier["resume"])])
        elif self.messages:
            lignes.append("")
            lignes.extend(f"! {message}" for message in self.messages)
        return "\n".join(lignes)


def _maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sans_accents(texte: str) -> str:
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texte)
        if not unicodedata.combining(caractere)
    )


def normaliser_titre(titre: str) -> str:
    valeur = " ".join(str(titre or "").split())
    if len(valeur) < 3:
        raise ErreurRecherche("Donnez un titre d'au moins trois caractères.")
    if len(valeur) > 240:
        raise ErreurRecherche("Le titre dépasse 240 caractères.")
    return valeur


def compter_mots_cles(valeur: str) -> int:
    """Compte les mots-clés saisis, avant toute troncature silencieuse."""
    brut = valeur.replace(",", chr(10)).replace(";", chr(10))
    return len([ligne.strip() for ligne in brut.splitlines() if ligne.strip()])


def normaliser_mots_cles(valeur: str | list[str] | tuple[str, ...]) -> list[str]:
    """Découpe les mots-clés sur les virgules, points-virgules et retours."""
    if isinstance(valeur, str):
        morceaux = re.split(r"[,;\n\r]+", valeur)
    else:
        morceaux = [str(element) for element in valeur]
    resultat: list[str] = []
    vus: set[str] = set()
    for morceau in morceaux:
        mot = " ".join(morceau.split()).strip(" -–—•*")
        if not mot:
            continue
        cle = _sans_accents(mot).casefold()
        if cle in vus:
            continue
        if len(mot) > 80:
            mot = mot[:78].rstrip() + "…"
        vus.add(cle)
        resultat.append(mot)
        if len(resultat) >= MAX_MOTS_CLES:
            break
    if not resultat:
        raise ErreurRecherche("Ajoutez au moins un mot-clé (un par ligne ou séparés par des virgules).")
    return resultat


def domaine_racine(url: str) -> str:
    """Domaine affichable, sans ``www`` ni sous-domaine Wikipédia."""

    try:
        hote = (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if not hote:
        return ""
    if hote == "wikipedia.org" or hote.endswith(".wikipedia.org"):
        return "wikipedia.org"
    morceaux = hote.split(".")
    if len(morceaux) <= 2:
        return hote
    deux_derniers = ".".join(morceaux[-2:])
    if deux_derniers in SUFFIXES_PUBLICS:
        return ".".join(morceaux[-3:])
    return deux_derniers


def domaine_connu(domaine: str) -> bool:
    domaine = (domaine or "").lower()
    if domaine in DOMAINES_REFERENCE:
        return True
    return any(domaine.endswith(suffixe) for suffixe in (".gouv.fr", ".gov", ".edu", ".ac.uk"))


def _url_propre(url: str) -> str:
    return str(url or "").split("#", 1)[0].strip()


def _tronquer(texte: str, limite: int) -> str:
    texte = " ".join(str(texte or "").split())
    if len(texte) <= limite:
        return texte
    coupe = texte[:limite]
    for separateur in (". ", "! ", "? ", " "):
        position = coupe.rfind(separateur)
        if position >= limite // 2:
            return coupe[: position + 1].rstrip() + " […]"
    return coupe.rstrip() + " […]"


def _empreinte(titre: str, mots_cles: list[str], mode: str) -> str:
    payload = json.dumps(
        {
            "titre": titre.casefold(),
            "mots_cles": [mot.casefold() for mot in mots_cles],
            "mode": mode,
            "version": PROMPT_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Transports réseau (injectables)
# ---------------------------------------------------------------------------


def _get_json(url: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    reponse = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if reponse.status_code != 200:
        raise ErreurRecherche(f"Wikipédia a répondu HTTP {reponse.status_code}.")
    charge = reponse.json()
    if not isinstance(charge, dict):
        raise ErreurRecherche("Réponse Wikipédia illisible.")
    return charge


def _post_exa(
    url: str,
    entetes: dict[str, str],
    corps: dict[str, Any],
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    reponse = requests.post(url, headers=entetes, json=corps, timeout=timeout)
    try:
        charge = reponse.json()
    except ValueError:
        charge = {}
    return reponse.status_code, charge if isinstance(charge, dict) else {}


TransportWikipedia = Callable[[str, dict[str, Any], int], dict[str, Any]]
TransportExa = Callable[[str, dict[str, str], dict[str, Any], int], tuple[int, dict[str, Any]]]


class ClientWikipedia:
    def __init__(self, langue: str = "fr", transport: TransportWikipedia | None = None, timeout: int = 15):
        self.langue = langue
        self.transport = transport or _get_json
        self.timeout = timeout

    def rechercher(self, mot_cle: str, limite: int = MAX_PAGES_WIKI) -> list[dict[str, Any]]:
        """Pages correspondant au mot-clé, en essayant le français puis l'anglais."""
        for langue in ("fr", "en"):
            url = URL_WIKIPEDIA if langue == "fr" else URL_WIKIPEDIA_EN
            parametres = {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrsearch": mot_cle,
                "gsrnamespace": "0",
                "gsrlimit": str(max(1, min(limite, 5))),
                "prop": "extracts|pageimages|info",
                "inprop": "url",
                "explaintext": "1",
                "exintro": "1",
                "exlimit": "max",
                "piprop": "thumbnail",
                "pithumbsize": "240",
                "utf8": "1",
            }
            try:
                charge = self.transport(url, parametres, self.timeout)
            except requests.RequestException as probleme:
                raise ErreurRecherche(
                    f"Wikipédia est injoignable ({probleme.__class__.__name__})."
                ) from probleme
            pages = ((charge.get("query") or {}).get("pages") or [])
            if isinstance(pages, dict):
                pages = list(pages.values())
            resultat = []
            for page in sorted(pages, key=lambda element: element.get("index", 0)):
                if not isinstance(page, dict) or page.get("missing"):
                    continue
                titre = str(page.get("title") or "").strip()
                if not titre:
                    continue
                url_page = str(page.get("fullurl") or "").strip()
                if not url_page:
                    url_page = f"https://{langue}.wikipedia.org/wiki/" + titre.replace(" ", "_")
                resultat.append(
                    {
                        "titre": titre,
                        "url": url_page,
                        "extrait": _tronquer(str(page.get("extract") or ""), MAX_EXTRAIT_WIKI),
                        "langue": langue,
                        "mot_cle": mot_cle,
                        "domaine": "wikipedia.org",
                        "type": "wikipedia",
                        "image": ((page.get("thumbnail") or {}).get("source") or ""),
                    }
                )
            if resultat:
                return resultat
        return []


class ClientExa:
    def __init__(self, cle: str, transport: TransportExa | None = None, timeout: int = 25):
        self.cle = (cle or "").strip()
        self.transport = transport or _post_exa
        self.timeout = timeout

    def rechercher(self, requete: str, nombre: int = MAX_RESULTATS_EXA) -> list[dict[str, Any]]:
        if not self.cle:
            raise ErreurRecherche("EXA_API_KEY est absente de .env.")
        corps = {
            "query": requete,
            "numResults": max(1, min(nombre, 10)),
            "contents": {
                "text": {"maxCharacters": MAX_EXTRAIT_WEB},
                "highlights": {"numSentences": 3, "highlightsPerUrl": 2},
            },
        }
        entetes = {"x-api-key": self.cle, "Content-Type": "application/json"}
        try:
            statut, charge = self.transport(URL_EXA, entetes, corps, self.timeout)
        except requests.RequestException as probleme:
            raise ErreurRecherche(
                f"Exa est injoignable ({probleme.__class__.__name__}). Vérifiez la connexion."
            ) from probleme
        if statut in (401, 403):
            raise ErreurRecherche("Exa refuse la clé EXA_API_KEY (clé invalide ou expirée).")
        if statut == 429:
            raise ErreurRecherche("Exa limite le débit ou le crédit de recherche : réessayez plus tard.")
        if statut != 200:
            raise ErreurRecherche(f"Exa a répondu HTTP {statut}.")
        resultats = charge.get("results") or []
        if not isinstance(resultats, list):
            return []
        propre: list[dict[str, Any]] = []
        for resultat in resultats:
            if not isinstance(resultat, dict):
                continue
            url = _url_propre(str(resultat.get("url") or ""))
            if not url.startswith(("http://", "https://")):
                continue
            extrait = ""
            highlights = resultat.get("highlights")
            if isinstance(highlights, list):
                extrait = " ".join(str(element) for element in highlights if element)
            if not extrait:
                extrait = str(resultat.get("text") or "")
            if not extrait:
                extrait = str(resultat.get("summary") or "")
            propre.append(
                {
                    "titre": " ".join(str(resultat.get("title") or "Sans titre").split()),
                    "url": url,
                    "extrait": _tronquer(extrait, MAX_EXTRAIT_WEB),
                    "date": str(resultat.get("publishedDate") or ""),
                    "domaine": domaine_racine(url),
                    "type": "web",
                }
            )
        return propre


# ---------------------------------------------------------------------------
# Mode sans IA
# ---------------------------------------------------------------------------


def _pages_wikipedia(
    client: ClientWikipedia, mots_cles: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    pages: list[dict[str, Any]] = []
    manquants: list[str] = []
    vus: set[str] = set()
    for mot in mots_cles:
        try:
            trouvees = client.rechercher(mot)
        except ErreurRecherche:
            raise
        if not trouvees:
            manquants.append(mot)
            continue
        for page in trouvees[:MAX_PAGES_WIKI]:
            url = _url_propre(page.get("url", ""))
            if url in vus:
                continue
            vus.add(url)
            pages.append(page)
    return pages, manquants


def _sources_wikipedia(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    for index, page in enumerate(pages, start=1):
        sources.append({**page, "id": f"W{index}", "connu": True})
    return sources


def _extraits_locaux(pages: list[dict]) -> list[dict]:
    extraits = []
    for page in pages[:12]:
        texte = str(page.get("extrait") or page.get("resume") or "").strip()
        if texte:
            extraits.append({"titre": page.get("titre") or "Page Wikipédia",
                             "url": page.get("url") or "", "extrait": texte[:360]})
    return extraits


def _resume_local(pages: list[dict], manquants: list[str]) -> str:
    if not pages:
        return ""
    premiere = str(pages[0].get("extrait") or pages[0].get("resume") or "").strip()
    phrase = (premiere[:240] + "…") if len(premiere) > 240 else premiere
    complement = (
        f" Extraction locale de {len(pages)} page(s) Wikipédia, sans synthèse IA."
    )
    if manquants:
        complement += " Aucun résultat pour : " + ", ".join(manquants) + "."
    return (phrase + complement).strip()


def dossier_sans_ia(
    titre: str, mots_cles: list[str], client: ClientWikipedia
) -> dict[str, Any]:
    pages, manquants = _pages_wikipedia(client, mots_cles)
    sources = _sources_wikipedia(pages)
    a_verifier = [
        "Lecture directe des pages Wikipédia, sans synthèse automatique : "
        "recoupez chaque information importante avec au moins trois sources indépendantes."
    ]
    if manquants:
        a_verifier.append(
            "Aucune page Wikipédia trouvée pour : " + ", ".join(manquants) + "."
        )
    if not pages:
        raise ErreurRecherche(
            "Aucune page Wikipédia trouvée pour ces mots-clés. Essayez des termes plus généraux."
        )
    return {
        "titre": titre,
        "mots_cles": mots_cles,
        "mode": "sans_ia",
        "resume": _resume_local(pages, manquants),
        "extraits_locaux": _extraits_locaux(pages),
        "manquants": manquants,
        "intention": None,
        "faits": [],
        "chiffres": [],
        "contradictions": [],
        "angles": [],
        "a_verifier": a_verifier,
        "pages_wiki": pages,
        "sources": sources,
        "recoupement": {
            "regle": "3 domaines web indépendants et reconnus",
            "minimum": SOURCES_POUR_RECOUPER,
            "note": "Wikipédia sert de point de départ et ne compte pas dans le recoupement.",
        },
    }


# ---------------------------------------------------------------------------
# Mode avec IA
# ---------------------------------------------------------------------------


PROMPT_INTENTION = """Tu prépares un dossier documentaire pour une vidéo YouTube.

Tu reçois un titre de travail et des mots-clés. Déduis ce que le spectateur
s'attend à comprendre et ce que le créateur veut probablement démontrer, sans
inventer de faits sur le sujet. Formule ensuite des requêtes de recherche
précises, en français, qui permettront de vérifier le sujet sur des sources
externes sérieuses.

Règles :
1. Réponds uniquement par un objet JSON conforme au schéma, sans Markdown.
2. Ne présente aucune hypothèse comme un fait : ce sont des attentes éditoriales.
3. Les requêtes doivent chercher des données, des définitions, des études, des
   chiffres officiels ou des analyses contradictoires — pas des opinions de réseaux sociaux.
4. Entre 4 et 8 requêtes, chacune différente et directement utile au dossier.
"""

PROMPT_DOSSIER = """Tu rédiges un dossier de recherche sourcé pour une vidéo YouTube.

Règles absolues :
1. Réponds uniquement par un objet JSON conforme au schéma, sans texte autour.
2. N'utilise que les sources fournies. Chaque affirmation et chaque chiffre doit
   citer au moins un identifiant de source réel (W1, S1, …).
3. Une affirmation n'est « recoupée » que si AU MOINS TROIS sources web de
   domaines différents et reconnues la confirment. Le calcul final est refait
   localement : ne triche pas avec les identifiants.
4. Si une information n'est pas suffisamment confirmée, place-la dans
   « a_verifier » ou dans « contradictions » plutôt que de l'affirmer.
5. N'invente aucun chiffre, aucune date, aucun nom. Si le corpus ne contient
   pas l'information, dis-le explicitement.
6. « angles » propose des traitements éditoriaux appuyés sur le corpus, sans
   ajouter de faits nouveaux.
7. Le résumé fait 4 à 8 phrases et distingue clairement ce qui est établi de ce
   qui reste à vérifier.
"""


def analyser_intention(
    service: ServiceLlm, titre: str, mots_cles: list[str]
) -> tuple[dict[str, Any] | None, int, str | None]:
    schema = prompts.charger_schema("research_intent.schema.json")
    demande = (
        f"Titre de travail : {titre}\n"
        f"Mots-clés : {', '.join(mots_cles)}\n\n"
        "Rends l'analyse d'intention et les requêtes de recherche."
    )
    messages = [
        {"role": "system", "content": PROMPT_INTENTION},
        {"role": "user", "content": demande},
    ]
    donnees, _brut, tentatives, erreur = scoring.demander_json(service, messages, schema)
    return donnees, tentatives, erreur


def _requetes_web(intention: dict[str, Any], titre: str, mots_cles: list[str]) -> list[str]:
    candidats = list(intention.get("requetes") or []) + [titre] + list(mots_cles)
    resultat: list[str] = []
    vus: set[str] = set()
    for candidat in candidats:
        requete = " ".join(str(candidat).split())
        if len(requete) < 3:
            continue
        cle = _sans_accents(requete).casefold()
        if cle in vus:
            continue
        vus.add(cle)
        resultat.append(requete)
        if len(resultat) >= MAX_REQUETES_WEB:
            break
    return resultat


def _sources_web(client: ClientExa, requetes: list[str]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    vus: set[str] = set()
    for requete in requetes:
        for resultat in client.rechercher(requete):
            url = _url_propre(resultat.get("url", ""))
            if url in vus:
                continue
            vus.add(url)
            sources.append(resultat)
            if len(sources) >= MAX_SOURCES_WEB:
                return sources
    return sources


def _identifiants_valides(valeurs: Any, connus: set[str]) -> list[str]:
    if not isinstance(valeurs, list):
        return []
    resultat: list[str] = []
    for valeur in valeurs:
        identifiant = str(valeur or "").strip().upper()
        if identifiant in connus and identifiant not in resultat:
            resultat.append(identifiant)
    return resultat


def _domaines_recoupes(identifiants: list[str], par_id: dict[str, dict[str, Any]]) -> list[str]:
    domaines: set[str] = set()
    for identifiant in identifiants:
        source = par_id.get(identifiant) or {}
        if source.get("type") != "web":
            continue
        domaine = str(source.get("domaine") or "")
        if domaine == "wikipedia.org" or not domaine:
            continue
        if not source.get("connu"):
            continue
        domaines.add(domaine)
    return sorted(domaines)


def _nettoyer_dossier(
    donnees: dict[str, Any],
    sources: list[dict[str, Any]],
    intention: dict[str, Any],
    titre: str,
    mots_cles: list[str],
) -> dict[str, Any]:
    par_id = {source["id"]: source for source in sources if source.get("id")}
    connus = set(par_id)
    a_verifier = [
        str(element).strip()
        for element in (donnees.get("a_verifier") or [])
        if str(element).strip()
    ]

    def sources_ou_verifier(valeurs: Any, libelle: str) -> list[str]:
        identifiants = _identifiants_valides(valeurs, connus)
        if not identifiants:
            a_verifier.append(f"{libelle} : aucune source réelle n'a été citée, information écartée.")
        return identifiants

    faits: list[dict[str, Any]] = []
    for element in donnees.get("faits") or []:
        if not isinstance(element, dict):
            continue
        affirmation = " ".join(str(element.get("affirmation") or "").split())
        if not affirmation:
            continue
        identifiants = sources_ou_verifier(
            element.get("sources"), f"« {affirmation[:120]} »"
        )
        if not identifiants:
            continue
        domaines = _domaines_recoupes(identifiants, par_id)
        statut = "recoupe" if len(domaines) >= SOURCES_POUR_RECOUPER else "a_verifier"
        faits.append(
            {
                "affirmation": affirmation,
                "precision": " ".join(str(element.get("precision") or "").split()),
                "sources": identifiants,
                "domaines": domaines,
                "statut": statut,
                "libelle_statut": (
                    f"Recoupé · {len(domaines)} domaines"
                    if statut == "recoupe"
                    else f"À vérifier · {len(domaines)} domaine(s) concordant(s)"
                ),
            }
        )

    chiffres: list[dict[str, Any]] = []
    for element in donnees.get("chiffres") or []:
        if not isinstance(element, dict):
            continue
        valeur = " ".join(str(element.get("valeur") or "").split())
        contexte = " ".join(str(element.get("contexte") or "").split())
        if not valeur or not contexte:
            continue
        identifiants = sources_ou_verifier(element.get("sources"), f"Chiffre « {valeur} »")
        if not identifiants:
            continue
        domaines = _domaines_recoupes(identifiants, par_id)
        statut = "recoupe" if len(domaines) >= SOURCES_POUR_RECOUPER else "a_verifier"
        chiffres.append(
            {
                "valeur": valeur,
                "contexte": contexte,
                "sources": identifiants,
                "domaines": domaines,
                "statut": statut,
                "libelle_statut": (
                    f"Recoupé · {len(domaines)} domaines"
                    if statut == "recoupe"
                    else f"À vérifier · {len(domaines)} domaine(s) concordant(s)"
                ),
            }
        )

    contradictions = []
    for element in donnees.get("contradictions") or []:
        if not isinstance(element, dict):
            continue
        point = " ".join(str(element.get("point") or "").split())
        explication = " ".join(str(element.get("explication") or "").split())
        if not point or not explication:
            continue
        identifiants = _identifiants_valides(element.get("sources"), connus)
        contradictions.append(
            {"point": point, "explication": explication, "sources": identifiants}
        )

    angles = []
    for element in donnees.get("angles") or []:
        if not isinstance(element, dict):
            continue
        titre_angle = " ".join(str(element.get("titre") or "").split())
        angle = " ".join(str(element.get("angle") or "").split())
        pourquoi = " ".join(str(element.get("pourquoi") or "").split())
        if not titre_angle or not angle or not pourquoi:
            continue
        angles.append(
            {
                "titre": titre_angle,
                "angle": angle,
                "pourquoi": pourquoi,
                "sources": _identifiants_valides(element.get("sources"), connus),
            }
        )

    # Dédupliquer les avertissements, dans l'ordre.
    vus: set[str] = set()
    avertissements = []
    for element in a_verifier:
        if element not in vus:
            vus.add(element)
            avertissements.append(element)

    return {
        "titre": titre,
        "mots_cles": mots_cles,
        "mode": "avec_ia",
        "resume": _tronquer(str(donnees.get("resume") or ""), 1800),
        "intention": intention,
        "faits": faits,
        "chiffres": chiffres,
        "contradictions": contradictions,
        "angles": angles,
        "a_verifier": avertissements,
        "pages_wiki": [source for source in sources if source.get("type") == "wikipedia"],
        "sources": sources,
        "recoupement": {
            "regle": "3 domaines web indépendants et reconnus",
            "minimum": SOURCES_POUR_RECOUPER,
            "note": "Wikipédia sert de point de départ et ne compte pas dans le recoupement.",
        },
    }


def _demander_dossier(
    service: ServiceLlm,
    titre: str,
    mots_cles: list[str],
    intention: dict[str, Any],
    sources: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, int, str | None]:
    schema = prompts.charger_schema("research_dossier.schema.json")
    lignes_sources = []
    for source in sources:
        extrait = _tronquer(str(source.get("extrait") or ""), 700)
        date = f" · date : {source['date']}" if source.get("date") else ""
        lignes_sources.append(
            f"[{source['id']}] {source.get('titre')} — {source.get('domaine')}{date}\n{extrait}"
        )
    corpus = "\n\n".join(lignes_sources) or "(aucune source récupérée)"
    intention_lisible = json.dumps(intention, ensure_ascii=False, indent=2)
    demande = (
        f"## TITRE DE TRAVAIL\n{titre}\n\n"
        f"## MOTS-CLÉS\n{', '.join(mots_cles)}\n\n"
        f"## INTENTION ET ATTENTES\n{intention_lisible}\n\n"
        f"## SOURCES RÉELLEMENT RÉCUPÉRÉES\n{corpus}\n\n"
        "Rédige le dossier JSON. Toute source citée doit exister ci-dessus."
    )
    messages = [
        {"role": "system", "content": PROMPT_DOSSIER},
        {"role": "user", "content": demande},
    ]
    return scoring.demander_json(service, messages, schema)


def dossier_avec_ia(
    titre: str,
    mots_cles: list[str],
    client_wiki: ClientWikipedia,
    client_exa: ClientExa,
    service: ServiceLlm,
) -> tuple[dict[str, Any], int]:
    intention, appels, erreur = analyser_intention(service, titre, mots_cles)
    if intention is None:
        raise ErreurRecherche(
            "Le LLM n'a pas produit d'analyse d'intention valide"
            + (f" ({erreur})." if erreur else ".")
        )
    pages, manquants = _pages_wikipedia(client_wiki, mots_cles)
    wiki_sources = _sources_wikipedia(pages)
    requetes = _requetes_web(intention, titre, mots_cles)
    web_sources = _sources_web(client_exa, requetes)
    sources: list[dict[str, Any]] = []
    for source in wiki_sources:
        source = dict(source)
        source["connu"] = True
        sources.append(source)
    for index, source in enumerate(web_sources, start=1):
        source = dict(source)
        source["id"] = f"S{index}"
        source["connu"] = bool(source.get("connu")) or domaine_connu(source.get("domaine", ""))
        sources.append(source)
    donnees, _brut, appels_synthese, erreur = _demander_dossier(
        service, titre, mots_cles, intention, sources
    )
    appels += appels_synthese
    if donnees is None:
        raise ErreurRecherche(
            "Le LLM n'a pas produit de dossier valide"
            + (f" ({erreur})." if erreur else ".")
        )
    dossier = _nettoyer_dossier(donnees, sources, intention, titre, mots_cles)
    if manquants:
        dossier["a_verifier"].insert(
            0,
            "Aucune page Wikipédia trouvée pour : " + ", ".join(manquants) + ".",
        )
    if not web_sources:
        dossier["a_verifier"].insert(
            0,
            "Exa n'a renvoyé aucune source web : aucun fait ne peut être recoupé par trois domaines.",
        )
    return dossier, appels


# ---------------------------------------------------------------------------
# Cache SQLite
# ---------------------------------------------------------------------------


def _ligne_vers_session(ligne: sqlite3.Row) -> dict[str, Any]:
    def charger(nom: str, defaut):
        brut = ligne[nom] if nom in ligne.keys() else None
        if not brut:
            return defaut
        try:
            return json.loads(brut)
        except (TypeError, ValueError):
            return defaut

    dossier = charger("dossier_json", None)
    if dossier is None:
        dossier = {
            "resume": "",
            "faits": [],
            "chiffres": [],
            "contradictions": [],
            "angles": [],
            "a_verifier": [ligne["erreur"]] if ligne["erreur"] else [],
            "sources": charger("sources_json", []),
            "pages_wiki": charger("pages_json", []),
            "intention": charger("intention_json", None),
        }
    return {
        "id": ligne["id"],
        "titre": ligne["titre"],
        "mots_cles": charger("mots_cles_json", []),
        "mode": ligne["mode"],
        "valide": bool(ligne["valide"]),
        "erreur": ligne["erreur"],
        "cree_le": ligne["cree_le"],
        "fournisseur": ligne["fournisseur"],
        "modele": ligne["modele"],
        "dossier": dossier,
        "sources": charger("sources_json", []),
        "pages_wiki": charger("pages_json", []),
        "intention": charger("intention_json", None),
    }


def derniere_session(conn: sqlite3.Connection, mode: str | None = None) -> dict[str, Any] | None:
    if mode in ("sans_ia", "avec_ia"):
        ligne = conn.execute(
            "SELECT * FROM research_sessions WHERE valide = 1 AND mode = ? "
            "ORDER BY id DESC LIMIT 1",
            (mode,),
        ).fetchone()
    else:
        ligne = conn.execute(
            "SELECT * FROM research_sessions WHERE valide = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _ligne_vers_session(ligne) if ligne else None


def sessions_recentes(
    conn: sqlite3.Connection, limite: int = 3
) -> list[dict[str, Any]]:
    """Derniers dossiers valides, dans l'ordre utilisé par le cerveau IA."""
    lignes = conn.execute(
        "SELECT * FROM research_sessions WHERE valide = 1 "
        "ORDER BY id DESC LIMIT ?",
        (max(1, int(limite)),),
    ).fetchall()
    return [
        session
        for ligne in lignes
        if (session := _ligne_vers_session(ligne)) is not None
    ]


def contexte_pour_cerveau(
    conn: sqlite3.Connection, limite: int = 3
) -> tuple[str, list[dict[str, Any]]]:
    """Recherches récentes, avec leurs faits recoupés et leurs sources.

    Le cerveau reçoit un bloc borné mais fidèle au dossier : une information
    marquée « à vérifier » ne doit jamais devenir une affirmation dans sa
    synthèse. L'instantané ne contient que les métadonnées utiles au cache.
    """
    lignes = conn.execute(
        "SELECT * FROM research_sessions WHERE valide = 1 "
        "ORDER BY id DESC LIMIT ?",
        (max(1, int(limite)),),
    ).fetchall()
    if not lignes:
        return "(aucune recherche documentaire enregistrée)", []

    morceaux: list[str] = []
    instantane: list[dict[str, Any]] = []
    for ligne in lignes:
        session = _ligne_vers_session(ligne)
        if session is None:
            continue
        dossier = session.get("dossier") or {}
        mode = "avec_ia" if session.get("mode") == "avec_ia" else "sans_ia"
        titre = str(session.get("titre") or "Sans titre")
        date = str(session.get("cree_le") or "")[:10]
        morceaux.append(f"- [{mode}] « {titre} » — {date}")
        resume = _tronquer(str(dossier.get("resume") or ""), 700)
        if resume:
            morceaux.append("  Synthèse : " + resume)

        faits = [
            fait
            for fait in dossier.get("faits") or []
            if isinstance(fait, dict) and fait.get("statut") == "recoupe"
        ]
        for fait in faits[:4]:
            domaines = ", ".join(fait.get("domaines") or [])
            suffixe = f" (domaines : {domaines})" if domaines else ""
            morceaux.append(
                "  Fait recoupé : "
                + _tronquer(str(fait.get("affirmation") or ""), 450)
                + suffixe
            )
        a_verifier = [
            str(element).strip()
            for element in dossier.get("a_verifier") or []
            if str(element).strip()
        ]
        for element in a_verifier[:3]:
            morceaux.append("  À vérifier : " + _tronquer(element, 300))

        pages = [page for page in dossier.get("pages_wiki") or [] if isinstance(page, dict)]
        for page in pages[:2]:
            extrait = _tronquer(str(page.get("extrait") or ""), 450)
            morceaux.append(f"  Wikipédia : {page.get('titre')} — {extrait}")

        domaines = []
        for source in dossier.get("sources") or []:
            domaine = str(source.get("domaine") or "").strip()
            if domaine and domaine not in domaines:
                domaines.append(domaine)
        if domaines:
            morceaux.append("  Sources web/Wikipédia : " + ", ".join(domaines[:8]))
        instantane.append(
            {
                "id": session.get("id"),
                "mode": mode,
                "titre": titre,
                "cree_le": session.get("cree_le"),
            }
        )
    if not morceaux:
        return "(aucune recherche documentaire enregistrée)", []
    return "\n".join(morceaux), instantane


def _enregistrer(
    conn: sqlite3.Connection,
    *,
    run_id: int | None,
    titre: str,
    mots_cles: list[str],
    mode: str,
    empreinte: str,
    fournisseur: str | None,
    modele: str | None,
    dossier: dict[str, Any] | None,
    erreur: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO research_sessions (
            run_id, titre, mots_cles_json, mode, empreinte, prompt_version,
            fournisseur, modele, intention_json, sources_json, pages_json,
            dossier_json, valide, erreur, cree_le
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empreinte, mode, prompt_version) DO UPDATE SET
            run_id = excluded.run_id,
            titre = excluded.titre,
            mots_cles_json = excluded.mots_cles_json,
            fournisseur = excluded.fournisseur,
            modele = excluded.modele,
            intention_json = excluded.intention_json,
            sources_json = excluded.sources_json,
            pages_json = excluded.pages_json,
            dossier_json = excluded.dossier_json,
            valide = excluded.valide,
            erreur = excluded.erreur,
            cree_le = excluded.cree_le
        """,
        (
            run_id,
            titre,
            json.dumps(mots_cles, ensure_ascii=False),
            mode,
            empreinte,
            PROMPT_VERSION,
            fournisseur,
            modele,
            json.dumps((dossier or {}).get("intention"), ensure_ascii=False),
            json.dumps((dossier or {}).get("sources") or [], ensure_ascii=False),
            json.dumps((dossier or {}).get("pages_wiki") or [], ensure_ascii=False),
            json.dumps(dossier, ensure_ascii=False) if dossier else None,
            1 if dossier else 0,
            erreur,
            _maintenant(),
        ),
    )
    conn.commit()


def _session_en_cache(
    conn: sqlite3.Connection, empreinte: str, mode: str
) -> dict[str, Any] | None:
    ligne = conn.execute(
        "SELECT * FROM research_sessions WHERE empreinte = ? AND mode = ? "
        "AND prompt_version = ? AND valide = 1",
        (empreinte, mode, PROMPT_VERSION),
    ).fetchone()
    return _ligne_vers_session(ligne) if ligne else None


def generer(
    conn: sqlite3.Connection,
    config: Config,
    *,
    titre: str,
    mots_cles: str | list[str] | tuple[str, ...],
    mode: str = "sans_ia",
    service: ServiceLlm | None = None,
    client_wikipedia: ClientWikipedia | None = None,
    client_exa: ClientExa | None = None,
    run_id: int | None = None,
    forcer: bool = False,
) -> ResumeRecherche:
    """Génère (ou relit) un dossier documentaire.

    Les erreurs de saisie lèvent ``ErreurRecherche``. Une panne réseau ou une
    réponse LLM invalide est consignée dans la base et renvoyée dans
    ``messages`` : l'interface peut ainsi expliquer l'échec sans traceback.
    """
    if mode not in ("sans_ia", "avec_ia"):
        raise ErreurRecherche("Le mode doit être « sans_ia » ou « avec_ia ».")
    titre_propre = normaliser_titre(titre)
    mots_propres = normaliser_mots_cles(mots_cles)
    empreinte = _empreinte(titre_propre, mots_propres, mode)
    resume = ResumeRecherche(titre=titre_propre, mots_cles=mots_propres, mode=mode)

    if not forcer:
        cache = _session_en_cache(conn, empreinte, mode)
        if cache and cache.get("dossier"):
            resume.dossier = cache["dossier"]
            resume.depuis_cache = True
            return resume

    if client_wikipedia is None:
        client_wikipedia = ClientWikipedia()
    fournisseur = modele = None
    if mode == "avec_ia":
        if not config.exa_cle:
            raise ErreurRecherche(
                "La recherche avec IA nécessite EXA_API_KEY. "
                "Ajoutez la clé dans la page Clés & modèle, ou utilisez le mode sans IA."
            )
        if service is None:
            raise ErreurRecherche("Le fournisseur LLM est requis pour la recherche avec IA.")
        if client_exa is None:
            client_exa = ClientExa(config.exa_cle)
        fournisseur = getattr(getattr(service, "fournisseur", None), "nom", None)
        modele = getattr(getattr(service, "fournisseur", None), "modele", None)

    try:
        if mode == "sans_ia":
            dossier = dossier_sans_ia(titre_propre, mots_propres, client_wikipedia)
        else:
            dossier, appels = dossier_avec_ia(
                titre_propre, mots_propres, client_wikipedia, client_exa, service  # type: ignore[arg-type]
            )
            resume.appels_llm = appels
    except (ErreurRecherche, ErreurLlm) as probleme:
        message = str(probleme)
        resume.messages.append(message)
        _enregistrer(
            conn,
            run_id=run_id,
            titre=titre_propre,
            mots_cles=mots_propres,
            mode=mode,
            empreinte=empreinte,
            fournisseur=fournisseur,
            modele=modele,
            dossier=None,
            erreur=message,
        )
        return resume

    resume.dossier = dossier
    _enregistrer(
        conn,
        run_id=run_id,
        titre=titre_propre,
        mots_cles=mots_propres,
        mode=mode,
        empreinte=empreinte,
        fournisseur=fournisseur,
        modele=modele,
        dossier=dossier,
    )
    return resume
