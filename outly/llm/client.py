"""Client minimal pour toute API « OpenAI Chat Completions ».

Pourquoi pas le SDK officiel : il ajoute une dépendance lourde et masque les
temporisations. Ici, tout est explicite — rythme local, respect de `Retry-After`,
dégradation du mode JSON et bascule vers un second fournisseur.

La clé n'est jamais journalisée ni incluse dans un message d'erreur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from ..config import Config, url_locale


class ErreurLlm(Exception):
    """Erreur normalisée, affichable telle quelle à l'utilisateur."""


class LimiteDebit(ErreurLlm):
    """Le fournisseur refuse temporairement pour cause de débit."""


@dataclass
class Fournisseur:
    nom: str  # « principal » ou « secours »
    base_url: str
    modele: str
    cle: str


@dataclass
class ReponseLlm:
    texte: str
    modele: str
    fournisseur: str
    brut: dict = field(default_factory=dict)
    mode_json: str = "auto"


@dataclass
class ReponseHttpLlm:
    statut: int
    charge: dict | list = field(default_factory=dict)
    entetes: dict = field(default_factory=dict)


def transport_requests(
    url: str, entetes: dict, corps: dict | None, timeout: int
) -> ReponseHttpLlm:
    """Transport réel. `corps` à None signifie un GET (liste des modèles)."""
    if corps is None:
        reponse = requests.get(url, headers=entetes, timeout=timeout)
    else:
        reponse = requests.post(url, headers=entetes, json=corps, timeout=timeout)
    try:
        charge = reponse.json()
    except ValueError:
        charge = {}
    return ReponseHttpLlm(
        reponse.status_code,
        charge if isinstance(charge, (dict, list)) else {},
        dict(reponse.headers),
    )


def _message_erreur(charge: dict | list) -> str:
    """Message d'erreur lisible, y compris pour les réponses en tableau.

    Gemini renvoie parfois `[{"error": {...}}]` : sans ce déballage, l'erreur
    devenait « réponse vide » et masquait la vraie cause (clé refusée, palier
    indisponible, modèle inconnu).
    """
    if isinstance(charge, list):
        for element in charge:
            if isinstance(element, dict) and element.get("error"):
                return _message_erreur(element)
        return str(charge or "réponse vide")[:300]
    erreur = (charge or {}).get("error")
    if isinstance(erreur, dict):
        return str(erreur.get("message") or erreur.get("type") or erreur)
    if isinstance(erreur, str):
        return erreur
    return str(charge or "réponse vide")[:300]


def _retry_after(reponse: ReponseHttpLlm) -> float | None:
    valeur = (reponse.entetes or {}).get("Retry-After")
    if not valeur:
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


