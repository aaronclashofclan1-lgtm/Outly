"""Tableau de bord « Studio YouTube » : les données de sa propre chaîne.

YouTube Data API expose les vues, likes et commentaires publics. Les courbes
de rétention, le temps de visionnage, les sources de trafic, l'âge du public
et les impressions ne sont visibles que par le propriétaire de la chaîne :
ils passent par la YouTube Analytics API et un jeton OAuth 2.0.

Le module couvre donc trois choses :
  * le parcours OAuth « application de bureau » (redirection loopback) ;
  * la lecture des rapports Analytics et des métadonnées Data API ;
  * un instantané JSON local, pour que l'accueil et la page Studio
    s'affichent sans réseau.

Aucun jeton n'est journalisé ni écrit ailleurs que dans `.env`, sur ce PC.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .youtube_api import ReponseHttp

AUTORISATION = "https://accounts.google.com/o/oauth2/v2/auth"
JETON = "https://oauth2.googleapis.com/token"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
DATA = "https://www.googleapis.com/youtube/v3/"

SCOPES = (
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
)

TENTATIVES_MAX = 2


class ErreurStudio(Exception):
    """Erreur OAuth ou API, avec un message directement affichable."""


class ErreurJeton(ErreurStudio):
    """Jeton expiré ou révoqué : un rafraîchissement peut suffire."""


class ErreurDroit(ErreurStudio):
    """Le compte connecté n'a pas les droits sur cette chaîne."""


# ---------------------------------------------------------------------------
# Transport HTTP (injectable pour les tests)
# ---------------------------------------------------------------------------


def transport_get(url: str, params: dict, entetes: dict, timeout: int) -> ReponseHttp:
    reponse = requests.get(url, params=params, headers=entetes, timeout=timeout)
    try:
        charge = reponse.json()
    except ValueError:
        charge = {}
    return ReponseHttp(
        reponse.status_code,
        charge if isinstance(charge, dict) else {},
        dict(reponse.headers),
    )


def transport_post(url: str, donnees: dict, timeout: int) -> ReponseHttp:
    reponse = requests.post(url, data=donnees, timeout=timeout)
    try:
        charge = reponse.json()
    except ValueError:
        charge = {}
    return ReponseHttp(
        reponse.status_code,
        charge if isinstance(charge, dict) else {},
        dict(reponse.headers),
    )


def _message_erreur(charge: dict) -> str:
    erreur = (charge or {}).get("error")
    if isinstance(erreur, dict):
        return str(erreur.get("message") or erreur.get("status") or "erreur inconnue")
    if erreur:
        return str(erreur)
    return str((charge or {}).get("error_description") or "erreur inconnue")


# ---------------------------------------------------------------------------
# OAuth 2.0 « application de bureau » : redirection loopback
# ---------------------------------------------------------------------------


def url_autorisation(
    client_id: str,
    redirect_uri: str,
    etat: str,
    scopes: tuple[str, ...] = SCOPES,
) -> str:
    """URL de consentement Google. `offline` + `consent` garantissent un refresh token."""
    parametres = {
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": etat,
    }
    return AUTORISATION + "?" + urlencode(parametres)


def echanger_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    poster: Callable[[str, dict, int], ReponseHttp] = transport_post,
    timeout: int = 30,
) -> dict[str, Any]:
    """Échange le code d'autorisation contre un jeton durable."""
    reponse = poster(
        JETON,
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout,
    )
    if reponse.statut != 200:
        raise ErreurStudio(
            "Google a refusé l'échange du code d'autorisation "
            f"({reponse.statut}) : {_message_erreur(reponse.charge)}. "
            "Vérifiez que l'identifiant client est bien de type « Application de bureau »."
        )
    if not reponse.charge.get("refresh_token"):
        raise ErreurStudio(
            "Google n'a pas renvoyé de jeton durable (refresh token). "
            "Révoquez l'accès d'Outly sur myaccount.google.com/permissions, puis reconnectez-vous."
        )
    return reponse.charge


