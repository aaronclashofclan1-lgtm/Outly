"""Client bas niveau de l'API YouTube Data v3.

Ordre des opérations pour chaque appel : cache → contrôle de budget → HTTP →
journalisation → mise en cache. Ainsi, un appel servi par le cache ne coûte
jamais de quota, et le compteur ne peut pas dériver de la réalité.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

import requests

from .cache import Cache
from .config import Config
from .models import Chaine
from .quota import BudgetDepasse, CompteurQuota, compartiment_et_cout

BASE = "https://www.googleapis.com/youtube/v3/"
PART_CHAINE = "snippet,statistics,contentDetails,brandingSettings,topicDetails"
PART_VIDEO = "snippet,statistics,contentDetails"
TENTATIVES_MAX = 3


class ErreurApi(Exception):
    """Erreur d'API normalisée, avec un message directement affichable."""

    def __init__(self, message: str, statut: int | None = None, facture: bool = False):
        super().__init__(message)
        self.statut = statut
        # Les requêtes invalides sont facturées d'après la documentation Google.
        self.facture = facture


class ErreurQuota(ErreurApi):
    """Quota épuisé côté Google : inutile de continuer aujourd'hui."""


class ErreurCle(ErreurApi):
    """Problème de clé ou de projet : il faut corriger la configuration."""


class ErreurIntrouvable(ErreurApi):
    """Ressource inconnue (handle inexistant, chaîne supprimée)."""


@dataclass
class ReponseHttp:
    statut: int
    charge: dict = field(default_factory=dict)
    entetes: dict = field(default_factory=dict)


def transport_requests(url: str, params: dict, timeout: int) -> ReponseHttp:
    """Transport réel (requests). Injecté pour permettre les tests hors réseau."""
    reponse = requests.get(url, params=params, timeout=timeout)
    try:
        charge = reponse.json()
    except ValueError:
        charge = {}
    return ReponseHttp(reponse.status_code, charge if isinstance(charge, dict) else {}, dict(reponse.headers))


def _extraire_erreur(charge: dict) -> tuple[str | None, str]:
    """Décode une erreur Google, même si sa forme n'est pas celle attendue."""
    erreur = (charge or {}).get("error") if isinstance(charge, dict) else None
    if not isinstance(erreur, dict):
        return None, str(erreur or "réponse d'erreur sans message")[:300]
    details = erreur.get("errors") or []
    raison = None
    if isinstance(details, list) and details and isinstance(details[0], dict):
        raison = details[0].get("reason")
    message = erreur.get("message") or "réponse d'erreur sans message"
    return raison, str(message)


def _delai_retry_after(reponse: ReponseHttp) -> float | None:
    valeur = (reponse.entetes or {}).get("Retry-After")
    if not valeur:
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


REASONS_QUOTA = {"quotaExceeded", "dailyLimitExceeded"}
REASONS_CLE = {"keyInvalid", "accessNotConfigured", "ipRefererBlocked", "keyExpired"}