class FournisseurLlm:
    """Un fournisseur : endpoint, modèle, clé et politique de temporisation."""

    def __init__(
        self,
        fournisseur: Fournisseur,
        config: Config,
        transport: Callable[[str, dict, dict, int], ReponseHttpLlm] | None = None,
        dormir: Callable[[float], None] = time.sleep,
        horloge: Callable[[], float] = time.monotonic,
    ):
        self.fournisseur = fournisseur
        self._config = config
        self._transport = transport or transport_requests
        self._dormir = dormir
        self._horloge = horloge
        self._dernier_appel: float | None = None
        self._mode_json = config.llm.json_mode
        self._degradations = 0

    @property
    def nom(self) -> str:
        return f"{self.fournisseur.nom}:{self.fournisseur.modele}"

    def _attendre_rythme(self) -> None:
        """Respecte un débit maximal configurable (le quota gratuit est bas)."""
        rpm = self._config.llm.requests_per_minute
        if rpm <= 0:
            return
        intervalle = 60.0 / rpm
        maintenant = self._horloge()
        if self._dernier_appel is not None:
            reste = intervalle - (maintenant - self._dernier_appel)
            if reste > 0:
                self._dormir(reste)
                maintenant = self._horloge() + reste
        self._dernier_appel = maintenant

    def _corps(self, messages: list[dict]) -> dict:
        corps: dict = {
            "model": self.fournisseur.modele,
            "messages": messages,
            "temperature": self._config.llm.temperature,
        }
        if self._mode_json in ("auto", "json_object"):
            corps["response_format"] = {"type": "json_object"}
        return corps

    def completer(self, messages: list[dict]) -> ReponseLlm:
        url = f"{self.fournisseur.base_url.rstrip('/')}/chat/completions"
        entetes = {"Content-Type": "application/json"}
        # Ollama local ignore l'authentification : envoyer « Bearer » vide
        # ferait échouer certains proxys stricts. On omet l'en-tête.
        if self.fournisseur.cle:
            entetes["Authorization"] = f"Bearer {self.fournisseur.cle}"
        tentatives = 0
        derniere_limite = False

        while tentatives <= self._config.llm.max_retries:
            self._attendre_rythme()
            try:
                reponse = self._transport(
                    url, entetes, self._corps(messages), self._config.llm.timeout_s
                )
            except requests.RequestException as erreur:
                derniere_limite = False
                if tentatives >= self._config.llm.max_retries:
                    raise ErreurLlm(
                        f"Connexion au fournisseur LLM impossible ({erreur.__class__.__name__}). "
                        "Vérifiez l'accès réseau et LLM_BASE_URL."
                    ) from erreur
                tentatives += 1
                self._dormir(2**tentatives)
                continue

            if reponse.statut == 200:
                return self._lire(reponse)

            message = _message_erreur(reponse.charge)

            # Certains endpoints de compatibilité refusent `response_format`.
            # On le retire une seule fois, puis on continue sans.
            if (
                reponse.statut == 400
                and self._mode_json in ("auto", "json_object")
                and self._degradations == 0
                and (
                    "response_format" in message.lower()
                    or "format" in message.lower()
                )
            ):
                self._degradations += 1
                self._mode_json = "none"
                continue

            if reponse.statut == 429:
                derniere_limite = True
                if tentatives >= self._config.llm.max_retries:
                    raise LimiteDebit(
                        f"Limite de débit atteinte chez {self.nom} après "
                        f"{tentatives + 1} tentatives : {message}. "
                        "Réduisez llm.requests_per_minute ou attendez la réinitialisation "
                        "du palier gratuit."
                    )
                tentatives += 1
                self._dormir(min(_retry_after(reponse) or 5.0 * tentatives, 60.0))
                continue

            if reponse.statut in (401, 403):
                aide = "Vérifiez LLM_API_KEY et les droits du compte."
                if "billing" in message.lower() or "free tier" in message.lower():
                    aide = (
                        "Le palier gratuit de ce fournisseur n'est pas disponible pour "
                        "ce projet : activez la facturation dans la console du fournisseur, "
                        "ou renseignez LLM_FALLBACK_* pour un autre fournisseur. Le rapport "
                        "reste exploitable avec le score de proximité local."
                    )
                raise ErreurLlm(
                    f"Clé LLM refusée par {self.nom} ({reponse.statut}) : "
                    f"{message.rstrip('.')}. {aide}"
                )
            if reponse.statut == 404:
                raise ErreurLlm(
                    f"Modèle introuvable chez {self.nom} : « {self.fournisseur.modele} ». "
                    "Lancez : python -m outly doctor --probe  pour lister les modèles "
                    "réellement disponibles sur votre compte."
                )
            if reponse.statut >= 500:
                derniere_limite = False
                if tentatives >= self._config.llm.max_retries:
                    raise ErreurLlm(
                        f"Le fournisseur LLM a renvoyé {reponse.statut} : {message}. "
                        "Reessayez plus tard."
                    )
                tentatives += 1
                self._dormir(2**tentatives)
                continue

            raise ErreurLlm(
                f"Requête LLM refusée ({reponse.statut}) : {message}"
            )

        if derniere_limite:
            raise LimiteDebit(f"Limite de débit persistante chez {self.nom}.")
        raise ErreurLlm(f"Échec de l'appel au LLM après {tentatives} tentatives.")

    def _lire(self, reponse: ReponseHttpLlm) -> ReponseLlm:
        if not isinstance(reponse.charge, dict):
            raise ErreurLlm(f"Réponse inattendue de {self.nom} (format non objet).")
        choix = reponse.charge.get("choices") or []
        contenu: object = None
        if choix and isinstance(choix[0], dict):
            message = choix[0].get("message") or {}
            contenu = message.get("content")
            if isinstance(contenu, list):
                contenu = "".join(
                    partie.get("text", "") for partie in contenu if isinstance(partie, dict)
                )
        if not isinstance(contenu, str) or not contenu.strip():
            raise ErreurLlm(
                f"Réponse vide du fournisseur {self.nom}. "
                "Le modèle a peut-être refusé la requête (filtre de contenu)."
            )
        return ReponseLlm(
            texte=contenu,
            modele=self.fournisseur.modele,
            fournisseur=self.fournisseur.nom,
            brut=reponse.charge,
            mode_json=self._mode_json,
        )

    def lister_modeles(self) -> list[str]:
        """Liste les modèles visibles par la clé (utile pour `doctor --probe`).

        GET et non POST : certains fournisseurs (Gemini) répondent 404 à un
        POST sur /models. Les identifiants « models/xxx » sont normalisés.
        """
        url = f"{self.fournisseur.base_url.rstrip('/')}/models"
        entetes = {}
        if self.fournisseur.cle:
            entetes["Authorization"] = f"Bearer {self.fournisseur.cle}"
        reponse = self._transport(
            url,
            entetes,
            None,
            self._config.llm.timeout_s,
        )
        if reponse.statut != 200:
            raise ErreurLlm(
                f"Impossible de lister les modèles ({reponse.statut}) : "
                f"{_message_erreur(reponse.charge)}"
            )
        if isinstance(reponse.charge, list):
            donnees = reponse.charge
        else:
            donnees = reponse.charge.get("data") or reponse.charge.get("models") or []
        noms = []
        for element in donnees:
            if isinstance(element, dict):
                nom = element.get("id") or element.get("name")
            else:
                nom = element
            if isinstance(nom, str) and nom.strip():
                noms.append(nom.removeprefix("models/"))
        return noms