def rafraichir_jeton(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    poster: Callable[[str, dict, int], ReponseHttp] = transport_post,
    timeout: int = 30,
) -> str:
    """Transforme le jeton durable en jeton d'accès d'une heure."""
    reponse = poster(
        JETON,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout,
    )
    if reponse.statut != 200:
        raise ErreurJeton(
            "Impossible de rafraîchir l'autorisation YouTube "
            f"({reponse.statut}) : {_message_erreur(reponse.charge)}. "
            "Le jeton a peut-être expiré (application en mode « Test ») ou été révoqué : "
            "reconnectez votre chaîne."
        )
    jeton = str(reponse.charge.get("access_token") or "")
    if not jeton:
        raise ErreurJeton("Google n'a pas renvoyé de jeton d'accès.")
    return jeton


class _RetourOauth(BaseHTTPRequestHandler):
    """Reçoit la redirection de Google sur la boucle locale."""

    def do_GET(self) -> None:  # noqa: N802 (nom imposé par http.server)
        params = {
            cle: valeurs[0]
            for cle, valeurs in parse_qs(urlparse(self.path).query).items()
        }
        self.server.outly_params = params  # type: ignore[attr-defined]
        if params.get("code"):
            titre = "Autorisation reçue"
            texte = (
                "Outly peut maintenant lire les statistiques de votre chaîne. "
                "Fermez cet onglet et revenez à l'application."
            )
            couleur = "#1F7A5C"
        else:
            titre = "Autorisation refusée"
            texte = (
                params.get("error_description")
                or params.get("error")
                or "Aucun code d'autorisation n'a été reçu."
            )
            couleur = "#A3271B"
        corps = (
            "<!doctype html><html lang=fr><meta charset=utf-8>"
            "<title>Outly · YouTube</title>"
            "<body style=\"font-family:Segoe UI,sans-serif;background:#160D12;"
            "color:#F7E9E1;display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0\">"
            f"<main style=\"max-width:520px;padding:32px;border:1px solid #402530;"
            f"border-radius:12px;background:#21131A\"><h1 style=\"color:{couleur};"
            f"font-size:22px;margin:0 0 12px\">{titre}</h1>"
            f"<p style=\"line-height:1.5;margin:0;color:#D3ADA8\">{texte}</p>"
            "</main></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *_args) -> None:
        """Le serveur local ne doit rien écrire dans la console."""


class FluxOauth:
    """Parcours d'autorisation complet, pilotable depuis un fil de travail.

    `demarrer()` ouvre le navigateur ; `attendre()` bloque jusqu'au retour de
    Google (ou au délai) et rend le refresh token. Le serveur écoute
    uniquement sur 127.0.0.1 et se ferme avec `fermer()`.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        poster: Callable[[str, dict, int], ReponseHttp] = transport_post,
        ouvrir: Callable[[str], Any] | None = None,
        timeout: int = 240,
    ):
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._poster = poster
        self._ouvrir = ouvrir or webbrowser.open
        self._timeout = max(30, int(timeout))
        self._serveur: HTTPServer | None = None
        self._redirect_uri = ""
        self._etat = ""

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def demarrer(self) -> str:
        if not self._client_id or not self._client_secret:
            raise ErreurStudio(
                "Renseignez l'identifiant client et le secret client OAuth avant de connecter."
            )
        try:
            self._serveur = HTTPServer(("127.0.0.1", 0), _RetourOauth)
        except OSError as probleme:
            raise ErreurStudio(
                f"Impossible d'ouvrir le serveur local d'autorisation : {probleme}"
            ) from probleme
        self._serveur.timeout = 1.0
        self._serveur.outly_params = {}  # type: ignore[attr-defined]
        port = self._serveur.server_address[1]
        self._redirect_uri = f"http://127.0.0.1:{port}/"
        self._etat = secrets.token_urlsafe(20)
        url = url_autorisation(self._client_id, self._redirect_uri, self._etat)
        self._ouvrir(url)
        return url

    def attendre(self) -> str:
        if self._serveur is None:
            raise ErreurStudio("Le parcours d'autorisation n'a pas été démarré.")
        fin = time.monotonic() + self._timeout
        params: dict[str, str] = {}
        while time.monotonic() < fin:
            params = dict(getattr(self._serveur, "outly_params", {}) or {})
            if params:
                break
            self._serveur.handle_request()
        if not params:
            raise ErreurStudio(
                "Délai dépassé : aucune réponse de Google. Relancez la connexion et "
                "autorisez l'accès dans le navigateur."
            )
        if params.get("error"):
            raise ErreurStudio(
                "Autorisation refusée : "
                + str(params.get("error_description") or params.get("error"))
            )
        if params.get("state") != self._etat:
            raise ErreurStudio(
                "Réponse d'autorisation inattendue (jeton d'état différent). "
                "Relancez la connexion depuis Outly."
            )
        code = str(params.get("code") or "")
        if not code:
            raise ErreurStudio("Google n'a pas renvoyé de code d'autorisation.")
        donnees = echanger_code(
            self._client_id,
            self._client_secret,
            code,
            self._redirect_uri,
            poster=self._poster,
        )
        return str(donnees["refresh_token"])

    def fermer(self) -> None:
        if self._serveur is not None:
            try:
                self._serveur.server_close()
            except OSError:
                pass
            self._serveur = None


# ---------------------------------------------------------------------------
# Client Analytics + Data API
# ---------------------------------------------------------------------------


class StudioClient:
    """Lecture des rapports de la chaîne du compte autorisé.

    `rafraichir` est rappelé une seule fois sur une réponse 401 : le jeton
    d'accès vit une heure, le refresh token reste dans `.env`.
    """

    def __init__(
        self,
        jeton: str,
        *,
        rafraichir: Callable[[], str] | None = None,
        transport: Callable[[str, dict, dict, int], ReponseHttp] | None = None,
        timeout: int = 45,
    ):
        self._jeton = jeton
        self._rafraichir = rafraichir
        self._transport = transport or transport_get
        self._timeout = int(timeout)

    @property
    def jeton(self) -> str:
        return self._jeton

    def _get(self, url: str, params: dict, essai: bool = True) -> dict:
        entetes = {"Authorization": f"Bearer {self._jeton}"}
        reponse = self._transport(url, params, entetes, self._timeout)
        if reponse.statut == 401 and essai and self._rafraichir is not None:
            self._jeton = self._rafraichir()
            return self._get(url, params, essai=False)
        if reponse.statut == 401:
            raise ErreurJeton(
                "YouTube a refusé le jeton d'accès (401). Reconnectez votre chaîne."
            )
        if reponse.statut == 403:
            raise ErreurDroit(
                "Accès refusé par YouTube (403) : "
                + _message_erreur(reponse.charge)
                + ". Le compte Google connecté doit être propriétaire de la chaîne. "
                "Vérifiez aussi que « YouTube Analytics API » est activée dans le projet."
            )
        if reponse.statut != 200:
            raise ErreurStudio(
                f"YouTube a répondu {reponse.statut} : {_message_erreur(reponse.charge)}"
            )
        return reponse.charge

    def analytics(self, **parametres) -> dict:
        requete = {"ids": "channel==MINE"}
        requete.update(parametres)
        return self._get(ANALYTICS, requete)

    def chaine(self) -> dict:
        charge = self._get(
            DATA + "channels",
            {
                "part": "snippet,statistics,contentDetails",
                "mine": "true",
                "maxResults": 1,
            },
        )
        items = charge.get("items") or []
        return items[0] if items else {}

    def videos(self, identifiants: list[str]) -> dict[str, dict]:
        resultat: dict[str, dict] = {}
        uniques = [identifiant for identifiant in dict.fromkeys(identifiants) if identifiant]
        for debut in range(0, len(uniques), 50):
            lot = uniques[debut : debut + 50]
            charge = self._get(
                DATA + "videos",
                {
                    "part": "snippet,contentDetails,statistics,liveStreamingDetails",
                    "id": ",".join(lot),
                    "maxResults": 50,
                },
            )
            for item in charge.get("items") or []:
                if item.get("id"):
                    resultat[str(item["id"])] = item
        return resultat


# ---------------------------------------------------------------------------
# Rapports
# ---------------------------------------------------------------------------


@dataclass
class Video:
    identifiant: str = ""
    titre: str = ""
    publie_le: str = ""
    duree_s: int = 0
    vues: int = 0
    minutes: float = 0.0
    duree_moyenne_s: float = 0.0
    pourcentage_vu: float = 0.0
    jaime: int = 0
    commentaires: int = 0
    partages: int = 0
    impressions: int | None = None
    ctr: float | None = None
    live: str = "none"


@dataclass
class Tableau:
    chaine: str = ""
    chaine_id: str = ""
    abonnes: int = 0
    vues_totales: int = 0
    nombre_videos: int = 0
    debut: str = ""
    fin: str = ""
    vues: int = 0
    minutes: float = 0.0
    duree_moyenne_s: float = 0.0
    pourcentage_vu: float = 0.0
    abonnes_gagnes: int = 0
    abonnes_perdus: int = 0
    jaime: int = 0
    commentaires: int = 0
    partages: int = 0
    impressions: int | None = None
    ctr: float | None = None
    serie_jours: list[dict] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    appareils: list[dict] = field(default_factory=list)
    demographie: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ecrit_le: str = ""


def lignes(charge: dict) -> list[dict]:
    """Tableau de rapport → lignes nommées (les en-têtes de l'API font foi)."""
    entetes = [
        str(colonne.get("name") or "")
        for colonne in charge.get("columnHeaders") or []
        if isinstance(colonne, dict)
    ]
    resultat: list[dict] = []
    for ligne in charge.get("rows") or []:
        if isinstance(ligne, (list, tuple)) and entetes:
            resultat.append(dict(zip(entetes, ligne)))
    return resultat


def _nombre(valeur: Any) -> float:
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return 0.0


def _entier(valeur: Any) -> int:
    return int(round(_nombre(valeur)))


def plage(jours: int = 28, aujourd_hui: date | None = None) -> tuple[str, str]:
    """Fenêtre glissante terminée hier (les rapports ont un décalage de 1 à 2 jours)."""
    fin = (aujourd_hui or date.today()) - timedelta(days=1)
    debut = fin - timedelta(days=max(1, int(jours)) - 1)
    return debut.isoformat(), fin.isoformat()


def duree_iso(texte: str) -> int:
    """`PT1H4M32S` → secondes (0 si le format n'est pas reconnu)."""
    trouve = re.fullmatch(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(texte or "")
    )
    if not trouve:
        return 0
    jours, heures, minutes, secondes = (int(valeur or 0) for valeur in trouve.groups())
    return jours * 86400 + heures * 3600 + minutes * 60 + secondes


def _premier(charge: dict) -> dict:
    trouve = lignes(charge)
    return trouve[0] if trouve else {}


def collecter(
    client: StudioClient,
    jours: int = 28,
    max_videos: int = 50,
    aujourd_hui: date | None = None,
) -> Tableau:
    """Rassemble les indicateurs visibles dans YouTube Studio.

    Les rapports facultatifs (impressions, démographie) peuvent être refusés
    sans faire échouer le tableau : la note correspondante est conservée.
    """
    debut, fin = plage(jours, aujourd_hui)
    tableau = Tableau(
        debut=debut,
        fin=fin,
        ecrit_le=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    infos = client.chaine()
    snippet = infos.get("snippet") or {}
    statistiques = infos.get("statistics") or {}
    tableau.chaine = str(snippet.get("title") or "")
    tableau.chaine_id = str(infos.get("id") or "")
    tableau.abonnes = _entier(statistiques.get("subscriberCount"))
    tableau.vues_totales = _entier(statistiques.get("viewCount"))
    tableau.nombre_videos = _entier(statistiques.get("videoCount"))

    totaux = _premier(
        client.analytics(
            startDate=debut,
            endDate=fin,
            metrics=(
                "views,estimatedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,subscribersGained,subscribersLost,"
                "likes,comments,shares"
            ),
        )
    )
    tableau.vues = _entier(totaux.get("views"))
    tableau.minutes = _nombre(totaux.get("estimatedMinutesWatched"))
    tableau.duree_moyenne_s = _nombre(totaux.get("averageViewDuration"))
    tableau.pourcentage_vu = _nombre(totaux.get("averageViewPercentage"))
    tableau.abonnes_gagnes = _entier(totaux.get("subscribersGained"))
    tableau.abonnes_perdus = _entier(totaux.get("subscribersLost"))
    tableau.jaime = _entier(totaux.get("likes"))
    tableau.commentaires = _entier(totaux.get("comments"))
    tableau.partages = _entier(totaux.get("shares"))

    try:
        quotidien = client.analytics(
            startDate=debut,
            endDate=fin,
            dimensions="day",
            metrics="views,estimatedMinutesWatched",
            sort="day",
        )
        tableau.serie_jours = [
            {
                "jour": str(ligne.get("day") or ""),
                "vues": _entier(ligne.get("views")),
                "minutes": _nombre(ligne.get("estimatedMinutesWatched")),
            }
            for ligne in lignes(quotidien)
        ]
    except ErreurStudio as probleme:
        tableau.notes.append(f"Série quotidienne indisponible : {probleme}")

    try:
        rapport_videos = client.analytics(
            startDate=debut,
            endDate=fin,
            dimensions="video",
            metrics=(
                "views,estimatedMinutesWatched,averageViewDuration,"
                "averageViewPercentage,likes,comments,shares"
            ),
            sort="-views",
            maxResults=max(1, min(200, int(max_videos))),
        )
        rangees = lignes(rapport_videos)
    except ErreurStudio as probleme:
        tableau.notes.append(f"Vidéos indisponibles : {probleme}")
        rangees = []

    impressions: dict[str, tuple[int, float]] = {}
    try:
        rapport_impressions = client.analytics(
            startDate=debut,
            endDate=fin,
            dimensions="video",
            metrics="videoThumbnailImpressions,videoThumbnailImpressionsClickRate",
            sort="-videoThumbnailImpressions",
            maxResults=max(1, min(200, int(max_videos))),
        )
        for ligne in lignes(rapport_impressions):
            identifiant = str(ligne.get("video") or "")
            if identifiant:
                impressions[identifiant] = (
                    _entier(ligne.get("videoThumbnailImpressions")),
                    _nombre(ligne.get("videoThumbnailImpressionsClickRate")),
                )
    except ErreurStudio as probleme:
        tableau.notes.append(
            "Impressions et taux de clic indisponibles (l'API les réserve aux "
            f"vidéos éligibles) : {probleme}"
        )

    details: dict[str, dict] = {}
    identifiants = [str(ligne.get("video") or "") for ligne in rangees]
    try:
        details = client.videos(identifiants)
    except ErreurStudio as probleme:
        tableau.notes.append(
            "Titres et durées des vidéos indisponibles depuis la Data API : "
            f"{probleme}"
        )

    for ligne in rangees:
        identifiant = str(ligne.get("video") or "")
        if not identifiant:
            continue
        item = details.get(identifiant) or {}
        meta = item.get("snippet") or {}
        direct = str(meta.get("liveBroadcastContent") or "none")
        if direct == "none" and (item.get("liveStreamingDetails") or {}).get("actualStartTime"):
            direct = "was_live"
        impressions_video = impressions.get(identifiant)
        video = Video(
            identifiant=identifiant,
            titre=str(meta.get("title") or identifiant),
            publie_le=str(meta.get("publishedAt") or ""),
            duree_s=duree_iso((item.get("contentDetails") or {}).get("duration", "")),
            vues=_entier(ligne.get("views")),
            minutes=_nombre(ligne.get("estimatedMinutesWatched")),
            duree_moyenne_s=_nombre(ligne.get("averageViewDuration")),
            pourcentage_vu=_nombre(ligne.get("averageViewPercentage")),
            jaime=_entier(ligne.get("likes")),
            commentaires=_entier(ligne.get("comments")),
            partages=_entier(ligne.get("shares")),
            impressions=impressions_video[0] if impressions_video else None,
            ctr=impressions_video[1] if impressions_video else None,
            live=direct,
        )
        tableau.videos.append(video)

    if impressions:
        total_impressions = sum(valeur[0] for valeur in impressions.values())
        total_clics = sum(valeur[0] * valeur[1] / 100 for valeur in impressions.values())
        tableau.impressions = total_impressions
        tableau.ctr = (total_clics / total_impressions * 100) if total_impressions else None

    for dimension, metrique, destination in (
        ("insightTrafficSourceType", "views", "sources"),
        ("deviceType", "views", "appareils"),
    ):
        try:
            charge = client.analytics(
                startDate=debut,
                endDate=fin,
                dimensions=dimension,
                metrics=metrique,
                sort=f"-{metrique}",
            )
            setattr(
                tableau,
                destination,
                [
                    {
                        "cle": str(ligne.get(dimension) or ""),
                        "valeur": _nombre(ligne.get(metrique)),
                    }
                    for ligne in lignes(charge)
                ],
            )
        except ErreurStudio as probleme:
            tableau.notes.append(f"Répartition {dimension} indisponible : {probleme}")

    try:
        charge = client.analytics(
            startDate=debut,
            endDate=fin,
            dimensions="ageGroup,gender",
            metrics="viewerPercentage",
            sort="-viewerPercentage",
        )
        tableau.demographie = [
            {
                "age": str(ligne.get("ageGroup") or ""),
                "genre": str(ligne.get("gender") or ""),
                "pourcentage": _nombre(ligne.get("viewerPercentage")),
            }
            for ligne in lignes(charge)
        ]
    except ErreurStudio as probleme:
        tableau.notes.append(f"Audience (âge et genre) indisponible : {probleme}")

    return tableau


def type_video(video: dict) -> str:
    """Classe une vidéo Studio : ``short``, ``longue`` ou ``autre``.

    Un direct ou un replay va dans « autre » pour ne pas le compter comme une
    vidéo longue classique. Une durée inconnue reste également à part.
    """
    live = str(video.get("live") or "none").lower()
    if live not in ("none", ""):
        return "autre"
    try:
        duree = float(video.get("duree_s") or 0)
    except (TypeError, ValueError):
        duree = 0
    if duree <= 0:
        return "autre"
    return "short" if duree <= 180 else "longue"


def filtrer_videos(videos: list[dict], genre: str) -> list[dict]:
    """Vidéos d'un onglet, dans l'ordre reçu."""
    return [video for video in videos if isinstance(video, dict) and type_video(video) == genre]


def retention(
    client: StudioClient,
    video_id: str,
    debut: str,
    fin: str,
) -> list[dict]:
    """Courbe de rétention d'une vidéo : ratio du temps écoulé → audience restante."""
    if not video_id:
        return []
    charge = client.analytics(
        startDate=debut,
        endDate=fin,
        dimensions="elapsedVideoTimeRatio",
        metrics="audienceWatchRatio,relativeRetentionPerformance",
        filters=f"video=={video_id}",
    )
    points: list[dict] = []
    vus: set[float] = set()
    for ligne in lignes(charge):
        ratio = min(1.0, max(0.0, _nombre(ligne.get("elapsedVideoTimeRatio"))))
        if ratio in vus:
            continue
        vus.add(ratio)
        points.append(
            {
                "ratio": ratio,
                "maintien": max(0.0, _nombre(ligne.get("audienceWatchRatio"))),
                "relatif": _nombre(ligne.get("relativeRetentionPerformance")),
            }
        )
    points.sort(key=lambda point: point["ratio"])
    return points[:500]


# ---------------------------------------------------------------------------
# Instantané local (sans secret)
# ---------------------------------------------------------------------------


def chemin_cache(racine: Path) -> Path:
    return Path(racine) / "data" / "studio.json"


def ecrire_cache(racine: Path, tableau: Tableau) -> Path:
    chemin = chemin_cache(racine)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    charge = asdict(tableau)
    temporaire = chemin.with_name(chemin.name + ".tmp")
    temporaire.write_text(
        json.dumps(charge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporaire.replace(chemin)
    return chemin


def lire_cache(racine: Path) -> dict | None:
    chemin = chemin_cache(racine)
    if not chemin.is_file():
        return None
    try:
        charge = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(charge, dict):
        return None
    if not isinstance(charge.get("videos"), list):
        charge["videos"] = []
    for cle in ("serie_jours", "sources", "appareils", "demographie", "notes"):
        if not isinstance(charge.get(cle), list):
            charge[cle] = []
    return charge


def identifiants(env: dict[str, str]) -> dict[str, str]:
    """Identifiants OAuth lus dans `.env` (jamais affichés en clair)."""
    return {
        "client_id": (env.get("YOUTUBE_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (env.get("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip(),
        "refresh_token": (env.get("YOUTUBE_REFRESH_TOKEN") or "").strip(),
    }


__all__ = [
    "FluxOauth",
    "SCOPES",
    "StudioClient",
    "Tableau",
    "Video",
    "collecter",
    "duree_iso",
    "echanger_code",
    "ecrire_cache",
    "identifiants",
    "lignes",
    "lire_cache",
    "plage",
    "rafraichir_jeton",
    "retention",
    "transport_get",
    "transport_post",
    "type_video",
    "filtrer_videos",
    "url_autorisation",
]