class ClientYoutube:
    def __init__(
        self,
        cle: str,
        compteur: CompteurQuota,
        cache: Cache,
        config: Config,
        transport: Callable[[str, dict, int], ReponseHttp] | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ):
        self._cle = cle
        self._compteur = compteur
        self._cache = cache
        self._config = config
        self._transport = transport or transport_requests
        self._dormir = dormir
        self._timeout = 30

    # ------------------------------------------------------------------
    # Cœur : un appel = cache, budget, HTTP, journal
    # ------------------------------------------------------------------
    def _url(self, endpoint: str) -> str:
        return BASE + endpoint.split(".")[0]

    def _http(self, endpoint: str, params: dict) -> dict:
        url = self._url(endpoint)
        complets = dict(params)
        complets["key"] = self._cle
        derniere: ErreurApi | None = None

        for tentative in range(TENTATIVES_MAX):
            try:
                reponse = self._transport(url, complets, self._timeout)
            except requests.RequestException as erreur:
                derniere = ErreurApi(
                    f"Connexion impossible à l'API YouTube ({erreur.__class__.__name__}). "
                    "Vérifiez votre connexion réseau."
                )
                if tentative == TENTATIVES_MAX - 1:
                    raise derniere
                self._dormir(2**tentative)
                continue

            if reponse.statut == 200:
                return reponse.charge

            raison, message = _extraire_erreur(reponse.charge)

            if reponse.statut in (429, 500, 502, 503, 504):
                if tentative < TENTATIVES_MAX - 1:
                    attente = _delai_retry_after(reponse) or float(2**tentative)
                    self._dormir(min(attente, 30))
                    continue
                raise ErreurApi(
                    f"YouTube a répondu {reponse.statut} après {TENTATIVES_MAX} tentatives. "
                    "Réessayez dans quelques minutes.",
                    statut=reponse.statut,
                )

            if raison in REASONS_QUOTA:
                raise ErreurQuota(
                    "Quota YouTube épuisé côté Google (10 000 unités ou 100 recherches "
                    "par jour). Le compteur local n'est pas en cause : attendez la "
                    "réinitialisation à minuit, heure du Pacifique.",
                    statut=reponse.statut,
                )
            if raison in REASONS_CLE:
                raise ErreurCle(
                    f"Clé API refusée par YouTube ({raison}) : {message}. "
                    "Vérifiez YOUTUBE_API_KEY et que « YouTube Data API v3 » est activée "
                    "sur ce projet Google Cloud.",
                    statut=reponse.statut,
                )
            if reponse.statut == 404:
                raise ErreurIntrouvable(
                    f"Ressource introuvable ({raison or 'notFound'}) : {message}.",
                    statut=reponse.statut,
                    facture=True,
                )

            raise ErreurApi(
                f"L'API YouTube a refusé la requête ({reponse.statut}"
                f"{' · ' + raison if raison else ''}) : {message}",
                statut=reponse.statut,
                facture=reponse.statut < 500,
            )

        raise derniere or ErreurApi("Échec inconnu de l'appel à l'API YouTube.")

    def _obtenir(self, endpoint: str, params: dict, ttl_jours: int) -> dict:
        empreinte = Cache.empreinte(endpoint, params)
        en_cache = self._cache.lire(empreinte)
        if en_cache is not None:
            self._compteur.enregistrer(
                endpoint, 0, depuis_cache=True, empreinte=empreinte, http_status=200
            )
            return en_cache

        # Lève BudgetDepasse AVANT de dépenser : l'appelant décide de s'arrêter.
        self._compteur.verifier(endpoint)

        try:
            charge = self._http(endpoint, params)
        except ErreurApi as erreur:
            self._compteur.enregistrer(
                endpoint,
                1 if erreur.facture else 0,
                empreinte=empreinte,
                http_status=erreur.statut,
                erreur=str(erreur)[:400],
            )
            raise

        _, cout = compartiment_et_cout(endpoint)
        self._compteur.enregistrer(endpoint, cout, empreinte=empreinte, http_status=200)
        self._cache.ecrire(empreinte, endpoint, charge, ttl_jours)
        return charge

    # ------------------------------------------------------------------
    # Méthodes métier
    # ------------------------------------------------------------------
    def chaine_par_handle(self, handle: str) -> Chaine | None:
        """Résout un @handle en chaîne. 1 unité (non mis en cache longtemps)."""
        propre = handle.strip().lstrip("@")
        donnees = self._obtenir(
            "channels.list",
            {"part": PART_CHAINE, "forHandle": propre, "maxResults": 1},
            self._config.cache.ttl_channel_days,
        )
        items = donnees.get("items") or []
        if not items:
            return None
        return Chaine.depuis_api(items[0])

    def chaine_par_username(self, nom: str) -> Chaine | None:
        """Résout un ancien nom d'utilisateur (URL /user/...). 1 unité."""
        donnees = self._obtenir(
            "channels.list",
            {"part": PART_CHAINE, "forUsername": nom.strip(), "maxResults": 1},
            self._config.cache.ttl_channel_days,
        )
        items = donnees.get("items") or []
        if not items:
            return None
        return Chaine.depuis_api(items[0])

    def chaines_par_ids(self, ids: Iterable[str]) -> list[Chaine]:
        """Récupère des chaînes par lots de 50 identifiants (1 unité par lot)."""
        uniques = [identifiant for identifiant in dict.fromkeys(ids) if identifiant]
        resultat: list[Chaine] = []
        taille = self._config.youtube.batch_size
        for debut in range(0, len(uniques), taille):
            lot = uniques[debut : debut + taille]
            donnees = self._obtenir(
                "channels.list",
                {"part": PART_CHAINE, "id": ",".join(lot), "maxResults": 50},
                self._config.cache.ttl_channel_days,
            )
            for item in donnees.get("items") or []:
                resultat.append(Chaine.depuis_api(item))
        return resultat

    def rechercher_chaines(
        self,
        requete: str,
        pays: str | None = None,
        langue: str | None = None,
    ) -> list[dict]:
        """Recherche de chaînes par mot-clé. 1 appel du compartiment « recherches ».

        `pays=None` et `langue=None` donnent une recherche mondiale : les deux
        paramètres sont optionnels côté API. C'est ainsi que l'option
        internationale élargit la découverte sans changer le coût par appel.
        """
        parametres = {
            "part": "snippet",
            "q": requete,
            "type": "channel",
            "maxResults": 50,
        }
        if pays:
            parametres["regionCode"] = pays.upper()
        if langue:
            parametres["relevanceLanguage"] = langue.lower()
        donnees = self._obtenir(
            "search.list",
            parametres,
            self._config.cache.ttl_search_days,
        )
        return donnees.get("items") or []

    def sections_chaine(self, channel_id: str) -> list[str]:
        """Chaînes mises en avant dans les sections d'une chaîne. 1 unité."""
        donnees = self._obtenir(
            "channelSections.list",
            {"part": "snippet,contentDetails", "channelId": channel_id},
            7,
        )
        trouves: list[str] = []
        for section in donnees.get("items") or []:
            for identifiant in (section.get("contentDetails") or {}).get("channels") or []:
                if identifiant:
                    trouves.append(identifiant)
        return list(dict.fromkeys(trouves))

    def elements_playlist(self, playlist_id: str, max_results: int) -> list[dict]:
        """Dernières vidéos d'une playlist « uploads » (1 unité par page de 50)."""
        donnees = self._obtenir(
            "playlistItems.list",
            {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": max(1, min(50, max_results)),
            },
            self._config.cache.ttl_videos_days,
        )
        return donnees.get("items") or []

    def videos_infos(self, ids: Iterable[str]) -> dict[str, dict]:
        """Détails de vidéos par lots de 50 (1 unité par lot)."""
        uniques = [identifiant for identifiant in dict.fromkeys(ids) if identifiant]
        resultat: dict[str, dict] = {}
        taille = self._config.youtube.batch_size
        for debut in range(0, len(uniques), taille):
            lot = uniques[debut : debut + taille]
            donnees = self._obtenir(
                "videos.list",
                {"part": PART_VIDEO, "id": ",".join(lot), "maxResults": 50},
                self._config.cache.ttl_videos_days,
            )
            for item in donnees.get("items") or []:
                if item.get("id"):
                    resultat[item["id"]] = item
        return resultat


__all__ = [
    "BudgetDepasse",
    "ClientYoutube",
    "ErreurApi",
    "ErreurCle",
    "ErreurIntrouvable",
    "ErreurQuota",
    "ReponseHttp",
    "transport_requests",
]