class ServiceLlm:
    """Un fournisseur principal et, si configuré, un secours automatique."""

    def __init__(self, principal: FournisseurLlm, secours: FournisseurLlm | None = None):
        self._principal = principal
        self._secours = secours
        self.appels = 0
        self.basculees = 0
        self.messages: list[str] = []

    @property
    def nom_modele(self) -> str:
        return self._principal.fournisseur.modele

    @property
    def nom_fournisseur(self) -> str:
        return self._principal.fournisseur.nom

    def completer(self, messages: list[dict]) -> ReponseLlm:
        self.appels += 1
        try:
            return self._principal.completer(messages)
        except ErreurLlm as erreur:
            self.messages.append(f"{self._principal.nom} : {erreur}")
            if self._secours is None:
                raise
            self.basculees += 1
            self.appels += 1
            self.messages.append(
                f"Bascule automatique vers {self._secours.nom} après l'échec du fournisseur principal."
            )
            return self._secours.completer(messages)

    def lister_modeles(self) -> list[str]:
        return self._principal.lister_modeles()


def construire_service(config: Config) -> ServiceLlm:
    """Construit le service à partir de .env, avec un secours optionnel.

    Un serveur local (Ollama sur `http://localhost:11434/v1`) n'a pas de clé :
    `LLM_API_KEY` n'est donc exigée que pour un fournisseur distant.
    """
    manquants = [
        nom
        for nom, valeur in (
            ("LLM_BASE_URL", config.llm_base_url),
            ("LLM_MODEL", config.llm_modele),
        )
        if not valeur
    ]
    if not config.llm_cle and not url_locale(config.llm_base_url):
        manquants.append("LLM_API_KEY")
    if manquants:
        raise ErreurLlm(
            "LLM non configuré : renseignez " + ", ".join(manquants) + " dans .env."
        )
    principal = FournisseurLlm(
        Fournisseur(
            "principal",
            config.llm_base_url,
            config.llm_modele,
            config.llm_cle or "",
        ),
        config,
    )
    secours = None
    if (
        config.llm.fallback_enabled
        and config.llm_secours_base_url
        and config.llm_secours_modele
        and (config.llm_secours_cle or url_locale(config.llm_secours_base_url))
    ):
        secours = FournisseurLlm(
            Fournisseur(
                "secours",
                config.llm_secours_base_url,
                config.llm_secours_modele,
                config.llm_secours_cle or "",
            ),
            config,
        )
    return ServiceLlm(principal, secours)
