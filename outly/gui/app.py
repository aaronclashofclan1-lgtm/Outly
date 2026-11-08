"""Fenêtre principale d'Outly (CustomTkinter).

L'interface est un poste de pilotage, pas un second moteur :
  * elle édite les mêmes fichiers que la CLI (`.env`, `config.yaml`,
    `positioning.md`, `seeds.txt`, `keywords.txt`) ;
  * elle lance les mêmes commandes, dans un sous-processus, et affiche leur
    sortie en direct ;
  * elle relit la base SQLite pour présenter les résultats.

Tkinter n'est pas thread-safe : aucun fil de travail ne touche aux widgets.
Le sous-processus communique par une `queue.Queue`, vidée par `after()`.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from urllib.parse import urlparse

import customtkinter as ctk

from .. import (
    __version__,
    cerveau,
    studio,
    emailing,
    idees,
    notes,
    plan,
    planification,
    playlist,
    report,
    transcripts,
    comparaison,
)
from ..config import racine_projet, url_locale
from . import data, disposition, documents, icones, settings
from .reseau import CarteCerveau
from .spinner import Spinner
from .runner import (
    CODES,
    Fin,
    Tache,
    arguments_brain,
    arguments_doctor,
    arguments_etape,
    arguments_idees,
    arguments_production,
    arguments_run,
    arguments_strategie,
    estimation_execution,
    valider_config,
)

# --- Palette « studio de création » (prune, corail, ambre) ------------------
# La même famille de teintes porte les deux thèmes. Chaque couleur est un
# couple (clair, sombre) que CustomTkinter résout tout seul : changer
# d'apparence ne reconstruit aucun widget, aucune page n'est perdue, aucun
# champ n'est vidé.
#
# Le sombre est l'ambiance par défaut : fond prune très profond, surfaces
# légèrement plus chaudes, corail pour l'action, ambre pour l'alerte et vert
# signal pour la réussite. Le clair reprend un papier chaud, comme une table
# de montage éclairée : le corail y garde le même rôle.
FOND = ("#FBF4EE", "#160D12")
SURFACE = ("#FFFDFB", "#21131A")
SURFACE_HAUTE = ("#F6E9DF", "#2C1A23")
BORDURE = ("#EBD8CA", "#402530")
CHAMP = ("#FFF9F4", "#1B1016")
TEXTE = ("#2A1620", "#F7E9E1")
DOUX = ("#7A4A55", "#D3ADA8")
DOUX_FONCE = ("#5E3540", "#BE938F")
ACCENT = ("#C2402F", "#F26B4F")
ACCENT_SURVOL = ("#A93224", "#FF8368")
ACCENT_CLAIR = ("#D95A45", "#FF8B72")
ACCENT_DOUX = ("#FBE7DF", "#3A1D22")
ACCENT_TEXTE = ("#9C2F22", "#FFB09D")
TEXTE_SUR_ACCENT = ("#FFF8F4", "#2A0F0B")
OMBRE = ("#EFE0D5", "#0F070B")
OK = ("#1F7A5C", "#74D6B4")
OK_DOUX = ("#E7F5EF", "#14332B")
ALERTE = ("#9A5B00", "#F0C070")
ALERTE_DOUX = ("#FDF1DF", "#33270F")
ERREUR = ("#A3271B", "#FF9B8A")
ERREUR_DOUX = ("#FBE7E4", "#3A1A16")
# Journal : fond dense dans les deux thèmes, texte clair dans les deux cas.
JOURNAL_FOND = ("#2A1620", "#150C11")
JOURNAL_TEXTE = ("#FBF0E8", "#F3E3D9")
# Barre latérale identique dans les deux thèmes : elle porte la teinte la plus
# dense, et le logo (Tk natif, qui n'accepte pas les couleurs à deux états)
# s'y fond sans rectangle parasite.
BARRE_LATERALE = "#3A1B28"
SIDEBAR_TEXTE = "#FFF6EE"
SIDEBAR_DOUX = "#E3C7BF"
SIDEBAR_HOVER = "#4E2434"
SIDEBAR_ACTIF = "#C2402F"

# Libellés du sélecteur d'apparence → valeur attendue par CustomTkinter.
THEMES = {"Clair": "light", "Sombre": "dark"}
THEMES_INVERSES = {valeur: libelle for libelle, valeur in THEMES.items()}
TEXTES_TABLEAU = {"Normale": "normal", "Grande": "grand"}
TEXTES_TABLEAU_INVERSES = {valeur: libelle for libelle, valeur in TEXTES_TABLEAU.items()}


def _couleur(valeur):
    """Couleur unique pour ce que Tk et ttk ne savent pas colorer par thème.

    CustomTkinter résout seul les couples (clair, sombre) ; `ttk.Style`, les
    balises de `Treeview` et les rares widgets Tk natifs, eux, n'acceptent
    qu'une chaîne. On leur donne donc la moitié correspondant à l'ambiance
    active.
    """
    if isinstance(valeur, tuple):
        return valeur[0] if ctk.get_appearance_mode() == "Light" else valeur[1]
    return valeur

# Fournisseurs proposés dans « Clés & modèle ». Chaque entrée préremplit
# l'URL de base ET un modèle connu : l'utilisateur n'a plus qu'à coller sa clé.
# Ollama local ne demande aucune clé (le champ peut rester vide).
FOURNISSEURS = {
    "Gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.5-flash-lite"),
    "Groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    "Mistral": ("https://api.mistral.ai/v1", "ministral-8b-latest"),
    "Ollama Cloud": ("https://ollama.com/v1", "gemma4:31b"),
    "Ollama local": ("http://localhost:11434/v1", "gpt-oss:20b"),
}
FOURNISSEURS_SECOURS = ("Aucun", *FOURNISSEURS, "Personnalisé")

# Colonnes du tableau des résultats. `triable` active le clic sur l'en-tête.
COLONNES_RESULTATS = (
    ("score", "Score", 56, True),
    ("chaine", "Chaîne", 136, True),
    ("abonnes", "Abonnés", 72, True),
    ("mediane", "Médiane", 80, True),
    ("outlier", "Meilleur ×", 78, True),
    ("statut", "Statut", 104, True),
)

# Réglages exposés. Le reste de config.yaml reste éditable à la main.
REGLAGES_DECOUVERTE = [
    ("youtube", "max_search_calls_per_run", "Appels de recherche", 0, 100),
    ("youtube", "max_units_per_run", "Budget d'unités par exécution", 0, 10000),
    ("youtube", "videos_per_channel", "Vidéos analysées par chaîne", 1, 50),
    ("youtube", "subscriber_min", "Abonnés minimum", 0, 10**9),
    ("youtube", "subscriber_max", "Abonnés maximum", 0, 10**10),
    ("youtube", "min_channel_age_days", "Âge minimum (jours)", 0, 3650),
    ("youtube", "analysis_limit", "Chaînes enrichies par exécution", 1, 1000),
]
REGLAGES_LLM = [
    ("metrics", "short_max_seconds", "Durée max d'un Short (s)", 1, 3600),
    ("llm", "max_channels_per_run", "Chaînes scorées par le LLM", 1, 1000),
    ("llm", "min_score_keep", "Score minimum pour retenir", 0, 100),
    ("llm", "requests_per_minute", "Requêtes LLM par minute", 1, 600),
    ("hooks", "variants", "Variantes de hooks", 1, 12),
    ("hooks", "max_videos", "Vidéos outliers analysées", 1, 20),
]
BASCULES = [
    ("discovery", "use_channel_sections", "Explorer les chaînes mises en avant"),
    ("discovery", "use_description_links", "Explorer les liens des descriptions"),
    ("llm", "fallback_enabled", "Basculer sur le LLM de secours"),
    ("transcripts", "enabled", "Lire les sous-titres (non officiel)"),
    ("hooks", "enabled", "Générer les hooks après chaque veille"),
]

PAGES = {
    "audit": ("Audit", "Votre chaîne, votre secteur, vos concurrents et votre stratégie."),
    "strategie": (
        "Stratégie",
        "Ce que disent vos dernières vidéos, et ce qu'il faut ajuster.",
    ),
    "accueil": ("Accueil", "L'état de votre radar, en un coup d'œil."),
    "studio": (
        "Studio YouTube",
        "Vues, rétention, formats et évolution des concurrents.",
    ),
    "lancer": ("Lancer", "Exécutez la veille et suivez le journal en direct."),
    "resultats": ("Chaînes", "Les concurrents classés par proximité avec vous."),
    "comparateur": (
        "Comparateur",
        "Deux ou trois concurrents, les mêmes indicateurs côte à côte.",
    ),
    "hooks": ("Hooks", "Des accroches inspirées des outliers, adaptées à votre sujet."),
    "cerveau": (
        "Cerveau IA",
        "Croisez notes, recherches, idées et signaux du marché pour trouver des angles.",
    ),
    "idees": ("Idées", "Des sujets de vidéos, chacun avec trois titres à angles différents."),
    "recherche": (
        "Recherche",
        "Un titre, des mots-clés et des sources : Wikipédia seule ou avec l'IA.",
    ),
    "notes": ("Notes", "Votre mémoire éditoriale : hooks, concepts, sujets et références."),
    "regarder": ("À regarder", "Les vidéos et chaînes que vous avez mises de côté."),
    "plan": ("Plan éditorial", "Vos prochaines vidéos, de l'idée à la publication."),
    "contenu": ("Mon contenu", "Votre positionnement, vos seeds et vos mots-clés."),
    "cles": ("Clés & modèle", "Les accès aux services, conservés sur ce PC."),
    "reglages": ("Réglages", "Apparence, budgets, filtres et options d'exploration."),
    "email": ("Rapports e-mail", "Recevez vos analyses, à la fréquence de votre choix."),
    "schemas": (
        "Schémas vidéo",
        "Préparer les plans, le cadre, la caméra et les textes d'une vidéo.",
    ),
    "tableau": (
        "Tableau blanc",
        "Dessiner et organiser vos idées librement, sans quitter Outly.",
    ),
}
# Navigation par groupes : le parcours (pilotage → analyse → bibliothèque →
# réglages) se lit de haut en bas, au lieu d'une liste plate de dix entrées.
HUBS_ONGLETS = {
    "diagnostic": (
        "Audit & stratégie",
        (("audit", "Audit"), ("strategie", "Stratégie")),
    ),
    "production": (
        "Studio & plan",
        (("studio", "Studio YouTube"), ("plan", "Plan éditorial"), ("schemas", "Schémas vidéo")),
    ),
    "veille": (
        "Chaînes & veille",
        (("resultats", "Chaînes"), ("comparateur", "Comparateur"), ("regarder", "À regarder")),
    ),
    "carnet": (
        "Notes & tableau",
        (("notes", "Notes"), ("tableau", "Tableau blanc")),
    ),
    "atelier": (
        "Création",
        (("recherche", "Recherche"), ("idees", "Idées"), ("hooks", "Hooks"), ("cerveau", "Cerveau IA")),
    ),
    "configuration": (
        "Réglages",
        (("reglages", "Réglages"), ("cles", "Clés & modèle")),
    ),
}
PAGE_VERS_HUB = {
    page: hub for hub, (_titre, entrees) in HUBS_ONGLETS.items() for page, _libelle in entrees
}
NAVIGATION_GROUPES = (
    (
        "PILOTAGE",
        (
            ("diagnostic", "Audit & stratégie"),
            ("accueil", "Accueil"),
            ("production", "Studio & plan"),
            ("lancer", "Lancer"),
        ),
    ),
    (
        "ANALYSE",
        (
            ("veille", "Chaînes & veille"),
            ("atelier", "Création"),
        ),
    ),
    (
        "BIBLIOTHÈQUE",
        (("carnet", "Notes & tableau"), ("email", "Rapports e-mail")),
    ),
    (
        "CONFIGURATION",
        (("contenu", "Mon contenu"), ("configuration", "Réglages")),
    ),
)
NAVIGATION = [entree for _groupe, entrees in NAVIGATION_GROUPES for entree in entrees]

FREQUENCES_UI = {
    "Jamais": "jamais",
    "Quotidien": "quotidien",
    "Hebdomadaire": "hebdomadaire",
    "Mensuel": "mensuel",
}
FREQUENCES_INVERSES = {valeur: libelle for libelle, valeur in FREQUENCES_UI.items()}
JOURS_UI = tuple(jour.capitalize() for jour in emailing.JOURS_SEMAINE)
# Valeurs proposées quand la section est absente d'un config.yaml plus ancien.
DEFAUTS_EMAIL = {"heure": "08:00", "jour_mois": "1"}
DEFAUTS_REGLAGES = {("hooks", "variants"): 6, ("hooks", "max_videos"): 6}

# Entrée neutre des menus du comparateur : elle n'est jamais associée à une chaîne.
COMPARATEUR_VIDE = "— aucune —"

NOMS_EDITEURS = {"positioning": "Positionnement", "seeds": "Seeds", "keywords": "Mots-clés"}
CLES_EDITEURS = {valeur: cle for cle, valeur in NOMS_EDITEURS.items()}

AIDE_EDITEURS = {
    "positioning": (
        "Décrivez votre client idéal, votre promesse, votre ton, vos formats, votre "
        "différenciation, les sujets proches et les sujets exclus. C'est le document "
        "envoyé au LLM pour juger la proximité (200 caractères minimum). "
        "« Importer » accepte Markdown, texte, JSON, HTML, OpenDocument et PDF."
    ),
    "seeds": (
        "Une chaîne par ligne : @handle, URL de chaîne ou identifiant UC… . "
        "Ces chaînes servent de point de départ et de référence de niche. "
        "L'import ne garde que les lignes courtes du document."
    ),
    "keywords": (
        "Une expression par ligne. Chaque ligne est cherchée en France puis dans le "
        "monde (1 appel du compartiment de 100 par passe). "
        "L'import ne garde que les lignes courtes du document."
    ),
}


def _nombre(valeur: float | int | None, decimales: int = 0) -> str:
    if valeur is None:
        return "—"
    if isinstance(valeur, float) and not valeur.is_integer():
        return f"{valeur:,.{decimales or 1}f}".replace(",", " ").replace(".", ",")
    return f"{int(valeur):,}".replace(",", " ")


def _duree(secondes: int | float | None) -> str:
    """Durée d'une vidéo au format 4:32 ou 1:04:32."""
    if secondes is None:
        return ""
    total = max(0, int(secondes))
    heures, reste = divmod(total, 3600)
    minutes, secondes = divmod(reste, 60)
    if heures:
        return f"{heures}:{minutes:02d}:{secondes:02d}"
    return f"{minutes}:{secondes:02d}"


def _date_lisible(valeur: str | None) -> str:
    """Date ISO (UTC en base) affichée dans le fuseau horaire du poste."""
    if not valeur:
        return "—"
    try:
        return (
            datetime.fromisoformat(valeur.replace("Z", "+00:00"))
            .astimezone()
            .strftime("%d/%m/%Y %H:%M")
        )
    except ValueError:
        return valeur


def _lignes_utiles(texte: str) -> int:
    """Lignes réellement prises en compte par la CLI (hors vides et commentaires)."""
    total = 0
    for brute in texte.splitlines():
        ligne = brute.strip()
        if ligne and not ligne.startswith("#"):
            total += 1
    return total


class Barre(ctk.CTkProgressBar):
    """Jauge horizontale : bleu, orange puis rouge à l'approche du plafond."""

    def __init__(self, parent: tk.Misc, hauteur: int = 8):
        super().__init__(
            parent,
            height=hauteur,
            corner_radius=hauteur // 2,
            fg_color=SURFACE_HAUTE,
            progress_color=ACCENT_CLAIR,
            border_width=0,
        )
        self.set(0)

    def definir(self, valeur: int, maximum: int) -> None:
        ratio = min(1.0, max(0.0, int(valeur) / max(1, int(maximum))))
        couleur = ACCENT_CLAIR if ratio < 0.7 else (ALERTE if ratio < 0.9 else ERREUR)
        self.configure(progress_color=couleur)
        self.set(ratio)


class Vignettes:
    """Télécharge les miniatures YouTube hors du fil Tkinter.

    Les requêtes réseau ne doivent jamais bloquer l'interface : un fil par
    vidéo, un résultat poussé dans une file, puis un rappel exécuté dans le fil
    Tk. Les échecs sont mémorisés pour ne pas retenter à chaque affichage.
    """

    def __init__(self, application: "Application"):
        self._application = application
        self._file: queue.Queue = queue.Queue()
        self._cache: dict[str, object] = {}
        self._en_cours: set[str] = set()
        self._echecs: set[str] = set()
        self._attente: dict[str, list] = {}
        self._application.after(150, self._pomper)

    def demander(self, video_id: str, rappel) -> None:
        if not video_id:
            rappel(None)
            return
        if video_id in self._cache:
            rappel(self._cache[video_id])
            return
        if video_id in self._echecs:
            rappel(None)
            return
        self._attente.setdefault(video_id, []).append(rappel)
        if video_id in self._en_cours:
            return
        self._en_cours.add(video_id)
        threading.Thread(
            target=self._charger, args=(video_id,), name="outly-vignette", daemon=True
        ).start()

    def _charger(self, video_id: str) -> None:
        image = None
        try:
            import requests
            from PIL import Image
            from io import BytesIO

            reponse = requests.get(
                f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg", timeout=7
            )
            if reponse.ok and reponse.content:
                image = Image.open(BytesIO(reponse.content)).convert("RGB")
        except Exception:
            image = None
        self._file.put((video_id, image))

    def _pomper(self) -> None:
        while True:
            try:
                video_id, image = self._file.get_nowait()
            except queue.Empty:
                break
            self._en_cours.discard(video_id)
            if image is None:
                self._echecs.add(video_id)
            else:
                self._cache[video_id] = image
            for rappel in self._attente.pop(video_id, []):
                try:
                    rappel(image)
                except tk.TclError:
                    pass
                except Exception:
                    pass
        try:
            if self._application.winfo_exists():
                self._application.after(150, self._pomper)
        except tk.TclError:
            pass


def _alleger_dessins_ctk() -> None:
    """Retire les vidages complets que CustomTkinter glisse dans ses dessins.

    Dans la version 6, plusieurs widgets (`CTkScrollbar`, `CTkOptionMenu`…)
    terminent leur `_draw()` par un `update_idletasks()`. Comme un dessin en
    déclenche un autre pendant la mise en page, ce vidage rejoue tout le cycle
    des dizaines de fois : cinq secondes pour une seule page de réglages.

    Le tracé reste identique ; seul le vidage immédiat disparaît, Tk le faisant
    de toute façon à la fin du cycle d'événements en cours. Le remplacement
    n'est appliqué qu'aux classes dont le `_draw` contient réellement ce
    vidage, et une seule fois par classe.
    """
    import importlib

    modules = (
        ("ctk_scrollbar", "CTkScrollbar"),
        ("ctk_optionmenu", "CTkOptionMenu"),
        ("ctk_combobox", "CTkComboBox"),
        ("ctk_segmented_button", "CTkSegmentedButton"),
    )
    for nom_module, nom_classe in modules:
        try:
            module = importlib.import_module(
                f"customtkinter.windows.widgets.{nom_module}"
            )
        except ImportError:
            continue  # widget absent de cette version
        classe = getattr(module, nom_classe, None)
        if classe is None or getattr(classe, "_outly_allege", False):
            continue

        origine = classe._draw

        def dessin(self, *args, _origine=origine, **kwargs):
            vidages = []
            for objet in (self, getattr(self, "_canvas", None)):
                if objet is not None and hasattr(objet, "update_idletasks"):
                    vidages.append((objet, objet.update_idletasks))
                    objet.update_idletasks = lambda *a, **k: None
            try:
                return _origine(self, *args, **kwargs)
            finally:
                for objet, fonction in vidages:
                    objet.update_idletasks = fonction

        classe._draw = dessin
        classe._outly_allege = True


class Application(ctk.CTk):
    def __init__(self, racine: Path):
        self.racine = Path(racine)
        _alleger_dessins_ctk()
        # Préférences d'affichage (thème, ombres, taille du texte, démarrage) :
        # lues avant la création des widgets, pour que la fenêtre s'ouvre
        # directement dans la bonne ambiance sans reconstruction ni perte de
        # saisie.
        try:
            self._preferences = settings.lire_preferences(self.racine)
        except Exception:
            self._preferences = dict(settings.PREFERENCES_DEFAUT)
        apparence = str(self._preferences.get("theme", settings.THEME_DEFAUT))
        ctk.set_appearance_mode(apparence)
        super().__init__(fg_color=FOND)
        # Pas de `withdraw()` ici : CustomTkinter retient un appel fait avant
        # l'existence de la fenêtre (`_withdraw_called_before_window_exists`)
        # et ne la réaffiche plus jamais, même après `deiconify()`. La fenêtre
        # n'est de toute façon dessinée qu'au démarrage de la boucle, une fois
        # dimensionnée : rien à masquer.
        self._theme_var = tk.StringVar(
            value=THEMES_INVERSES.get(ctk.get_appearance_mode().lower(), "Sombre")
        )
        self._taille_tableau_var = tk.StringVar(
            value=TEXTES_TABLEAU_INVERSES.get(
                str(self._preferences.get("texte_tableau", "normal")), "Normale"
            )
        )
        self._ombres_var = tk.BooleanVar(value=bool(self._preferences.get("ombres", True)))
        self._confirmer_var = tk.BooleanVar(
            value=bool(self._preferences.get("confirmer_suppression", True))
        )
        self._demarrage_var = tk.BooleanVar(
            value=self._preferences.get("demarrage") == "derniere"
        )
        self._ombres_cartes: list[tuple[ctk.CTkFrame, ctk.CTkFrame]] = []
        self._separateurs: list[tk.PanedWindow] = []
        # Zones de blocs personnalisables : (page, zone) → conteneur.
        self._zones_blocs: dict[tuple[str, str], tk.Misc] = {}
        self._theme_en_cours = False
        # Libellés à réajuster globalement, jamais depuis leur propre
        # événement <Configure> (source de boucles de mise en page).
        self._labels_wrap: list[tuple[ctk.CTkLabel, int]] = []
        self._wrap_apres = None
        self._page_courante = "accueil"
        self._derniere_page_hub: dict[str, str] = {}
        self._barre_onglets: ctk.CTkFrame | None = None
        self.title("Outly — veille concurrentielle YouTube")
        self.configure(fg_color=FOND)
        self._polices()
        self._dimensionner()
        self._style_tableau()

        # État
        self._instantane: data.Instantane | None = None
        self._lignes: list[report.LigneRapport] = []
        self._index: dict[str, report.LigneRapport] = {}
        self._tache: Tache | None = None
        self._libelle_tache = ""
        self._quota_avant = (0, 0)
        self._chargement = True
        self._modifie = False
        self._boutons_execution: list[ctk.CTkButton] = []
        # Les options de lancement existent avant la page Lancer : F5 ou un
        # lancement depuis une autre page ne doit jamais lever AttributeError.
        self._option_recherches = tk.StringVar(value="20")
        self._option_outliers = tk.BooleanVar(value=False)
        self._option_transcriptions = tk.BooleanVar(value=False)
        self._options_initialisees = False
        self._estimation_execution = tk.StringVar(value=estimation_execution(20))
        for _variable in (
            self._option_recherches,
            self._option_outliers,
            self._option_transcriptions,
        ):
            _variable.trace_add(
                "write", lambda *_args: self._maj_estimation_execution()
            )
        self._lignes_etat: dict[str, tuple[ctk.CTkLabel, ctk.CTkLabel]] = {}
        self._env_vars: dict[str, tk.StringVar] = {}
        self._entrees_secretes: list[ctk.CTkEntry] = []
        self._reglages: dict[tuple[str, str], tk.StringVar] = {}
        self._bascule: dict[tuple[str, str], tk.BooleanVar] = {}
        # Les interrupteurs sont dessinés avec la page Réglages, mais d'autres
        # pages les lisent : les variables existent donc dès le départ.
        for _section, _cle, _libelle in BASCULES:
            _variable = tk.BooleanVar(value=False)
            _variable.trace_add("write", self._marquer_modifie)
            self._bascule[(_section, _cle)] = _variable
        self._editeurs: dict[str, ctk.CTkTextbox] = {}
        self._compteurs: dict[str, tk.StringVar] = {}
        self._ma_chaine = tk.StringVar()
        self._international = tk.BooleanVar(value=True)
        self._champs_email: dict[str, tk.StringVar] = {}
        self._frequence = tk.StringVar(value="Jamais")
        self._jour_semaine = tk.StringVar(value="Lundi")
        self._email_statut: dict[str, ctk.CTkLabel] = {}
        self._email_mdp: ctk.CTkLabel | None = None
        self._email_note: ctk.CTkLabel | None = None
        self._email_actions: list[ctk.CTkButton] = []
        self._dossier_import = Path.home()
        self._bascule[("moi", "recherche_internationale")] = self._international
        self._ma_chaine.trace_add("write", self._marquer_modifie)
        self._international.trace_add("write", self._marquer_modifie)
        self._jour_semaine.trace_add("write", self._marquer_modifie)
        self._filtre_timer: str | None = None
        self._tri: tuple[str, bool] = ("score", True)
        self._hooks_sujet = tk.StringVar()
        self._hooks_etat = tk.StringVar(value="Aucune génération pour l'instant.")
        self._hooks_sujet.trace_add("write", self._marquer_modifie)
        self._index_playlist: dict[str, playlist.Element] = {}
        # Bibliothèque de notes : les champs sont chargés dans l'éditeur, puis
        # sauvegardés automatiquement après une courte pause de frappe.
        self._notes_index: dict[str, notes.Note] = {}
        self._note_courante_id: int | None = None
        self._note_chargement = False
        self._note_dirty = False
        self._note_sauvegarde_timer: str | None = None
        self._notes_filtre_timer: str | None = None
        self._notes_recherche = tk.StringVar()
        self._notes_type_filtre = tk.StringVar(value="Tous")
        self._notes_statut_filtre = tk.StringVar(value="Tous")
        self._notes_epinglees = tk.BooleanVar(value=False)
        self._notes_compte = tk.StringVar(value="")
        self._note_titre = tk.StringVar()
        self._note_tags = tk.StringVar()
        self._note_source = tk.StringVar()
        self._note_type = tk.StringVar(value=notes.TYPES["note"])
        self._note_statut = tk.StringVar(value=notes.STATUTS["brouillon"])
        self._note_epinglee = tk.BooleanVar(value=False)
        self._note_note = tk.StringVar(value=notes.NOTES_CHOIX[0])
        self._note_meta = tk.StringVar(value="Sélectionnez une note dans la liste.")
        self._note_sauvegarde_etat = tk.StringVar(value="")
        self._note_editeur_widgets: list = []
        self._cerveau_question = tk.StringVar()
        self._cerveau_archives = tk.BooleanVar(value=False)
        self._cerveau_forcer = tk.BooleanVar(value=False)
        self._cerveau_etat = tk.StringVar(value="Aucune analyse pour l'instant.")
        # Idées de vidéos : sujets + trois titres, générés par le LLM.
        self._idees_question = tk.StringVar()
        self._idees_nombre = tk.StringVar(value="5")
        self._idees_forcer = tk.BooleanVar(value=False)
        self._idees_compte = tk.StringVar(value="")
        self._idees_etat = tk.StringVar(value="Aucune recherche pour l'instant.")
        self._vignettes_images: dict[str, ctk.CTkImage] = {}
        self._vignettes = Vignettes(self)
        # Plan éditorial : fiches chargées depuis la base locale.
        self._plan_index: dict[str, plan.Fiche] = {}
        self._plan_courant_id: int | None = None
        self._plan_titre = tk.StringVar()
        self._plan_date = tk.StringVar()
        self._plan_statut = tk.StringVar(value=plan.STATUTS["idee"])
        self._plan_format = tk.StringVar(value=plan.FORMATS["longue"])
        self._plan_compte = tk.StringVar(value="")
        self._plan_resume = tk.StringVar(value="")
        self._plan_etat = tk.StringVar(value="")
        # Comparateur : trois menus indépendants et leurs correspondances.
        self._comparateur_choix: list[tk.StringVar] = []
        self._comparateur_menus: list[ctk.CTkOptionMenu] = []
        self._comparateur_labels: dict[str, str] = {}
        self._comparateur_index: dict[str, report.LigneRapport] = {}
        self._comparateur_note = tk.StringVar(value="")
        self._comparateur_scores = []
        self._strategie_vue = tk.StringVar(value="Vue d'ensemble")
        self._strategie_etat = tk.StringVar(value="Aucun diagnostic pour l'instant.")
        self._strategie_forcer = tk.BooleanVar(value=False)
        self._strategie_jours = tk.StringVar(value="10")
        self._production_resume = tk.StringVar(value="Aucune estimation.")
        self._production_detail: dict | None = None
        self._navigation_groupes = dict(
            self._preferences.get("navigation_groupes") or {}
        )
        self._nav_conteneurs: dict[str, ctk.CTkFrame] = {}
        self._nav_entetes: dict[str, ctk.CTkButton] = {}
        self._nav_chevrons: dict[str, ctk.CTkLabel] = {}
        self._nav_frames: dict[str, ctk.CTkFrame] = {}
        self._zones_libelles: dict[tuple[str, str], str] = {}
        self._pages_avec_panneau: set[str] = set()
        self._page_en_construction: str | None = None

        self.report_callback_exception = self._erreur_callback
        self._construire()
        self._remplir()
        self._chargement = False
        self._afficher_page(self._page_de_demarrage())
        self._remplir_plan()
        self._maj_options_comparateur()
        self._rafraichir_couleurs_plates()
        # La fenêtre apparaît ici directement à sa taille définitive :
        # CustomTkinter la dévoile au démarrage de la boucle d'événements.
        self.protocol("WM_DELETE_WINDOW", self._fermer)
        self.bind_all("<Control-s>", lambda _: self._raccourci_enregistrer())
        self.bind_all("<F5>", lambda _: self._demarrer_run())
        self.bind("<Configure>", self._planifier_wraps, add="+")
        self.after(120, self._rafraichir_tout)
        # CustomTkinter réapplique sa propre icône 200 ms après la création de
        # la fenêtre : on impose la nôtre une fois la fenêtre affichée.
        self.after(600, self._forcer_icone_windows)
        # La fenêtre est masquée pendant la construction ; on s'assure qu'elle
        # est bien à l'écran une fois la boucle d'événements lancée.
        self.after(0, self._afficher_fenetre)
        self.after(250, self._afficher_fenetre)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _polices(self) -> None:
        """Choisit les meilleures polices réellement installées."""
        familles = {nom.lower(): nom for nom in tkfont.families(self)}

        def choisir(*candidats: str) -> str:
            for candidat in candidats:
                if candidat.lower() in familles:
                    return familles[candidat.lower()]
            return "TkDefaultFont"

        self._police = choisir("Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans")
        self._mono = choisir("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono")

    def _dimensionner(self) -> None:
        """Taille confortable, plafonnée par l'écran (portables 1366 × 768)."""
        self.update_idletasks()
        ecran_l = self.winfo_screenwidth()
        ecran_h = self.winfo_screenheight()
        largeur = max(1240, min(1300, ecran_l - 60))
        hauteur = max(720, min(860, ecran_h - 90))
        # En dessous, le tableau et le panneau de détail ne tiennent plus ensemble.
        self.minsize(1200, 700)
        x = max(0, (ecran_l - largeur) // 2)
        y = max(0, (ecran_h - hauteur) // 2 - 20)
        self.geometry(f"{largeur}x{hauteur}+{x}+{y}")

    def _style_tableau(self) -> None:
        """Habille le Treeview aux couleurs du thème : en-têtes plats, lignes sombres."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.layout("Outly.Treeview", [("Outly.Treeview.treearea", {"sticky": "nswe"})])
        # ttk ne connaît pas les couples clair/sombre : on lui donne la couleur
        # résolue de l'ambiance active, et on rappelle cette méthode à chaque
        # changement de thème.
        police = self._police_tableau()
        style.configure(
            "Outly.Treeview",
            background=_couleur(SURFACE),
            fieldbackground=_couleur(SURFACE),
            foreground=_couleur(TEXTE),
            borderwidth=0,
            rowheight=38 + (police[1] - 14) * 4,
            font=police,
        )
        style.map(
            "Outly.Treeview",
            background=[("selected", _couleur(ACCENT))],
            foreground=[("selected", _couleur(TEXTE_SUR_ACCENT))],
        )
        style.configure(
            "Outly.Treeview.Heading",
            background=_couleur(SURFACE),
            foreground=_couleur(DOUX),
            relief="flat",
            borderwidth=0,
            padding=(8, 10),
            font=(self._police, police[1] - 1, "bold"),
        )
        style.map(
            "Outly.Treeview.Heading",
            background=[("active", _couleur(SURFACE_HAUTE))],
            foreground=[("active", _couleur(TEXTE))],
        )

    def _construire(self) -> None:
        self._barre_laterale()
        self._zone_principale()

    # --- apparence ----------------------------------------------------
    def _police_tableau(self) -> tuple:
        """Police des tableaux, selon la préférence de taille."""
        taille = 16 if self._taille_tableau_var.get() == "Grande" else 14
        return (self._police, taille)

    def _changer_theme(self, libelle: str) -> None:
        """Applique l'ambiance choisie et la mémorise pour les prochains lancements.

        Le changement de thème redessine tous les widgets : une exception dans
        un seul d'entre eux ne doit jamais fermer l'application. En cas d'échec,
        on revient au thème précédent et on consigne le détail.
        """
        mode = THEMES.get(libelle, "dark")
        if self._theme_en_cours:
            return
        self._theme_en_cours = True
        precedent = ctk.get_appearance_mode().lower()
        try:
            self._theme_var.set(THEMES_INVERSES.get(mode, "Sombre"))
            if precedent != mode:
                ctk.set_appearance_mode(mode)
                try:
                    self._style_tableau()
                    self._rafraichir_couleurs_plates()
                    self._maj_onglets(self._page_courante)
                except Exception as probleme:
                    try:
                        ctk.set_appearance_mode(precedent)
                    except Exception:
                        pass
                    self._theme_var.set(THEMES_INVERSES.get(precedent, "Sombre"))
                    self._message.set(f"Thème non appliqué : {probleme}")
                    _journaliser_plantage(
                        self.racine,
                        "Échec du changement de thème :\n" + traceback.format_exc(),
                    )
                    return
                self._message.set(f"Thème {libelle.lower()} appliqué.")
                self._planifier_wraps()
            self._enregistrer_preference("theme", mode)
        except Exception as probleme:
            try:
                ctk.set_appearance_mode(precedent)
            except Exception:
                pass
            try:
                self._message.set(f"Thème inchangé : {probleme}")
            except Exception:
                pass
            _journaliser_plantage(
                self.racine,
                "Échec du changement de thème :\n" + traceback.format_exc(),
            )
        finally:
            self._theme_en_cours = False

    def _changer_taille_tableau(self, libelle: str) -> None:
        """Agrandit le texte des tableaux sans reconstruire la page."""
        valeur = TEXTES_TABLEAU.get(libelle, "normal")
        self._taille_tableau_var.set(libelle if libelle in TEXTES_TABLEAU else "Normale")
        self._enregistrer_preference("texte_tableau", valeur)
        self._style_tableau()
        self._rafraichir_couleurs_plates()
        self._message.set(f"Texte des tableaux : {libelle.lower()}.")

    def _basculer_ombres(self) -> None:
        actives = bool(self._ombres_var.get())
        self._enregistrer_preference("ombres", actives)
        self._appliquer_ombres()
        self._message.set("Ombres affichées." if actives else "Ombres désactivées.")

    def _appliquer_ombres(self) -> None:
        """Retire ou repose l'ombre sous chaque carte, sans la reconstruire."""
        for ombre, carte in self._ombres_cartes:
            try:
                if self._ombres_var.get():
                    ombre.place(x=2, y=3, relwidth=1.0, relheight=1.0)
                    carte.pack_configure(padx=(0, 6), pady=(0, 6))
                else:
                    ombre.place_forget()
                    carte.pack_configure(padx=0, pady=0)
            except tk.TclError:
                continue

    def _basculer_demarrage(self) -> None:
        actif = bool(self._demarrage_var.get())
        self._enregistrer_preference("demarrage", "derniere" if actif else "accueil")
        if actif:
            self._enregistrer_preference("derniere_page", self._page_courante)
        self._message.set(
            "L'application rouvrira la dernière page."
            if actif
            else "L'application rouvrira sur l'accueil."
        )

    def _page_de_demarrage(self) -> str:
        if self._preferences.get("demarrage") != "derniere":
            return "accueil"
        page = str(self._preferences.get("derniere_page", "accueil"))
        return page if page in self._constructeurs else "accueil"

    def _enregistrer_preference(self, cle: str, valeur) -> None:
        """Mémorise un réglage d'interface ; un échec d'écriture ne bloque rien."""
        self._preferences[cle] = valeur
        try:
            settings.ecrire_preferences(self.racine, **{cle: valeur})
        except settings.ErreurFichier as probleme:
            self._message.set(f"Réglage appliqué, mais non mémorisé : {probleme}")

    def _confirmer(self, titre: str, message: str, parent: tk.Misc | None = None) -> bool:
        """Demande confirmation, sauf si l'utilisateur l'a désactivée."""
        if not self._confirmer_var.get():
            return True
        return messagebox.askyesno(titre, message, parent=parent or self)

    def _rafraichir_couleurs_plates(self) -> None:
        """Repose les couleurs que ttk et Tk ne résolvent pas par thème."""
        texte = _couleur(TEXTE)
        doux = _couleur(DOUX_FONCE)
        if hasattr(self, "_arbre"):
            # Le gras marque les chaînes retenues sans toucher au fond : la
            # sélection garde ainsi le contraste habituel.
            police = self._police_tableau()
            self._arbre.tag_configure("retenue", foreground=texte, font=(*police, "bold"))
            self._arbre.tag_configure("ecartee", foreground=doux, font=police)
        if hasattr(self, "_arbre_playlist"):
            self._arbre_playlist.tag_configure("video", foreground=texte)
            self._arbre_playlist.tag_configure("channel", foreground=_couleur(ACCENT_TEXTE))
        if hasattr(self, "_arbre_plan"):
            self._arbre_plan.tag_configure("publie", foreground=doux)
            self._arbre_plan.tag_configure("retard", foreground=_couleur(ALERTE))
            self._arbre_plan.tag_configure("proche", foreground=_couleur(OK))
        for graphique in getattr(self, "_studio_graphiques", []):
            try:
                graphique.rafraichir()
            except tk.TclError:
                continue
        for paned in getattr(self, "_separateurs", []):
            try:
                paned.configure(bg=_couleur(BORDURE))
            except tk.TclError:
                continue
        if hasattr(self, "_journal"):
            # Le journal garde un fond dense dans les deux thèmes : ses balises
            # restent donc claires, sinon le texte disparaît.
            for etiquette in ("titre", "ok", "alerte", "erreur"):
                self._journal.tag_config(
                    etiquette, foreground="#F2FAFD", underline=etiquette != "ok"
                )
            self._journal.tag_config("discret", foreground="#CBE6EE")

    def _chemin_icone(self) -> Path | None:
        """Premier `.ico` disponible : dossier de travail, puis paquet embarqué."""
        for chemin in (
            self.racine / "logo.ico",
            self.racine / "assets" / "logo.ico",
            Path(__file__).resolve().parent / "logo.ico",
        ):
            if chemin.is_file():
                return chemin
        return None

    def _afficher_fenetre(self) -> None:
        """Garantit que la fenêtre est réellement à l'écran.

        CustomTkinter masque la fenêtre au démarrage de `mainloop()` pour
        peindre sa barre de titre, puis ne la restaure pas toujours :
        l'application tourne alors sans aucune fenêtre visible. Ce rappel,
        exécuté une fois la boucle démarrée, la remet à l'écran.
        """
        try:
            self.deiconify()
            self.lift()
            _journaliser_demarrage(
                self.racine,
                f"affichage : {self.state()} · viewable {self.winfo_viewable()}",
            )
        except tk.TclError:
            pass

    def _forcer_icone_windows(self) -> None:
        """Impose le logo comme icône Win32 de la fenêtre.

        Tk et CustomTkinter se disputent l'icône : le `iconbitmap` posé au
        démarrage peut être remplacé par le carré bleu de CustomTkinter (ou
        une icône vide). WM_SETICON met à jour la barre de titre, la barre des
        tâches et Alt+Tab, quelle que soit la dernière icône choisie.
        """
        if not sys.platform.startswith("win"):
            return
        chemin = self._chemin_icone()
        if chemin is None:
            return
        _journaliser_demarrage(self.racine, "icône : début")
        try:
            import ctypes
            from ctypes import wintypes

            IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON, GA_ROOT = 1, 0x10, 0x80, 2
            ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE,
                wintypes.LPCWSTR,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.SendMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND

            fenetre = user32.GetAncestor(self.winfo_id(), GA_ROOT) or self.winfo_id()
            images = []
            for taille, emplacement in ((32, ICON_BIG), (16, ICON_SMALL), (16, ICON_SMALL2)):
                image = user32.LoadImageW(
                    None, str(chemin), IMAGE_ICON, taille, taille, LR_LOADFROMFILE
                )
                if not image:
                    continue
                user32.SendMessageW(fenetre, WM_SETICON, emplacement, image)
                images.append(image)
            # Windows ne copie pas les handles : cette référence les garde
            # vivants pour toute la durée de la fenêtre.
            self._icones_windows = images
            _journaliser_demarrage(self.racine, "icône : posée")
        except Exception:
            # Une icône est décorative : jamais d'erreur bloquante ici.
            _journaliser_demarrage(self.racine, "icône : échec (ignoré)")

    def _charger_logo(self) -> tk.PhotoImage | None:
        """Affiche `logo.png` s'il existe, sinon le nom en toutes lettres.

        Tk lit le PNG nativement : aucune dépendance d'image n'est ajoutée.
        L'image est sous-échantillonnée (jamais étirée) pour tenir dans 180 × 46
        pixels. Un fichier illisible est ignoré : le nom s'affiche à sa place.
        """
        candidats = [
            self.racine / "logo.png",
            self.racine / "assets" / "logo.png",
            Path(__file__).resolve().parent / "logo.png",
        ]
        for chemin in candidats:
            if not chemin.is_file():
                continue
            try:
                image = tk.PhotoImage(file=str(chemin))
            except (tk.TclError, OSError):
                continue
            facteur = max(
                1,
                -(-image.width() // 180),
                -(-image.height() // 46),
            )
            if facteur > 1:
                image = image.subsample(facteur, facteur)
            # Tk n'attache pas l'image au widget : sans cette référence, elle
            # serait détruite par le ramasse-miettes et le logo disparaîtrait.
            self._logo_image = image
            try:
                # Même logo dans la barre des tâches et sur la fenêtre.
                self.iconphoto(True, image)
            except tk.TclError:
                pass
            # Windows : icône .ico multipage. `iconbitmap` est indispensable
            # avec CustomTkinter, qui remplace sinon l'icône par la sienne
            # (le carré bleu) au premier affichage de la fenêtre.
            chemin_ico = self._chemin_icone()
            if chemin_ico is not None:
                try:
                    self.iconbitmap(str(chemin_ico))
                except tk.TclError:
                    pass
            else:
                # Pas de .ico : bloque malgré tout l'icône par défaut de CTk.
                self._iconbitmap_method_called = True
            return image
        return None

    def _barre_laterale(self) -> None:
        barre = ctk.CTkFrame(self, fg_color=BARRE_LATERALE, corner_radius=0, width=236)
        barre.pack(side="left", fill="y")
        barre.pack_propagate(False)

        marque = ctk.CTkFrame(barre, fg_color="transparent")
        marque.pack(fill="x", padx=20, pady=(14, 10))
        logo = self._charger_logo()
        if logo is not None:
            # Icône + nom : le logo seul (carré) laisserait la marque anonyme.
            # `tk.Label` plutôt que CTkLabel : le PhotoImage natif y est accepté
            # sans avertissement, et aucune dépendance d'image n'est requise.
            lockup = ctk.CTkFrame(marque, fg_color="transparent")
            lockup.pack(anchor="w")
            tk.Label(lockup, image=logo, bg=BARRE_LATERALE, borderwidth=0).pack(side="left")
            ctk.CTkLabel(
                lockup,
                text="Outly",
                font=(self._police, 25, "bold"),
                text_color=SIDEBAR_TEXTE,
                anchor="w",
            ).pack(side="left", padx=(10, 0))
        else:
            ctk.CTkLabel(
                marque,
                text="Outly",
                font=(self._police, 26, "bold"),
                text_color=SIDEBAR_TEXTE,
                anchor="w",
            ).pack(anchor="w")
        ctk.CTkLabel(
            marque,
            text="veille concurrentielle",
            font=(self._police, 13),
            text_color=SIDEBAR_DOUX,
            anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        self._boutons_nav: dict[str, ctk.CTkButton] = {}
        navigation = ctk.CTkScrollableFrame(
            barre,
            fg_color=BARRE_LATERALE,
            corner_radius=0,
            scrollbar_button_color=SIDEBAR_HOVER,
            scrollbar_button_hover_color=SIDEBAR_ACTIF,
        )
        navigation.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 4))
        for groupe, entrees in NAVIGATION_GROUPES:
            ouvert = bool(self._navigation_groupes.get(groupe, True))
            entete = ctk.CTkFrame(navigation, fg_color="transparent", height=30)
            entete.pack(fill="x", padx=8, pady=(5, 0))
            bouton = ctk.CTkButton(
                entete,
                text=groupe,
                image=icones.groupe(groupe, 15, SIDEBAR_DOUX),
                compound="left",
                command=lambda g=groupe: self._basculer_groupe(g),
                anchor="w",
                height=28,
                corner_radius=5,
                fg_color="transparent",
                hover_color=SIDEBAR_HOVER,
                text_color=SIDEBAR_DOUX,
                font=(self._police, 10, "bold"),
            )
            bouton.pack(side="left", fill="x", expand=True)
            chevron = ctk.CTkLabel(
                entete,
                text="",
                image=icones.image(
                    "chevron-bas" if ouvert else "chevron-droit", 14, SIDEBAR_DOUX
                ),
                width=18,
            )
            chevron.pack(side="right", padx=(2, 5))
            chevron.bind("<Button-1>", lambda _e, g=groupe: self._basculer_groupe(g))
            self._nav_entetes[groupe] = bouton
            self._nav_chevrons[groupe] = chevron
            self._nav_frames[groupe] = entete
            conteneur = ctk.CTkFrame(navigation, fg_color="transparent")
            conteneur.pack(fill="x")
            self._nav_conteneurs[groupe] = conteneur
            if not ouvert:
                conteneur.pack_forget()
            for cle, libelle in entrees:
                bouton_page = ctk.CTkButton(
                    conteneur,
                    text=libelle,
                    image=icones.page(cle, 18, SIDEBAR_TEXTE),
                    compound="left",
                    command=lambda c=cle: self._afficher_page(c),
                    anchor="w",
                    height=28,
                    corner_radius=5,
                    fg_color="transparent",
                    hover_color=SIDEBAR_HOVER,
                    text_color=SIDEBAR_DOUX,
                    font=(self._police, 13),
                )
                bouton_page.pack(fill="x", padx=10, pady=0)
                self._boutons_nav[cle] = bouton_page

        pied = ctk.CTkFrame(barre, fg_color="transparent")
        pied.pack(side="bottom", fill="x", padx=14, pady=14)
        carte = ctk.CTkFrame(pied, fg_color=SURFACE, corner_radius=6)
        carte.pack(fill="x")
        interieur = ctk.CTkFrame(carte, fg_color="transparent")
        interieur.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(
            interieur,
            text="QUOTA DU JOUR",
            font=(self._police, 11, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        self._mini_unites = ctk.CTkLabel(
            interieur, text="—", font=(self._police, 13), text_color=TEXTE, anchor="w"
        )
        self._mini_unites.pack(anchor="w", pady=(6, 0))
        self._mini_barre_unites = Barre(interieur, 5)
        self._mini_barre_unites.pack(fill="x", pady=(5, 8))
        self._mini_recherches = ctk.CTkLabel(
            interieur, text="—", font=(self._police, 13), text_color=TEXTE, anchor="w"
        )
        self._mini_recherches.pack(anchor="w")
        self._mini_barre_recherches = Barre(interieur, 5)
        self._mini_barre_recherches.pack(fill="x", pady=(5, 0))
        ctk.CTkLabel(
            pied,
            text=f"v{__version__} · 100 % local",
            font=(self._police, 11),
            text_color=SIDEBAR_DOUX,
        ).pack(anchor="w", padx=4, pady=(10, 0))

    def _basculer_groupe(self, groupe: str) -> None:
        """Ouvre ou ferme un volet de navigation, et mémorise son état."""
        ouvert = not bool(self._navigation_groupes.get(groupe, True))
        self._navigation_groupes[groupe] = ouvert
        conteneur = self._nav_conteneurs.get(groupe)
        if conteneur is not None:
            if ouvert:
                suivants = [
                    nom
                    for nom, _entrees in NAVIGATION_GROUPES
                    if nom != groupe
                    and self._nav_frames.get(nom) is not None
                ]
                try:
                    index = next(
                        i for i, (nom, _e) in enumerate(NAVIGATION_GROUPES) if nom == groupe
                    )
                    ancre = None
                    for nom, _e in NAVIGATION_GROUPES[index + 1 :]:
                        if nom in self._nav_frames:
                            ancre = self._nav_frames[nom]
                            break
                    if ancre is not None:
                        conteneur.pack(fill="x", before=ancre)
                    else:
                        conteneur.pack(fill="x")
                except (ValueError, tk.TclError):
                    conteneur.pack(fill="x")
            else:
                conteneur.pack_forget()
        chevron = self._nav_chevrons.get(groupe)
        if chevron is not None:
            chevron.configure(
                image=icones.image(
                    "chevron-bas" if ouvert else "chevron-droit", 14, SIDEBAR_DOUX
                )
            )
        self._maj_nav_actifs()
        try:
            settings.ecrire_preferences(
                self.racine, navigation_groupes=dict(self._navigation_groupes)
            )
        except settings.ErreurFichier as probleme:
            self._message.set(f"Volets non enregistrés : {probleme}")

    def _hub_de_page(self, cle: str) -> str | None:
        return cle if cle in HUBS_ONGLETS else PAGE_VERS_HUB.get(cle)

    def _groupe_de_page(self, cle: str) -> str | None:
        hub = self._hub_de_page(cle) or cle
        for groupe, entrees in NAVIGATION_GROUPES:
            if any(nom == hub for nom, _libelle in entrees):
                return groupe
        return None

    def _ouvrir_groupe_page(self, cle: str) -> None:
        groupe = self._groupe_de_page(cle)
        if groupe and not self._navigation_groupes.get(groupe, True):
            self._basculer_groupe(groupe)

    def _maj_nav_actifs(self) -> None:
        for groupe, bouton in self._nav_entetes.items():
            entrees = dict(NAVIGATION_GROUPES).get(groupe, ())
            courant = self._hub_de_page(self._page_courante) or self._page_courante
            contient = any(nom == courant for nom, _libelle in entrees)
            ouvert = bool(self._navigation_groupes.get(groupe, True))
            bouton.configure(
                text_color=ACCENT_CLAIR if (contient and not ouvert) else SIDEBAR_DOUX
            )

    def _zone_principale(self) -> None:
        contenu = ctk.CTkFrame(self, fg_color=FOND, corner_radius=0)
        contenu.pack(side="left", fill="both", expand=True)

        entete = ctk.CTkFrame(contenu, fg_color="transparent")
        entete.pack(fill="x", padx=24, pady=(24, 16))
        self._icone_titre = ctk.CTkLabel(entete, text="", width=34)
        self._icone_titre.pack(side="left", padx=(0, 10), anchor="n")
        textes = ctk.CTkFrame(entete, fg_color="transparent")
        textes.pack(side="left", anchor="w")
        self._titre_page = tk.StringVar(value="Accueil")
        self._sous_titre_page = tk.StringVar(value="")
        ctk.CTkLabel(
            textes,
            textvariable=self._titre_page,
            font=(self._police, 28, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            textes,
            textvariable=self._sous_titre_page,
            font=(self._police, 14),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        droite = ctk.CTkFrame(entete, fg_color="transparent")
        droite.pack(side="right", anchor="e")
        self._pastille = ctk.CTkLabel(
            droite,
            text="  Chargement…  ",
            font=(self._police, 13, "bold"),
            text_color=DOUX,
            fg_color=SURFACE_HAUTE,
            corner_radius=4,
            height=28,
        )
        self._pastille.pack(side="right")
        self._entete_quota = ctk.CTkLabel(
            droite, text="", font=(self._police, 13), text_color=DOUX_FONCE
        )
        self._entete_quota.pack(side="right", padx=(0, 16))
        self._bouton_organiser = self._lien(
            droite,
            "Organiser",
            self._organiser_page_courante,
            width=110,
            image=icones.image("organiser", 16, _couleur(DOUX)),
            compound="left",
        )
        self._bouton_organiser.pack(side="right", padx=(0, 10))
        self._spinner_entete = Spinner(
            droite, taille=16, couleur=_couleur(ACCENT), fond=_couleur(FOND)
        )
        self._spinner_entete.pack(side="right", padx=(0, 8))
        self._spinner_entete.pack_forget()

        self._barre_onglets = ctk.CTkFrame(contenu, fg_color="transparent")
        self._conteneur = ctk.CTkFrame(contenu, fg_color=FOND, corner_radius=0)
        self._conteneur.pack(fill="both", expand=True, padx=24)
        self._conteneur.grid_rowconfigure(0, weight=1)
        self._conteneur.grid_columnconfigure(0, weight=1)

        # Aucune page n'est construite ici : CustomTkinter dessine chaque
        # widget au moment de sa création, et bâtir les treize pages d'avance
        # coûtait plusieurs secondes avant même que la fenêtre apparaisse.
        # Une page naît à sa première ouverture, puis reste en mémoire.
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._constructeurs = {
            "audit": self._page_audit,
            "accueil": self._page_accueil,
            "studio": self._page_studio,
            "contenu": self._page_contenu,
            "cles": self._page_cles,
            "reglages": self._page_reglages,
            "email": self._page_email,
            "lancer": self._page_lancer,
            "resultats": self._page_resultats,
            "comparateur": self._page_comparateur,
            "hooks": self._page_hooks,
            "cerveau": self._page_cerveau,
            "idees": self._page_idees,
            "recherche": self._page_recherche,
            "notes": self._page_notes,
            "regarder": self._page_regarder,
            "plan": self._page_plan,
            "strategie": self._page_strategie,
            "schemas": self._page_schemas,
            "tableau": self._page_tableau,
        }

        pied = ctk.CTkFrame(contenu, fg_color="transparent")
        pied.pack(fill="x", padx=24, pady=(8, 14))
        self._message = tk.StringVar(value="Prêt.")
        ctk.CTkLabel(
            pied,
            textvariable=self._message,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            pied,
            text="Studio de création · données et clés sur ce PC",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="right")

    # --- briques réutilisables ----------------------------------------
    def _zone_defilante(self, parent: tk.Misc) -> ctk.CTkFrame:
        zone = ctk.CTkScrollableFrame(
            parent,
            fg_color=FOND,
            corner_radius=0,
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        zone.pack(fill="both", expand=True)
        return zone

    def _auto_wrap(self, label: ctk.CTkLabel, marge: int = 6) -> None:
        """Enregistre un libellé pour un ajustement global et différé.

        Modifier `wraplength` change la hauteur demandée par le libellé ; le
        faire depuis son propre `<Configure>` entretient une boucle (la hauteur
        change, l'événement revient, la largeur change…). Les cartes vibrent et
        un changement de thème peut bloquer Tk. On ne s'abonne donc qu'à la
        fenêtre : un redimensionnement ou un changement de page déclenche une
        seule passe, sans rétroaction.
        """
        for existant, _marge in self._labels_wrap:
            if existant is label:
                return
        self._labels_wrap.append((label, marge))

    def _planifier_wraps(self, _event=None) -> None:
        """Diffère l'ajustement des libellés pour ne pas le répéter en rafale."""
        if self._wrap_apres is not None:
            try:
                self.after_cancel(self._wrap_apres)
            except tk.TclError:
                pass
        try:
            self._wrap_apres = self.after(120, self._ajuster_wraps)
        except tk.TclError:
            self._wrap_apres = None

    def _ajuster_wraps(self) -> None:
        """Une passe unique : largeur réelle de chaque libellé → wraplength."""
        self._wrap_apres = None
        vivants: list[tuple[ctk.CTkLabel, int]] = []
        for label, marge in self._labels_wrap:
            try:
                if not label.winfo_exists():
                    continue
                largeur = label.winfo_width()
                if largeur >= 60:
                    cible = max(80, largeur - marge)
                    actuelle = int(float(label.cget("wraplength") or 0))
                    if abs(actuelle - cible) > 12:
                        label.configure(wraplength=cible)
                vivants.append((label, marge))
            except (tk.TclError, ValueError, TypeError):
                continue
        self._labels_wrap = vivants

    def _decouper(self, parent: tk.Misc) -> tk.PanedWindow:
        """Deux panneaux côte à côte, séparés par une poignée glissable.

        Tk répartit lui-même l'espace : on rétrécit un panneau au profit de
        l'autre, comme deux fenêtres côte à côte. Chaque panneau garde une
        largeur minimale pour rester lisible.
        """
        paned = tk.PanedWindow(
            parent,
            orient="horizontal",
            bg=_couleur(BORDURE),
            bd=0,
            sashwidth=6,
            sashpad=0,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
        )
        self._separateurs.append(paned)
        return paned

    def _carte(
        self,
        parent: tk.Misc,
        titre: str | None = None,
        sous_titre: str | None = None,
        *,
        cle: str | None = None,
    ) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
        # Ombre portée discrète : un cadre de la couleur du fond, décalé de
        # trois pixels derrière la carte. Tk ne floute pas une ombre ; ce
        # décalage suffit à détacher la carte sans assombrir la lecture.
        enveloppe = ctk.CTkFrame(parent, fg_color="transparent")
        enveloppe._outly_carte = True
        enveloppe._outly_bloc = cle or (titre or "").strip()
        enveloppe._outly_titre = titre or ""
        ombre = ctk.CTkFrame(enveloppe, fg_color=OMBRE, corner_radius=7)
        ombre.place(x=2, y=3, relwidth=1.0, relheight=1.0)
        carte = ctk.CTkFrame(
            enveloppe, fg_color=SURFACE, corner_radius=7, border_width=1, border_color=BORDURE
        )
        carte.pack(fill="both", expand=True, padx=(0, 6), pady=(0, 6))
        self._ombres_cartes.append((ombre, carte))
        if not self._ombres_var.get():
            ombre.place_forget()
            carte.pack_configure(padx=0, pady=0)
        corps = ctk.CTkFrame(carte, fg_color="transparent")
        corps.pack(fill="both", expand=True, padx=18, pady=16)
        if titre:
            ctk.CTkLabel(
                corps,
                text=titre,
                font=(self._police, 15, "bold"),
                text_color=TEXTE,
                anchor="w",
            ).pack(anchor="w")
        if sous_titre:
            explication = ctk.CTkLabel(
                corps,
                text=sous_titre,
                font=(self._police, 13),
                text_color=DOUX,
                anchor="w",
                justify="left",
                wraplength=560,
            )
            explication.pack(anchor="w", fill="x", pady=(2, 12))
            self._auto_wrap(explication)
        elif titre:
            ctk.CTkLabel(corps, text="", font=(self._police, 6)).pack(pady=(0, 6))
        return enveloppe, corps

    def _bandeau_parcours(
        self,
        parent: tk.Misc,
        titre: str,
        etapes: list[tuple[str, object]],
        *,
        lien: tuple[str, object] | None = None,
    ) -> None:
        """Bandeau court : quoi faire, dans quel ordre, sans jargon."""
        cadre, corps = self._carte(
            parent,
            f"Parcours conseillé · {titre}",
            "Suivez les étapes de gauche à droite. Chaque bouton ouvre directement "
            "l'action utile.",
            cle=f"parcours-{titre.lower().replace(' ', '-')}",
        )
        cadre.pack(fill="x", pady=(0, 12))
        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x")
        for index, (texte, action) in enumerate(etapes, start=1):
            ctk.CTkLabel(
                rangee,
                text=str(index),
                width=22,
                height=22,
                corner_radius=11,
                fg_color=ACCENT,
                text_color=TEXTE_SUR_ACCENT,
                font=(self._police, 11, "bold"),
            ).pack(side="left", padx=(0, 6))
            libelle = ctk.CTkLabel(
                rangee,
                text=texte,
                font=(self._police, 12),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=250,
            )
            libelle.pack(side="left", padx=(0, 12))
            self._auto_wrap(libelle)
            if action is not None:
                self._lien(
                    rangee, "Faire", action, width=58,
                ).pack(side="left", padx=(0, 14))
        if lien is not None:
            self._lien(corps, lien[0], lien[1], width=170).pack(anchor="w", pady=(8, 0))

    def _bouton_accent(self, parent: tk.Misc, texte: str, commande, **options) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=texte,
            command=commande,
            height=36,
            corner_radius=6,
            fg_color=ACCENT,
            hover_color=ACCENT_SURVOL,
            text_color=TEXTE_SUR_ACCENT,
            font=(self._police, 14, "bold"),
            **options,
        )

    def _bouton(self, parent: tk.Misc, texte: str, commande, **options) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=texte,
            command=commande,
            height=32,
            corner_radius=6,
            fg_color="transparent",
            hover_color=SURFACE_HAUTE,
            border_width=1,
            border_color=BORDURE,
            text_color=TEXTE,
            font=(self._police, 13),
            **options,
        )

    def _lien(
        self, parent: tk.Misc, texte: str, commande, width: int = 110, **options
    ) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=texte,
            command=commande,
            height=26,
            width=width,
            corner_radius=5,
            fg_color="transparent",
            hover_color=SURFACE_HAUTE,
            border_width=1,
            border_color=BORDURE,
            text_color=DOUX,
            font=(self._police, 12),
            **options,
        )

    def _titre_section(self, parent: tk.Misc, texte: str, pady=(16, 4)) -> None:
        ctk.CTkLabel(
            parent,
            text=texte.upper(),
            font=(self._police, 11, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w", pady=pady)

    def _champ(
        self,
        parent: tk.Misc,
        libelle: str,
        nom: str,
        secret: bool = False,
        aide: str = "",
        placeholder: str = "",
    ) -> ctk.CTkEntry:
        bloc = ctk.CTkFrame(parent, fg_color="transparent")
        bloc.pack(fill="x", pady=(2, 10))
        ctk.CTkLabel(
            bloc, text=libelle, font=(self._police, 13, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w")
        variable = self._env_vars.get(nom)
        if variable is None:
            variable = tk.StringVar()
            variable.trace_add("write", self._marquer_modifie)
            self._env_vars[nom] = variable
        entree = ctk.CTkEntry(
            bloc,
            textvariable=variable,
            show="*" if secret else "",
            height=36,
            corner_radius=8,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text=placeholder,
            font=(self._police, 14),
        )
        entree.pack(fill="x", pady=(4, 0))
        if secret:
            self._entrees_secretes.append(entree)
        if aide:
            explication = ctk.CTkLabel(
                bloc,
                text=aide,
                font=(self._police, 12),
                text_color=DOUX_FONCE,
                justify="left",
                wraplength=680,
                anchor="w",
            )
            explication.pack(anchor="w", fill="x", pady=(3, 0))
            self._auto_wrap(explication)
        return entree

    # ------------------------------------------------------------------
    # Page — Accueil
    # ------------------------------------------------------------------
    def _page_accueil(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["accueil"] = page
        zone = self._zone_defilante(page)

        self._bandeau_parcours(
            zone,
            "Guide express",
            [
                ("Réglages et clés", lambda: self._afficher_page("cles")),
                ("Lancer une veille", lambda: self._afficher_page("lancer")),
                ("Lire les chaînes", lambda: self._afficher_page("resultats")),
                ("Audit puis stratégie", lambda: self._afficher_page("audit")),
            ],
            lien=("Ouvrir Mon contenu", lambda: self._afficher_page("contenu")),
        )
        grille = ctk.CTkFrame(zone, fg_color="transparent")
        grille.pack(fill="both", expand=True)
        grille.grid_columnconfigure(0, weight=3)
        grille.grid_columnconfigure(1, weight=2)
        grille.grid_rowconfigure(0, weight=1)

        gauche = ctk.CTkFrame(grille, fg_color="transparent")
        gauche.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        droite = ctk.CTkFrame(grille, fg_color="transparent")
        droite.grid(row=0, column=1, sticky="nsew")
        self._zones_blocs[("accueil", "gauche")] = gauche
        self._zones_blocs[("accueil", "droite")] = droite

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "infos", "gauche"),
            "Vos informations",
            "Cinq éléments suffisent. Tout se règle ici, puis reste modifiable à la main.",
            cle="infos",
        )
        cadre.pack(fill="x", pady=(0, 14))
        self._ligne_etat(corps, "cles", "Clés API", "Configurer", lambda: self._afficher_page("cles"))
        self._ligne_etat(
            corps,
            "ma_chaine",
            "Ma chaîne de départ",
            "Renseigner",
            lambda: self._afficher_page("contenu"),
        )
        self._ligne_etat(
            corps,
            "positionnement",
            "Positionnement",
            "Éditer",
            lambda: self._ouvrir_contenu("positioning"),
        )
        self._ligne_etat(
            corps,
            "concurrence",
            "Seeds et mots-clés",
            "Éditer",
            lambda: self._ouvrir_contenu("keywords"),
        )
        self._ligne_etat(
            corps,
            "base",
            "Base locale",
            "Voir les résultats",
            lambda: self._afficher_page("resultats"),
        )

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "veille", "gauche"),
            "Lancer une veille",
            "Découverte → enrichissement → scoring → rapport, en une fois.",
            cle="veille",
        )
        cadre.pack(fill="x")
        bouton = self._bouton_accent(corps, "Tout lancer maintenant", self._lancer_depuis_accueil)
        bouton.pack(fill="x", pady=(2, 8))
        self._boutons_execution.append(bouton)
        self._bouton(corps, "Tester la configuration (1 unité)", self._tester_connexions).pack(fill="x")
        cache = ctk.CTkLabel(
            corps,
            text="Le cache local évite de repayer ce qui a déjà été lu. Les recherches ont "
            "leur propre compartiment : 100 appels par jour.",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            wraplength=520,
            justify="left",
            anchor="w",
        )
        cache.pack(anchor="w", fill="x", pady=(12, 0))
        self._auto_wrap(cache)

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "audit", "droite"),
            "Audit & stratégie",
            "Secteur, concurrents, stratégie et dix idées prêtes à tourner.",
            cle="audit",
        )
        cadre.pack(fill="x", pady=(0, 8))
        bouton = self._bouton_accent(
            corps, "Auditer ma chaîne", lambda: self._afficher_page("audit")
        )
        bouton.pack(fill="x")
        self._boutons_execution.append(bouton)

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "quota", "droite"),
            "Quota YouTube du jour",
            "Deux compartiments séparés, réinitialisés à minuit (PT).",
            cle="quota",
        )
        cadre.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            corps, text="Pool principal", font=(self._police, 13, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        self._barre_unites = Barre(corps, 8)
        self._barre_unites.pack(fill="x", pady=(6, 4))
        self._texte_unites = ctk.CTkLabel(
            corps, text="—", font=(self._police, 12), text_color=DOUX_FONCE, anchor="w"
        )
        self._texte_unites.pack(anchor="w")

        ctk.CTkLabel(
            corps, text="Recherches", font=(self._police, 13, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w", pady=(14, 0))
        self._barre_recherches = Barre(corps, 8)
        self._barre_recherches.pack(fill="x", pady=(6, 4))
        self._texte_recherches = ctk.CTkLabel(
            corps, text="—", font=(self._police, 12), text_color=DOUX_FONCE, anchor="w"
        )
        self._texte_recherches.pack(anchor="w")

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "studio", "droite"),
            "Studio YouTube",
            "Vues, rétention et audience de votre chaîne.",
            cle="studio",
        )
        cadre.pack(fill="x", pady=(0, 8))
        self._texte_studio_accueil = tk.StringVar(value="Aucune donnée pour l'instant.")
        studio_texte = ctk.CTkLabel(
            corps,
            textvariable=self._texte_studio_accueil,
            font=(self._police, 12),
            text_color=DOUX,
            justify="left",
            wraplength=330,
            anchor="w",
        )
        studio_texte.pack(anchor="w", fill="x")
        self._auto_wrap(studio_texte)
        self._bouton_accent(
            corps, "Ouvrir le Studio", lambda: self._afficher_page("studio")
        ).pack(fill="x", pady=(10, 0))

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "execution", "droite"),
            "Dernière exécution",
            cle="execution",
        )
        cadre.pack(fill="x", pady=(0, 8))
        self._texte_execution = ctk.CTkLabel(
            corps,
            text="Aucune exécution pour l'instant.",
            font=(self._police, 13),
            text_color=DOUX,
            justify="left",
            wraplength=330,
            anchor="w",
        )
        self._texte_execution.pack(anchor="w", fill="x")
        self._auto_wrap(self._texte_execution)

        cadre, corps = self._carte(
            self._conteneur_bloc("accueil", "rapport", "droite"),
            "Rapport",
            cle="rapport",
        )
        cadre.pack(fill="x")
        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x")
        actions = ctk.CTkFrame(rangee, fg_color="transparent")
        actions.pack(side="right", padx=(10, 0))
        self._bouton_rapport = self._lien(
            actions, "Ouvrir", lambda: self._ouvrir(self._chemin_rapport()), width=80
        )
        self._bouton_rapport.pack(side="left")
        self._lien(actions, "Dossier", lambda: self._ouvrir(self.racine / "out"), width=80).pack(
            side="left", padx=(8, 0)
        )
        self._texte_rapport = ctk.CTkLabel(
            rangee,
            text="Pas encore généré.",
            font=(self._police, 13),
            text_color=DOUX,
            justify="left",
            wraplength=190,
            anchor="w",
        )
        self._texte_rapport.pack(side="left", fill="x", expand=True)
        self._auto_wrap(self._texte_rapport)
        self._appliquer_disposition("accueil")

    def _ligne_etat(
        self, parent: tk.Misc, cle: str, titre: str, action: str, commande
    ) -> None:
        ligne = ctk.CTkFrame(parent, fg_color="transparent")
        ligne.pack(fill="x", pady=7)
        bouton = self._lien(ligne, action, commande, width=140)
        bouton.pack(side="right", padx=(12, 0))
        point = ctk.CTkLabel(
            ligne, text="●", width=16, font=(self._police, 14), text_color=ALERTE
        )
        point.pack(side="left", padx=(0, 10))
        textes = ctk.CTkFrame(ligne, fg_color="transparent")
        textes.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            textes, text=titre, font=(self._police, 14, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w")
        detail = ctk.CTkLabel(
            textes,
            text="…",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=430,
        )
        detail.pack(anchor="w", fill="x", expand=True)
        self._auto_wrap(detail)
        self._lignes_etat[cle] = (point, detail)

    # ------------------------------------------------------------------
    # Page — Mon contenu
    # ------------------------------------------------------------------
    def _page_contenu(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["contenu"] = page

        panneau = ctk.CTkFrame(page, fg_color="transparent")
        panneau.pack(fill="both", expand=True)

        droite = ctk.CTkFrame(panneau, fg_color=FOND, width=300)
        droite.pack(side="right", fill="y", padx=(16, 0))
        droite.pack_propagate(False)

        gauche = ctk.CTkFrame(panneau, fg_color="transparent")
        gauche.pack(side="left", fill="both", expand=True)

        barre = ctk.CTkFrame(gauche, fg_color="transparent")
        barre.pack(fill="x", pady=(0, 12))
        self._segment_contenu = ctk.CTkSegmentedButton(
            barre,
            values=list(NOMS_EDITEURS.values()),
            command=self._choisir_editeur,
            height=34,
            corner_radius=9,
            fg_color=SURFACE,
            selected_color=SURFACE_HAUTE,
            selected_hover_color=ACCENT_DOUX,
            unselected_color=SURFACE,
            unselected_hover_color=SURFACE_HAUTE,
            text_color=TEXTE,
            font=(self._police, 14),
        )
        self._segment_contenu.pack(side="left")
        self._bouton_accent(barre, "Enregistrer", self._sauvegarder, width=130).pack(side="right")
        self._bouton(barre, "Importer un document…", self._importer, width=186).pack(
            side="right", padx=(0, 8)
        )

        carte = ctk.CTkFrame(
            gauche, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        carte.pack(fill="both", expand=True)
        self._cadre_editeurs = ctk.CTkFrame(carte, fg_color="transparent")
        self._cadre_editeurs.pack(fill="both", expand=True, padx=12, pady=12)
        self._cadre_editeurs.grid_rowconfigure(0, weight=1)
        self._cadre_editeurs.grid_columnconfigure(0, weight=1)

        for cle in NOMS_EDITEURS:
            editeur = ctk.CTkTextbox(
                self._cadre_editeurs,
                fg_color=CHAMP,
                border_color=BORDURE,
                border_width=1,
                corner_radius=10,
                text_color=TEXTE,
                font=(self._police, 14),
                wrap="word",
                undo=True,
                scrollbar_button_color=SURFACE_HAUTE,
                scrollbar_button_hover_color=BORDURE,
            )
            editeur.grid(row=0, column=0, sticky="nsew")
            editeur.bind("<KeyRelease>", lambda _event, c=cle: self._maj_compteur(c))
            self._editeurs[cle] = editeur

        bas = ctk.CTkFrame(gauche, fg_color="transparent")
        bas.pack(fill="x", pady=(10, 0))
        self._aide_editeur = tk.StringVar(value=AIDE_EDITEURS["positioning"])
        aide = ctk.CTkLabel(
            bas,
            textvariable=self._aide_editeur,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=640,
        )
        aide.pack(side="left", fill="x", expand=True)
        self._auto_wrap(aide)
        self._compteur_actif = tk.StringVar(value="0 caractère")
        ctk.CTkLabel(
            bas, textvariable=self._compteur_actif, font=(self._police, 12), text_color=DOUX
        ).pack(side="right", padx=(12, 0))

        cadre, corps = self._carte(droite, "Point de départ")
        cadre.pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(
            corps,
            text="Votre chaîne, pour trouver des concurrents qui vous ressemblent.",
            font=(self._police, 12),
            text_color=DOUX,
            justify="left",
            wraplength=240,
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        self._entree_ma_chaine = ctk.CTkEntry(
            corps,
            textvariable=self._ma_chaine,
            height=36,
            corner_radius=8,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="@ma-chaine ou URL",
            font=(self._police, 14),
        )
        self._entree_ma_chaine.pack(fill="x")
        ctk.CTkLabel(
            corps,
            text="Optionnel. L'outil part aussi de vos seeds et de vos mots-clés.",
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            justify="left",
            wraplength=240,
            anchor="w",
        ).pack(anchor="w", pady=(6, 12))
        self._switch_international = ctk.CTkSwitch(
            corps,
            text="Chercher à l'international",
            variable=self._international,
            onvalue=True,
            offvalue=False,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            button_hover_color=ACCENT_SURVOL,
            text_color=TEXTE,
            font=(self._police, 13),
        )
        self._switch_international.pack(anchor="w")
        ctk.CTkLabel(
            corps,
            text="Chaque requête est lancée en France puis dans le monde. "
            "Décochez pour rester francophone.",
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            justify="left",
            wraplength=240,
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))

        cadre, corps = self._carte(droite, "Conseils")
        cadre.pack(fill="x")
        ctk.CTkLabel(
            corps,
            text="• Le positionnement est la référence de comparaison : soyez concret.\n"
            "• « Importer » lit Markdown, texte, JSON, HTML, OpenDocument et PDF.\n"
            "• Une ligne de mot-clé = une recherche, et deux passes avec l'international.\n"
            "• Les fichiers restent lisibles et modifiables à la main.",
            font=(self._police, 12),
            text_color=DOUX,
            justify="left",
            wraplength=244,
            anchor="w",
        ).pack(anchor="w")

        self._segment_contenu.set("Positionnement")
        self._choisir_editeur("Positionnement")
        self._charger_contenu()

    def _choisir_editeur(self, libelle: str) -> None:
        cle = CLES_EDITEURS.get(libelle, "positioning")
        self._editeurs[cle].tkraise()
        self._aide_editeur.set(AIDE_EDITEURS[cle])
        self._maj_compteur(cle, marquer=False)

    def _maj_compteur(self, cle: str, marquer: bool = True) -> None:
        texte = self._editeurs[cle].get("1.0", "end-1c")
        if cle == "positioning":
            nombre = len(texte)
            valeur = f"{_nombre(nombre)} caractère{'s' if nombre > 1 else ''}"
            if nombre < 200:
                valeur += " · 200 minimum"
        else:
            nombre = _lignes_utiles(texte)
            valeur = f"{_nombre(nombre)} ligne{'s' if nombre > 1 else ''}"
        self._compteurs.setdefault(cle, tk.StringVar())
        self._compteurs[cle].set(valeur)
        if self._segment_contenu.get() == NOMS_EDITEURS[cle]:
            self._compteur_actif.set(valeur)
        if marquer:
            self._marquer_modifie()

    def _ouvrir_contenu(self, cle: str) -> None:
        libelle = NOMS_EDITEURS.get(cle, "Positionnement")
        self._afficher_page("contenu")
        self._segment_contenu.set(libelle)
        self._choisir_editeur(libelle)

    # ------------------------------------------------------------------
    # Import de documents (Markdown, JSON, HTML, OpenDocument, PDF)
    # ------------------------------------------------------------------
    def _editeur_actif(self) -> str:
        return CLES_EDITEURS.get(self._segment_contenu.get(), "positioning")

    def _lignes_importees(self, documents_importes: list[documents.Document]) -> list[str]:
        """Lignes courtes exploitables, dédoublonnées sur l'ensemble des fichiers."""
        resultat: list[str] = []
        vues: set[str] = set()
        for document in documents_importes:
            for ligne in documents.lignes_candidates(document.texte):
                cle = ligne.casefold()
                if cle not in vues:
                    vues.add(cle)
                    resultat.append(ligne)
        return resultat

    @staticmethod
    def _bloc_positionnement(document: documents.Document, avec_source: bool) -> str:
        corps = document.texte.strip()
        return f"*Source : {document.nom}*\n\n{corps}" if avec_source else corps

    def _texte_importe(
        self, documents_importes: list[documents.Document], avec_sources: bool
    ) -> str:
        if self._editeur_actif() == "positioning":
            blocs = [
                self._bloc_positionnement(document, avec_sources)
                for document in documents_importes
            ]
            return "\n\n---\n\n".join(blocs).strip()
        return "\n".join(self._lignes_importees(documents_importes))

    def _importer(self) -> None:
        chemins = filedialog.askopenfilenames(
            parent=self,
            title="Importer un document",
            initialdir=str(self._dossier_import),
            filetypes=[
                (
                    "Documents texte",
                    "*.md *.markdown *.txt *.json *.html *.htm *.odt *.odf *.fodt *.pdf",
                ),
                ("Markdown et texte", "*.md *.markdown *.txt"),
                ("PDF", "*.pdf"),
                ("OpenDocument", "*.odt *.odf *.fodt"),
                ("JSON", "*.json"),
                ("HTML", "*.html *.htm"),
                ("Tous les fichiers", "*.*"),
            ],
        )
        if not chemins:
            return
        self._dossier_import = Path(chemins[0]).parent

        documents_importes: list[documents.Document] = []
        erreurs: list[str] = []
        for chemin in chemins:
            try:
                documents_importes.append(documents.extraire(chemin))
            except documents.ErreurDocument as probleme:
                erreurs.append(f"• {Path(chemin).name} : {probleme}")

        if erreurs:
            messagebox.showerror("Import impossible", "\n\n".join(erreurs), parent=self)
        if not documents_importes:
            return

        if self._editeur_actif() != "positioning" and not self._lignes_importees(
            documents_importes
        ):
            messagebox.showinfo(
                "Aucune ligne exploitable",
                "Ce document ne contient aucune ligne courte utilisable comme seed ou "
                "mot-clé. Importez-le plutôt dans « Positionnement ».",
                parent=self,
            )
            return

        self._modale_import(documents_importes)

    def _modale_import(self, documents_importes: list[documents.Document]) -> None:
        """Aperçu du texte extrait, puis remplacement ou ajout à la fin."""
        cle = self._editeur_actif()
        libelle = NOMS_EDITEURS[cle]
        apercu = self._texte_importe(documents_importes, len(documents_importes) > 1)

        fenetre = ctk.CTkToplevel(self)
        fenetre.title("Importer un document")
        fenetre.configure(fg_color=FOND)
        fenetre.transient(self)
        fenetre.resizable(False, False)

        largeur, hauteur = 680, 560
        x = self.winfo_rootx() + max(0, (self.winfo_width() - largeur) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - hauteur) // 2)
        fenetre.geometry(f"{largeur}x{hauteur}+{x}+{y}")

        corps = ctk.CTkFrame(fenetre, fg_color="transparent")
        corps.pack(fill="both", expand=True, padx=22, pady=20)
        ctk.CTkLabel(
            corps,
            text=f"Importer vers « {libelle} »",
            font=(self._police, 18, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            corps,
            text="Vérifiez le texte extrait avant de l'ajouter. Rien n'est écrit sur le "
            "disque avant Ctrl+S.",
            font=(self._police, 12),
            text_color=DOUX,
            anchor="w",
            justify="left",
            wraplength=620,
        ).pack(anchor="w", pady=(2, 10))

        for document in documents_importes:
            ctk.CTkLabel(
                corps,
                text=f"{document.nom} · {document.format} · "
                f"{_nombre(len(document.texte))} caractères",
                font=(self._police, 12, "bold"),
                text_color=TEXTE,
                anchor="w",
            ).pack(anchor="w")
            for avertissement in document.avertissements:
                ctk.CTkLabel(
                    corps,
                    text=avertissement,
                    font=(self._police, 11),
                    text_color=ALERTE,
                    anchor="w",
                    justify="left",
                    wraplength=620,
                ).pack(anchor="w")

        if cle != "positioning":
            ctk.CTkLabel(
                corps,
                text=f"{_nombre(len(self._lignes_importees(documents_importes)))} ligne(s) "
                "courte(s) retenue(s), une par entrée.",
                font=(self._police, 12),
                text_color=DOUX,
                anchor="w",
            ).pack(anchor="w", pady=(4, 0))

        zone = ctk.CTkTextbox(
            corps,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            corner_radius=10,
            text_color=TEXTE,
            font=(self._mono, 12),
            wrap="word",
        )
        zone.pack(fill="both", expand=True, pady=(10, 12))
        limite = 6000
        zone.insert(
            "1.0",
            apercu[:limite] + ("\n\n[…] aperçu tronqué" if len(apercu) > limite else ""),
        )
        zone.configure(state="disabled")

        choix = {"mode": None}

        def terminer(mode: str | None) -> None:
            choix["mode"] = mode
            try:
                fenetre.grab_release()
            except tk.TclError:
                pass
            fenetre.destroy()

        boutons = ctk.CTkFrame(corps, fg_color="transparent")
        boutons.pack(fill="x")
        self._lien(boutons, "Annuler", lambda: terminer(None), width=100).pack(side="left")
        self._bouton_accent(
            boutons,
            "Ajouter à la fin" if cle == "positioning" else "Ajouter les lignes",
            lambda: terminer("ajouter"),
            width=190,
        ).pack(side="right")
        self._bouton(boutons, "Remplacer", lambda: terminer("remplacer"), width=118).pack(
            side="right", padx=(0, 8)
        )

        fenetre.protocol("WM_DELETE_WINDOW", lambda: terminer(None))
        fenetre.bind("<Escape>", lambda _event: terminer(None))
        fenetre.grab_set()
        fenetre.focus_set()
        self.wait_window(fenetre)

        if choix["mode"]:
            self._appliquer_import(documents_importes, choix["mode"])

    def _appliquer_import(
        self, documents_importes: list[documents.Document], mode: str
    ) -> None:
        """Insère le texte extrait dans l'éditeur visible, sans écrire de fichier."""
        cle = self._editeur_actif()
        editeur = self._editeurs[cle]
        actuel = editeur.get("1.0", "end-1c")

        if mode == "remplacer" or not actuel.strip():
            nouveau = self._texte_importe(documents_importes, len(documents_importes) > 1)
            if not nouveau:
                return
            nouveau += "\n"
        elif cle == "positioning":
            blocs = [actuel.rstrip()]
            blocs.extend(
                self._bloc_positionnement(document, True) for document in documents_importes
            )
            nouveau = "\n\n---\n\n".join(blocs) + "\n"
        else:
            existantes = {
                ligne.strip().casefold() for ligne in actuel.splitlines() if ligne.strip()
            }
            ajouts = [
                ligne
                for ligne in self._lignes_importees(documents_importes)
                if ligne.casefold() not in existantes
            ]
            if not ajouts:
                self._message.set("Aucune nouvelle ligne à ajouter.")
                return
            nouveau = actuel.rstrip("\n") + "\n" + "\n".join(ajouts) + "\n"

        editeur.delete("1.0", "end")
        editeur.insert("1.0", nouveau)
        self._maj_compteur(cle)
        self._message.set(
            f"Document importé dans « {NOMS_EDITEURS[cle]} » — enregistrez avec Ctrl+S."
        )

    # ------------------------------------------------------------------
    # Page — Clés et modèle
    # ------------------------------------------------------------------
    def _ouvrir_guides(self) -> None:
        """Ouvre le dossier des guides fournisseur, ou son index."""
        import os
        import subprocess
        import sys

        dossier = Path(self.racine) / "guides"
        index = dossier / "README.md"
        try:
            if sys.platform == "win32":
                os.startfile(str(index if index.is_file() else dossier))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(index if index.is_file() else dossier)])
            else:
                subprocess.Popen(["xdg-open", str(index if index.is_file() else dossier)])
        except OSError as probleme:
            self._message.set(f"Ouverture des guides impossible : {probleme}")

    def _page_cles(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["cles"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        cadre, corps = self._carte(
            contenu,
            "YouTube Data API v3",
            "La clé est écrite dans .env, exclu de git. Elle ne quitte jamais ce PC.",
        )
        cadre.pack(fill="x")
        self._champ(
            corps,
            "Clé YouTube",
            "YOUTUBE_API_KEY",
            secret=True,
            placeholder="AIza…",
            aide="Console Google Cloud → YouTube Data API v3 → Identifiants. "
            "Un seul projet : le partage de quota est interdit.",
        )

        cadre, corps = self._carte(
            contenu,
            "Fournisseur LLM",
            "Format OpenAI Chat Completions. Choisissez un fournisseur : son URL de base "
            "et un modèle sont préremplis automatiquement.",
        )
        cadre.pack(fill="x", pady=(14, 0))

        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            rangee, text="Fournisseur", font=(self._police, 13, "bold"), text_color=TEXTE
        ).pack(side="left", padx=(0, 10))
        self._fournisseur = tk.StringVar(value="Gemini")
        ctk.CTkOptionMenu(
            rangee,
            values=[*FOURNISSEURS, "Personnalisé"],
            variable=self._fournisseur,
            command=self._preset,
            width=200,
            height=32,
            corner_radius=8,
            fg_color=CHAMP,
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=ACCENT_DOUX,
            dropdown_text_color=TEXTE,
            text_color=TEXTE,
            font=(self._police, 13),
            dropdown_font=(self._police, 13),
        ).pack(side="left")

        note = ctk.CTkLabel(
            corps,
            text="Ollama Cloud : https://ollama.com/v1 — créez une clé sur ollama.com "
            "(Settings → Keys).\n"
            "Ollama local : http://localhost:11434/v1 — aucune clé ; Ollama doit tourner "
            "sur ce PC.",
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            justify="left",
            anchor="w",
            wraplength=640,
        )
        note.pack(anchor="w", fill="x", pady=(0, 12))
        self._auto_wrap(note)

        self._bouton(
            corps,
            "Ouvrir les guides d'installation",
            self._ouvrir_guides,
            width=240,
        ).pack(anchor="w", pady=(0, 12))

        self._champ(corps, "URL de base", "LLM_BASE_URL", placeholder="https://…/v1")
        self._champ(
            corps, "Modèle", "LLM_MODEL", placeholder="gemini-3.5-flash-lite",
            aide="Le modèle proposé est un point de départ : vérifiez son nom exact avec "
            "« Tester la configuration », ou listez-les avec doctor --probe.",
        )
        self._champ(
            corps,
            "Clé du fournisseur",
            "LLM_API_KEY",
            secret=True,
            placeholder="Vide pour Ollama local",
            aide="Ollama Cloud : collez ici la clé créée sur ollama.com. "
            "Ollama local : laissez ce champ vide, aucun mot de passe n'est demandé.",
        )

        cadre, corps = self._carte(
            contenu,
            "Fournisseur de secours",
            "Optionnel. Utilisé si le principal renvoie des erreurs de débit persistantes.",
        )
        cadre.pack(fill="x", pady=(14, 0))
        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            rangee, text="Fournisseur", font=(self._police, 13, "bold"), text_color=TEXTE
        ).pack(side="left", padx=(0, 10))
        self._fournisseur_secours = tk.StringVar(value="Aucun")
        ctk.CTkOptionMenu(
            rangee,
            values=list(FOURNISSEURS_SECOURS),
            variable=self._fournisseur_secours,
            command=lambda nom: self._preset(nom, secours=True),
            width=200,
            height=32,
            corner_radius=8,
            fg_color=CHAMP,
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=ACCENT_DOUX,
            dropdown_text_color=TEXTE,
            text_color=TEXTE,
            font=(self._police, 13),
            dropdown_font=(self._police, 13),
        ).pack(side="left")
        self._champ(corps, "URL de base (secours)", "LLM_FALLBACK_BASE_URL")
        self._champ(corps, "Modèle (secours)", "LLM_FALLBACK_MODEL")
        self._champ(
            corps,
            "Clé (secours)",
            "LLM_FALLBACK_API_KEY",
            secret=True,
            placeholder="Vide pour Ollama local",
        )

        cadre, corps = self._carte(
            contenu,
            "Recherche web (Exa)",
            "Optionnel : nécessaire uniquement au mode « Avec IA » de la page Recherche. "
            "Sans clé, le mode sans IA continue de fonctionner avec Wikipédia.",
        )
        cadre.pack(fill="x", pady=(14, 0))
        self._champ(
            corps,
            "Clé Exa",
            "EXA_API_KEY",
            secret=True,
            placeholder="exa-…",
            aide="Créez une clé sur dashboard.exa.ai/api-keys. Elle reste dans .env, "
            "sur ce PC. Exa renvoie des extraits de sites extérieurs au dossier.",
        )
        self._lien(
            corps,
            "Ouvrir la console Exa",
            lambda: webbrowser.open("https://dashboard.exa.ai/api-keys"),
            width=180,
        ).pack(anchor="w", pady=(4, 0))

        actions = ctk.CTkFrame(contenu, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))
        self._montrer = tk.BooleanVar(value=False)
        ctk.CTkSwitch(
            actions,
            text="Afficher les clés",
            variable=self._montrer,
            command=self._basculer_masque,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 13),
        ).pack(side="left")
        self._bouton_accent(actions, "Enregistrer", self._sauvegarder, width=130).pack(side="right")
        self._bouton(actions, "Tester (1 unité)", self._tester_connexions, width=150).pack(
            side="right", padx=(0, 10)
        )
        self._charger_cles()

    def _preset(self, nom: str, secours: bool = False) -> None:
        """Préremplit l'URL de base et le modèle du fournisseur choisi."""
        prefixe = "LLM_FALLBACK_" if secours else "LLM_"
        if nom == "Aucun":
            for suffixe in ("BASE_URL", "MODEL", "API_KEY"):
                variable = self._env_vars.get(prefixe + suffixe)
                if variable is not None:
                    variable.set("")
            self._message.set("Fournisseur de secours désactivé. Enregistrez pour appliquer.")
            return
        if nom not in FOURNISSEURS:
            return
        base, modele = FOURNISSEURS[nom]
        self._env_vars[prefixe + "BASE_URL"].set(base)
        self._env_vars[prefixe + "MODEL"].set(modele)
        self._message.set(
            f"{nom} pré-rempli ({base}). Enregistrez pour appliquer."
        )

    @staticmethod
    def _fournisseur_depuis(base_url: str) -> str:
        """Retrouve le nom du fournisseur à partir de son URL de base."""
        normalisee = (base_url or "").strip().rstrip("/").lower()
        if not normalisee:
            return "Gemini"
        for nom, (base, _) in FOURNISSEURS.items():
            if normalisee == base.rstrip("/").lower():
                return nom
        if "ollama.com" in normalisee:
            return "Ollama Cloud"
        if "ollama" in normalisee or "11434" in normalisee:
            return "Ollama local"
        return "Personnalisé"

    def _basculer_masque(self) -> None:
        for entree in self._entrees_secretes:
            entree.configure(show="" if self._montrer.get() else "*")

    # ------------------------------------------------------------------
    # Page — Réglages
    # ------------------------------------------------------------------
    def _page_reglages(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["reglages"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        # Apparence & confort : préférences propres à ce PC, appliquées tout de
        # suite. Elles ne touchent jamais à config.yaml ni à la veille.
        cadre, corps = self._carte(
            contenu,
            "Apparence & confort",
            "Réglages de l'interface, mémorisés dans data/preferences.json et appliqués "
            "immédiatement. Aucun effet sur les exécutions.",
        )
        cadre.pack(fill="x", pady=(0, 14))
        apparence = ctk.CTkFrame(corps, fg_color="transparent")
        apparence.pack(fill="x")
        apparence.grid_columnconfigure(0, weight=1)
        apparence.grid_columnconfigure(1, weight=1)

        bloc_theme = ctk.CTkFrame(apparence, fg_color="transparent")
        bloc_theme.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        ctk.CTkLabel(
            bloc_theme, text="Thème", font=(self._police, 13, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w")
        ctk.CTkLabel(
            bloc_theme,
            text="Le sombre fatigue moins les yeux sur une longue session.",
            font=(self._police, 12),
            text_color=DOUX,
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(2, 6))
        ctk.CTkSegmentedButton(
            bloc_theme,
            values=("Clair", "Sombre"),
            variable=self._theme_var,
            command=self._changer_theme,
            height=32,
            corner_radius=6,
            fg_color=SURFACE_HAUTE,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_SURVOL,
            unselected_color=SURFACE_HAUTE,
            unselected_hover_color=BORDURE,
            text_color=TEXTE,
            font=(self._police, 12),
        ).pack(fill="x")

        bloc_texte = ctk.CTkFrame(apparence, fg_color="transparent")
        bloc_texte.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            bloc_texte,
            text="Taille du texte des tableaux",
            font=(self._police, 13, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            bloc_texte,
            text="« Grande » agrandit les lignes sans rien tronquer.",
            font=(self._police, 12),
            text_color=DOUX,
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(2, 6))
        ctk.CTkSegmentedButton(
            bloc_texte,
            values=("Normale", "Grande"),
            variable=self._taille_tableau_var,
            command=self._changer_taille_tableau,
            height=32,
            corner_radius=6,
            fg_color=SURFACE_HAUTE,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_SURVOL,
            unselected_color=SURFACE_HAUTE,
            unselected_hover_color=BORDURE,
            text_color=TEXTE,
            font=(self._police, 12),
        ).pack(fill="x")

        bascules = ctk.CTkFrame(corps, fg_color="transparent")
        bascules.pack(fill="x", pady=(14, 0))
        bascules.grid_columnconfigure(0, weight=1)
        bascules.grid_columnconfigure(1, weight=1)
        for index, (texte, variable, commande) in enumerate(
            (
                ("Ombres portées sous les cartes", self._ombres_var, self._basculer_ombres),
                (
                    "Demander confirmation avant une suppression",
                    self._confirmer_var,
                    lambda: self._enregistrer_preference(
                        "confirmer_suppression", bool(self._confirmer_var.get())
                    ),
                ),
                (
                    "Rouvrir la dernière page au démarrage",
                    self._demarrage_var,
                    self._basculer_demarrage,
                ),
            )
        ):
            ctk.CTkSwitch(
                bascules,
                text=texte,
                variable=variable,
                command=commande,
                progress_color=ACCENT_CLAIR,
                button_color=ACCENT,
                text_color=TEXTE,
                font=(self._police, 12),
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 30), pady=6)

        grille = ctk.CTkFrame(contenu, fg_color="transparent")
        grille.pack(fill="x")
        grille.grid_columnconfigure(0, weight=1, uniform="reglages")
        grille.grid_columnconfigure(1, weight=1, uniform="reglages")

        cadre, corps = self._carte(
            grille, "Découverte & quotas", "Les filtres s'appliquent sans aucun appel réseau."
        )
        cadre.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for section, cle, libelle, mini, maxi in REGLAGES_DECOUVERTE:
            self._ligne_reglage(corps, section, cle, libelle, mini, maxi)

        cadre, corps = self._carte(
            grille, "Métriques & LLM", "Le scoring LLM est le principal coût en temps."
        )
        cadre.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        for section, cle, libelle, mini, maxi in REGLAGES_LLM:
            self._ligne_reglage(corps, section, cle, libelle, mini, maxi)

        cadre, corps = self._carte(
            contenu,
            "Options d'exploration",
            "Chaque option ajoute des appels : activez ce qui vous sert. "
            "Le point de départ se règle dans Mon contenu.",
        )
        cadre.pack(fill="x", pady=(14, 0))
        options = ctk.CTkFrame(corps, fg_color="transparent")
        options.pack(fill="x")
        for index, (section, cle, libelle) in enumerate(BASCULES):
            variable = self._bascule[(section, cle)]
            ctk.CTkSwitch(
                options,
                text=libelle,
                variable=variable,
                progress_color=ACCENT_CLAIR,
                button_color=ACCENT,
                text_color=TEXTE,
                font=(self._police, 13),
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 30), pady=7)
        options.grid_columnconfigure(0, weight=1)
        options.grid_columnconfigure(1, weight=1)

        actions = ctk.CTkFrame(contenu, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))
        ctk.CTkLabel(
            actions,
            text="config.yaml conserve ses commentaires : seules les valeurs modifiées sont réécrites.",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="left")
        self._bouton_accent(actions, "Enregistrer", self._sauvegarder, width=130).pack(side="right")
        self._charger_reglages()

    def _ligne_reglage(
        self, parent: tk.Misc, section: str, cle: str, libelle: str, mini: int, maxi: int
    ) -> None:
        ligne = ctk.CTkFrame(parent, fg_color="transparent")
        ligne.pack(fill="x", pady=4)
        ctk.CTkLabel(
            ligne, text=libelle, font=(self._police, 13), text_color=DOUX, anchor="w"
        ).pack(side="left")
        variable = tk.StringVar()
        variable.trace_add("write", self._marquer_modifie)
        self._reglages[(section, cle)] = variable
        ctk.CTkEntry(
            ligne,
            textvariable=variable,
            width=96,
            height=30,
            corner_radius=7,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            justify="right",
            font=(self._police, 13),
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Page — Rapports e-mail
    # ------------------------------------------------------------------
    def _variable_email(self, cle: str) -> tk.StringVar:
        variable = self._champs_email.get(cle)
        if variable is None:
            variable = tk.StringVar()
            variable.trace_add("write", self._marquer_modifie)
            self._champs_email[cle] = variable
        return variable

    def _champ_email(
        self,
        parent: tk.Misc,
        cle: str,
        libelle: str,
        aide: str = "",
        placeholder: str = "",
        largeur: int | None = None,
    ) -> ctk.CTkEntry:
        bloc = ctk.CTkFrame(parent, fg_color="transparent")
        bloc.pack(
            fill="x" if largeur is None else None,
            pady=(2, 10),
            anchor="w",
            side="top" if largeur is None else "left",
        )
        ctk.CTkLabel(
            bloc, text=libelle, font=(self._police, 13, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w")
        entree = ctk.CTkEntry(
            bloc,
            textvariable=self._variable_email(cle),
            height=36,
            width=largeur or 140,
            corner_radius=8,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text=placeholder,
            font=(self._police, 14),
        )
        entree.pack(fill="x" if largeur is None else None, pady=(4, 0), anchor="w")
        if aide:
            explication = ctk.CTkLabel(
                bloc,
                text=aide,
                font=(self._police, 12),
                text_color=DOUX_FONCE,
                justify="left",
                wraplength=420,
                anchor="w",
            )
            explication.pack(anchor="w", fill="x", pady=(3, 0))
            self._auto_wrap(explication)
        return entree

    def _bascule_email(self, parent: tk.Misc, cle: str, libelle: str) -> ctk.CTkSwitch:
        variable = tk.BooleanVar()
        variable.trace_add("write", self._marquer_modifie)
        self._bascule[("email", cle)] = variable
        return ctk.CTkSwitch(
            parent,
            text=libelle,
            variable=variable,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=TEXTE,
            font=(self._police, 13),
        )

    def _page_email(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["email"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        # --- État -----------------------------------------------------------
        cadre, corps = self._carte(
            contenu,
            "Envoi automatique",
            "Le rapport part en HTML, avec une version texte et, si vous le souhaitez, "
            "les CSV joints. Aucun quota YouTube n'est consommé par un envoi.",
        )
        cadre.pack(fill="x")
        tuiles = ctk.CTkFrame(corps, fg_color="transparent")
        tuiles.pack(fill="x")
        for colonne in range(3):
            tuiles.grid_columnconfigure(colonne, weight=1, uniform="email")
        for colonne, (cle, titre) in enumerate(
            (
                ("dernier", "DERNIER ENVOI"),
                ("prochain", "PROCHAIN ENVOI"),
                ("tache", "TÂCHE WINDOWS"),
            )
        ):
            tuile = ctk.CTkFrame(tuiles, fg_color=SURFACE_HAUTE, corner_radius=10)
            tuile.grid(row=0, column=colonne, sticky="nsew", padx=(0, 8) if colonne < 2 else 0)
            ctk.CTkLabel(
                tuile,
                text=titre,
                font=(self._police, 11, "bold"),
                text_color=DOUX_FONCE,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(10, 0))
            self._email_statut[cle] = ctk.CTkLabel(
                tuile,
                text="—",
                font=(self._police, 14, "bold"),
                text_color=TEXTE,
                anchor="w",
            )
            self._email_statut[cle].pack(anchor="w", padx=12, pady=(2, 11))

        self._email_note = ctk.CTkLabel(
            corps,
            text="",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            justify="left",
            anchor="w",
            wraplength=720,
        )
        self._email_note.pack(anchor="w", fill="x", pady=(10, 0))
        self._auto_wrap(self._email_note)

        # --- Réglages -------------------------------------------------------
        grille = ctk.CTkFrame(contenu, fg_color="transparent")
        grille.pack(fill="x", pady=(14, 0))
        grille.grid_columnconfigure(0, weight=1, uniform="email")
        grille.grid_columnconfigure(1, weight=1, uniform="email")

        cadre, corps = self._carte(
            grille,
            "Compte d'envoi",
            "L'expéditeur et le mot de passe sont fixés dans .env : ils ne se modifient "
            "pas depuis l'interface. Vous ne choisissez que l'adresse de réception.",
        )
        cadre.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(
            corps,
            text="EXPÉDITEUR  outly.analyze@gmail.com",
            font=(self._police, 12, "bold"),
            text_color=ACCENT_TEXTE,
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        self._email_mdp = ctk.CTkLabel(
            corps,
            text="MOT DE PASSE  vérification…",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            justify="left",
            anchor="w",
            wraplength=380,
        )
        self._email_mdp.pack(anchor="w", pady=(0, 12))
        self._champ_email(
            corps,
            "destinataire",
            "Adresse de réception",
            placeholder="vous@exemple.fr",
            aide="La seule adresse à saisir : elle reçoit les rapports, elle n'envoie rien.",
        )

        cadre, corps = self._carte(
            grille,
            "Rythme",
            "La fréquence est vérifiée à chaque contrôle : un envoi manqué part une seule "
            "fois au contrôle suivant.",
        )
        cadre.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(
            corps, text="Fréquence", font=(self._police, 13, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w")
        self._segment_email = ctk.CTkSegmentedButton(
            corps,
            values=list(FREQUENCES_UI),
            variable=self._frequence,
            command=self._frequence_changee,
            height=34,
            corner_radius=9,
            fg_color=SURFACE_HAUTE,
            selected_color=ACCENT_DOUX,
            selected_hover_color=ACCENT_DOUX,
            unselected_color=SURFACE,
            unselected_hover_color=SURFACE_HAUTE,
            text_color=TEXTE,
            font=(self._police, 13),
        )
        self._segment_email.pack(fill="x", pady=(6, 12))
        self._champ_email(
            corps,
            "heure",
            "Heure du contrôle",
            placeholder="08:00",
            aide="Heure locale. Après l'avoir changée, reprogrammez la tâche Windows.",
        )
        jour_ligne = ctk.CTkFrame(corps, fg_color="transparent")
        jour_ligne.pack(fill="x", pady=(2, 10))
        ctk.CTkLabel(
            jour_ligne,
            text="Jour (hebdomadaire)",
            font=(self._police, 13, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkOptionMenu(
            jour_ligne,
            values=list(JOURS_UI),
            variable=self._jour_semaine,
            height=34,
            corner_radius=8,
            fg_color=CHAMP,
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
            text_color=TEXTE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=SURFACE_HAUTE,
            dropdown_text_color=TEXTE,
            font=(self._police, 13),
        ).pack(anchor="w", pady=(4, 0))
        self._champ_email(
            corps,
            "jour_mois",
            "Jour du mois (mensuel)",
            placeholder="1",
            aide="De 1 à 28 : les 29, 30 et 31 n'existent pas tous les mois.",
        )
        self._bascule_email(corps, "joindre_csv", "Joindre channels.csv et outliers.csv")

        actions = ctk.CTkFrame(contenu, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))
        self._bouton_accent(actions, "Enregistrer", self._sauvegarder, width=120).pack(side="right")
        bouton_test = self._bouton(actions, "Envoyer un test", self._envoyer_test, width=142)
        bouton_test.pack(side="right", padx=(0, 8))
        self._email_actions.extend(
            [
                self._bouton(
                    actions, "Programmer sur ce PC", self._programmer_email, width=178
                ),
                self._bouton(actions, "Retirer", self._retirer_programmation, width=92),
            ]
        )
        for bouton in reversed(self._email_actions):
            bouton.pack(side="right", padx=(0, 8))
        self._charger_email()

    def _frequence_changee(self, _valeur=None) -> None:
        self._marquer_modifie()
        if self._email_note is None:
            return
        if self._frequence.get() == "Jamais":
            self._message.set("Envoi automatique désactivé : l'envoi de test reste disponible.")
        else:
            self._message.set(
                "Fréquence choisie. « Programmer sur ce PC » crée la tâche Windows quotidienne."
            )

    def _rafraichir_email_statut(self) -> None:
        if self._email_note is None or not self._email_statut:
            return  # page constuite à sa première ouverture
        etat = data.etat_email(self.racine)
        if etat.dernier is not None:
            self._email_statut["dernier"].configure(
                text=etat.dernier.strftime("%d/%m/%Y à %H:%M"), text_color=TEXTE
            )
        else:
            self._email_statut["dernier"].configure(text="Aucun", text_color=DOUX)

        if not etat.actif:
            self._email_statut["prochain"].configure(text="Désactivé", text_color=DOUX)
        elif etat.en_attente:
            self._email_statut["prochain"].configure(text="En attente d'envoi", text_color=ALERTE)
        elif etat.prochain is not None:
            self._email_statut["prochain"].configure(
                text=etat.prochain.strftime("%d/%m/%Y à %H:%M"), text_color=TEXTE
            )
        else:
            self._email_statut["prochain"].configure(text="—", text_color=DOUX)

        if self._email_mdp is not None:
            if etat.mot_de_passe:
                self._email_mdp.configure(
                    text="MOT DE PASSE  configuré dans .env (non modifiable ici)",
                    text_color=OK,
                )
            else:
                self._email_mdp.configure(
                    text="MOT DE PASSE  absent de .env — ajoutez SMTP_PASSWORD",
                    text_color=ALERTE,
                )

        if self._email_note is not None:
            if etat.problemes:
                self._email_note.configure(
                    text="À compléter avant le premier envoi : " + "; ".join(etat.problemes) + ".",
                    text_color=ALERTE,
                )
            elif etat.actif:
                self._email_note.configure(
                    text=f"Destinataire : {etat.destinataire or '—'} · fréquence : "
                    f"{FREQUENCES_INVERSES.get(etat.frequence, etat.frequence)} à {etat.heure}. "
                    "L'envoi automatique ne fonctionne que si la tâche Windows est programmée.",
                    text_color=DOUX_FONCE,
                )
            else:
                self._email_note.configure(
                    text="Choisissez une fréquence puis cliquez sur « Programmer sur ce PC ». "
                    "Sans tâche Windows, aucun envoi ne part automatiquement.",
                    text_color=DOUX_FONCE,
                )
        self._lire_tache_en_fond()

    def _depuis_fil(self, fonction) -> None:
        """Planifie un retour dans le fil Tk, même si la fenêtre se ferme.

        Un fil de fond (requête du planificateur) peut se terminer après
        `destroy()` : `after` lèverait alors RuntimeError. Ce n'est pas une
        erreur utile pour l'utilisateur, on l'ignore.
        """
        try:
            self.after(0, fonction)
        except (RuntimeError, tk.TclError):
            pass

    def _lire_tache_en_fond(self) -> None:
        if sys.platform != "win32":
            self._email_statut["tache"].configure(text="Windows uniquement", text_color=DOUX)
            for bouton in self._email_actions:
                bouton.configure(state="disabled")
            return

        def travail() -> None:
            programme = planification.tache_programmee()
            self._depuis_fil(lambda: self._afficher_tache(programme))

        threading.Thread(target=travail, name="outly-tache-email", daemon=True).start()

    def _afficher_tache(self, programme: bool) -> None:
        etiquette = self._email_statut.get("tache")
        if etiquette is None:
            return
        etiquette.configure(
            text="Programmée" if programme else "Non programmée",
            text_color=OK if programme else DOUX,
        )

    def _programmer_email(self) -> None:
        if sys.platform != "win32":
            messagebox.showinfo(
                "Windows uniquement",
                "La planification automatique s'appuie sur le Planificateur de tâches Windows.",
                parent=self,
            )
            return
        if not self._sauvegarder():
            return
        if self._frequence.get() == "Jamais":
            messagebox.showinfo(
                "Envoi désactivé",
                "Choisissez d'abord une fréquence autre que « Jamais ». La tâche "
                "quotidienne ne fait qu'appeler l'outil, qui décide ensuite s'il doit envoyer.",
                parent=self,
            )
            return
        heure = self._champs_email["heure"].get().strip()
        if not messagebox.askyesno(
            "Programmer sur ce PC",
            f"Créer une tâche Windows qui vérifiera l'envoi chaque jour à {heure} ?\n\n"
            "Si le PC est éteint à cette heure-là, l'envoi sera rattrapé au démarrage "
            "suivant. Aucun droit administrateur n'est nécessaire.",
            parent=self,
        ):
            return
        for bouton in self._email_actions:
            bouton.configure(state="disabled")
        self._message.set("Programmation de la tâche Windows…")

        def travail() -> None:
            ok, detail = planification.creer_tache(self.racine, heure)
            self._depuis_fil(lambda: self._fin_planification(ok, detail))

        threading.Thread(target=travail, name="outly-planification", daemon=True).start()

    def _retirer_programmation(self) -> None:
        if sys.platform != "win32":
            return
        if not messagebox.askyesno(
            "Retirer la tâche",
            "Retirer la tâche Windows ? L'envoi automatique s'arrête, les réglages "
            "et les rapports déjà envoyés sont conservés.",
            parent=self,
        ):
            return
        for bouton in self._email_actions:
            bouton.configure(state="disabled")
        self._message.set("Retrait de la tâche Windows…")

        def travail() -> None:
            ok, detail = planification.supprimer_tache()
            self._depuis_fil(lambda: self._fin_planification(ok, detail))

        threading.Thread(target=travail, name="outly-planification", daemon=True).start()

    def _fin_planification(self, ok: bool, detail: str) -> None:
        for bouton in self._email_actions:
            bouton.configure(state="normal")
        self._message.set(detail)
        if ok:
            messagebox.showinfo("Rapports e-mail", detail, parent=self)
        else:
            messagebox.showerror("Planification impossible", detail, parent=self)
        self._rafraichir_email_statut()

    def _envoyer_test(self) -> None:
        if not self._sauvegarder():
            return
        if not data.etat_email(self.racine).mot_de_passe:
            messagebox.showerror(
                "Envoi impossible",
                "Le mot de passe d'application Gmail n'est pas configuré. Ajoutez "
                "SMTP_PASSWORD dans .env : il se crée sur "
                "myaccount.google.com/apppasswords (compte à deux facteurs activé).",
                parent=self,
            )
            self._afficher_page("email")
            return
        self._afficher_page("lancer")
        self._lancer(["mail", "--test"], "Test e-mail")

    def _collecter_email(self) -> dict[tuple[str, str], object]:
        """Valeurs e-mail validées, prêtes pour `config.yaml`.

        Le mot de passe d'application (SMTP_PASSWORD) reste dans `.env` : il est
        seulement relu pour vérifier que l'envoi peut fonctionner. L'expéditeur
        et le serveur Gmail ne sont jamais écrits par l'interface.
        """
        if not self._champs_email:
            return {}  # page Rapports e-mail jamais ouverte : ne rien réécrire
        valeurs: dict[tuple[str, str], object] = {}
        for cle, variable in self._champs_email.items():
            valeurs[("email", cle)] = variable.get().strip()
        brut = str(valeurs.get(("email", "jour_mois"), ""))
        if not re.fullmatch(r"\d+", brut):
            raise ValueError("« Jour du mois » doit être un nombre entier.")
        valeurs[("email", "jour_mois")] = int(brut)

        frequence = FREQUENCES_UI.get(self._frequence.get(), "jamais")
        valeurs[("email", "frequence")] = frequence
        valeurs[("email", "jour_semaine")] = (
            emailing.JOURS_SEMAINE.index(self._jour_semaine.get().lower()) + 1
        )

        heure = str(valeurs[("email", "heure")])
        if not emailing.MOTIF_HEURE.match(heure):
            raise ValueError("« Heure du contrôle » doit être au format HH:MM (par exemple 08:00).")
        jour_mois = int(valeurs[("email", "jour_mois")])
        if not 1 <= jour_mois <= 28:
            raise ValueError("« Jour du mois » doit être compris entre 1 et 28.")

        destinataire = str(valeurs[("email", "destinataire")])
        if destinataire and not emailing.MOTIF_EMAIL.match(destinataire):
            raise ValueError(
                f"« Adresse de réception » n'est pas une adresse e-mail valide ({destinataire})."
            )

        if frequence != "jamais" and not destinataire:
            raise ValueError(
                "Pour activer l'envoi automatique, renseignez l'adresse de réception."
            )
        return valeurs

    # ------------------------------------------------------------------
    # Page — Lancer
    # ------------------------------------------------------------------
    def _page_lancer(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["lancer"] = page

        panneau = ctk.CTkFrame(page, fg_color="transparent")
        panneau.pack(fill="both", expand=True)
        gauche = ctk.CTkFrame(panneau, fg_color=FOND, width=300)
        gauche.pack(side="left", fill="y", padx=(0, 14))
        gauche.pack_propagate(False)

        cadre, corps = self._carte(
            gauche, "Exécution complète", "Découverte, enrichissement, scoring, rapport."
        )
        cadre.pack(fill="x", pady=(0, 14))
        ligne = ctk.CTkFrame(corps, fg_color="transparent")
        ligne.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            ligne, text="Appels de recherche max", font=(self._police, 13), text_color=DOUX
        ).pack(side="left")
        ctk.CTkEntry(
            ligne,
            textvariable=self._option_recherches,
            width=72,
            height=30,
            corner_radius=7,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            justify="right",
            font=(self._police, 13),
        ).pack(side="right")

        ctk.CTkSwitch(
            corps,
            text="Analyser les meilleurs outliers",
            variable=self._option_outliers,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=TEXTE,
            font=(self._police, 13),
        ).pack(anchor="w", pady=4)
        ctk.CTkSwitch(
            corps,
            text="Lire les sous-titres (non officiel)",
            variable=self._option_transcriptions,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=TEXTE,
            font=(self._police, 13),
        ).pack(anchor="w", pady=4)

        estimation = ctk.CTkFrame(
            corps,
            fg_color=SURFACE_HAUTE,
            corner_radius=7,
            border_width=1,
            border_color=BORDURE,
        )
        estimation.pack(fill="x", pady=(10, 2))
        ctk.CTkLabel(
            estimation,
            text="DURÉE ESTIMÉE",
            font=(self._police, 9, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(fill="x", padx=10, pady=(7, 0))
        ctk.CTkLabel(
            estimation,
            textvariable=self._estimation_execution,
            font=(self._police, 17, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(fill="x", padx=10)
        ctk.CTkLabel(
            estimation,
            text="Fourchette indicative : réseau, LLM et taille du lot.",
            font=(self._police, 9),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=260,
        ).pack(fill="x", padx=10, pady=(0, 7))

        bouton = self._bouton_accent(corps, "Tout lancer", self._demarrer_run)
        bouton.pack(fill="x", pady=(12, 6))
        self._boutons_execution.append(bouton)
        self._bouton_arreter = self._bouton(corps, "Arrêter", self._annuler)
        self._bouton_arreter.pack(fill="x")
        self._bouton_arreter.configure(state="disabled")

        cadre, corps = self._carte(
            gauche, "Étapes séparées", "Pour reprendre après une interruption sans tout refaire."
        )
        cadre.pack(fill="x")
        for etape, libelle in (
            ("discover", "Découvrir les chaînes"),
            ("enrich", "Enrichir (vidéos, métriques)"),
            ("score", "Scorer la proximité (LLM)"),
            ("outliers", "Analyser les outliers"),
            ("hooks", "Générer les hooks"),
            ("report", "Régénérer le rapport"),
        ):
            bouton = self._bouton(corps, libelle, lambda e=etape: self._lancer_etape(e))
            bouton.pack(fill="x", pady=2)
            self._boutons_execution.append(bouton)

        droite = ctk.CTkFrame(panneau, fg_color="transparent")
        droite.pack(side="left", fill="both", expand=True)
        cadre = ctk.CTkFrame(
            droite, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        cadre.pack(fill="both", expand=True)
        interieur = ctk.CTkFrame(cadre, fg_color="transparent")
        interieur.pack(fill="both", expand=True, padx=18, pady=16)

        entete = ctk.CTkFrame(interieur, fg_color="transparent")
        entete.pack(fill="x")
        ctk.CTkLabel(
            entete, text="Journal", font=(self._police, 16, "bold"), text_color=TEXTE
        ).pack(side="left")
        self._etat_execution = tk.StringVar(value="en attente")
        ctk.CTkLabel(
            entete, textvariable=self._etat_execution, font=(self._police, 12), text_color=DOUX
        ).pack(side="right")

        self._progression = ctk.CTkProgressBar(
            interieur,
            height=4,
            corner_radius=2,
            fg_color=SURFACE_HAUTE,
            progress_color=ACCENT_CLAIR,
            mode="indeterminate",
        )
        self._progression.pack(fill="x", pady=(10, 12))
        self._progression.set(0)

        self._journal = ctk.CTkTextbox(
            interieur,
            fg_color=JOURNAL_FOND,
            border_color=BORDURE,
            border_width=1,
            corner_radius=10,
            text_color=JOURNAL_TEXTE,
            font=(self._mono, 13),
            wrap="word",
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        self._journal.pack(fill="both", expand=True)
        self._journal.tag_config("titre", foreground="#FAFAFA", underline=True)
        self._journal.tag_config("ok", foreground="#FAFAFA")
        self._journal.tag_config("alerte", foreground="#FAFAFA", underline=True)
        self._journal.tag_config("erreur", foreground="#FAFAFA", underline=True)
        self._journal.tag_config("discret", foreground="#EBF6FA")
        self._journal.configure(state="disabled")

        self._resume_execution = ctk.CTkLabel(
            interieur,
            text="",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self._resume_execution.pack(fill="x", pady=(10, 0))
        self._auto_wrap(self._resume_execution)
        self._ecrire_journal("Prêt. Lancez une exécution ; le quota consommé s'affichera ici.")

    # ------------------------------------------------------------------
    # Page — Résultats
    # ------------------------------------------------------------------
    def _page_resultats(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["resultats"] = page

        barre = ctk.CTkFrame(page, fg_color="transparent")
        barre.pack(fill="x", pady=(0, 12))
        premiere = ctk.CTkFrame(barre, fg_color="transparent")
        premiere.pack(fill="x")
        self._filtre = tk.StringVar()
        entree = ctk.CTkEntry(
            premiere,
            textvariable=self._filtre,
            height=34,
            corner_radius=8,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Filtrer par nom, handle, format ou langue",
            font=(self._police, 14),
        )
        entree.pack(side="left", fill="x", expand=True)
        entree.bind("<KeyRelease>", self._filtre_modifie)
        self._bouton(premiere, "Actualiser", self._rafraichir_tout, width=110).pack(
            side="right", padx=(10, 0)
        )

        seconde = ctk.CTkFrame(barre, fg_color="transparent")
        seconde.pack(fill="x", pady=(8, 0))
        self._compte_resultats = tk.StringVar(value="")
        ctk.CTkLabel(
            seconde, textvariable=self._compte_resultats, font=(self._police, 12), text_color=DOUX_FONCE
        ).pack(side="left")
        self._filtre_statut = tk.StringVar(value="Toutes")
        ctk.CTkSegmentedButton(
            seconde,
            values=("Toutes", "Retenues", "Écartées"),
            variable=self._filtre_statut,
            command=self._filtre_statut_change,
            height=30,
            corner_radius=6,
            fg_color=SURFACE_HAUTE,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_SURVOL,
            unselected_color=SURFACE_HAUTE,
            unselected_hover_color=BORDURE,
            text_color=TEXTE,
            font=(self._police, 12),
        ).pack(side="left", padx=(18, 0))
        ctk.CTkLabel(
            seconde,
            text="● retenues",
            font=(self._police, 12, "bold"),
            text_color=OK,
        ).pack(side="left", padx=(16, 0))
        ctk.CTkLabel(
            seconde,
            text="○ écartées",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="left", padx=(8, 0))
        self._bouton(
            seconde, "Ouvrir report.md", lambda: self._ouvrir(self._chemin_rapport()), width=140
        ).pack(side="right")
        self._bouton(
            seconde, "Régénérer les exports", lambda: self._lancer_etape("report"), width=175
        ).pack(side="right", padx=(0, 8))

        panneau = self._decouper(page)
        panneau.pack(fill="both", expand=True)

        cadre_t = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(cadre_t, minsize=420, width=620, stretch="always")
        interieur = ctk.CTkFrame(cadre_t, fg_color="transparent")
        interieur.pack(fill="both", expand=True, padx=10, pady=10)

        self._arbre = ttk.Treeview(
            interieur,
            columns=[colonne[0] for colonne in COLONNES_RESULTATS],
            show="headings",
            selectmode="browse",
            style="Outly.Treeview",
        )
        for cle, libelle, largeur, triable in COLONNES_RESULTATS:
            self._arbre.heading(
                cle,
                text=libelle,
                command=(lambda c=cle: self._trier(c)) if triable else "",
            )
            self._arbre.column(
                cle,
                width=largeur,
                minwidth=52,
                anchor="w" if cle == "chaine" else "center",
                stretch=cle == "chaine",
            )
        self._maj_entetes()
        self._arbre.tag_configure("ecartee", foreground=_couleur(DOUX_FONCE))
        self._arbre.tag_configure("retenue", foreground=_couleur(TEXTE), font=(*self._police_tableau(), "bold"))
        self._arbre.bind("<<TreeviewSelect>>", lambda _: self._afficher_detail())
        self._arbre.bind("<Double-1>", lambda _: self._ouvrir_chaine())

        barre_v = ctk.CTkScrollbar(
            interieur,
            command=self._arbre.yview,
            width=12,
            fg_color="transparent",
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
        )
        self._arbre.configure(yscrollcommand=barre_v.set)
        barre_v.pack(side="right", fill="y")
        self._arbre.pack(side="left", fill="both", expand=True)

        self._vide = ctk.CTkFrame(interieur, fg_color="transparent")
        ctk.CTkLabel(
            self._vide,
            text="Aucune chaîne évaluée pour l'instant.",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            self._vide,
            text="Lancez une veille : les chaînes apparaîtront ici, classées.",
            font=(self._police, 13),
            text_color=DOUX,
        ).pack(pady=(0, 14))
        self._bouton(self._vide, "Ouvrir la page Lancer", lambda: self._afficher_page("lancer")).pack()
        self._vide.place(relx=0.5, rely=0.5, anchor="center")
        self._vide_visible = True

        cadre_d = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(cadre_d, minsize=320, width=390, stretch="always")
        cadre_d.pack_propagate(False)
        actions = ctk.CTkFrame(cadre_d, fg_color="transparent")
        actions.pack(side="bottom", fill="x", padx=14, pady=(8, 14))
        ligne_actions = ctk.CTkFrame(actions, fg_color="transparent")
        ligne_actions.pack(fill="x")
        self._bouton_chaine = self._bouton(
            ligne_actions, "Ouvrir la chaîne", self._ouvrir_chaine, width=156
        )
        self._bouton_chaine.pack(side="left")
        self._bouton_apercu = self._bouton(
            ligne_actions, "Aperçu", self._apercu_chaine, width=96
        )
        self._bouton_apercu.pack(side="left", padx=(8, 0))
        ligne_actions_2 = ctk.CTkFrame(actions, fg_color="transparent")
        ligne_actions_2.pack(fill="x", pady=(6, 0))
        self._bouton_enregistrer = self._bouton(
            ligne_actions_2, "Enregistrer", self._enregistrer_chaine, width=132
        )
        self._bouton_enregistrer.pack(side="left")
        self._bouton_copier = self._bouton(
            ligne_actions_2, "Copier l'ID", self._copier_id, width=104
        )
        self._bouton_copier.pack(side="left", padx=(8, 0))
        for bouton in (
            self._bouton_chaine,
            self._bouton_apercu,
            self._bouton_enregistrer,
            self._bouton_copier,
        ):
            bouton.configure(state="disabled")

        self._detail = ctk.CTkScrollableFrame(
            cadre_d,
            fg_color=SURFACE,
            corner_radius=0,
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        self._detail.pack(fill="both", expand=True, padx=(2, 0), pady=(2, 0))
        self._detail_corps = ctk.CTkFrame(self._detail, fg_color="transparent")
        self._detail_corps.pack(fill="both", expand=True, padx=14, pady=14)
        self._detail_initial()

    # ------------------------------------------------------------------
    # Page — Hooks
    # ------------------------------------------------------------------
    def _page_hooks(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["hooks"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        cadre, corps = self._carte(
            contenu,
            "Votre sujet",
            "Écrivez le titre exact de la vidéo que vous préparez, ou une phrase très "
            "précise. Les accroches s'inspirent des intros outliers et de vos notes "
            "personnelles (page Notes). Plus le sujet est concret, plus elles seront justes.",
        )
        cadre.pack(fill="x")
        entree = ctk.CTkEntry(
            corps,
            textvariable=self._hooks_sujet,
            height=40,
            corner_radius=9,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Ex. : Pourquoi 90 % des débutants abandonnent en 3 semaines",
            font=(self._police, 15),
        )
        entree.pack(fill="x", pady=(0, 12))

        options = ctk.CTkFrame(corps, fg_color="transparent")
        options.pack(fill="x")
        ctk.CTkSwitch(
            options,
            text="Générer automatiquement à la fin de chaque veille",
            variable=self._bascule[("hooks", "enabled")],
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=TEXTE,
            font=(self._police, 13),
        ).pack(side="left")

        note = ctk.CTkLabel(
            corps,
            text="L'analyse lit les sous-titres horodatés des vidéos outliers pour "
            "situer leurs intros. Activez « Lire les sous-titres » dans Réglages. "
            "Sans transcription, aucune accroche n'est inventée.",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            justify="left",
            anchor="w",
            wraplength=700,
        )
        note.pack(anchor="w", fill="x", pady=(10, 0))
        self._auto_wrap(note)

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x", pady=(12, 0))
        bouton = self._bouton_accent(actions, "Générer maintenant", self._generer_hooks)
        bouton.pack(side="left")
        self._boutons_execution.append(bouton)
        self._bouton(
            actions, "Voir le journal", lambda: self._afficher_page("lancer"), width=130
        ).pack(side="right")
        ctk.CTkLabel(
            corps,
            textvariable=self._hooks_etat,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(anchor="w", fill="x", pady=(10, 0))

        cadre, corps = self._carte(
            contenu,
            "Dernières variantes",
            "Chaque accroche indique le procédé copié, la vidéo outlier qui l'a inspirée "
            "et peut être conservée dans vos notes.",
        )
        cadre.pack(fill="x", pady=(14, 0))
        self._hooks_corps = ctk.CTkFrame(corps, fg_color="transparent")
        self._hooks_corps.pack(fill="x")
        self._remplir_hooks()

    def _remplir_hooks(self) -> None:
        if not hasattr(self, "_hooks_corps") or self._tache is not None:
            return
        for enfant in self._hooks_corps.winfo_children():
            enfant.destroy()

        jeu = data.jeu_hooks(self.racine)
        if jeu is None:
            self._hooks_etat.set(
                "Aucun jeu généré pour l'instant. Saisissez un sujet puis cliquez sur "
                "« Générer maintenant »."
            )
            self._detail_label_hooks(
                "Rien à afficher : la première génération apparaîtra ici.",
                couleur=DOUX_FONCE,
            )
            return

        variantes = jeu.get("variantes") or []
        date = _date_lisible(jeu.get("cree_le"))
        self._hooks_etat.set(
            f"Dernier jeu : {date} · {len(variantes)} variante(s) · "
            f"{jeu.get('fournisseur') or 'LLM'} / {jeu.get('modele') or 'modèle inconnu'}"
        )
        ctk.CTkLabel(
            self._hooks_corps,
            text=f"Sujet : {jeu.get('sujet') or '(non renseigné)'}",
            font=(self._police, 14, "bold"),
            text_color=ACCENT_TEXTE,
            anchor="w",
            justify="left",
            wraplength=720,
        ).pack(anchor="w", fill="x", pady=(0, 6))
        if jeu.get("analyse"):
            analyse = ctk.CTkLabel(
                self._hooks_corps,
                text=jeu["analyse"],
                font=(self._police, 13),
                text_color=DOUX,
                anchor="w",
                justify="left",
                wraplength=720,
            )
            analyse.pack(anchor="w", fill="x", pady=(0, 10))
            self._auto_wrap(analyse)

        noms_sources = {
            str(source.get("video_id")): str(source.get("titre") or "")
            for source in jeu.get("sources") or []
            if isinstance(source, dict)
        }
        for position, variante in enumerate(variantes, start=1):
            bloc = ctk.CTkFrame(
                self._hooks_corps,
                fg_color=SURFACE_HAUTE,
                corner_radius=10,
                border_width=1,
                border_color=BORDURE,
            )
            bloc.pack(fill="x", pady=(0, 10))
            entete = ctk.CTkFrame(bloc, fg_color="transparent")
            entete.pack(fill="x", padx=12, pady=(10, 4))
            ctk.CTkLabel(
                entete,
                text=f"{position}. {variante.get('titre') or 'Variante'}",
                font=(self._police, 14, "bold"),
                text_color=TEXTE,
                anchor="w",
            ).pack(side="left")
            self._lien(
                entete,
                "Noter",
                lambda v=variante: self._noter_hook(v, jeu),
                width=64,
            ).pack(side="right")
            self._lien(
                entete,
                "Copier",
                lambda v=variante: self._copier_texte(str(v.get("hook") or "")),
                width=78,
            ).pack(side="right", padx=(0, 6))
            hook = ctk.CTkLabel(
                bloc,
                text=variante.get("hook") or "",
                font=(self._police, 15),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=700,
            )
            hook.pack(anchor="w", fill="x", padx=12, pady=(0, 6))
            self._auto_wrap(hook)
            inspire = ", ".join(
                noms_sources.get(str(identifiant), str(identifiant))[:60]
                for identifiant in variante.get("inspire_de") or []
            )
            details = (
                f"≈ {variante.get('duree_secondes') or '?'} s · "
                f"{variante.get('technique') or 'procédé non précisé'}"
                + (f" · inspiré de {inspire}" if inspire else "")
            )
            ctk.CTkLabel(
                bloc,
                text=details,
                font=(self._police, 11),
                text_color=ACCENT_TEXTE,
                anchor="w",
                justify="left",
                wraplength=700,
            ).pack(anchor="w", fill="x", padx=12)
            if variante.get("pourquoi"):
                pourquoi = ctk.CTkLabel(
                    bloc,
                    text=variante["pourquoi"],
                    font=(self._police, 11),
                    text_color=DOUX_FONCE,
                    anchor="w",
                    justify="left",
                    wraplength=700,
                )
                pourquoi.pack(anchor="w", fill="x", padx=12, pady=(3, 10))
                self._auto_wrap(pourquoi)
            else:
                ctk.CTkLabel(bloc, text="", height=4).pack()

        intros = jeu.get("intros") or []
        if intros:
            self._titre_section(self._hooks_corps, "Intros détectées", pady=(10, 6))
            for intro in intros:
                bloc = ctk.CTkFrame(self._hooks_corps, fg_color="transparent")
                bloc.pack(fill="x", pady=(0, 8))
                titre = ctk.CTkLabel(
                    bloc,
                    text=f"{intro.get('titre') or '(vidéo)'} · {intro.get('chaine_titre') or ''}",
                    font=(self._police, 12, "bold"),
                    text_color=TEXTE,
                    anchor="w",
                    justify="left",
                    wraplength=650,
                )
                titre.pack(anchor="w", fill="x")
                self._auto_wrap(titre)
                ctk.CTkLabel(
                    bloc,
                    text=f"Intro {transcripts.formater_horodatage(intro.get('intro_debut_s'))} → "
                    f"{transcripts.formater_horodatage(intro.get('intro_fin_s'))} · "
                    f"{', '.join(intro.get('techniques') or []) or 'procédés non précisés'}",
                    font=(self._police, 11),
                    text_color=ACCENT_TEXTE,
                    anchor="w",
                    justify="left",
                    wraplength=700,
                ).pack(anchor="w", fill="x", pady=(2, 2))
                if intro.get("hook_texte"):
                    citation = ctk.CTkLabel(
                        bloc,
                        text=f"« {intro['hook_texte']} »",
                        font=(self._police, 11),
                        text_color=DOUX,
                        anchor="w",
                        justify="left",
                        wraplength=700,
                    )
                    citation.pack(anchor="w", fill="x")
                    self._auto_wrap(citation)
                self._lien(
                    bloc,
                    "Lire sur YouTube",
                    lambda v=intro.get("video_id"): webbrowser.open(
                        f"https://www.youtube.com/watch?v={v}"
                    ),
                    width=128,
                ).pack(anchor="w", pady=(4, 0))

    def _detail_label_hooks(self, texte: str, couleur: str = DOUX) -> None:
        ctk.CTkLabel(
            self._hooks_corps,
            text=texte,
            font=(self._police, 13),
            text_color=couleur,
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(anchor="w", fill="x", pady=(4, 4))

    def _noter_hook(self, variante: dict, jeu: dict) -> None:
        """Enregistre une variante comme note, pour la retravailler plus tard."""
        hook = str(variante.get("hook") or "").strip()
        if not hook:
            return
        inspiration = [str(element) for element in variante.get("inspire_de") or []]
        video_id = inspiration[0] if inspiration else None
        titre = "Hook — " + str(variante.get("titre") or jeu.get("sujet") or "variante")
        morceaux = [hook]
        if variante.get("pourquoi"):
            morceaux.append("Pourquoi : " + str(variante["pourquoi"]))
        if variante.get("technique"):
            morceaux.append("Technique : " + str(variante["technique"]))
        if jeu.get("sujet"):
            morceaux.append("Sujet : " + str(jeu["sujet"]))
        try:
            note_id = notes.creer(
                self.racine,
                titre=titre[:200],
                contenu="\n\n".join(morceaux),
                type="hook",
                statut="brouillon",
                tags=["hook", "outlier"],
                source=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                video_id=video_id,
            )
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Note impossible", str(probleme), parent=self)
            return
        self._remplir_liste_notes()
        self._message.set(
            f"Hook enregistré dans vos notes (#{note_id}). Retrouvez-le dans la page Notes."
        )

    def _copier_texte(self, texte: str) -> None:
        if not texte:
            return
        self.clipboard_clear()
        self.clipboard_append(texte)
        self._message.set("Texte copié dans le presse-papiers.")

    def _generer_hooks(self) -> None:
        if self._tache is not None:
            messagebox.showinfo(
                "Exécution en cours", "Attendez la fin ou arrêtez l'exécution.", parent=self
            )
            return
        sujet = self._hooks_sujet.get().strip()
        if not sujet:
            messagebox.showwarning(
                "Sujet manquant",
                "Écrivez le titre de votre vidéo ou un sujet précis avant de générer.",
                parent=self,
            )
            return
        transcriptions = bool(
            self._bascule[("transcripts", "enabled")].get()
            or self._option_transcriptions.get()
        )
        if not transcriptions:
            reponse = messagebox.askyesnocancel(
                "Sous-titres désactivés",
                "La lecture des sous-titres est désactivée, or elle est nécessaire pour "
                "situer les intros des nouvelles vidéos.\n\n"
                "Oui : l'activer pour cette génération.\n"
                "Non : utiliser uniquement les transcriptions déjà en cache.\n"
                "Annuler : revenir en arrière.",
                parent=self,
            )
            if reponse is None:
                return
            if reponse:
                self._bascule[("transcripts", "enabled")].set(True)
                transcriptions = True
        if self._note_dirty and not self._sauvegarder_note(silencieux=True):
            return
        if not self._sauvegarder():
            return
        self._hooks_etat.set("Génération en cours…")
        self._message.set("Génération des hooks en cours…")
        self._lancer(arguments_etape("hooks", transcriptions=transcriptions), "Hooks")

    # ------------------------------------------------------------------
    # Page — Cerveau IA
    # ------------------------------------------------------------------
    def _page_cerveau(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["cerveau"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        cadre, corps = self._carte(
            contenu,
            "Faire dialoguer vos idées",
            "Le cerveau IA lit vos notes, les recherches de la page Recherche, les "
            "idées de la page Idées, votre playlist « À regarder » et les outliers "
            "récents. Il ne se "
            "contente pas de résumer : il cherche des tensions, des angles et des titres "
            "que vous n'avez pas encore reliés.",
        )
        cadre.pack(fill="x")
        ctk.CTkLabel(
            corps,
            text="Votre question ou contrainte (facultatif)",
            font=(self._police, 12, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkEntry(
            corps,
            textvariable=self._cerveau_question,
            height=38,
            corner_radius=6,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Ex. : quels angles pour une série sur les débutants qui abandonnent ?",
            font=(self._police, 14),
        ).pack(fill="x", pady=(4, 12))

        options = ctk.CTkFrame(corps, fg_color="transparent")
        options.pack(fill="x")
        ctk.CTkSwitch(
            options,
            text="Inclure les notes archivées",
            variable=self._cerveau_archives,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 12),
        ).pack(side="left")
        ctk.CTkSwitch(
            options,
            text="Forcer une nouvelle analyse",
            variable=self._cerveau_forcer,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 12),
        ).pack(side="left", padx=(18, 0))
        self._cerveau_compte = tk.StringVar(value="")
        ctk.CTkLabel(
            options,
            textvariable=self._cerveau_compte,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="right")

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x", pady=(14, 0))
        bouton = self._bouton_accent(actions, "Analyser ma matière", self._lancer_cerveau)
        bouton.pack(side="left")
        self._boutons_execution.append(bouton)
        self._bouton(actions, "Ouvrir mes notes", lambda: self._afficher_page("notes"), width=142).pack(
            side="left", padx=(8, 0)
        )
        ctk.CTkLabel(
            corps,
            textvariable=self._cerveau_etat,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(anchor="w", fill="x", pady=(10, 0))

        cadre_carte, corps_carte = self._carte(
            contenu,
            "Carte du cerveau",
            "Chaque point est une information : une note, un hook, un mot-clé observé chez "
            "vos concurrents. Les traits relient ce qui se répond. Faites glisser pour "
            "tourner, molette pour zoomer.",
        )
        cadre_carte.pack(fill="x", pady=(14, 0))
        self._carte_cerveau = CarteCerveau(
            corps_carte, self._police, self.racine, mono=self._mono
        )
        self._carte_cerveau.pack(fill="both", expand=True)

        self._cerveau_corps = ctk.CTkFrame(contenu, fg_color="transparent")
        self._cerveau_corps.pack(fill="x", pady=(14, 0))
        self._remplir_cerveau()

    def _carte_matiere(
        self, matiere: dict, actives: list, nb_plan: int, nb_playlist: int
    ) -> None:
        """Affiche la matière exacte envoyée au cerveau à chaque analyse.

        Sans cette carte, l'analyse ressemble à une réponse sortie de nulle part :
        l'utilisateur ne voit ni ses recherches ni ses idées. Ici, chaque source
        est nommée, datée et accompagnée d'un lien vers sa page.
        """
        cadre, corps = self._carte(
            self._cerveau_corps,
            "La matière que le cerveau lit",
            "Notes, recherches de la page Recherche, idées de la page Idées, plan "
            "éditorial, playlist et outliers : tout est envoyé au modèle à chaque "
            "analyse. Voici exactement quoi.",
        )
        cadre.pack(fill="x", pady=(0, 12))

        def ligne(titre: str, valeur: str, lien: str | None = None, action=None) -> None:
            bloc = ctk.CTkFrame(corps, fg_color="transparent")
            bloc.pack(fill="x", pady=(8, 0))
            entete = ctk.CTkFrame(bloc, fg_color="transparent")
            entete.pack(fill="x")
            ctk.CTkLabel(
                entete,
                text=titre,
                font=(self._police, 12, "bold"),
                text_color=DOUX,
                anchor="w",
            ).pack(side="left")
            if lien and action:
                self._lien(entete, lien, action, width=132).pack(side="right")
            libelle = ctk.CTkLabel(
                bloc,
                text=valeur,
                font=(self._police, 12),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=720,
            )
            libelle.pack(anchor="w", fill="x", pady=(2, 0))
            self._auto_wrap(libelle)

        ligne(
            "NOTES ACTIVES",
            f"{len(actives)} note(s)" if actives else "Aucune note active.",
            "Ouvrir",
            lambda: self._afficher_page("notes"),
        )
        if matiere["recherches"]:
            morceaux = []
            for session in matiere["recherches"]:
                mode = "Wikipédia + IA" if session.get("mode") == "avec_ia" else "Wikipédia"
                morceaux.append(
                    f"• « {session.get('titre')} » ({mode}, "
                    f"{_date_lisible(session.get('cree_le')).split()[0]}) — "
                    f"{session.get('nb_faits')} fait(s) recoupé(s), "
                    f"{session.get('nb_sources')} source(s)"
                )
            recherches_texte = "\n".join(morceaux)
        else:
            recherches_texte = (
                "Aucune recherche pour l'instant : la page Recherche en ajoute une "
                "en un clic, et le cerveau la lira dès la prochaine analyse."
            )
        ligne(
            "RECHERCHES DOCUMENTAIRES",
            recherches_texte,
            "Ouvrir",
            lambda: self._afficher_page("recherche"),
        )
        if matiere["idees"]:
            morceaux = []
            for jeu in matiere["idees"]:
                sujets = " · ".join(jeu.get("sujets") or []) or "(sujets non affichés)"
                morceaux.append(
                    f"• {jeu.get('nb_idees')} idée(s) du "
                    f"{_date_lisible(jeu.get('cree_le')).split()[0]} : {sujets}"
                )
            idees_texte = "\n".join(morceaux)
        else:
            idees_texte = (
                "Aucune idée générée pour l'instant : la page Idées en produit trois "
                "à huit, avec trois titres par sujet."
            )
        ligne(
            "IDÉES DE LA PAGE « IDÉES »",
            idees_texte,
            "Ouvrir",
            lambda: self._afficher_page("idees"),
        )
        ligne(
            "PLAN ÉDITORIAL · À REGARDER",
            f"{nb_plan} fiche(s) au plan · {nb_playlist} élément(s) à regarder",
        )

    def _remplir_cerveau(self) -> None:
        if not hasattr(self, "_cerveau_corps"):
            return
        if self._tache is not None and self._libelle_tache == "Cerveau IA":
            return
        for enfant in self._cerveau_corps.winfo_children():
            enfant.destroy()
        actives = notes.lister(self.racine, inclure_archivees=False)
        matiere = data.matiere_cerveau(self.racine)
        nb_recherches = len(matiere["recherches"])
        nb_idees = sum(int(jeu.get("nb_idees") or 0) for jeu in matiere["idees"])
        nb_plan = len(plan.lister(self.racine))
        nb_playlist = len(playlist.lister(self.racine))
        self._cerveau_compte.set(
            f"{len(actives)} note(s) · {nb_recherches} recherche(s) · "
            f"{nb_idees} idée(s) · {nb_plan} au plan · {nb_playlist} à regarder"
        )
        self._carte_matiere(matiere, actives, nb_plan, nb_playlist)
        session = data.derniere_session_cerveau(self.racine)
        if session is None:
            self._cerveau_etat.set(
                "Aucune analyse pour l'instant. Ajoutez de la matière, puis lancez l'analyse."
            )
            cadre, corps = self._carte(
                self._cerveau_corps,
                "Ce qui alimente le cerveau",
                "Les notes restent prioritaires, mais une recherche, des idées déjà "
                "générées ou une playlist suffisent à démarrer. Plus la matière est "
                "précise, plus les angles seront spécifiques.",
            )
            cadre.pack(fill="x")
            for texte in (
                "Une note, un hook ou une tension que vous voulez défendre.",
                "Une recherche documentaire avec ses faits recoupés et ses sources.",
                "Une idée déjà générée, une vidéo mise de côté ou une question ouverte.",
            ):
                ctk.CTkLabel(
                    corps,
                    text="•  " + texte,
                    font=(self._police, 13),
                    text_color=DOUX,
                    anchor="w",
                    justify="left",
                ).pack(anchor="w", fill="x", pady=2)
            return

        reponse = session.get("reponse") or {}
        notes_par_id = {
            str(note.get("id")): note
            for note in session.get("notes") or []
            if isinstance(note, dict)
        }
        self._cerveau_etat.set(
            f"Dernière analyse : {_date_lisible(session.get('cree_le'))} · "
            f"{session.get('fournisseur') or 'LLM'} / {session.get('modele') or 'modèle inconnu'}"
            f" · matière lue : {nb_recherches} recherche(s), {nb_idees} idée(s)"
            + (f" · question : {session.get('question')}" if session.get("question") else "")
        )
        synthese = str(reponse.get("synthese") or "")
        if synthese:
            cadre, corps = self._carte(self._cerveau_corps, "Ce que vos notes font apparaître")
            cadre.pack(fill="x", pady=(0, 12))
            texte = ctk.CTkLabel(
                corps,
                text=synthese,
                font=(self._police, 14),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=760,
            )
            texte.pack(anchor="w", fill="x")
            self._auto_wrap(texte)

        angles = reponse.get("angles") or []
        if angles:
            self._titre_section(self._cerveau_corps, "Angles à explorer", pady=(4, 6))
            for position, angle in enumerate(angles, start=1):
                self._carte_angle(self._cerveau_corps, angle, position, notes_par_id)

        sujets = reponse.get("sujets") or []
        if sujets:
            cadre, corps = self._carte(
                self._cerveau_corps,
                "Titres possibles",
                "Des formulations directement issues des angles ci-dessus.",
            )
            cadre.pack(fill="x", pady=(6, 12))
            for sujet in sujets:
                ligne = ctk.CTkFrame(corps, fg_color=SURFACE_HAUTE, corner_radius=5)
                ligne.pack(fill="x", pady=(0, 6))
                textes = ctk.CTkFrame(ligne, fg_color="transparent")
                textes.pack(side="left", fill="x", expand=True, padx=12, pady=9)
                titre = ctk.CTkLabel(
                    textes,
                    text=sujet.get("titre") or "Sujet",
                    font=(self._police, 14, "bold"),
                    text_color=TEXTE,
                    anchor="w",
                    justify="left",
                    wraplength=560,
                )
                titre.pack(anchor="w", fill="x")
                self._auto_wrap(titre)
                if sujet.get("angle"):
                    ctk.CTkLabel(
                        textes,
                        text=sujet["angle"],
                        font=(self._police, 12),
                        text_color=DOUX,
                        anchor="w",
                        justify="left",
                    ).pack(anchor="w", fill="x", pady=(2, 0))
                self._lien(
                    ligne,
                    "Créer une note",
                    lambda s=sujet: self._noter_angle(s, [], "sujet"),
                    width=120,
                ).pack(side="right", padx=(8, 10))

        questions = reponse.get("questions") or []
        if questions:
            cadre, corps = self._carte(self._cerveau_corps, "Questions à creuser")
            cadre.pack(fill="x", pady=(0, 12))
            for question in questions:
                ctk.CTkLabel(
                    corps,
                    text="→  " + str(question),
                    font=(self._police, 13),
                    text_color=DOUX,
                    anchor="w",
                    justify="left",
                ).pack(anchor="w", fill="x", pady=2)

        liens = reponse.get("liens") or []
        if liens:
            cadre, corps = self._carte(
                self._cerveau_corps,
                "Rapprochements entre vos notes",
                "Deux idées qui se répondent, et ce qu'elles produisent ensemble.",
            )
            cadre.pack(fill="x", pady=(0, 12))
            for lien in liens:
                bloc = ctk.CTkFrame(corps, fg_color=SURFACE_HAUTE, corner_radius=5)
                bloc.pack(fill="x", pady=(0, 6))
                references = " + ".join(
                    (notes_par_id.get(str(identifiant), {}).get("titre") or f"Note {identifiant}")[:70]
                    for identifiant in lien.get("notes") or []
                )
                ctk.CTkLabel(
                    bloc,
                    text=references,
                    font=(self._police, 12, "bold"),
                    text_color=DOUX_FONCE,
                    anchor="w",
                    justify="left",
                ).pack(anchor="w", fill="x", padx=12, pady=(8, 2))
                idée = ctk.CTkLabel(
                    bloc,
                    text=lien.get("idee") or "",
                    font=(self._police, 13),
                    text_color=TEXTE,
                    anchor="w",
                    justify="left",
                )
                idée.pack(anchor="w", fill="x", padx=12, pady=(0, 9))
                self._auto_wrap(idée)

        if notes_par_id:
            cadre, corps = self._carte(
                self._cerveau_corps,
                "Notes mobilisées",
                "Cliquez sur une note pour l'ouvrir et continuer à écrire.",
            )
            cadre.pack(fill="x")
            for identifiant, note in notes_par_id.items():
                self._lien(
                    corps,
                    f"{note.get('etiquette_type') or 'Note'} · {note.get('titre') or identifiant}",
                    lambda i=identifiant: self._ouvrir_note(int(i)),
                    width=300,
                ).pack(anchor="w", fill="x", pady=2)

    # ------------------------------------------------------------------
    # Page — Idées de vidéos
    # ------------------------------------------------------------------
    def _page_audit(self) -> None:
        from .audit_page import construire

        construire(self)

    def _remplir_audit(self) -> None:
        from .audit_page import _remplir_audit

        _remplir_audit(self)

    def _page_recherche(self) -> None:
        from .recherche_page import construire

        construire(self)

    def _page_schemas(self) -> None:
        from .schemas_page import construire

        construire(self)

    def _remplir_schemas(self) -> None:
        from .schemas_page import remplir

        remplir(self)

    def _page_tableau(self) -> None:
        from .whiteboard_page import construire

        construire(self)

    def _remplir_recherche(self) -> None:
        from .recherche_page import _remplir

        _remplir(self)

    def _page_studio(self) -> None:
        from .studio_page import construire

        construire(self)

    def _remplir_studio(self) -> None:
        from .studio_page import _remplir

        _remplir(self)

    def _page_idees(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["idees"] = page
        zone = self._zone_defilante(page)
        contenu = ctk.CTkFrame(zone, fg_color="transparent")
        contenu.pack(fill="x")

        cadre, corps = self._carte(
            contenu,
            "Trouver des idées de vidéos",
            "Le LLM croise vos notes classées, votre positionnement, vos mots-clés, ceux de "
            "vos concurrents, leurs meilleurs outliers et la dernière analyse du cerveau IA. "
            "Chaque idée arrive avec trois titres portés par des angles différents.",
        )
        cadre.pack(fill="x")
        ctk.CTkLabel(
            corps,
            text="Votre contrainte ou votre envie (facultatif)",
            font=(self._police, 12, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkEntry(
            corps,
            textvariable=self._idees_question,
            height=38,
            corner_radius=6,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Ex. : une série pour les débutants qui abandonnent au bout de 3 vidéos",
            font=(self._police, 14),
        ).pack(fill="x", pady=(4, 12))

        options = ctk.CTkFrame(corps, fg_color="transparent")
        options.pack(fill="x")
        ctk.CTkLabel(
            options, text="IDÉES", font=(self._police, 10, "bold"), text_color=DOUX
        ).pack(side="left", padx=(0, 6))
        self._idees_nombre_menu = ctk.CTkOptionMenu(
            options,
            values=["3", "5", "8"],
            variable=self._idees_nombre,
            width=76,
            height=30,
            corner_radius=5,
            fg_color=CHAMP,
            button_color=ACCENT,
            button_hover_color=ACCENT_SURVOL,
            text_color=TEXTE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=SURFACE_HAUTE,
            dropdown_text_color=TEXTE,
            font=(self._police, 12),
        )
        self._idees_nombre_menu.pack(side="left")
        ctk.CTkSwitch(
            options,
            text="Forcer une nouvelle recherche",
            variable=self._idees_forcer,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 12),
        ).pack(side="left", padx=(18, 0))
        ctk.CTkLabel(
            options,
            textvariable=self._idees_compte,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="right")

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x", pady=(14, 0))
        bouton = self._bouton_accent(actions, "Chercher des idées", self._lancer_idees)
        bouton.pack(side="left")
        self._boutons_execution.append(bouton)
        self._bouton(
            actions, "Ouvrir mes notes", lambda: self._afficher_page("notes"), width=142
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            corps,
            textvariable=self._idees_etat,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=760,
        ).pack(anchor="w", fill="x", pady=(10, 0))

        self._idees_corps = ctk.CTkFrame(contenu, fg_color="transparent")
        self._idees_corps.pack(fill="x", pady=(14, 0))
        self._remplir_idees()

    def _remplir_idees(self) -> None:
        if not hasattr(self, "_idees_corps"):
            return
        if self._tache is not None and self._libelle_tache == "Idées":
            return
        for enfant in self._idees_corps.winfo_children():
            enfant.destroy()
        actives = notes.lister(self.racine, inclure_archivees=False)
        self._idees_compte.set(
            f"{len(actives)} note(s) active(s) · "
            f"{sum(1 for note in actives if note.epingle)} épinglée(s)"
        )
        session = data.dernieres_idees(self.racine)
        if session is None:
            self._idees_etat.set(
                "Aucune recherche pour l'instant. Lancez-la, puis gardez les titres qui "
                "vous parlent : ils partent au plan éditorial ou dans vos notes."
            )
            cadre, corps = self._carte(
                self._idees_corps,
                "Ce qui nourrit les idées",
                "Plus votre matière est précise, plus les trois angles d'une même idée "
                "seront réellement différents.",
            )
            cadre.pack(fill="x")
            for texte in (
                "Vos notes, surtout les mieux classées : concepts, hooks, sujets, références.",
                "Votre positionnement et vos mots-clés, pour rester dans votre niche.",
                "Les mots-clés et les titres outliers des chaînes proches, preuve de demande.",
                "La dernière analyse du cerveau IA, pour prolonger ses angles et ses questions.",
            ):
                ctk.CTkLabel(
                    corps,
                    text="•  " + texte,
                    font=(self._police, 13),
                    text_color=DOUX,
                    anchor="w",
                    justify="left",
                ).pack(anchor="w", fill="x", pady=2)
            return

        idees_generees = session.get("idees") or []
        self._idees_etat.set(
            f"Dernière recherche : {_date_lisible(session.get('cree_le'))} · "
            f"{len(idees_generees)} idée(s) · "
            f"{session.get('fournisseur') or 'LLM'} / "
            f"{session.get('modele') or 'modèle inconnu'}"
            + (f" · contrainte : {session.get('question')}" if session.get("question") else "")
        )
        notes_par_id = {
            str(note.get("id")): note
            for note in session.get("notes") or []
            if isinstance(note, dict)
        }
        if session.get("introduction"):
            cadre, corps = self._carte(self._idees_corps, "Ce que la matière fait ressortir")
            cadre.pack(fill="x", pady=(0, 12))
            texte = ctk.CTkLabel(
                corps,
                text=session["introduction"],
                font=(self._police, 14),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=760,
            )
            texte.pack(anchor="w", fill="x")
            self._auto_wrap(texte)

        if idees_generees:
            self._titre_section(self._idees_corps, "Idées et titres", pady=(4, 6))
        for position, idee in enumerate(idees_generees, start=1):
            self._carte_idee(self._idees_corps, idee, position, notes_par_id)

    def _carte_idee(
        self, parent: tk.Misc, idee: dict, position: int, notes_par_id: dict
    ) -> None:
        bloc = ctk.CTkFrame(
            parent, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        bloc.pack(fill="x", pady=(0, 12))
        entete = ctk.CTkFrame(bloc, fg_color="transparent")
        entete.pack(fill="x", padx=16, pady=(12, 4))
        self._lien(
            entete,
            "Créer une note",
            lambda i=idee: self._idee_en_note(i),
            width=118,
        ).pack(side="right", padx=(8, 0))
        textes = ctk.CTkFrame(entete, fg_color="transparent")
        textes.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            textes,
            text=f"{position:02d} · "
            + ("CONCEPT" if idee.get("type") == "concept" else "SUJET"),
            font=(self._police, 10, "bold"),
            text_color=ACCENT_TEXTE,
            anchor="w",
        ).pack(anchor="w")
        titre = ctk.CTkLabel(
            textes,
            text=idee.get("sujet") or "Idée",
            font=(self._police, 16, "bold"),
            text_color=TEXTE,
            anchor="w",
            justify="left",
            wraplength=560,
        )
        titre.pack(anchor="w", fill="x", pady=(2, 0))
        self._auto_wrap(titre)

        if idee.get("pourquoi"):
            self._libelle_angle(bloc, "POURQUOI CETTE IDÉE", idee["pourquoi"], DOUX)
        if idee.get("sources"):
            ctk.CTkLabel(
                bloc,
                text="Sources : " + " · ".join(str(source) for source in idee["sources"]),
                font=(self._police, 11),
                text_color=DOUX_FONCE,
                anchor="w",
                justify="left",
                wraplength=720,
            ).pack(anchor="w", fill="x", padx=16, pady=(6, 0))
        references = [
            (notes_par_id.get(str(identifiant), {}).get("titre") or f"Note {identifiant}")
            for identifiant in idee.get("notes") or []
        ]
        if references:
            ctk.CTkLabel(
                bloc,
                text="Notes : " + " · ".join(references),
                font=(self._police, 11),
                text_color=DOUX_FONCE,
                anchor="w",
                justify="left",
                wraplength=720,
            ).pack(anchor="w", fill="x", padx=16, pady=(2, 0))

        ctk.CTkLabel(
            bloc,
            text="TROIS TITRES, TROIS ANGLES",
            font=(self._police, 10, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(10, 4))
        for angle in idee.get("angles") or []:
            ligne = ctk.CTkFrame(bloc, fg_color=SURFACE_HAUTE, corner_radius=5)
            ligne.pack(fill="x", padx=16, pady=(0, 6))
            ctk.CTkLabel(
                ligne,
                text=str(angle.get("angle") or "Angle").upper(),
                font=(self._police, 10, "bold"),
                text_color=ACCENT_TEXTE,
                anchor="w",
            ).pack(anchor="w", padx=12, pady=(7, 0))
            texte = ctk.CTkLabel(
                ligne,
                text=angle.get("titre") or "",
                font=(self._police, 14, "bold"),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=640,
            )
            texte.pack(anchor="w", fill="x", padx=12, pady=(2, 4))
            self._auto_wrap(texte)
            actions = ctk.CTkFrame(ligne, fg_color="transparent")
            actions.pack(fill="x", padx=8, pady=(0, 6))
            self._lien(
                actions,
                "Au plan",
                lambda i=idee, a=angle: self._idee_au_plan(i, a),
                width=76,
            ).pack(side="left")
            self._lien(
                actions,
                "Copier",
                lambda a=angle: self._copier_texte(str(a.get("titre") or "")),
                width=70,
            ).pack(side="left", padx=(4, 0))

    def _texte_idee(self, idee: dict, angle: dict | None = None) -> str:
        """Résumé lisible d'une idée, réutilisé par la note et par le plan."""
        morceaux: list[str] = []
        if angle:
            morceaux.append(f"Angle : {angle.get('angle') or ''}")
            morceaux.append(f"Titre : {angle.get('titre') or ''}")
        morceaux.append(f"Sujet : {idee.get('sujet') or ''}")
        if idee.get("pourquoi"):
            morceaux += ["", f"Pourquoi : {idee['pourquoi']}"]
        if idee.get("sources"):
            morceaux += ["", "Sources : " + " · ".join(idee["sources"])]
        autres = [
            f"- {autre.get('angle') or 'Angle'} : {autre.get('titre') or ''}"
            for autre in idee.get("angles") or []
            if not angle or autre.get("titre") != angle.get("titre")
        ]
        if autres:
            morceaux += ["", "Autres angles :", *autres]
        return "\n".join(morceaux).strip()

    def _idee_au_plan(self, idee: dict, angle: dict) -> None:
        titre = str(angle.get("titre") or idee.get("sujet") or "Idée").strip()
        try:
            plan.ajouter(self.racine, titre=titre, notes=self._texte_idee(idee, angle))
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Plan éditorial", str(probleme), parent=self)
            return
        self._message.set(f"« {titre} » ajouté au plan éditorial.")
        self._remplir_plan()

    def _angle_vers_hook(self, angle: dict) -> None:
        sujet = str(angle.get("titre") or angle.get("hook") or "").strip()
        if not sujet:
            return
        self._hooks_sujet.set(sujet)
        self._hooks_etat.set("Sujet repris depuis une idée. Générez les accroches.")
        self._afficher_page("hooks")
        self._message.set("Titre de l'idée transmis aux Hooks.")

    def _idee_en_note(self, idee: dict) -> None:
        try:
            notes.creer(
                self.racine,
                titre=str(idee.get("sujet") or "Idée")[:200],
                contenu=self._texte_idee(idee),
                type="sujet",
                tags=["idée générée"],
                source="Idées générées par Outly",
            )
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Notes", str(probleme), parent=self)
            return
        self._message.set("Idée enregistrée dans vos notes.")
        self._remplir_liste_notes()

    def _lancer_idees(self) -> None:
        if self._tache is not None:
            return
        self._idees_etat.set("Recherche d'idées en cours…")
        self._message.set("Recherche d'idées en cours…")
        self._lancer(
            arguments_idees(
                question=self._idees_question.get(),
                nombre=int(self._idees_nombre.get() or 5),
                forcer=self._idees_forcer.get(),
            ),
            "Idées",
        )

    def _carte_angle(
        self, parent: tk.Misc, angle: dict, position: int, notes_par_id: dict
    ) -> None:
        bloc = ctk.CTkFrame(
            parent, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        bloc.pack(fill="x", pady=(0, 10))
        entete = ctk.CTkFrame(bloc, fg_color="transparent")
        entete.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            entete,
            text=f"{position:02d}  {angle.get('titre') or 'Angle'}",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
            anchor="w",
            justify="left",
            wraplength=480,
        ).pack(side="left", fill="x", expand=True)
        self._lien(
            entete,
            "Créer un hook",
            lambda a=angle: self._angle_vers_hook(a),
            width=112,
        ).pack(side="right")
        self._lien(
            entete,
            "Créer une note",
            lambda a=angle: self._noter_angle(a, notes_par_id, "hook"),
            width=118,
        ).pack(side="right", padx=(0, 6))
        self._lien(
            entete,
            "Copier",
            lambda a=angle: self._copier_texte(str(a.get("hook") or "")),
            width=70,
        ).pack(side="right", padx=(0, 6))

        if angle.get("promesse"):
            self._libelle_angle(bloc, "PROMESSE", angle["promesse"], DOUX)
        hook = ctk.CTkFrame(bloc, fg_color=SURFACE_HAUTE, corner_radius=5)
        hook.pack(fill="x", padx=16, pady=(4, 8))
        ctk.CTkLabel(
            hook,
            text="HOOK À DIRE",
            font=(self._police, 10, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w", padx=12, pady=(8, 2))
        texte_hook = ctk.CTkLabel(
            hook,
            text=angle.get("hook") or "",
            font=(self._police, 14),
            text_color=TEXTE,
            anchor="w",
            justify="left",
            wraplength=700,
        )
        texte_hook.pack(anchor="w", fill="x", padx=12, pady=(0, 10))
        self._auto_wrap(texte_hook)
        if angle.get("pourquoi"):
            self._libelle_angle(bloc, "POURQUOI ÇA MARCHE", angle["pourquoi"], DOUX)
        references = [
            (notes_par_id.get(str(identifiant), {}).get("titre") or f"Note {identifiant}")
            for identifiant in angle.get("notes") or []
        ]
        if references:
            ctk.CTkLabel(
                bloc,
                text="Sources : " + " · ".join(references),
                font=(self._police, 11),
                text_color=DOUX_FONCE,
                anchor="w",
                justify="left",
                wraplength=720,
            ).pack(anchor="w", fill="x", padx=16, pady=(0, 12))

    def _libelle_angle(self, parent: tk.Misc, etiquette: str, texte: str, couleur: str) -> None:
        ctk.CTkLabel(
            parent,
            text=etiquette,
            font=(self._police, 10, "bold"),
            text_color=couleur,
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(6, 2))
        libelle = ctk.CTkLabel(
            parent,
            text=texte,
            font=(self._police, 13),
            text_color=TEXTE,
            anchor="w",
            justify="left",
            wraplength=720,
        )
        libelle.pack(anchor="w", fill="x", padx=16)
        self._auto_wrap(libelle)

    def _noter_angle(self, angle: dict, notes_par_id: dict, type_note: str) -> None:
        titre = str(angle.get("titre") or "Idée du cerveau IA").strip()
        morceaux = []
        if angle.get("promesse"):
            morceaux.append("Promesse : " + str(angle["promesse"]))
        if angle.get("hook"):
            morceaux.append("Hook : " + str(angle["hook"]))
        if angle.get("angle"):
            morceaux.append("Angle : " + str(angle["angle"]))
        if angle.get("pourquoi"):
            morceaux.append("Pourquoi : " + str(angle["pourquoi"]))
        references = [
            str(notes_par_id.get(str(identifiant), {}).get("titre") or identifiant)
            for identifiant in angle.get("notes") or []
        ]
        if references:
            morceaux.append("Notes sources : " + " · ".join(references))
        try:
            note_id = notes.creer(
                self.racine,
                titre=titre,
                contenu="\n\n".join(morceaux),
                type=type_note,
                statut="brouillon",
                tags=["cerveau", "angle", type_note],
            )
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Note impossible", str(probleme), parent=self)
            return
        self._message.set(f"Note #{note_id} enregistrée dans votre bibliothèque.")
        self._remplir_liste_notes()

    def _lancer_cerveau(self) -> None:
        if self._tache is not None:
            messagebox.showinfo(
                "Exécution en cours", "Attendez la fin ou arrêtez l'exécution.", parent=self
            )
            return
        if not notes.lister(
            self.racine, inclure_archivees=self._cerveau_archives.get()
        ):
            messagebox.showwarning(
                "Aucune note",
                "Le cerveau IA a besoin de votre matière. Créez d'abord quelques notes "
                "(hooks, concepts, sujets, idées).",
                parent=self,
            )
            self._afficher_page("notes")
            return
        if self._note_dirty and not self._sauvegarder_note(silencieux=True):
            return
        if not self._sauvegarder():
            return
        self._cerveau_etat.set("Analyse en cours…")
        self._message.set("Le cerveau IA relie vos notes…")
        self._lancer(
            arguments_brain(
                question=self._cerveau_question.get(),
                inclure_archives=self._cerveau_archives.get(),
                forcer=self._cerveau_forcer.get(),
            ),
            "Cerveau IA",
        )

    def _ouvrir_note(self, note_id: int) -> None:
        self._afficher_page("notes")
        # Une note ouverte depuis le cerveau doit être visible, même si un
        # ancien filtre de recherche était encore actif.
        self._notes_recherche.set("")
        self._notes_type_filtre.set("Tous")
        self._notes_statut_filtre.set("Tous")
        self._notes_epinglees.set(False)
        note = self._notes_index.get(str(note_id)) or notes.lire(self.racine, note_id)
        if note is not None:
            self._remplir_liste_notes(selection=note.id)
            self._note_courante_id = note.id
            self._charger_note(note)

    # ------------------------------------------------------------------
    # Page — Notes
    # ------------------------------------------------------------------
    def _page_notes(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["notes"] = page
        acces = ctk.CTkFrame(page, fg_color="transparent")
        acces.pack(fill="x", pady=(0, 10))
        self._bouton_accent(
            acces,
            "Ouvrir le tableau blanc",
            lambda: self._afficher_page("tableau"),
            width=190,
        ).pack(side="left")
        ctk.CTkLabel(
            acces,
            text="Pour dessiner un schéma, une carte mentale ou préparer un plan.",
            font=(self._police, 11),
            text_color=DOUX,
            anchor="w",
        ).pack(side="left", padx=(10, 0))
        panneau = self._decouper(page)
        panneau.pack(fill="both", expand=True)

        gauche = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(gauche, minsize=290, width=380, stretch="always")
        entete_g = ctk.CTkFrame(gauche, fg_color="transparent")
        entete_g.pack(fill="x", padx=14, pady=(14, 8))
        ctk.CTkLabel(
            entete_g,
            text="Bibliothèque",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            entete_g,
            textvariable=self._notes_compte,
            font=(self._police, 11),
            text_color=DOUX,
            anchor="e",
        ).pack(side="right")
        self._bouton_accent(gauche, "+  Nouvelle note", self._nouvelle_note).pack(
            fill="x", padx=14, pady=(0, 10)
        )
        self._recherche_notes = ctk.CTkEntry(
            gauche,
            textvariable=self._notes_recherche,
            height=34,
            corner_radius=6,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Rechercher un titre, un tag, une idée…",
            font=(self._police, 13),
        )
        self._recherche_notes.pack(fill="x", padx=14, pady=(0, 8))
        self._recherche_notes.bind("<KeyRelease>", self._notes_filtre_modifie)

        filtres = ctk.CTkFrame(gauche, fg_color="transparent")
        filtres.pack(fill="x", padx=14, pady=(0, 6))
        style_menu = {
            "height": 30,
            "corner_radius": 5,
            "fg_color": CHAMP,
            "button_color": ACCENT,
            "button_hover_color": ACCENT_SURVOL,
            "text_color": TEXTE,
            "dropdown_fg_color": SURFACE,
            "dropdown_hover_color": SURFACE_HAUTE,
            "dropdown_text_color": TEXTE,
            "font": (self._police, 12),
        }
        ctk.CTkOptionMenu(
            filtres,
            values=["Tous", *notes.TYPES.values()],
            variable=self._notes_type_filtre,
            command=lambda _: self._remplir_liste_notes(),
            width=148,
            **style_menu,
        ).pack(side="left")
        ctk.CTkOptionMenu(
            filtres,
            values=["Tous", *notes.STATUTS.values()],
            variable=self._notes_statut_filtre,
            command=lambda _: self._remplir_liste_notes(),
            width=132,
            **style_menu,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkSwitch(
            gauche,
            text="Épinglées seulement",
            variable=self._notes_epinglees,
            command=self._remplir_liste_notes,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 11),
        ).pack(anchor="w", padx=14, pady=(0, 8))

        cadre_liste = ctk.CTkFrame(gauche, fg_color="transparent")
        cadre_liste.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self._arbre_notes = ttk.Treeview(
            cadre_liste,
            columns=("titre", "type", "note", "maj"),
            show="headings",
            selectmode="browse",
            style="Outly.Treeview",
        )
        self._arbre_notes.heading("titre", text="Titre")
        self._arbre_notes.heading("type", text="Type")
        self._arbre_notes.heading("note", text="Note")
        self._arbre_notes.heading("maj", text="Modifiée")
        self._arbre_notes.column("titre", width=178, minwidth=120, stretch=True, anchor="w")
        self._arbre_notes.column("type", width=72, minwidth=60, stretch=False, anchor="w")
        self._arbre_notes.column("note", width=62, minwidth=54, stretch=False, anchor="center")
        self._arbre_notes.column("maj", width=86, minwidth=72, stretch=False, anchor="w")
        self._arbre_notes.bind("<<TreeviewSelect>>", lambda _: self._selectionner_note())
        barre_n = ctk.CTkScrollbar(
            cadre_liste,
            command=self._arbre_notes.yview,
            width=10,
            fg_color="transparent",
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
        )
        self._arbre_notes.configure(yscrollcommand=barre_n.set)
        barre_n.pack(side="right", fill="y")
        self._arbre_notes.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            gauche,
            text="Sauvegarde automatique après chaque pause de frappe. "
            "L'historique conserve les versions précédentes.",
            font=(self._police, 11),
            text_color=DOUX,
            anchor="w",
            justify="left",
            wraplength=300,
        ).pack(anchor="w", fill="x", padx=14, pady=(0, 12))

        droite = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(droite, minsize=380, width=620, stretch="always")
        entete_d = ctk.CTkFrame(droite, fg_color="transparent")
        entete_d.pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(
            entete_d,
            text="Éditeur",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            entete_d,
            textvariable=self._note_sauvegarde_etat,
            font=(self._police, 11),
            text_color=DOUX,
            anchor="e",
        ).pack(side="right")
        ctk.CTkLabel(
            droite,
            textvariable=self._note_meta,
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=18, pady=(0, 8))

        corps = ctk.CTkFrame(droite, fg_color="transparent")
        corps.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        ctk.CTkLabel(
            corps, text="TITRE", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        self._note_titre_entree = ctk.CTkEntry(
            corps,
            textvariable=self._note_titre,
            height=40,
            corner_radius=6,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Titre de la note",
            font=(self._police, 17, "bold"),
        )
        self._note_titre_entree.pack(fill="x", pady=(4, 10))

        meta = ctk.CTkFrame(corps, fg_color="transparent")
        meta.pack(fill="x", pady=(0, 8))
        self._note_type_menu = ctk.CTkOptionMenu(
            meta,
            values=list(notes.TYPES.values()),
            variable=self._note_type,
            command=lambda _: self._note_modifiee(),
            width=132,
            **style_menu,
        )
        self._note_type_menu.pack(side="left")
        self._note_statut_menu = ctk.CTkOptionMenu(
            meta,
            values=list(notes.STATUTS.values()),
            variable=self._note_statut,
            command=lambda _: self._note_modifiee(),
            width=126,
            **style_menu,
        )
        self._note_statut_menu.pack(side="left", padx=(8, 0))
        self._note_switch_epingle = ctk.CTkSwitch(
            meta,
            text="Épingler",
            variable=self._note_epinglee,
            command=self._note_modifiee,
            progress_color=ACCENT_CLAIR,
            button_color=ACCENT,
            text_color=DOUX,
            font=(self._police, 11),
        )
        self._note_switch_epingle.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(
            meta, text="NOTE", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(side="left", padx=(14, 4))
        self._note_note_menu = ctk.CTkOptionMenu(
            meta,
            values=notes.NOTES_CHOIX,
            variable=self._note_note,
            command=lambda _: self._note_modifiee(),
            width=104,
            **style_menu,
        )
        self._note_note_menu.pack(side="left")

        champs = ctk.CTkFrame(corps, fg_color="transparent")
        champs.pack(fill="x", pady=(0, 8))
        champs.grid_columnconfigure(0, weight=1)
        champs.grid_columnconfigure(1, weight=1)
        bloc_tags = ctk.CTkFrame(champs, fg_color="transparent")
        bloc_tags.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(
            bloc_tags, text="TAGS", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        self._note_tags_entree = ctk.CTkEntry(
            bloc_tags,
            textvariable=self._note_tags,
            height=32,
            corner_radius=5,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="hook, storytelling, audience",
            font=(self._police, 12),
        )
        self._note_tags_entree.pack(fill="x", pady=(3, 0))
        bloc_source = ctk.CTkFrame(champs, fg_color="transparent")
        bloc_source.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            bloc_source,
            text="SOURCE / LIEN",
            font=(self._police, 10, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        self._note_source_entree = ctk.CTkEntry(
            bloc_source,
            textvariable=self._note_source,
            height=32,
            corner_radius=5,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="https://…",
            font=(self._police, 12),
        )
        self._note_source_entree.pack(fill="x", pady=(3, 0))

        ctk.CTkLabel(
            corps, text="CONTENU", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        self._note_contenu = ctk.CTkTextbox(
            corps,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            corner_radius=5,
            text_color=TEXTE,
            font=(self._police, 14),
            wrap="word",
            undo=True,
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        self._note_contenu.pack(fill="both", expand=True, pady=(4, 8))
        self._note_contenu.bind("<<Modified>>", self._note_contenu_modifie)

        actions = ctk.CTkFrame(droite, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 14))
        self._note_bouton_sauver = self._bouton_accent(
            actions, "Enregistrer", lambda: self._sauvegarder_note(), width=112
        )
        self._note_bouton_sauver.pack(side="left")
        self._note_bouton_dupliquer = self._bouton(
            actions, "Dupliquer", self._dupliquer_note, width=88
        )
        self._note_bouton_dupliquer.pack(side="left", padx=(8, 0))
        self._note_bouton_historique = self._bouton(
            actions, "Historique", self._ouvrir_historique_note, width=92
        )
        self._note_bouton_historique.pack(side="left", padx=(8, 0))
        self._note_bouton_source = self._bouton(
            actions, "Source", self._ouvrir_source_note, width=78
        )
        self._note_bouton_source.pack(side="left", padx=(8, 0))
        self._note_bouton_supprimer = self._bouton(
            actions, "Supprimer", self._supprimer_note, width=80
        )
        self._note_bouton_supprimer.pack(side="right")

        self._note_editeur_widgets = [
            self._note_titre_entree,
            self._note_type_menu,
            self._note_statut_menu,
            self._note_switch_epingle,
            self._note_note_menu,
            self._note_tags_entree,
            self._note_source_entree,
            self._note_contenu,
            self._note_bouton_sauver,
            self._note_bouton_dupliquer,
            self._note_bouton_historique,
            self._note_bouton_source,
            self._note_bouton_supprimer,
        ]
        for variable in (self._note_titre, self._note_tags, self._note_source):
            variable.trace_add("write", self._note_modifiee)
        self._activer_editeur_note(False)
        self._remplir_liste_notes()

    def _notes_filtre_modifie(self, _event=None) -> None:
        if self._notes_filtre_timer is not None:
            self.after_cancel(self._notes_filtre_timer)
        self._notes_filtre_timer = self.after(180, self._remplir_liste_notes)

    def _remplir_liste_notes(self, selection: int | None = None) -> None:
        if not hasattr(self, "_arbre_notes"):
            return
        self._notes_filtre_timer = None
        if selection is None:
            selection = self._note_courante_id
        type_filtre = self._notes_type_filtre.get()
        statut_filtre = self._notes_statut_filtre.get()
        liste = notes.lister(
            self.racine,
            recherche=self._notes_recherche.get(),
            type_note=None if type_filtre == "Tous" else type_filtre,
            statut=None if statut_filtre == "Tous" else statut_filtre,
            epinglees_seulement=self._notes_epinglees.get(),
        )
        self._note_chargement = True
        self._arbre_notes.delete(*self._arbre_notes.get_children())
        self._notes_index = {str(note.id): note for note in liste}
        for note in liste:
            titre = ("★ " if note.epingle else "") + note.titre
            self._arbre_notes.insert(
                "",
                "end",
                iid=str(note.id),
                values=(
                    titre,
                    note.etiquette_type,
                    note.note_lisible,
                    _date_lisible(note.maj_le)[:10],
                ),
            )
        epinglees = sum(1 for note in liste if note.epingle)
        self._notes_compte.set(f"{len(liste)} note(s)" + (f" · {epinglees} ★" if epinglees else ""))
        if selection is None and self._note_courante_id is None and liste:
            selection = liste[0].id
        if selection is not None and str(selection) in self._notes_index:
            self._arbre_notes.selection_set(str(selection))
            self._arbre_notes.see(str(selection))
            if self._note_courante_id != int(selection):
                self._note_courante_id = int(selection)
                self._note_chargement = False
                self._charger_note(self._notes_index[str(selection)])
                return
        self._note_chargement = False
        if not liste and self._note_courante_id is None:
            self._vider_editeur_note()

    def _selectionner_note(self) -> None:
        if self._note_chargement:
            return
        selection = self._arbre_notes.selection()
        if not selection:
            return
        identifiant = selection[0]
        if self._note_courante_id is not None and str(self._note_courante_id) == identifiant:
            return
        if self._note_dirty and not self._sauvegarder_note(silencieux=True):
            return
        note = self._notes_index.get(identifiant)
        if note is None:
            return
        self._note_courante_id = note.id
        self._charger_note(note)

    def _charger_note(self, note: notes.Note) -> None:
        self._note_chargement = True
        self._note_titre.set(note.titre)
        self._note_tags.set(", ".join(note.tags))
        self._note_source.set(note.source)
        self._note_type.set(note.etiquette_type)
        self._note_statut.set(note.etiquette_statut)
        self._note_epinglee.set(note.epingle)
        self._note_note.set(note.note_lisible)
        self._note_contenu.delete("1.0", "end")
        self._note_contenu.insert("1.0", note.contenu)
        self._note_contenu.edit_modified(False)
        self._note_meta.set(
            f"Créée le {_date_lisible(note.cree_le)} · modifiée le {_date_lisible(note.maj_le)}"
            + (f" · source : {note.source[:90]}" if note.source else "")
        )
        self._note_sauvegarde_etat.set("À jour")
        self._note_dirty = False
        self._note_chargement = False
        self._activer_editeur_note(True)
        self._note_bouton_source.configure(
            state="normal" if note.source.startswith("http") else "disabled"
        )
        self._note_titre_entree.focus_set()

    def _note_contenu_modifie(self, _event=None) -> None:
        if self._note_chargement:
            self._note_contenu.edit_modified(False)
            return
        modifie = bool(self._note_contenu.edit_modified())
        self._note_contenu.edit_modified(False)
        if modifie:
            self._note_modifiee()

    def _note_modifiee(self, *_args) -> None:
        if self._note_chargement or self._note_courante_id is None:
            return
        self._note_dirty = True
        self._note_sauvegarde_etat.set("Modifications en attente…")
        if self._note_sauvegarde_timer is not None:
            self.after_cancel(self._note_sauvegarde_timer)
        self._note_sauvegarde_timer = self.after(900, self._sauvegarder_note_auto)

    def _sauvegarder_note_auto(self) -> None:
        self._note_sauvegarde_timer = None
        self._sauvegarder_note(silencieux=True)

    def _sauvegarder_note(self, silencieux: bool = False) -> bool:
        if self._note_sauvegarde_timer is not None:
            self.after_cancel(self._note_sauvegarde_timer)
            self._note_sauvegarde_timer = None
        if self._note_courante_id is None:
            return False
        type_code = notes.TYPES_LABELLES.get(self._note_type.get(), "note")
        statut_code = notes.STATUTS_LABELLES.get(self._note_statut.get(), "brouillon")
        try:
            a_change = notes.modifier(
                self.racine,
                self._note_courante_id,
                titre=self._note_titre.get(),
                contenu=self._note_contenu.get("1.0", "end-1c"),
                type=type_code,
                statut=statut_code,
                tags=self._note_tags.get(),
                epingle=self._note_epinglee.get(),
                note_sur_10=notes.normaliser_note(self._note_note.get()),
                source=self._note_source.get(),
            )
        except notes.ErreurNotes as probleme:
            self._note_sauvegarde_etat.set("Échec de l'enregistrement")
            if not silencieux:
                messagebox.showerror("Note non enregistrée", str(probleme), parent=self)
            return False
        note = notes.lire(self.racine, self._note_courante_id)
        if note is not None:
            self._note_meta.set(
                f"Créée le {_date_lisible(note.cree_le)} · modifiée le {_date_lisible(note.maj_le)}"
                + (f" · source : {note.source[:90]}" if note.source else "")
            )
            self._note_bouton_source.configure(
                state="normal" if note.source.startswith("http") else "disabled"
            )
        self._note_dirty = False
        self._note_sauvegarde_etat.set("Enregistré à " + datetime.now().strftime("%H:%M"))
        if a_change:
            self._remplir_liste_notes(selection=self._note_courante_id)
        return True

    def _nouvelle_note(self) -> None:
        if self._note_dirty and not self._sauvegarder_note(silencieux=True):
            return
        type_filtre = self._notes_type_filtre.get()
        type_code = notes.TYPES_LABELLES.get(type_filtre, "note")
        try:
            note_id = notes.creer(
                self.racine,
                titre="Nouvelle note",
                type=type_code,
                tags=[],
            )
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Création impossible", str(probleme), parent=self)
            return
        self._notes_recherche.set("")
        self._notes_type_filtre.set("Tous")
        self._notes_statut_filtre.set("Tous")
        self._notes_epinglees.set(False)
        self._remplir_liste_notes(selection=note_id)
        note = notes.lire(self.racine, note_id)
        if note is not None:
            self._note_courante_id = note.id
            self._charger_note(note)
        self.after(80, self._selectionner_titre_note)

    def _selectionner_titre_note(self) -> None:
        self._note_titre_entree.focus_set()
        self._note_titre_entree.select_range(0, "end")

    def _dupliquer_note(self) -> None:
        if self._note_courante_id is None:
            return
        if self._note_dirty and not self._sauvegarder_note(silencieux=True):
            return
        source = notes.lire(self.racine, self._note_courante_id)
        if source is None:
            return
        try:
            note_id = notes.creer(
                self.racine,
                titre=source.titre + " (copie)",
                contenu=source.contenu,
                type=source.type,
                statut="brouillon",
                tags=source.tags,
                note_sur_10=source.note_sur_10,
                source=source.source,
            )
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Duplication impossible", str(probleme), parent=self)
            return
        self._remplir_liste_notes(selection=note_id)
        note = notes.lire(self.racine, note_id)
        if note is not None:
            self._note_courante_id = note.id
            self._charger_note(note)
        self._message.set("Note dupliquée.")

    def _supprimer_note(self) -> None:
        if self._note_courante_id is None:
            return
        if not self._confirmer(
            "Supprimer la note",
            "Supprimer définitivement cette note et tout son historique ?",
        ):
            return
        try:
            notes.supprimer(self.racine, self._note_courante_id)
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Suppression impossible", str(probleme), parent=self)
            return
        self._note_courante_id = None
        self._vider_editeur_note()
        self._remplir_liste_notes()
        self._message.set("Note supprimée.")

    def _vider_editeur_note(self) -> None:
        self._note_chargement = True
        self._note_titre.set("")
        self._note_tags.set("")
        self._note_source.set("")
        self._note_note.set(notes.NOTES_CHOIX[0])
        self._note_contenu.delete("1.0", "end")
        self._note_contenu.edit_modified(False)
        self._note_meta.set("Sélectionnez une note, ou créez-en une nouvelle.")
        self._note_sauvegarde_etat.set("")
        self._note_dirty = False
        self._note_chargement = False
        self._activer_editeur_note(False)

    def _activer_editeur_note(self, actif: bool) -> None:
        etat = "normal" if actif else "disabled"
        for widget in self._note_editeur_widgets:
            try:
                widget.configure(state=etat)
            except (tk.TclError, ValueError):
                pass

    def _ouvrir_source_note(self) -> None:
        source = self._note_source.get().strip()
        if source.startswith("http"):
            webbrowser.open(source)

    def _ouvrir_historique_note(self) -> None:
        if self._note_courante_id is None:
            return
        versions = notes.revisions(self.racine, self._note_courante_id)
        fenetre = ctk.CTkToplevel(self)
        fenetre.title("Historique de la note")
        fenetre.geometry("760x560")
        fenetre.configure(fg_color=FOND)
        fenetre.transient(self)
        try:
            fenetre.grab_set()
        except tk.TclError:
            pass
        ctk.CTkLabel(
            fenetre,
            text="Versions précédentes",
            font=(self._police, 20, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(fill="x", padx=20, pady=(18, 2))
        ctk.CTkLabel(
            fenetre,
            text="Restaurer remplace le contenu actuel ; la version actuelle est conservée "
            "dans l'historique.",
            font=(self._police, 12),
            text_color=DOUX,
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 10))
        zone = ctk.CTkScrollableFrame(fenetre, fg_color=FOND)
        zone.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        if not versions:
            ctk.CTkLabel(
                zone,
                text="Aucune version précédente : l'historique se remplit à la première "
                "modification du contenu.",
                font=(self._police, 13),
                text_color=DOUX,
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=8, pady=8)
            return
        for version in versions:
            bloc = ctk.CTkFrame(zone, fg_color=SURFACE, corner_radius=6)
            bloc.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                bloc,
                text=f"{_date_lisible(version.cree_le)} · {version.titre}",
                font=(self._police, 13, "bold"),
                text_color=TEXTE,
                anchor="w",
                justify="left",
            ).pack(anchor="w", fill="x", padx=14, pady=(10, 3))
            apercu = ctk.CTkLabel(
                bloc,
                text=(" ".join(version.contenu.split())[:300] or "(contenu vide)"),
                font=(self._police, 12),
                text_color=DOUX,
                anchor="w",
                justify="left",
            )
            apercu.pack(anchor="w", fill="x", padx=14, pady=(0, 8))
            self._auto_wrap(apercu)
            self._lien(
                bloc,
                "Restaurer",
                lambda v=version.id, f=fenetre: self._restaurer_version(v, f),
                width=94,
            ).pack(anchor="e", padx=14, pady=(0, 10))

    def _restaurer_version(self, version_id: int, fenetre: ctk.CTkToplevel) -> None:
        if not messagebox.askyesno(
            "Restaurer cette version",
            "Le contenu actuel sera remplacé par cette version.",
            parent=fenetre,
        ):
            return
        try:
            note_id = notes.restaurer(self.racine, version_id)
        except notes.ErreurNotes as probleme:
            messagebox.showerror("Restauration impossible", str(probleme), parent=fenetre)
            return
        fenetre.destroy()
        self._note_courante_id = note_id
        note = notes.lire(self.racine, note_id)
        self._remplir_liste_notes(selection=note_id)
        if note is not None:
            self._charger_note(note)
        self._message.set("Version restaurée.")

    # ------------------------------------------------------------------
    # Page — À regarder
    # ------------------------------------------------------------------
    def _page_regarder(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["regarder"] = page

        barre = ctk.CTkFrame(page, fg_color="transparent")
        barre.pack(fill="x", pady=(0, 12))
        self._compte_playlist = tk.StringVar(value="")
        ctk.CTkLabel(
            barre,
            textvariable=self._compte_playlist,
            font=(self._police, 12),
            text_color=DOUX_FONCE,
        ).pack(side="left")
        self._bouton_playlist_vider = self._bouton(
            barre, "Tout effacer", self._vider_playlist, width=110
        )
        self._bouton_playlist_vider.pack(side="right")
        self._bouton_playlist_retirer = self._bouton(
            barre, "Retirer", self._retirer_playlist, width=90
        )
        self._bouton_playlist_retirer.pack(side="right", padx=(0, 8))
        self._bouton_playlist_ouvrir = self._bouton(
            barre, "Ouvrir", self._ouvrir_playlist, width=90
        )
        self._bouton_playlist_ouvrir.pack(side="right", padx=(0, 8))
        self._bouton(barre, "Actualiser", self._remplir_playlist, width=100).pack(
            side="right", padx=(0, 8)
        )
        self._playlist_note = tk.StringVar(value=notes.NOTES_CHOIX[0])
        self._playlist_note_menu = ctk.CTkOptionMenu(
            barre,
            values=notes.NOTES_CHOIX,
            variable=self._playlist_note,
            command=self._noter_playlist,
            width=112,
            height=30,
            corner_radius=5,
            fg_color=CHAMP,
            button_color=ACCENT,
            button_hover_color=ACCENT_SURVOL,
            text_color=TEXTE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=SURFACE_HAUTE,
            dropdown_text_color=TEXTE,
            font=(self._police, 12),
            state="disabled",
        )
        self._playlist_note_menu.pack(side="right", padx=(8, 0))

        cadre = ctk.CTkFrame(
            page, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        cadre.pack(fill="both", expand=True)
        interieur = ctk.CTkFrame(cadre, fg_color="transparent")
        interieur.pack(fill="both", expand=True, padx=10, pady=10)

        colonnes = (
            ("type", "Type", 80),
            ("titre", "Titre", 380),
            ("detail", "Détails", 330),
            ("note", "Note", 62),
            ("date", "Ajouté le", 132),
        )
        self._arbre_playlist = ttk.Treeview(
            interieur,
            columns=[colonne[0] for colonne in colonnes],
            show="headings",
            selectmode="browse",
            style="Outly.Treeview",
        )
        for cle, libelle, largeur in colonnes:
            self._arbre_playlist.heading(cle, text=libelle)
            self._arbre_playlist.column(
                cle,
                width=largeur,
                minwidth=64,
                anchor="w" if cle in ("titre", "detail") else "center",
                stretch=cle in ("titre", "detail"),
            )
        self._arbre_playlist.tag_configure("video", foreground=_couleur(TEXTE))
        self._arbre_playlist.tag_configure("channel", foreground=_couleur(ACCENT_TEXTE))
        self._arbre_playlist.bind(
            "<<TreeviewSelect>>", lambda _: self._maj_boutons_playlist()
        )
        self._arbre_playlist.bind("<Double-1>", lambda _: self._ouvrir_playlist())

        barre_v = ctk.CTkScrollbar(
            interieur,
            command=self._arbre_playlist.yview,
            width=12,
            fg_color="transparent",
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
        )
        self._arbre_playlist.configure(yscrollcommand=barre_v.set)
        barre_v.pack(side="right", fill="y")
        self._arbre_playlist.pack(side="left", fill="both", expand=True)

    def _maj_boutons_playlist(self) -> None:
        if not hasattr(self, "_arbre_playlist"):
            return
        actif = bool(self._arbre_playlist.selection())
        for bouton in (
            getattr(self, "_bouton_playlist_ouvrir", None),
            getattr(self, "_bouton_playlist_retirer", None),
        ):
            if bouton is not None:
                bouton.configure(state="normal" if actif else "disabled")
        if hasattr(self, "_bouton_playlist_vider"):
            self._bouton_playlist_vider.configure(
                state="normal" if self._arbre_playlist.get_children() else "disabled"
            )
        element = self._element_playlist_selectionne()
        if hasattr(self, "_playlist_note_menu"):
            # La variable est mise à jour directement : `set()` du menu
            # redéclencherait la commande et reclasserait l'élément.
            self._playlist_note.set(
                element.note_lisible if element else notes.NOTES_CHOIX[0]
            )
            self._playlist_note_menu.configure(
                state="normal" if element else "disabled"
            )

    def _noter_playlist(self, valeur: str) -> None:
        """Applique le classement choisi à l'élément sélectionné."""
        element = self._element_playlist_selectionne()
        if element is None:
            return
        try:
            note = notes.normaliser_note(valeur)
            playlist.noter(self.racine, element.element_id, element.type, note)
        except (notes.ErreurNotes, playlist.ErreurPlaylist) as probleme:
            messagebox.showerror("Classement de la playlist", str(probleme), parent=self)
            return
        self._message.set(f"« {element.titre} » : {notes.etiquette_note(note)}.")
        self._remplir_playlist()

    def _remplir_playlist(self) -> None:
        if not hasattr(self, "_arbre_playlist"):
            return
        selection = self._arbre_playlist.selection()
        ancienne = selection[0] if selection else None
        self._arbre_playlist.delete(*self._arbre_playlist.get_children())
        self._index_playlist.clear()
        elements = playlist.lister(self.racine)
        for element in elements:
            iid = f"{element.type}:{element.element_id}"
            self._arbre_playlist.insert(
                "",
                "end",
                iid=iid,
                values=(
                    element.etiquette_type,
                    element.titre,
                    element.sous_titre,
                    element.note_lisible,
                    _date_lisible(element.ajoute_le),
                ),
                tags=(element.type,),
            )
            self._index_playlist[iid] = element
        if elements:
            videos = sum(1 for element in elements if element.type == "video")
            self._compte_playlist.set(
                f"{len(elements)} élément(s) · {videos} vidéo(s) · "
                f"{len(elements) - videos} chaîne(s)"
            )
        else:
            self._compte_playlist.set(
                "Aucun élément. Dans Chaînes, utilisez « Enregistrer » sur une vidéo "
                "ou une chaîne."
            )
        if ancienne and self._arbre_playlist.exists(ancienne):
            self._arbre_playlist.selection_set(ancienne)
        self._maj_boutons_playlist()

    def _element_playlist_selectionne(self) -> playlist.Element | None:
        if not hasattr(self, "_arbre_playlist"):
            return None
        selection = self._arbre_playlist.selection()
        if not selection:
            return None
        return self._index_playlist.get(selection[0])

    def _ouvrir_playlist(self) -> None:
        element = self._element_playlist_selectionne()
        if element is None:
            return
        url = element.url
        if not url:
            url = (
                f"https://www.youtube.com/watch?v={element.element_id}"
                if element.type == "video"
                else f"https://www.youtube.com/channel/{element.element_id}"
            )
        webbrowser.open(url)

    def _retirer_playlist(self) -> None:
        element = self._element_playlist_selectionne()
        if element is None:
            return
        try:
            playlist.retirer(self.racine, element.element_id, element.type)
        except playlist.ErreurPlaylist as probleme:
            messagebox.showerror("Playlist", str(probleme), parent=self)
            return
        self._message.set(f"« {element.titre} » retiré de « À regarder ».")
        self._remplir_playlist()

    def _vider_playlist(self) -> None:
        if not self._confirmer(
            "Vider la playlist",
            "Retirer tous les éléments enregistrés dans « À regarder » ?",
        ):
            return
        try:
            nombre = playlist.vider(self.racine)
        except playlist.ErreurPlaylist as probleme:
            messagebox.showerror("Playlist", str(probleme), parent=self)
            return
        self._message.set(f"{nombre} élément(s) retiré(s) de « À regarder ».")
        self._remplir_playlist()

    # ------------------------------------------------------------------
    # Page — Plan éditorial
    # ------------------------------------------------------------------
    def _page_strategie(self) -> None:
        from .strategie_page import construire as construire_strategie

        construire_strategie(self)

    def _remplir_strategie(self) -> None:
        if not hasattr(self, "_strategie_vues"):
            return
        from .strategie_page import remplir as remplir_strategie

        remplir_strategie(self)
        self._decouvrir_zones("strategie")

    def _lancer_strategie(self) -> None:
        if self._tache is not None:
            messagebox.showinfo(
                "Exécution en cours", "Attendez la fin ou arrêtez l'exécution.", parent=self
            )
            return
        try:
            jours = int(self._strategie_jours.get().strip() or "10")
        except ValueError:
            jours = 10
        self._strategie_etat.set("Analyse des dernières vidéos en cours…")
        self._message.set("Diagnostic de stratégie en cours…")
        self._lancer(
            arguments_strategie(jours=jours, forcer=self._strategie_forcer.get()),
            "Stratégie",
        )

    def _estimer_production(self) -> None:
        if self._plan_courant_id is None:
            self._enregistrer_plan()
        if self._plan_courant_id is None:
            return
        if self._tache is not None:
            messagebox.showinfo(
                "Exécution en cours", "Attendez la fin ou arrêtez l'exécution.", parent=self
            )
            return
        self._production_resume.set("Estimation en cours…")
        self._lancer(
            arguments_production(self._plan_courant_id, forcer=True), "Production"
        )

    def _maj_production_resume(self, plan_id: int | None) -> None:
        if not hasattr(self, "_production_resume"):
            return
        if plan_id is None:
            self._production_resume.set("Enregistrez la fiche pour l'estimer.")
            return
        try:
            resultat = data.estimation_production(self.racine, plan_id)
        except Exception:
            resultat = None
        if not resultat:
            self._production_resume.set(
                "Aucune estimation. Lancez « Estimer la production » pour obtenir "
                "le temps, le matériel et le footage."
            )
            return
        estimation = resultat.get("estimation") or {}
        budget = estimation.get("budget_eur") or {}
        prefixe = "Locale · " if resultat.get("source") == "locale" else ""
        self._production_resume.set(
            prefixe
            + f"{estimation.get('temps_total_heures', '?')} h · difficulté "
            f"{estimation.get('difficulte', '?')}/5 · "
            f"{budget.get('minimum', '?')} à {budget.get('maximum', '?')} €"
        )

    def _afficher_production(self, plan_id: int | None) -> None:
        if plan_id is None:
            return
        resultat = data.estimation_production(self.racine, plan_id)
        if not resultat:
            return
        estimation = resultat.get("estimation") or {}
        fiche = self._plan_index.get(str(plan_id))
        fenetre = ctk.CTkToplevel(self)
        fenetre.title("Estimation de production")
        fenetre.geometry("760x760")
        fenetre.minsize(620, 520)
        fenetre.transient(self)
        corps = ctk.CTkScrollableFrame(fenetre, fg_color=FOND, corner_radius=0)
        corps.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(
            corps,
            text=fiche.titre if fiche else "Fiche du plan",
            font=(self._police, 21, "bold"),
            text_color=TEXTE,
            anchor="w",
            justify="left",
            wraplength=680,
        ).pack(fill="x")
        budget = estimation.get("budget_eur") or {}
        if resultat.get("source") == "locale":
            ctk.CTkLabel(
                corps,
                text="ESTIMATION LOCALE · aucune clé LLM · fourchette indicative, pas un devis",
                font=(self._police, 10, "bold"),
                text_color=ALERTE,
                anchor="w",
            ).pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            corps,
            text=f"{estimation.get('temps_total_heures', '?')} h · difficulté "
            f"{estimation.get('difficulte', '?')}/5 · budget "
            f"{budget.get('minimum', '?')} à {budget.get('maximum', '?')} €",
            font=(self._police, 13, "bold"),
            text_color=ACCENT_TEXTE,
            anchor="w",
        ).pack(fill="x", pady=(4, 12))

        def section(titre: str, lignes: list[str]) -> None:
            if not lignes:
                return
            cadre, interieur = self._carte(corps, titre)
            cadre.pack(fill="x", pady=(0, 10))
            for ligne in lignes:
                libelle = ctk.CTkLabel(
                    interieur,
                    text="•  " + ligne,
                    font=(self._police, 12),
                    text_color=TEXTE,
                    anchor="w",
                    justify="left",
                    wraplength=650,
                )
                libelle.pack(fill="x", pady=1)
                self._auto_wrap(libelle)

        section(
            "Temps par phase",
            [
                f"{p.get('phase')} · {p.get('heures')} h — " + ", ".join(p.get("taches") or [])
                for p in estimation.get("phases") or []
            ],
        )
        section(
            "Matériel et quantités",
            [
                f"{m.get('categorie')} × {m.get('quantite')} — {m.get('besoin')} "
                f"({m.get('budget_eur')} €)"
                for m in estimation.get("materiel") or []
            ],
        )
        tournage = estimation.get("tournage") or {}
        section(
            "Tournage",
            [f"Durée : {tournage.get('duree_heures', '?')} h"]
            + ["Lieu : " + str(v) for v in tournage.get("lieux") or []]
            + ["Plan : " + str(v) for v in tournage.get("plans") or []],
        )
        section(
            "Footage à prévoir",
            [
                f"{f.get('type')} · {f.get('quantite')} · {f.get('duree')} — {f.get('description')}"
                for f in estimation.get("footage") or []
            ],
        )
        post = estimation.get("post_production") or {}
        section("Post-production", [f"Durée : {post.get('duree_heures', '?')} h"] + list(post.get("etapes") or []))
        section("Risques", list(estimation.get("risques") or []))
        section("Checklist", list(estimation.get("checklist") or []))
        section("Hypothèses", list(estimation.get("hypotheses") or []))

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x", pady=(4, 0))
        texte = json.dumps(estimation, ensure_ascii=False, indent=2)
        self._bouton(
            actions,
            "Copier",
            lambda: (self.clipboard_clear(), self.clipboard_append(texte), self._message.set("Estimation copiée.")),
            width=110,
        ).pack(side="left")
        if fiche is not None:
            self._bouton(
                actions,
                "Ajouter aux notes",
                lambda: self._ajouter_estimation_notes(fiche, estimation),
                width=170,
            ).pack(side="left", padx=(8, 0))
        self._bouton(actions, "Fermer", fenetre.destroy, width=100).pack(side="right")

    def _ajouter_estimation_notes(self, fiche: plan.Fiche, estimation: dict) -> None:
        lignes = ["", "--- ESTIMATION DE PRODUCTION ---"]
        for phase in estimation.get("phases") or []:
            lignes.append(f"{phase.get('phase')} : {phase.get('heures')} h")
        for materiel in estimation.get("materiel") or []:
            lignes.append(
                f"{materiel.get('categorie')} × {materiel.get('quantite')} : "
                f"{materiel.get('besoin')}"
            )
        lignes.append("Checklist : " + " | ".join(estimation.get("checklist") or []))
        try:
            plan.modifier(
                self.racine,
                fiche.identifiant,
                titre=fiche.titre,
                date_prevue=fiche.date_prevue or "",
                statut=fiche.statut,
                format=fiche.format,
                notes=fiche.notes + "\n".join(lignes),
            )
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Plan éditorial", str(probleme), parent=self)
            return
        self._remplir_plan(fiche.identifiant)
        self._message.set("Estimation ajoutée aux notes de la fiche.")

    def _page_plan(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["plan"] = page
        self._bandeau_parcours(
            page,
            "Plan éditorial",
            [
                ("Créer une fiche", self._nouveau_plan),
                ("Fixer la date et le format", None),
                ("Produire puis publier", None),
            ],
            lien=("Voir mes concurrents", lambda: self._afficher_page("resultats")),
        )
        panneau = self._decouper(page)
        panneau.pack(fill="both", expand=True)

        cadre_liste = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(cadre_liste, minsize=420, width=560, stretch="always")
        entete = ctk.CTkFrame(cadre_liste, fg_color="transparent")
        entete.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(
            entete,
            text="Calendrier de production",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            entete,
            textvariable=self._plan_compte,
            font=(self._police, 11),
            text_color=DOUX,
            anchor="e",
        ).pack(side="right")
        self._bouton_accent(cadre_liste, "+  Nouvelle fiche", self._nouveau_plan).pack(
            fill="x", padx=16, pady=(0, 10)
        )

        cadre_arbre = ctk.CTkFrame(cadre_liste, fg_color="transparent")
        cadre_arbre.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        colonnes = (
            ("date", "Date", 112),
            ("titre", "Vidéo", 330),
            ("statut", "Étape", 110),
            ("format", "Format", 112),
        )
        self._arbre_plan = ttk.Treeview(
            cadre_arbre,
            columns=[colonne[0] for colonne in colonnes],
            show="headings",
            selectmode="browse",
            style="Outly.Treeview",
        )
        for cle, libelle, largeur in colonnes:
            self._arbre_plan.heading(cle, text=libelle)
            self._arbre_plan.column(
                cle,
                width=largeur,
                minwidth=76,
                anchor="w" if cle == "titre" else "center",
                stretch=cle == "titre",
            )
        self._arbre_plan.tag_configure("publie", foreground=_couleur(DOUX_FONCE))
        self._arbre_plan.tag_configure("retard", foreground=_couleur(ALERTE))
        self._arbre_plan.tag_configure("proche", foreground=_couleur(OK))
        self._arbre_plan.bind("<<TreeviewSelect>>", lambda _: self._selectionner_plan())
        barre = ctk.CTkScrollbar(
            cadre_arbre,
            command=self._arbre_plan.yview,
            width=12,
            fg_color="transparent",
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
        )
        self._arbre_plan.configure(yscrollcommand=barre.set)
        barre.pack(side="right", fill="y")
        self._arbre_plan.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            cadre_liste,
            textvariable=self._plan_resume,
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(fill="x", padx=16, pady=(0, 14))

        cadre_edit = ctk.CTkFrame(
            panneau, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        panneau.add(cadre_edit, minsize=400, width=450, stretch="always")
        corps = ctk.CTkFrame(cadre_edit, fg_color="transparent")
        corps.pack(fill="both", expand=True, padx=16, pady=14)
        ctk.CTkLabel(
            corps,
            text="Fiche de production",
            font=(self._police, 15, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            corps,
            text="Une idée, une date, une étape. Tout reste sur ce PC, sans quota.",
            font=(self._police, 11),
            text_color=DOUX,
            anchor="w",
            justify="left",
            wraplength=380,
        ).pack(anchor="w", pady=(2, 12))

        ctk.CTkLabel(
            corps, text="TITRE", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        ctk.CTkEntry(
            corps,
            textvariable=self._plan_titre,
            height=38,
            corner_radius=7,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="Ex. : 5 erreurs qui tuent votre première vidéo",
            font=(self._police, 14, "bold"),
        ).pack(fill="x", pady=(4, 10))

        lignes = ctk.CTkFrame(corps, fg_color="transparent")
        lignes.pack(fill="x", pady=(0, 10))
        lignes.grid_columnconfigure(0, weight=1)
        lignes.grid_columnconfigure(1, weight=1)
        bloc_date = ctk.CTkFrame(lignes, fg_color="transparent")
        bloc_date.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(
            bloc_date,
            text="DATE PRÉVUE",
            font=(self._police, 10, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkEntry(
            bloc_date,
            textvariable=self._plan_date,
            height=34,
            corner_radius=6,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            text_color=TEXTE,
            placeholder_text="JJ/MM/AAAA",
            font=(self._police, 12),
        ).pack(fill="x", pady=(4, 0))
        bloc_format = ctk.CTkFrame(lignes, fg_color="transparent")
        bloc_format.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            bloc_format,
            text="FORMAT",
            font=(self._police, 10, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkOptionMenu(
            bloc_format,
            values=list(plan.FORMATS.values()),
            variable=self._plan_format,
            height=34,
            corner_radius=6,
            fg_color=CHAMP,
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=ACCENT_DOUX,
            dropdown_text_color=TEXTE,
            text_color=TEXTE,
            font=(self._police, 12),
            dropdown_font=(self._police, 12),
        ).pack(fill="x", pady=(4, 0))

        ctk.CTkLabel(
            corps, text="ÉTAPE", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        ctk.CTkOptionMenu(
            corps,
            values=list(plan.STATUTS.values()),
            variable=self._plan_statut,
            height=34,
            corner_radius=6,
            fg_color=CHAMP,
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
            dropdown_fg_color=SURFACE,
            dropdown_hover_color=ACCENT_DOUX,
            dropdown_text_color=TEXTE,
            text_color=TEXTE,
            font=(self._police, 12),
            dropdown_font=(self._police, 12),
        ).pack(fill="x", pady=(4, 10))

        ctk.CTkLabel(
            corps, text="NOTES", font=(self._police, 10, "bold"), text_color=DOUX, anchor="w"
        ).pack(anchor="w")
        self._plan_notes = ctk.CTkTextbox(
            corps,
            fg_color=CHAMP,
            border_color=BORDURE,
            border_width=1,
            corner_radius=6,
            text_color=TEXTE,
            font=(self._police, 12),
            wrap="word",
            undo=True,
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        self._plan_notes.pack(fill="both", expand=True, pady=(4, 10))

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x")
        self._bouton_accent(actions, "Enregistrer", self._enregistrer_plan, width=126).pack(
            side="left"
        )
        self._bouton(actions, "Nouvelle", self._nouveau_plan, width=100).pack(
            side="left", padx=(8, 0)
        )
        self._bouton(actions, "Supprimer", self._supprimer_plan, width=104).pack(side="right")
        self._bouton(corps, "Marquer publiée", self._publier_plan).pack(fill="x", pady=(8, 0))

        cadre_prod = ctk.CTkFrame(corps, fg_color=SURFACE_HAUTE, corner_radius=6)
        cadre_prod.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(
            cadre_prod,
            text="PRODUCTION",
            font=(self._police, 9, "bold"),
            text_color=DOUX,
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(
            cadre_prod,
            textvariable=self._production_resume,
            font=(self._police, 10),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=360,
        ).pack(fill="x", padx=10)
        self._bouton(
            cadre_prod,
            "Estimer la production",
            self._estimer_production,
            image=icones.image("estimer", 16, _couleur(DOUX)),
            compound="left",
        ).pack(fill="x", padx=10, pady=(6, 9))
        self._maj_production_resume(None)

        cadre_taches = ctk.CTkFrame(corps, fg_color=SURFACE_HAUTE, corner_radius=6)
        cadre_taches.pack(fill="x", pady=(8, 0))
        entete_taches = ctk.CTkFrame(cadre_taches, fg_color="transparent")
        entete_taches.pack(fill="x", padx=10, pady=(7, 2))
        ctk.CTkLabel(
            entete_taches, text="TÂCHES DE PRODUCTION", font=(self._police, 9, "bold"),
            text_color=DOUX, anchor="w",
        ).pack(side="left")
        self._plan_taches_compte = tk.StringVar(value="0 / 0")
        ctk.CTkLabel(
            entete_taches, textvariable=self._plan_taches_compte,
            font=(self._police, 10), text_color=DOUX_FONCE,
        ).pack(side="right")
        self._plan_taches_liste = ctk.CTkScrollableFrame(
            cadre_taches, fg_color="transparent", height=104,
            scrollbar_button_color=SURFACE_HAUTE, scrollbar_button_hover_color=BORDURE,
        )
        self._plan_taches_liste.pack(fill="x", padx=4, pady=(0, 2))
        saisie = ctk.CTkFrame(cadre_taches, fg_color="transparent")
        saisie.pack(fill="x", padx=8, pady=(2, 8))
        self._plan_tache_texte = tk.StringVar()
        entree_tache = ctk.CTkEntry(
            saisie, textvariable=self._plan_tache_texte, height=30, corner_radius=5,
            fg_color=CHAMP, border_color=BORDURE, border_width=1, text_color=TEXTE,
            placeholder_text="Ex. : écrire le script, réserver le lieu…",
            font=(self._police, 11),
        )
        entree_tache.pack(side="left", fill="x", expand=True, padx=(0, 6))
        entree_tache.bind("<Return>", lambda _e: self._ajouter_tache_plan())
        self._bouton(saisie, "Ajouter", self._ajouter_tache_plan, width=76).pack(side="right")
        self._maj_taches_plan(None)

        ctk.CTkLabel(
            corps,
            textvariable=self._plan_etat,
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=390,
        ).pack(fill="x", pady=(8, 0))

        self._remplir_plan()

    def _remplir_plan(self, selection: int | None = None) -> None:
        if not hasattr(self, "_arbre_plan"):
            return
        if selection is None:
            selection = self._plan_courant_id
        fiches = plan.lister(self.racine)
        self._arbre_plan.delete(*self._arbre_plan.get_children())
        self._plan_index = {str(fiche.identifiant): fiche for fiche in fiches}
        for fiche in fiches:
            etiquettes: tuple[str, ...] = ()
            if fiche.publie:
                etiquettes = ("publie",)
            elif fiche.jours_restants is not None:
                if fiche.jours_restants < 0:
                    etiquettes = ("retard",)
                elif fiche.jours_restants <= 7:
                    etiquettes = ("proche",)
            self._arbre_plan.insert(
                "",
                "end",
                iid=str(fiche.identifiant),
                values=(
                    fiche.date_affichable or "—",
                    fiche.titre,
                    fiche.etiquette_statut,
                    fiche.etiquette_format,
                ),
                tags=etiquettes,
            )
        en_cours = [fiche for fiche in fiches if not fiche.publie]
        self._plan_compte.set(f"{len(fiches)} fiche(s) · {len(en_cours)} à produire")
        prochaines = sorted(
            (fiche for fiche in en_cours if fiche.date is not None),
            key=lambda fiche: fiche.date,
        )
        if fiches:
            morceaux = [f"{len(en_cours)} fiche(s) en cours"]
            if prochaines:
                prochaine = prochaines[0]
                jours = prochaine.jours_restants or 0
                echeance = (
                    "aujourd'hui"
                    if jours == 0
                    else ("demain" if jours == 1 else f"dans {jours} jour(s)")
                )
                morceaux.append(
                    f"prochaine publication le {prochaine.date_affichable} ({echeance})"
                )
            self._plan_resume.set(" · ".join(morceaux))
        else:
            self._plan_resume.set(
                "Aucune fiche : créez la première pour suivre une vidéo de l'idée à la publication."
            )
        if selection is not None and str(selection) in self._plan_index:
            self._arbre_plan.selection_set(str(selection))
            self._arbre_plan.see(str(selection))

    def _selectionner_plan(self) -> None:
        if not hasattr(self, "_arbre_plan"):
            return
        selection = self._arbre_plan.selection()
        if not selection:
            return
        fiche = self._plan_index.get(selection[0])
        if fiche is None:
            return
        self._plan_courant_id = fiche.identifiant
        self._plan_titre.set(fiche.titre)
        self._plan_date.set(fiche.date_affichable)
        self._plan_statut.set(fiche.etiquette_statut)
        self._plan_format.set(fiche.etiquette_format)
        self._plan_notes.delete("1.0", "end")
        self._plan_notes.insert("1.0", fiche.notes)
        self._plan_etat.set(f"Dernière modification : {_date_lisible(fiche.maj_le)}.")
        self._maj_production_resume(fiche.identifiant)
        self._maj_taches_plan(fiche.identifiant)

    def _nouveau_plan(self) -> None:
        self._plan_courant_id = None
        self._plan_titre.set("")
        self._plan_date.set("")
        self._plan_statut.set(plan.STATUTS["idee"])
        self._plan_format.set(plan.FORMATS["longue"])
        self._plan_notes.delete("1.0", "end")
        if hasattr(self, "_arbre_plan") and self._arbre_plan.selection():
            self._arbre_plan.selection_remove(self._arbre_plan.selection())
        self._maj_taches_plan(None)
        self._plan_etat.set("Nouvelle fiche : donnez un titre, puis enregistrez.")

    def _enregistrer_plan(self) -> None:
        titre = self._plan_titre.get().strip()
        notes = self._plan_notes.get("1.0", "end-1c")
        try:
            if self._plan_courant_id is None:
                identifiant = plan.ajouter(
                    self.racine,
                    titre=titre,
                    date_prevue=self._plan_date.get(),
                    statut=self._plan_statut.get(),
                    format=self._plan_format.get(),
                    notes=notes,
                )
            else:
                identifiant = self._plan_courant_id
                plan.modifier(
                    self.racine,
                    identifiant,
                    titre=titre,
                    date_prevue=self._plan_date.get(),
                    statut=self._plan_statut.get(),
                    format=self._plan_format.get(),
                    notes=notes,
                )
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Plan éditorial", str(probleme), parent=self)
            return
        self._plan_courant_id = identifiant
        self._remplir_plan(identifiant)
        self._maj_taches_plan(identifiant)
        self._plan_etat.set(f"« {titre} » enregistrée.")
        self._message.set("Plan éditorial enregistré.")

    def _maj_taches_plan(self, plan_id: int | None = None) -> None:
        if not hasattr(self, "_plan_taches_liste"):
            return
        for enfant in self._plan_taches_liste.winfo_children():
            enfant.destroy()
        if plan_id is None:
            plan_id = self._plan_courant_id
        if plan_id is None:
            self._plan_taches_compte.set("0 / 0")
            ctk.CTkLabel(
                self._plan_taches_liste,
                text="Sélectionnez ou enregistrez une fiche pour gérer ses tâches.",
                font=(self._police, 10), text_color=DOUX_FONCE,
                anchor="w", justify="left", wraplength=330,
            ).pack(fill="x", padx=8, pady=6)
            return
        taches = plan.lister_taches(self.racine, plan_id)
        faites = sum(1 for tache in taches if tache.fait)
        self._plan_taches_compte.set(f"{faites} / {len(taches)}")
        if not taches:
            ctk.CTkLabel(
                self._plan_taches_liste,
                text="Aucune tâche. Ajoutez la première ci-dessous.",
                font=(self._police, 10), text_color=DOUX_FONCE,
                anchor="w",
            ).pack(fill="x", padx=8, pady=6)
            return
        for tache in taches:
            ligne = ctk.CTkFrame(self._plan_taches_liste, fg_color="transparent")
            ligne.pack(fill="x", padx=4, pady=1)
            variable = tk.BooleanVar(value=tache.fait)
            ctk.CTkCheckBox(
                ligne, text=tache.libelle, variable=variable,
                command=lambda i=tache.identifiant, v=variable: self._basculer_tache_plan(i, v),
                font=(self._police, 11), text_color=DOUX_FONCE if tache.fait else TEXTE,
                fg_color=ACCENT, hover_color=ACCENT_SURVOL, border_color=BORDURE,
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                ligne, text="×", width=24, height=22,
                fg_color="transparent", hover_color=ALERTE_DOUX, text_color=DOUX,
                command=lambda i=tache.identifiant: self._supprimer_tache_plan(i),
            ).pack(side="right", padx=(4, 0))

    def _basculer_tache_plan(self, identifiant: int, variable: tk.BooleanVar) -> None:
        try:
            plan.basculer_tache(self.racine, identifiant, bool(variable.get()))
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Tâche", str(probleme), parent=self)
        self._maj_taches_plan()

    def _ajouter_tache_plan(self) -> None:
        if self._plan_courant_id is None:
            self._plan_etat.set("Enregistrez d'abord la fiche, puis ajoutez ses tâches.")
            return
        libelle = self._plan_tache_texte.get().strip()
        try:
            plan.ajouter_tache(self.racine, self._plan_courant_id, libelle)
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Tâche", str(probleme), parent=self)
            return
        self._plan_tache_texte.set("")
        self._maj_taches_plan()
        self._plan_etat.set("Tâche ajoutée à la fiche.")

    def _supprimer_tache_plan(self, identifiant: int) -> None:
        if not self._confirmer("Supprimer la tâche", "Retirer cette tâche de la fiche ?"):
            return
        try:
            plan.supprimer_tache(self.racine, identifiant)
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Tâche", str(probleme), parent=self)
            return
        self._maj_taches_plan()
        self._plan_etat.set("Tâche supprimée.")

    def _supprimer_plan(self) -> None:
        if self._plan_courant_id is None:
            self._plan_etat.set("Sélectionnez une fiche dans la liste avant de la supprimer.")
            return
        fiche = self._plan_index.get(str(self._plan_courant_id))
        titre = fiche.titre if fiche else "cette fiche"
        if not self._confirmer(
            "Supprimer la fiche", f"Supprimer « {titre} » du plan éditorial ?"
        ):
            return
        try:
            plan.supprimer(self.racine, self._plan_courant_id)
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Plan éditorial", str(probleme), parent=self)
            return
        self._nouveau_plan()
        self._remplir_plan()
        self._plan_etat.set(f"« {titre} » supprimée.")
        self._message.set("Fiche retirée du plan éditorial.")

    def _publier_plan(self) -> None:
        if self._plan_courant_id is None:
            self._plan_etat.set("Sélectionnez une fiche à marquer publiée.")
            return
        fiche = self._plan_index.get(str(self._plan_courant_id))
        if fiche is None:
            return
        try:
            plan.modifier(
                self.racine,
                fiche.identifiant,
                titre=fiche.titre,
                date_prevue=fiche.date_prevue or "",
                statut="publie",
                format=fiche.format,
                notes=fiche.notes,
            )
        except plan.ErreurPlan as probleme:
            messagebox.showerror("Plan éditorial", str(probleme), parent=self)
            return
        self._plan_statut.set(plan.STATUTS["publie"])
        self._remplir_plan(fiche.identifiant)
        self._plan_etat.set(f"« {fiche.titre} » marquée publiée.")
        self._message.set("Publication enregistrée.")

    # ------------------------------------------------------------------
    # Page — Comparateur
    # ------------------------------------------------------------------
    def _page_comparateur(self) -> None:
        page = ctk.CTkFrame(self._conteneur, fg_color=FOND, corner_radius=0)
        self._pages["comparateur"] = page
        # La page peut être reconstruite après une réinitialisation : les menus
        # de l'ancienne page sont détruits et ne doivent pas rester référencés.
        self._comparateur_choix = []
        self._comparateur_menus = []

        cadre, corps = self._carte(
            page,
            "Comparer jusqu'à trois chaînes",
            "Les mêmes indicateurs côte à côte : audience, régularité, formats et "
            "meilleur outlier. Lecture locale, aucun appel réseau.",
        )
        cadre.pack(fill="x")
        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x")
        style_menu = {
            "height": 34,
            "corner_radius": 6,
            "fg_color": CHAMP,
            "button_color": SURFACE_HAUTE,
            "button_hover_color": BORDURE,
            "dropdown_fg_color": SURFACE,
            "dropdown_hover_color": ACCENT_DOUX,
            "dropdown_text_color": TEXTE,
            "text_color": TEXTE,
            "font": (self._police, 12),
            "dropdown_font": (self._police, 12),
        }
        for index in range(3):
            rangee.grid_columnconfigure(index, weight=1)
            variable = tk.StringVar(value=COMPARATEUR_VIDE)
            menu = ctk.CTkOptionMenu(
                rangee,
                values=[COMPARATEUR_VIDE],
                variable=variable,
                command=lambda _valeur: self._remplir_comparateur(),
                width=250,
                **style_menu,
            )
            menu.grid(row=0, column=index, sticky="ew", padx=(0, 10))
            self._comparateur_choix.append(variable)
            self._comparateur_menus.append(menu)

        cadre_verdict, corps_verdict = self._carte(
            page,
            "Quelle chaîne suivre en priorité ?",
            "Un score local, sans IA ni réseau : proximité éditoriale, outlier, "
            "régularité, fraîcheur et preuve d'audience. Copiez des mécaniques, pas du contenu.",
        )
        cadre_verdict.pack(fill="x", pady=(14, 0))
        self._comparateur_verdict = ctk.CTkFrame(corps_verdict, fg_color="transparent")
        self._comparateur_verdict.pack(fill="x")

        cadre_table = ctk.CTkFrame(
            page, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
        )
        cadre_table.pack(fill="both", expand=True, pady=(14, 0))
        interieur = ctk.CTkFrame(cadre_table, fg_color="transparent")
        interieur.pack(fill="both", expand=True, padx=12, pady=12)
        self._arbre_comparateur = ttk.Treeview(
            interieur,
            columns=("critere", "a", "b", "c"),
            show="headings",
            style="Outly.Treeview",
            height=16,
        )
        self._arbre_comparateur.heading("critere", text="Indicateur")
        self._arbre_comparateur.column(
            "critere", width=210, minwidth=150, anchor="w", stretch=False
        )
        for colonne in ("a", "b", "c"):
            self._arbre_comparateur.heading(colonne, text="—")
            self._arbre_comparateur.column(
                colonne, width=230, minwidth=140, anchor="w", stretch=True
            )
        barre = ctk.CTkScrollbar(
            interieur,
            command=self._arbre_comparateur.yview,
            width=12,
            fg_color="transparent",
            button_color=SURFACE_HAUTE,
            button_hover_color=BORDURE,
        )
        self._arbre_comparateur.configure(yscrollcommand=barre.set)
        barre.pack(side="right", fill="y")
        self._arbre_comparateur.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(
            page,
            textvariable=self._comparateur_note,
            font=(self._police, 11),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=940,
        ).pack(fill="x", pady=(8, 0))

    def _maj_options_comparateur(self) -> None:
        """Alimente les menus avec les chaînes réellement évaluées."""
        if not hasattr(self, "_comparateur_menus"):
            return
        labels = [COMPARATEUR_VIDE]
        correspondance: dict[str, str] = {}
        # Index séparé de `_index` : ce dernier ne contient que les lignes
        # visibles après le filtre de la page Chaînes. Une chaîne choisie dans
        # le comparateur doit rester affichable même hors filtre.
        self._comparateur_index = {ligne.channel_id: ligne for ligne in self._lignes}
        for ligne in self._lignes:
            base = " ".join((ligne.titre or "Sans titre").split())
            if len(base) > 38:
                base = base[:36] + "…"
            etiquette = base
            if etiquette in correspondance:
                suffixe = ligne.channel_id[-6:]
                etiquette = f"{base} · {suffixe}"
                numero = 2
                while etiquette in correspondance:
                    etiquette = f"{base} · {suffixe}-{numero}"
                    numero += 1
            correspondance[etiquette] = ligne.channel_id
            labels.append(etiquette)
        self._comparateur_labels = correspondance
        for menu, variable in zip(self._comparateur_menus, self._comparateur_choix):
            menu.configure(values=labels)
            if variable.get() not in labels:
                variable.set(COMPARATEUR_VIDE)

    def _lignes_comparateur(self) -> list:
        """Chaînes choisies, indépendamment du filtre de la page Chaînes."""
        return [
            self._comparateur_index.get(
                self._comparateur_labels.get(variable.get(), "")
            )
            for variable in self._comparateur_choix
        ]

    def _remplir_comparateur(self) -> None:
        if not hasattr(self, "_arbre_comparateur"):
            return
        self._arbre_comparateur.delete(*self._arbre_comparateur.get_children())
        lignes = self._lignes_comparateur()
        for index, colonne in enumerate(("a", "b", "c")):
            ligne = lignes[index] if index < len(lignes) else None
            titre = ligne.titre if ligne is not None else "—"
            titre = " ".join(titre.split())
            if len(titre) > 26:
                titre = titre[:24] + "…"
            self._arbre_comparateur.heading(colonne, text=titre)

        choisies = [ligne for ligne in lignes if ligne is not None]
        self._comparateur_scores = comparaison.classer(choisies)
        self._remplir_verdict_comparateur(choisies)
        if not self._lignes:
            self._comparateur_note.set(
                "Aucune chaîne évaluée pour l'instant : lancez une veille, puis revenez ici."
            )
        elif len(choisies) < 2:
            self._comparateur_note.set(
                "Choisissez au moins deux chaînes ci-dessus pour lire la comparaison."
            )
        else:
            self._comparateur_note.set(
                "Trois colonnes restent disponibles : ajoutez une chaîne pour arbitrer."
            )

        def mediane(ligne, nom: str) -> str:
            if ligne is None or ligne.metriques is None:
                return "—"
            return _nombre(getattr(ligne.metriques, nom))

        scores_par_id = {score.channel_id: score for score in self._comparateur_scores}
        criteres = (
            (
                "priorite",
                "Priorité de suivi",
                lambda l: "—" if l is None or l.channel_id not in scores_par_id
                else f"{scores_par_id[l.channel_id].score}/100 · {scores_par_id[l.channel_id].confiance}",
            ),
            (
                "score",
                "Score de proximité",
                lambda l: "—" if l is None or l.score_affiche is None else str(l.score_affiche),
            ),
            ("abonnes", "Abonnés", lambda l: "—" if l is None else _nombre(l.abonnes)),
            ("mediane_longues", "Médiane longues", lambda l: mediane(l, "mediane_vues_longues")),
            ("mediane_shorts", "Médiane Shorts", lambda l: mediane(l, "mediane_vues_short")),
            (
                "cadence",
                "Cadence (vidéos/sem.)",
                lambda l: "—" if l is None or l.metriques is None
                else _nombre(l.metriques.cadence_semaine, 1),
            ),
            (
                "outlier",
                "Meilleur outlier",
                lambda l: "—" if l is None or l.metriques is None or l.metriques.meilleur_outlier is None
                else "×" + _nombre(l.metriques.meilleur_outlier, 1),
            ),
            (
                "formats",
                "Shorts / longues",
                lambda l: "—" if l is None or l.metriques is None
                else f"{l.metriques.n_shorts} / {l.metriques.n_longues}",
            ),
            (
                "derniere",
                "Dernière vidéo",
                lambda l: "—" if l is None or l.metriques is None
                else f"il y a {_nombre(l.metriques.jours_depuis_publication)} j",
            ),
            (
                "format",
                "Format détecté",
                lambda l: "—" if l is None or not l.format_detecte else l.format_detecte,
            ),
            (
                "recouvrement",
                "Recouvrement d'audience",
                lambda l: "—" if l is None or not l.recouvrement else l.recouvrement,
            ),
            ("langue", "Langue", lambda l: "—" if l is None or not l.langue else l.langue),
            (
                "statut",
                "Statut dans le radar",
                lambda l: "—" if l is None
                else ("Retenue" if l.statut == "retenue" else "Écartée"),
            ),
        )
        for cle, libelle, valeur in criteres:
            self._arbre_comparateur.insert(
                "",
                "end",
                iid=cle,
                values=[libelle] + [valeur(ligne) for ligne in lignes],
            )

    def _remplir_verdict_comparateur(self, choisies: list) -> None:
        if not hasattr(self, "_comparateur_verdict"):
            return
        for enfant in self._comparateur_verdict.winfo_children():
            enfant.destroy()
        scores = self._comparateur_scores
        if len(choisies) < 2 or not scores:
            ctk.CTkLabel(
                self._comparateur_verdict,
                text="Choisissez au moins deux chaînes pour obtenir un classement.",
                font=(self._police, 12),
                text_color=DOUX,
                anchor="w",
            ).pack(fill="x")
            return
        grille = ctk.CTkFrame(self._comparateur_verdict, fg_color="transparent")
        grille.pack(fill="x")
        for index, score in enumerate(scores[:3]):
            grille.grid_columnconfigure(index, weight=1, uniform="suivi")
            bloc = ctk.CTkFrame(
                grille,
                fg_color=OK_DOUX if index == 0 else SURFACE_HAUTE,
                corner_radius=7,
                border_width=1,
                border_color=OK if index == 0 else BORDURE,
            )
            bloc.grid(
                row=0, column=index, sticky="nsew",
                padx=(0, 8) if index < len(scores[:3]) - 1 else 0,
            )
            ctk.CTkLabel(
                bloc,
                text=("À SUIVRE EN PRIORITÉ" if index == 0 else f"PRIORITÉ {score.rang}"),
                font=(self._police, 9, "bold"),
                text_color=OK if index == 0 else DOUX,
                anchor="w",
            ).pack(fill="x", padx=12, pady=(9, 0))
            ctk.CTkLabel(
                bloc,
                text=f"{score.score}/100",
                font=(self._police, 24, "bold"),
                text_color=TEXTE,
                anchor="w",
            ).pack(fill="x", padx=12)
            ctk.CTkLabel(
                bloc,
                text=score.titre,
                font=(self._police, 12, "bold"),
                text_color=TEXTE,
                anchor="w",
                justify="left",
                wraplength=250,
            ).pack(fill="x", padx=12)
            for raison in score.raisons[:3]:
                ctk.CTkLabel(
                    bloc,
                    text="•  " + raison,
                    font=(self._police, 10),
                    text_color=DOUX,
                    anchor="w",
                    justify="left",
                    wraplength=250,
                ).pack(fill="x", padx=12, pady=(2, 0))
            if score.manques:
                ctk.CTkLabel(
                    bloc,
                    text="Confiance " + score.confiance + " · " + score.manques[0],
                    font=(self._police, 9),
                    text_color=ALERTE,
                    anchor="w",
                    wraplength=250,
                ).pack(fill="x", padx=12, pady=(4, 9))
            else:
                ctk.CTkFrame(bloc, fg_color="transparent", height=8).pack()
        ctk.CTkLabel(
            self._comparateur_verdict,
            text=comparaison.recommandation(scores),
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=900,
        ).pack(fill="x", pady=(9, 0))

    def _filtre_modifie(self, _event=None) -> None:
        if self._filtre_timer is not None:
            try:
                self.after_cancel(self._filtre_timer)
            except ValueError:
                pass
        self._filtre_timer = self.after(140, self._remplir_arbre)

    def _maj_entetes(self) -> None:
        """Affiche une flèche sur la colonne de tri active."""
        if not hasattr(self, "_arbre"):
            return
        for cle, libelle, _, _ in COLONNES_RESULTATS:
            texte = libelle
            if self._tri[0] == cle:
                texte += " ▼" if self._tri[1] else " ▲"
            self._arbre.heading(cle, text=texte)

    def _trier(self, cle: str) -> None:
        """Trie le tableau ; un second clic inverse l'ordre."""
        if self._tri[0] == cle:
            self._tri = (cle, not self._tri[1])
        else:
            self._tri = (cle, True)
        self._maj_entetes()
        self._remplir_arbre()

    def _valeur_tri(self, ligne: report.LigneRapport, cle: str):
        if cle == "score":
            return ligne.score_affiche if ligne.score_affiche is not None else -1
        if cle == "chaine":
            return (ligne.titre or "").lower()
        if cle == "abonnes":
            return ligne.abonnes if ligne.abonnes is not None else -1
        if cle == "mediane":
            if ligne.metriques and ligne.metriques.mediane_vues_longues is not None:
                return ligne.metriques.mediane_vues_longues
            return -1
        if cle == "cadence":
            if ligne.metriques and ligne.metriques.cadence_semaine is not None:
                return ligne.metriques.cadence_semaine
            return -1
        if cle == "outlier":
            if ligne.metriques and ligne.metriques.meilleur_outlier is not None:
                return ligne.metriques.meilleur_outlier
            return -1
        if cle == "statut":
            return 0 if ligne.statut == "retenue" else 1
        return 0

    def _trier_lignes(self, lignes: list[report.LigneRapport]) -> list[report.LigneRapport]:
        """Trie chaque groupe séparément : les retenues restent toujours en tête."""
        cle, descendant = self._tri
        retenues = [ligne for ligne in lignes if ligne.statut == "retenue"]
        ecartees = [ligne for ligne in lignes if ligne.statut != "retenue"]
        retenues.sort(key=lambda ligne: self._valeur_tri(ligne, cle), reverse=descendant)
        ecartees.sort(key=lambda ligne: self._valeur_tri(ligne, cle), reverse=descendant)
        return retenues + ecartees

    # ------------------------------------------------------------------
    # Remplissage initial
    # ------------------------------------------------------------------
    def _remplir(self) -> None:
        """Lit une seule fois les fichiers de réglage, sans construire de page.

        Chaque page se remplit à sa première ouverture : l'affichage de la
        fenêtre ne dépend donc plus du nombre de champs à créer.
        """
        try:
            self._configuration = settings.lire_config(self.racine / "config.yaml")
        except settings.ErreurFichier as probleme:
            self._configuration = {}
            self._message.set(str(probleme))
        else:
            self._message.set("Prêt.")
        self._env_initial = settings.lire_env(self.racine / ".env")

        # Les variables qui existent déjà sont remplies tout de suite :
        # enregistrer depuis une page encore fermée ne doit jamais écraser une
        # valeur du fichier par du vide.
        for (section, cle), variable in self._bascule.items():
            variable.set(bool((self._configuration.get(section) or {}).get(cle, False)))
        configuration = self._configuration
        moi = configuration.get("moi") or {}
        self._ma_chaine.set(str(moi.get("chaine", "") or ""))
        self._international.set(bool(moi.get("recherche_internationale", True)))
        self._hooks_sujet.set(
            str((configuration.get("hooks") or {}).get("sujet", "") or "")
        )
        self._modifie = False

    # --- remplissage différé des pages de configuration ----------------
    def _charger_cles(self) -> None:
        """Alimente la page Clés & modèle depuis `.env`, une seule fois."""
        etat = self._chargement
        self._chargement = True
        try:
            for nom, variable in self._env_vars.items():
                variable.set(self._env_initial.get(nom, ""))
            # Aucune URL enregistrée : on préremplit Gemini, le fournisseur
            # gratuit documenté. L'utilisateur peut en changer d'un clic.
            if not self._env_vars["LLM_BASE_URL"].get().strip():
                base_defaut, modele_defaut = FOURNISSEURS["Gemini"]
                self._env_vars["LLM_BASE_URL"].set(base_defaut)
                if not self._env_vars["LLM_MODEL"].get().strip():
                    self._env_vars["LLM_MODEL"].set(modele_defaut)
            self._fournisseur.set(
                self._fournisseur_depuis(self._env_vars["LLM_BASE_URL"].get())
            )
            base_secours = self._env_vars["LLM_FALLBACK_BASE_URL"].get().strip()
            modele_secours = self._env_vars["LLM_FALLBACK_MODEL"].get().strip()
            cle_secours = self._env_vars["LLM_FALLBACK_API_KEY"].get().strip()
            if base_secours and modele_secours and (
                cle_secours or url_locale(base_secours)
            ):
                self._fournisseur_secours.set(self._fournisseur_depuis(base_secours))
            else:
                self._fournisseur_secours.set("Aucun")
        finally:
            self._chargement = etat

    def _charger_contenu(self) -> None:
        """Charge les trois éditeurs et le point de départ, une seule fois."""
        etat = self._chargement
        self._chargement = True
        try:
            for cle, chemin in (
                ("positioning", self.racine / "positioning.md"),
                ("seeds", self.racine / "seeds.txt"),
                ("keywords", self.racine / "keywords.txt"),
            ):
                editeur = self._editeurs.get(cle)
                if editeur is None or editeur.get("1.0", "end-1c"):
                    continue
                editeur.insert("1.0", settings.lire_texte(chemin))
                self._maj_compteur(cle, marquer=False)
        finally:
            self._chargement = etat

    def _charger_reglages(self) -> None:
        """Remplit les champs numériques de Réglages depuis `config.yaml`."""
        configuration = self._configuration
        etat = self._chargement
        self._chargement = True
        try:
            for (section, cle), variable in self._reglages.items():
                valeur = (configuration.get(section) or {}).get(cle, "")
                if (valeur is None or valeur == "") and (section, cle) in DEFAUTS_REGLAGES:
                    valeur = DEFAUTS_REGLAGES[(section, cle)]
                variable.set("" if valeur is None else str(valeur))
            for (section, cle), variable in self._bascule.items():
                variable.set(bool((configuration.get(section) or {}).get(cle, False)))
            moi = configuration.get("moi") or {}
            self._ma_chaine.set(str(moi.get("chaine", "") or ""))
            self._international.set(bool(moi.get("recherche_internationale", True)))
        finally:
            self._chargement = etat

    def _charger_email(self) -> None:
        """Remplit la page Rapports e-mail et son état, une seule fois."""
        section_email = self._configuration.get("email") or {}
        etat = self._chargement
        self._chargement = True
        try:
            for cle, variable in self._champs_email.items():
                valeur = section_email.get(cle, DEFAUTS_EMAIL.get(cle, ""))
                variable.set("" if valeur is None else str(valeur))
            self._frequence.set(
                FREQUENCES_INVERSES.get(
                    str(section_email.get("frequence", "jamais")), "Jamais"
                )
            )
            try:
                jour = int(section_email.get("jour_semaine", 1))
            except (TypeError, ValueError):
                jour = 1
            self._jour_semaine.set(JOURS_UI[max(0, min(len(JOURS_UI) - 1, jour - 1))])
            for (section, cle), variable in self._bascule.items():
                if section == "email":
                    variable.set(
                        bool((self._configuration.get(section) or {}).get(cle, False))
                    )
        finally:
            self._chargement = etat
        self._rafraichir_email_statut()

    def _marquer_modifie(self, *_args) -> None:
        if self._chargement:
            return
        self._modifie = True
        self._message.set("Modifications non enregistrées (Ctrl+S)")

    # ------------------------------------------------------------------
    # Sauvegarde et validation
    # ------------------------------------------------------------------
    def _collecter_reglages(self) -> dict[tuple[str, str], int]:
        valeurs: dict[tuple[str, str], int] = {}
        for (section, cle), variable in self._reglages.items():
            brut = variable.get().strip()
            if not re.fullmatch(r"-?\d+", brut):
                raise ValueError(f"« {section}.{cle} » doit être un nombre entier (lu : {brut!r}).")
            valeurs[(section, cle)] = int(brut)
        return valeurs

    def _sauvegarder(self) -> bool:
        try:
            reglages = self._collecter_reglages()
            reglages_email = self._collecter_email()
            self._verifier_coherence(reglages)
            # Une page jamais ouverte n'a pas de champs : ne rien écrire pour
            # elle vaut mieux que d'écraser un réglage par une valeur vide.
            valeurs_env = {
                nom: variable.get().strip() for nom, variable in self._env_vars.items()
            }
            if valeurs_env:
                settings.ecrire_env(self.racine / ".env", valeurs_env)
            if reglages or reglages_email:
                settings.ecrire_config(
                    self.racine / "config.yaml", {**reglages, **reglages_email}
                )
            bascules = {
                (section, cle): variable.get()
                for (section, cle), variable in self._bascule.items()
            }
            if bascules:
                settings.ecrire_config(self.racine / "config.yaml", bascules)
            settings.ecrire_config(
                self.racine / "config.yaml",
                {("moi", "chaine"): self._ma_chaine.get().strip()},
            )
            settings.ecrire_config(
                self.racine / "config.yaml",
                {("hooks", "sujet"): self._hooks_sujet.get().strip()},
            )
            for cle, chemin in (
                ("positioning", self.racine / "positioning.md"),
                ("seeds", self.racine / "seeds.txt"),
                ("keywords", self.racine / "keywords.txt"),
            ):
                editeur = self._editeurs.get(cle)
                if editeur is None:
                    continue
                settings.ecrire_texte(chemin, editeur.get("1.0", "end-1c"))
        except (settings.ErreurFichier, ValueError) as probleme:
            self._message.set(f"Enregistrement impossible : {probleme}")
            messagebox.showerror("Enregistrement impossible", str(probleme), parent=self)
            return False

        validation = valider_config(self.racine)
        if not validation.reussie:
            if validation.lancee:
                messagebox.showerror(
                    "Configuration refusée",
                    "Les fichiers ont été écrits mais la configuration est incohérente :\n\n"
                    + self._extrait_erreur(validation.sortie),
                    parent=self,
                )
            else:
                messagebox.showerror(
                    "Vérification impossible",
                    "Les fichiers ont bien été enregistrés, mais la relecture de la "
                    "configuration a échoué :\n\n"
                    + validation.sortie
                    + "\n\nVérifiez les droits d'accès au dossier et relancez "
                    "l'application.",
                    parent=self,
                )
            return False

        self._modifie = False
        self._message.set(f"Enregistré à {datetime.now().strftime('%H:%M')}.")
        self._rafraichir_tableau_de_bord()
        self._rafraichir_email_statut()
        return True

    def _verifier_coherence(self, valeurs: dict[tuple[str, str], int]) -> None:
        """Contrôles que la CLI refuserait, faits avant d'écrire le fichier."""
        if not valeurs:
            return  # page Réglages jamais ouverte : rien à contrôler
        try:
            configuration = settings.lire_config(self.racine / "config.yaml")
        except settings.ErreurFichier:
            configuration = {}

        def lire(section: str, cle: str) -> int:
            if (section, cle) in valeurs:
                return valeurs[(section, cle)]
            return int((configuration.get(section) or {}).get(cle, 0) or 0)

        if valeurs[("youtube", "subscriber_min")] > valeurs[("youtube", "subscriber_max")]:
            raise ValueError("« Abonnés minimum » dépasse « Abonnés maximum ».")
        if valeurs[("youtube", "max_search_calls_per_run")] > lire("youtube", "max_search_calls_per_day"):
            raise ValueError(
                "« Recherches par exécution » dépasse le budget journalier "
                f"({lire('youtube', 'max_search_calls_per_day')})."
            )
        if valeurs[("youtube", "max_units_per_run")] > lire("youtube", "max_units_per_day"):
            raise ValueError(
                "« Budget d'unités par exécution » dépasse le budget journalier "
                f"({lire('youtube', 'max_units_per_day')})."
            )
        if valeurs[("metrics", "short_max_seconds")] < 1:
            raise ValueError("La durée maximale d'un Short doit être positive.")

    @staticmethod
    def _extrait_erreur(sortie: str) -> str:
        lignes = [ligne for ligne in sortie.splitlines() if "ERREUR" in ligne or "Erreur" in ligne]
        return "\n".join(lignes[-6:]) or sortie[-800:]

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _maj_onglets(self, cle: str) -> None:
        """Affiche les onglets du regroupement au-dessus de la page courante."""
        if self._barre_onglets is None:
            return
        hub = self._hub_de_page(cle)
        for enfant in self._barre_onglets.winfo_children():
            enfant.destroy()
        if not hub:
            self._barre_onglets.pack_forget()
            return
        titre, entrees = HUBS_ONGLETS[hub]
        ctk.CTkLabel(
            self._barre_onglets, text=titre, font=(self._police, 11, "bold"),
            text_color=DOUX, anchor="w",
        ).pack(side="left", padx=(0, 10))
        for page, libelle in entrees:
            actif = page == cle
            bouton = ctk.CTkButton(
                self._barre_onglets,
                text=libelle,
                image=icones.page(page, 16, TEXTE_SUR_ACCENT if actif else DOUX),
                compound="left",
                command=lambda p=page: self._afficher_page(p),
                height=30,
                corner_radius=6,
                fg_color=ACCENT if actif else "transparent",
                hover_color=SURFACE_HAUTE,
                text_color=TEXTE_SUR_ACCENT if actif else DOUX,
                border_width=0 if actif else 1,
                border_color=BORDURE,
                font=(self._police, 12, "bold" if actif else "normal"),
            )
            bouton.pack(side="left", padx=(0, 6))
        self._barre_onglets.pack(fill="x", padx=24, pady=(0, 8), before=self._conteneur)

    def _son_notification(self, succes: bool = True) -> None:
        """Signal sonore court, sans dépendance externe."""
        try:
            if sys.platform == "win32":
                import winsound

                winsound.MessageBeep(
                    winsound.MB_ICONASTERISK if succes else winsound.MB_ICONHAND
                )
            else:
                self.bell()
        except Exception:
            try:
                self.bell()
            except tk.TclError:
                pass

    def _fabriquer_page(self, cle: str) -> None:
        """Construit une page à sa première ouverture, puis la garde en mémoire."""
        if cle in self._pages:
            return
        constructeur = self._constructeurs.get(cle)
        if constructeur is None:
            return
        self._page_en_construction = cle
        try:
            constructeur()
        finally:
            self._page_en_construction = None
        page = self._pages.get(cle)
        if page is not None:
            page.grid(row=0, column=0, sticky="nsew")
            # Construire une page en arrière-plan ne doit pas la faire passer
            # devant la page courante. `_afficher_page` la remonte ensuite.
            page.lower()
            self._decouvrir_zones(cle)
            self._appliquer_disposition(cle)

    def _afficher_page(self, cle: str) -> None:
        if cle in HUBS_ONGLETS:
            entrees = HUBS_ONGLETS[cle][1]
            cle = self._derniere_page_hub.get(cle, entrees[0][0])
        hub = self._hub_de_page(cle)
        if hub:
            self._derniere_page_hub[hub] = cle
        self._fabriquer_page(cle)
        if cle not in self._pages:
            return
        self._ouvrir_groupe_page(cle)
        self._pages[cle].tkraise()
        self._page_courante = cle
        if hasattr(self, "_bouton_organiser"):
            self._bouton_organiser.pack(side="right", padx=(0, 10))
        if hasattr(self, "_icone_titre"):
            image = icones.page(cle, 30, _couleur(ACCENT))
            self._icone_titre.configure(image=image, text="" if image else "◆")
            self._icone_titre._outly_image = image
        if cle == "regarder":
            self._remplir_playlist()
        titre, sous_titre = PAGES[cle]
        self._titre_page.set(titre)
        self._sous_titre_page.set(sous_titre)
        for nom, bouton in self._boutons_nav.items():
            actif = nom == (self._hub_de_page(cle) or cle)
            bouton.configure(
                fg_color=SIDEBAR_ACTIF if actif else "transparent",
                text_color=SIDEBAR_TEXTE if actif else SIDEBAR_DOUX,
            )
        self._maj_nav_actifs()
        self._maj_onglets(cle)
        self._planifier_wraps()
        if cle == "notes":
            self._remplir_liste_notes()
        elif cle == "cerveau":
            self._remplir_cerveau()
        elif cle == "studio":
            self._remplir_studio()
        elif cle == "recherche":
            self._remplir_recherche()
        elif cle == "schemas":
            self._remplir_schemas()
        elif cle == "plan":
            self._remplir_plan()
        elif cle == "comparateur":
            self._maj_options_comparateur()
            self._remplir_comparateur()
        elif cle == "strategie":
            self._remplir_strategie()

    # ------------------------------------------------------------------
    # Organisation des blocs
    # ------------------------------------------------------------------
    def _decouvrir_zones(self, page: str) -> None:
        """Découvre les cartes d'une page qui n'a pas de disposition déclarée.

        Les pages historiques (accueil, audit) gardent leurs zones explicites.
        Pour les autres, chaque conteneur qui porte au moins deux cartes devient
        une zone organisable. Cela évite de réécrire chaque page pour un simple
        réordonnancement.
        """
        if page in disposition.PAGES:
            return
        page_frame = self._pages.get(page)
        if page_frame is None:
            return
        for cle_zone in [
            cle for cle in self._zones_blocs if cle[0] == page and cle[1].startswith("auto-")
        ]:
            self._zones_blocs.pop(cle_zone, None)
            self._zones_libelles.pop(cle_zone, None)
        parents: list[tuple[tk.Misc, list[tk.Misc]]] = []

        def contient_carte(widget: tk.Misc) -> bool:
            parent = widget.master
            while parent is not None:
                if getattr(parent, "_outly_carte", False):
                    return True
                parent = getattr(parent, "master", None)
            return False

        def visiter(widget: tk.Misc) -> None:
            try:
                enfants = list(widget.winfo_children())
            except tk.TclError:
                return
            cartes = [
                enfant
                for enfant in enfants
                if getattr(enfant, "_outly_carte", False)
                and enfant.winfo_manager() == "pack"
            ]
            if len(cartes) >= 1 and not contient_carte(widget):
                parents.append((widget, cartes))
            for enfant in enfants:
                visiter(enfant)

        visiter(page_frame)
        parents.sort(key=lambda item: (item[0].winfo_rooty(), item[0].winfo_rootx()))
        for index, (parent, cartes) in enumerate(parents, start=1):
            zone = f"auto-{index}"
            self._zones_blocs[(page, zone)] = parent
            self._zones_libelles[(page, zone)] = (
                "Sections" if len(parents) == 1 else f"Zone {index}"
            )
            vus: dict[str, int] = {}
            for position, carte in enumerate(cartes, start=1):
                identifiant = getattr(carte, "_outly_bloc", "") or f"bloc-{position}"
                base = identifiant
                numero = vus.get(base, 0) + 1
                vus[base] = numero
                if numero > 1:
                    identifiant = f"{base}-{numero}"
                carte._outly_bloc = identifiant
                if not getattr(carte, "_outly_titre", ""):
                    carte._outly_titre = identifiant

    def _organiser_page_courante(self) -> None:
        self._decouvrir_zones(self._page_courante)
        self._organiser_page(self._page_courante)

    def _conteneur_bloc(self, page: str, identifiant: str, defaut: str) -> tk.Misc:
        """Conteneur d'un bloc, selon la colonne enregistrée par l'utilisateur."""
        zone = disposition.zone(self.racine, page, identifiant, defaut)
        conteneur = self._zones_blocs.get((page, zone))
        if conteneur is None:
            conteneur = self._zones_blocs.get((page, defaut))
        return conteneur if conteneur is not None else self._conteneur

    def _cartes_zone(self, page: str, zone: str) -> list:
        """Cartes actuellement présentes dans une zone, dans leur ordre visuel."""
        conteneur = self._zones_blocs.get((page, zone))
        if conteneur is None:
            return []
        try:
            if not conteneur.winfo_exists():
                return []
            # `winfo_children()` suit l'ordre de création ; `pack_slaves()`
            # suit l'ordre visuel, celui que l'utilisateur voit et déplace.
            enfants = list(conteneur.pack_slaves())
            if not enfants:
                enfants = list(conteneur.winfo_children())
            return [enfant for enfant in enfants if getattr(enfant, "_outly_carte", False)]
        except tk.TclError:
            return []

    def _appliquer_disposition(self, page: str) -> None:
        """Réapplique l'ordre enregistré sans reconstruire les cartes."""
        for (nom_page, zone), _conteneur in list(self._zones_blocs.items()):
            if nom_page != page:
                continue
            cartes = self._cartes_zone(page, zone)
            if not cartes:
                continue
            identifiants: list[str] = []
            for carte in cartes:
                identifiant = getattr(carte, "_outly_bloc", "")
                if identifiant and identifiant not in identifiants:
                    identifiants.append(identifiant)
            ordre = disposition.ordre(self.racine, page, zone, identifiants)
            par_id: dict[str, object] = {}
            for carte in cartes:
                par_id.setdefault(getattr(carte, "_outly_bloc", ""), carte)
            for carte in cartes:
                try:
                    pady = carte.pack_info().get("pady", 0)
                except tk.TclError:
                    pady = 0
                carte._outly_pady = pady

            # Certaines zones mélangent des cartes et des widgets fixes (un
            # tableau extensible, une note de bas de page). Les replacer à la
            # fin avec ``pack`` les rend invisibles : le widget extensible
            # occupe alors toute la hauteur. On réordonne donc les cartes en
            # place, autour de leur premier voisin non déplaçable.
            tous = list(_conteneur.pack_slaves())
            non_cartes = [w for w in tous if not getattr(w, "_outly_carte", False)]
            if non_cartes:
                positions = [
                    index
                    for index, w in enumerate(tous)
                    if getattr(w, "_outly_carte", False)
                ]
                if positions:
                    apres = next(
                        (
                            w
                            for w in tous[positions[-1] + 1 :]
                            if not getattr(w, "_outly_carte", False)
                        ),
                        None,
                    )
                    if apres is not None:
                        for identifiant in ordre:
                            carte = par_id.get(identifiant)
                            if carte is not None:
                                carte.pack_configure(before=apres)
                    elif positions[0] > 0:
                        avant = tous[positions[0] - 1]
                        for identifiant in reversed(ordre):
                            carte = par_id.get(identifiant)
                            if carte is not None:
                                carte.pack_configure(after=avant)
                continue

            for carte in cartes:
                carte.pack_forget()
            for identifiant in ordre:
                carte = par_id.get(identifiant)
                if carte is not None:
                    carte.pack(fill="x", pady=getattr(carte, "_outly_pady", 0))

    def _reconstruire_page(self, page: str) -> bool:
        """Reconstruit une page après un déplacement entre deux colonnes."""
        if self._tache is not None:
            self._message.set("Attendez la fin de l'exécution pour déplacer un bloc.")
            return False
        ancienne = self._pages.pop(page, None)
        if ancienne is not None:
            try:
                ancienne.destroy()
            except tk.TclError:
                pass
        constructeur = self._constructeurs.get(page)
        if constructeur is None:
            return False
        constructeur()
        reconstruite = self._pages.get(page)
        if reconstruite is not None:
            reconstruite.grid(row=0, column=0, sticky="nsew")
        self._decouvrir_zones(page)
        self._appliquer_disposition(page)
        self._afficher_page(page)
        self._rafraichir_tout()
        return True

    def _organiser_page(self, page: str) -> None:
        """Fenêtre de personnalisation : ordre des blocs, colonne, réinitialisation."""
        zones = [
            zone
            for (nom_page, zone) in self._zones_blocs
            if nom_page == page and self._cartes_zone(page, zone)
        ]
        if not zones:
            # Les pages éditeurs (Notes, Plan, Mon contenu) n'ont pas de cartes
            # déplaçables. Le bouton reste présent partout : on explique la
            # situation et on propose une réinitialisation globale.
            fenetre = ctk.CTkToplevel(self)
            fenetre.title("Organiser cette page")
            fenetre.geometry("460x250")
            fenetre.transient(self)
            corps = ctk.CTkFrame(fenetre, fg_color=FOND, corner_radius=0)
            corps.pack(fill="both", expand=True, padx=18, pady=18)
            ctk.CTkLabel(
                corps,
                text="Cette page est un éditeur unique",
                font=(self._police, 17, "bold"),
                text_color=TEXTE,
                anchor="w",
            ).pack(fill="x")
            ctk.CTkLabel(
                corps,
                text="Ses panneaux ne sont pas des cartes déplaçables. Vous pouvez "
                "réinitialiser les dispositions enregistrées pour repartir de l'ordre "
                "par défaut.",
                font=(self._police, 12),
                text_color=DOUX,
                justify="left",
                wraplength=410,
                anchor="w",
            ).pack(fill="x", pady=(8, 14))

            def reinitialiser() -> None:
                try:
                    disposition.reinitialiser(self.racine)
                except settings.ErreurFichier as probleme:
                    self._message.set(f"Réinitialisation impossible : {probleme}")
                fenetre.destroy()
                self._message.set("Dispositions réinitialisées.")

            self._bouton(corps, "Réinitialiser les dispositions", reinitialiser).pack(
                fill="x"
            )
            self._bouton(corps, "Fermer", fenetre.destroy).pack(fill="x", pady=(8, 0))
            return

        fenetre = ctk.CTkToplevel(self)
        fenetre.title("Organiser les blocs")
        fenetre.geometry("560x540")
        fenetre.minsize(460, 360)
        fenetre.transient(self)
        corps = ctk.CTkScrollableFrame(fenetre, fg_color=FOND, corner_radius=0)
        corps.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(
            corps,
            text="Organiser les blocs",
            font=(self._police, 19, "bold"),
            text_color=TEXTE,
            anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            corps,
            text="Les changements sont enregistrés tout de suite. Utilisez les flèches "
            "pour l'ordre, et ↔ pour changer de colonne sur l'accueil.",
            font=(self._police, 12),
            text_color=DOUX_FONCE,
            anchor="w",
            justify="left",
            wraplength=470,
        ).pack(fill="x", pady=(2, 12))
        liste = ctk.CTkFrame(corps, fg_color="transparent")
        liste.pack(fill="x")

        def redessiner() -> None:
            for enfant in liste.winfo_children():
                enfant.destroy()
            for zone in zones:
                cartes = self._cartes_zone(page, zone)
                identifiants = [getattr(c, "_outly_bloc", "") for c in cartes]
                ordre = disposition.ordre(self.racine, page, zone, identifiants)
                par_id = {getattr(c, "_outly_bloc", ""): c for c in cartes}
                ctk.CTkLabel(
                    liste,
                    text=(
                        disposition.ZONES.get((page, zone))
                        or self._zones_libelles.get((page, zone), zone)
                    ).upper(),
                    font=(self._police, 10, "bold"),
                    text_color=DOUX,
                    anchor="w",
                ).pack(fill="x", pady=(10, 4))
                for position, identifiant in enumerate(ordre):
                    carte = par_id.get(identifiant)
                    if carte is None:
                        continue
                    ligne = ctk.CTkFrame(liste, fg_color=SURFACE, corner_radius=6)
                    ligne.pack(fill="x", pady=2)
                    ctk.CTkLabel(
                        ligne,
                        text=getattr(carte, "_outly_titre", identifiant),
                        font=(self._police, 13),
                        text_color=TEXTE,
                        anchor="w",
                        justify="left",
                        wraplength=260,
                    ).pack(side="left", fill="x", expand=True, padx=10, pady=8)
                    actions = ctk.CTkFrame(ligne, fg_color="transparent")
                    actions.pack(side="right", padx=(4, 8))
                    if len(zones) > 1:
                        ctk.CTkButton(
                            actions,
                            text="↔",
                            width=30,
                            height=26,
                            fg_color="transparent",
                            hover_color=SURFACE_HAUTE,
                            border_width=1,
                            border_color=BORDURE,
                            text_color=DOUX,
                            command=lambda z=zone, i=identifiant: changer_zone(z, i),
                        ).pack(side="left", padx=2)
                    ctk.CTkButton(
                        actions,
                        text="↑",
                        width=30,
                        height=26,
                        fg_color="transparent",
                        hover_color=SURFACE_HAUTE,
                        border_width=1,
                        border_color=BORDURE,
                        text_color=TEXTE,
                        state="normal" if position > 0 else "disabled",
                        command=lambda z=zone, i=identifiant: deplacer(z, i, -1),
                    ).pack(side="left", padx=2)
                    ctk.CTkButton(
                        actions,
                        text="↓",
                        width=30,
                        height=26,
                        fg_color="transparent",
                        hover_color=SURFACE_HAUTE,
                        border_width=1,
                        border_color=BORDURE,
                        text_color=TEXTE,
                        state="normal" if position < len(ordre) - 1 else "disabled",
                        command=lambda z=zone, i=identifiant: deplacer(z, i, 1),
                    ).pack(side="left", padx=2)

        def deplacer(zone: str, identifiant: str, delta: int) -> None:
            cartes = self._cartes_zone(page, zone)
            identifiants = [getattr(c, "_outly_bloc", "") for c in cartes]
            ordre = disposition.ordre(self.racine, page, zone, identifiants)
            if identifiant not in ordre:
                return
            index = ordre.index(identifiant)
            cible = index + delta
            if not 0 <= cible < len(ordre):
                return
            ordre[index], ordre[cible] = ordre[cible], ordre[index]
            try:
                disposition.enregistrer_zones(self.racine, page, {zone: ordre})
            except settings.ErreurFichier as probleme:
                self._message.set(f"Organisation non enregistrée : {probleme}")
                return
            self._appliquer_disposition(page)
            redessiner()

        def changer_zone(zone: str, identifiant: str) -> None:
            autres = [autre for autre in zones if autre != zone]
            if not autres:
                return
            cible = autres[0]
            ordre_source = disposition.ordre(
                self.racine, page, zone,
                [getattr(c, "_outly_bloc", "") for c in self._cartes_zone(page, zone)],
            )
            ordre_cible = disposition.ordre(
                self.racine, page, cible,
                [getattr(c, "_outly_bloc", "") for c in self._cartes_zone(page, cible)],
            )
            if identifiant in ordre_source:
                ordre_source.remove(identifiant)
            if identifiant not in ordre_cible:
                ordre_cible.append(identifiant)
            try:
                disposition.enregistrer_zones(
                    self.racine, page, {zone: ordre_source, cible: ordre_cible}
                )
            except settings.ErreurFichier as probleme:
                self._message.set(f"Déplacement non enregistré : {probleme}")
                return
            fenetre.destroy()
            if self._reconstruire_page(page):
                self._organiser_page(page)

        def reinitialiser() -> None:
            try:
                disposition.reinitialiser(self.racine, page)
            except settings.ErreurFichier as probleme:
                self._message.set(f"Réinitialisation impossible : {probleme}")
                return
            fenetre.destroy()
            if self._reconstruire_page(page):
                self._message.set("Disposition de la page réinitialisée.")

        redessiner()
        pied = ctk.CTkFrame(corps, fg_color="transparent")
        pied.pack(fill="x", pady=(16, 0))
        self._bouton(pied, "Réinitialiser cette page", reinitialiser, width=180).pack(side="left")
        self._bouton_accent(pied, "Fermer", fenetre.destroy, width=110).pack(side="right")
        try:
            fenetre.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------
    def _tester_connexions(self) -> None:
        if not self._sauvegarder():
            return
        self._afficher_page("lancer")
        self._lancer(arguments_doctor(True), "Diagnostic")

    def _lancer_depuis_accueil(self) -> None:
        instantane = data.instantane(self.racine)
        if not instantane.pret:
            messagebox.showwarning(
                "Informations manquantes",
                "Complétez d'abord :\n\n• " + "\n• ".join(instantane.problemes),
                parent=self,
            )
            if instantane.erreur_config or not instantane.cle_youtube or not instantane.llm_complet:
                self._afficher_page("cles")
            else:
                self._afficher_page("contenu")
            return
        if not messagebox.askyesno(
            "Lancer la veille",
            "Lancer une exécution complète ?\n\n"
            "Coût indicatif : environ 1 unité par chaîne enrichie et 1 appel par recherche, "
            "moins ce que le cache local couvre déjà.\n\n"
            "Le rapport sera écrit dans out/report.md.",
            parent=self,
        ):
            return
        self._afficher_page("lancer")
        self._demarrer_run()

    def _demarrer_run(self) -> None:
        if self._tache is not None:
            messagebox.showinfo("Exécution en cours", "Attendez la fin ou arrêtez l'exécution.", parent=self)
            return
        if not self._sauvegarder():
            return
        # F5 peut être utilisé sans avoir jamais ouvert la page Lancer.
        if not hasattr(self, "_option_recherches"):
            self._fabriquer_page("lancer")
        try:
            recherches = int((self._option_recherches.get().strip() or "20"))
        except ValueError:
            messagebox.showerror(
                "Valeur invalide", "« Recherches max » doit être un nombre entier.", parent=self
            )
            return
        if recherches < 0:
            messagebox.showerror(
                "Valeur invalide", "« Recherches max » ne peut pas être négatif.", parent=self
            )
            return
        arguments = arguments_run(
            max_recherches=recherches,
            avec_outliers=self._option_outliers.get(),
            transcriptions=self._option_transcriptions.get(),
        )
        self._lancer(arguments, "Exécution complète")

    def _maj_estimation_execution(self) -> None:
        """Met à jour la fourchette affichée quand une option change."""
        if not hasattr(self, "_estimation_execution"):
            return
        try:
            recherches = int(self._option_recherches.get())
        except (TypeError, ValueError):
            self._estimation_execution.set("Durée estimée indisponible")
            return
        self._estimation_execution.set(
            estimation_execution(
                recherches,
                avec_outliers=self._option_outliers.get(),
                transcriptions=self._option_transcriptions.get(),
            )
        )

    def _lancer_etape(self, etape: str) -> None:
        if self._tache is not None:
            return
        if not self._sauvegarder():
            return
        recherches = None
        if etape == "discover":
            try:
                recherches = int(self._option_recherches.get().strip() or "0")
            except ValueError:
                recherches = None
        arguments = arguments_etape(
            etape, max_recherches=recherches, transcriptions=self._option_transcriptions.get()
        )
        self._afficher_page("lancer")
        self._lancer(arguments, etape)

    def _lancer(self, arguments: list[str], libelle: str) -> None:
        if self._tache is not None:
            return
        # Le journal et le bouton d'arrêt vivent sur la page Lancer : elle est
        # construite ici si l'utilisateur lance une étape depuis une autre page.
        self._fabriquer_page("lancer")
        instantane = data.instantane(self.racine)
        self._quota_avant = (instantane.unites_jour, instantane.recherches_jour)

        self._vider_journal()
        self._ecrire_journal(f"=== {libelle} · {datetime.now().strftime('%H:%M:%S')} ===", "titre")
        self._ecrire_journal("$ python -m outly " + " ".join(arguments), "discret")
        self._ecrire_journal("")

        self._basculer_execution(True)
        self._etat_execution.set(f"en cours · {libelle}")
        self._progression.start()
        if hasattr(self, "_spinner_entete"):
            self._spinner_entete.pack(side="right", padx=(0, 8))
            self._spinner_entete.demarrer()
        self._resume_execution.configure(text="")

        self._libelle_tache = libelle
        self._tache = Tache(self.racine, arguments)
        try:
            self._tache.demarrer()
        except OSError as probleme:
            self._tache = None
            self._basculer_execution(False)
            self._progression.stop()
            self._etat_execution.set("échec du démarrage")
            self._ecrire_journal(f"! Démarrage impossible : {probleme}", "erreur")
            messagebox.showerror("Démarrage impossible", str(probleme), parent=self)
            return
        self.after(120, self._pomper)

    def _pomper(self) -> None:
        if self._tache is None:
            return
        while True:
            try:
                element = self._tache.file.get_nowait()
            except queue.Empty:
                break
            if isinstance(element, Fin):
                self._terminer(element)
                return
            self._ecrire_journal(str(element))
        if self._tache is not None:
            self.after(120, self._pomper)

    def _terminer(self, fin: Fin) -> None:
        if self._libelle_tache == "Recherche":
            self._son_notification(fin.code == 0 and not fin.annule)
        self._tache = None
        self._progression.stop()
        self._progression.set(0)
        if hasattr(self, "_spinner_entete"):
            self._spinner_entete.arreter()
            self._spinner_entete.pack_forget()
        self._basculer_execution(False)
        self._bouton_arreter.configure(state="disabled")

        message = CODES.get(fin.code, f"Code de sortie {fin.code}.")
        if fin.annule:
            message = "Exécution arrêtée par vous."
        self._etat_execution.set(
            "terminé"
            if fin.code == 0 and not fin.annule
            else ("arrêté" if fin.annule else "terminé avec alerte")
        )
        self._ecrire_journal("")
        etiquette = "ok" if fin.code == 0 and not fin.annule else "alerte"
        self._ecrire_journal(f"=== {message} ===", etiquette)

        instantane = data.instantane(self.racine)
        unites = max(0, instantane.unites_jour - self._quota_avant[0])
        recherches = max(0, instantane.recherches_jour - self._quota_avant[1])
        resume = (
            f"Quota consommé : {unites} unité(s) et {recherches} recherche(s). "
            f"Aujourd'hui : {instantane.unites_jour}/{instantane.budget_unites} unités, "
            f"{instantane.recherches_jour}/{instantane.budget_recherches} recherches."
        )
        self._ecrire_journal(resume, "discret")
        self._resume_execution.configure(text=resume)

        self._rafraichir_tout()
        if self._instantane and self._instantane.nb_chaines:
            self._message.set("Terminé. Les résultats sont à jour dans la page Chaînes.")
        else:
            self._message.set(message)

        if self._libelle_tache == "Hooks" and fin.code != 0 and not fin.annule:
            self._hooks_etat.set(
                "Aucune variante générée — ouvrez le journal (page Lancer) pour le détail."
            )
            messagebox.showwarning(
                "Hooks",
                "La génération n'a produit aucune variante.\n\n"
                "Ouvrez la page Lancer pour lire le journal : sous-titres désactivés, "
                "aucune transcription disponible ou fournisseur LLM indisponible.",
                parent=self,
            )
        if self._libelle_tache == "Cerveau IA" and fin.code != 0 and not fin.annule:
            self._cerveau_etat.set(
                "Analyse interrompue — ouvrez la page Lancer pour lire le journal."
            )
            messagebox.showwarning(
                "Cerveau IA",
                "L'analyse n'a produit aucun angle.\n\n"
                "Vérifiez le journal : notes absentes, fournisseur LLM indisponible "
                "ou réponse invalide.",
                parent=self,
            )
        if self._libelle_tache == "Idées" and fin.code != 0 and not fin.annule:
            self._idees_etat.set(
                "Recherche interrompue — ouvrez la page Lancer pour lire le journal."
            )
            messagebox.showwarning(
                "Idées de vidéos",
                "La recherche n'a produit aucune idée complète.\n\n"
                "Vérifiez le journal : notes absentes, fournisseur LLM indisponible, "
                "ou réponses avec moins de trois angles distincts.",
                parent=self,
            )
        if self._libelle_tache == "Stratégie":
            self._remplir_strategie()
            if fin.code != 0 and not fin.annule and hasattr(self, "_strategie_etat"):
                self._strategie_etat.set(
                    "Diagnostic interrompu — ouvrez la page Lancer pour lire le journal."
                )
        if self._libelle_tache == "Production":
            self._maj_production_resume(self._plan_courant_id)
            if fin.code == 0 and not fin.annule:
                self._afficher_production(self._plan_courant_id)
        if self._libelle_tache == "Recherche" and fin.code != 0 and not fin.annule:
            if hasattr(self, "_recherche_etat"):
                self._recherche_etat.set(
                    "Recherche interrompue — ouvrez la page Lancer pour lire le journal."
                )
            messagebox.showwarning(
                "Recherche documentaire",
                "Le dossier n'a pas pu être produit.\n\n"
                "Vérifiez le journal : Wikipédia injoignable, clé Exa absente ou refusée, "
                "fournisseur LLM indisponible, ou réponse invalide.",
                parent=self,
            )

    def _annuler(self) -> None:
        if self._tache is None:
            return
        if not messagebox.askyesno("Arrêter", "Arrêter l'exécution en cours ?", parent=self):
            return
        self._tache.annuler()
        self._etat_execution.set("arrêt demandé…")
        self._ecrire_journal("! Arrêt demandé.", "alerte")

    def _basculer_execution(self, actif: bool) -> None:
        for bouton in self._boutons_execution:
            bouton.configure(state="disabled" if actif else "normal")
        self._bouton_arreter.configure(state="normal" if actif else "disabled")

    # --- journal ------------------------------------------------------
    def _vider_journal(self) -> None:
        self._journal.configure(state="normal")
        self._journal.delete("1.0", "end")
        self._journal.configure(state="disabled")

    def _ecrire_journal(self, ligne: str, etiquette: str | None = None) -> None:
        if etiquette is None:
            if ligne.startswith("!"):
                etiquette = "alerte"
            elif ligne.startswith("Erreur") or "Traceback" in ligne:
                etiquette = "erreur"
            elif ligne and set(ligne.strip()) == {"="}:
                etiquette = "titre"
            elif re.match(r"^\d(/\d)?\s*·", ligne):
                etiquette = "titre"
            elif ligne.startswith("  "):
                etiquette = "discret"
        self._journal.configure(state="normal")
        en_bas = self._journal.yview()[1] > 0.999
        self._journal.insert("end", ligne + "\n", etiquette or ())
        if int(self._journal.index("end-1c").split(".")[0]) > 5000:
            self._journal.delete("1.0", "1001.0")
        self._journal.configure(state="disabled")
        if en_bas:
            self._journal.see("end")

    # ------------------------------------------------------------------
    # Rafraîchissement
    # ------------------------------------------------------------------
    def _rafraichir_tout(self) -> None:
        self._rafraichir_tableau_de_bord()
        self._rafraichir_email_statut()
        self._lignes = data.resultats(self.racine)
        self._index = {ligne.channel_id: ligne for ligne in self._lignes}
        self._remplir_arbre()
        self._remplir_playlist()
        self._remplir_hooks()
        self._remplir_liste_notes()
        self._remplir_cerveau()
        self._remplir_idees()
        self._remplir_recherche()
        self._remplir_audit()
        self._remplir_studio()
        self._remplir_plan()
        self._remplir_strategie()
        self._maj_options_comparateur()
        self._remplir_comparateur()

    def _rafraichir_tableau_de_bord(self) -> None:
        self._fabriquer_page("accueil")
        instantane = data.instantane(self.racine)
        self._instantane = instantane

        # Pastille d'état.
        if instantane.erreur_config:
            texte, couleur, fond = "Configuration invalide", ERREUR, ERREUR_DOUX
        elif instantane.problemes:
            texte, couleur, fond = "À compléter", ALERTE, ALERTE_DOUX
        else:
            texte, couleur, fond = "Prêt", OK, OK_DOUX
        self._pastille.configure(text=f"  {texte}  ", text_color=couleur, fg_color=fond)
        self._entete_quota.configure(
            text=f"{_nombre(instantane.unites_jour)} u · "
            f"{_nombre(instantane.recherches_jour)} rech. aujourd'hui"
        )

        # Studio YouTube : résumé du dernier instantané, sans appel réseau.
        if hasattr(self, "_texte_studio_accueil"):
            try:
                studio_donnees = studio.lire_cache(self.racine)
            except Exception:
                studio_donnees = None
            if studio_donnees:
                self._texte_studio_accueil.set(
                    f"{_nombre(studio_donnees.get('vues'))} vues · "
                    f"{_nombre(studio_donnees.get('minutes'))} min vues · "
                    f"{len(studio_donnees.get('videos') or [])} vidéo(s)\n"
                    f"Période {studio_donnees.get('debut', '')} → "
                    f"{studio_donnees.get('fin', '')}"
                )
            else:
                self._texte_studio_accueil.set(
                    "Aucune donnée pour l'instant. Connectez votre chaîne pour "
                    "voir les vues, la rétention et l'audience."
                )

        # Clés.
        youtube = "YouTube : clé présente" if instantane.cle_youtube else "YouTube : clé manquante"
        if instantane.llm_complet:
            hote = urlparse(instantane.llm_base_url).netloc or instantane.llm_base_url
            llm = f"LLM : {instantane.llm_modele} via {hote}"
        else:
            llm = "LLM : fournisseur incomplet"
        self._maj_etat("cles", instantane.cle_youtube and instantane.llm_complet, f"{youtube} · {llm}")
        if instantane.erreur_config:
            self._maj_etat("cles", False, f"config.yaml : {instantane.erreur_config.splitlines()[0]}")

        # Ma chaîne de départ.
        if instantane.ma_chaine:
            portee = "France + international" if instantane.international else "France seule"
            self._maj_etat(
                "ma_chaine",
                True,
                f"{instantane.ma_chaine} · {portee}",
                couleur=ACCENT_TEXTE,
            )
        else:
            self._maj_etat(
                "ma_chaine", False, "Aucune chaîne de départ (optionnel).", couleur=DOUX_FONCE
            )

        # Positionnement.
        self._maj_etat(
            "positionnement",
            instantane.positionnement_caracteres >= 200,
            f"{_nombre(instantane.positionnement_caracteres)} caractères "
            + ("(200 minimum)" if instantane.positionnement_caracteres < 200 else "· complet"),
        )

        # Concurrence.
        self._maj_etat(
            "concurrence",
            bool(instantane.seeds or instantane.mots_cles),
            f"{instantane.seeds} seed(s) · {instantane.mots_cles} mot(s)-clé(s)",
        )

        # Base.
        self._maj_etat(
            "base",
            instantane.nb_chaines > 0,
            f"{instantane.nb_chaines} chaîne(s) évaluée(s) · {instantane.nb_retenues} retenue(s) "
            f"· {instantane.nb_outliers} outlier(s)",
        )

        # Quota.
        self._barre_unites.definir(instantane.unites_jour, instantane.budget_unites)
        self._texte_unites.configure(
            text=f"{_nombre(instantane.unites_jour)} / {_nombre(instantane.budget_unites)} unités"
        )
        self._barre_recherches.definir(instantane.recherches_jour, instantane.budget_recherches)
        self._texte_recherches.configure(
            text=f"{_nombre(instantane.recherches_jour)} / "
            f"{_nombre(instantane.budget_recherches)} appels"
        )
        self._mini_unites.configure(
            text=f"{_nombre(instantane.unites_jour)} / {_nombre(instantane.budget_unites)} unités"
        )
        self._mini_barre_unites.definir(instantane.unites_jour, instantane.budget_unites)
        self._mini_recherches.configure(
            text=f"{_nombre(instantane.recherches_jour)} / "
            f"{_nombre(instantane.budget_recherches)} recherches"
        )
        self._mini_barre_recherches.definir(
            instantane.recherches_jour, instantane.budget_recherches
        )

        # Dernière exécution.
        if instantane.derniere_execution is None:
            self._texte_execution.configure(text="Aucune exécution pour l'instant.")
        else:
            execution = instantane.derniere_execution
            self._texte_execution.configure(
                text=f"{execution.mode} · {execution.libelle_statut}\n"
                f"{_date_lisible(execution.debut)} · {_nombre(execution.unites)} unités · "
                f"{_nombre(execution.recherches)} recherches · "
                f"{_nombre(execution.appels_llm)} appels LLM"
            )

        # Rapport.
        if instantane.rapport:
            ecrit = instantane.rapport_ecrit_le
            self._texte_rapport.configure(
                text="À jour"
                + (f" · {ecrit.strftime('%d/%m/%Y %H:%M')}" if ecrit else "")
            )
            self._bouton_rapport.configure(state="normal")
        else:
            self._texte_rapport.configure(text="Pas encore généré.")
            self._bouton_rapport.configure(state="disabled")

        if not self._options_initialisees and hasattr(self, "_option_recherches"):
            self._option_recherches.set(str(instantane.max_recherches_run))
            self._options_initialisees = True

    def _maj_etat(self, cle: str, bon: bool, detail: str, couleur: str | None = None) -> None:
        if cle not in self._lignes_etat:
            return
        point, etiquette = self._lignes_etat[cle]
        point.configure(text_color=couleur or (OK if bon else ALERTE))
        etiquette.configure(text=detail)

    def _chemin_rapport(self) -> Path:
        if self._instantane and self._instantane.rapport:
            return self._instantane.rapport
        return self.racine / "out" / "report.md"

    # ------------------------------------------------------------------
    # Résultats
    # ------------------------------------------------------------------
    def _remplir_arbre(self) -> None:
        self._filtre_timer = None
        if not hasattr(self, "_arbre"):
            return
        filtre = self._filtre.get().strip().lower()
        choix_statut = self._filtre_statut.get() if hasattr(self, "_filtre_statut") else "Toutes"
        ancienne_selection = self._arbre.selection()
        self._arbre.delete(*self._arbre.get_children())
        self._index.clear()
        lignes = [
            ligne
            for ligne in self._lignes
            if (
                choix_statut == "Toutes"
                or (ligne.statut == "retenue") == (choix_statut == "Retenues")
            )
            and (
                not filtre
                or filtre in (ligne.titre or "").lower()
                or filtre in (ligne.handle or "").lower()
                or filtre in (ligne.format_detecte or "").lower()
                or filtre in (ligne.langue or "").lower()
            )
        ]
        lignes = self._trier_lignes(lignes)
        for ligne in lignes:
            metriques = ligne.metriques
            valeurs = (
                ligne.score_affiche if ligne.score_affiche is not None else "—",
                ligne.titre,
                _nombre(ligne.abonnes),
                _nombre(metriques.mediane_vues_longues if metriques else None),
                ("×" + _nombre(metriques.meilleur_outlier, 1))
                if metriques and metriques.meilleur_outlier
                else "—",
                "● Retenue" if ligne.statut == "retenue" else "○ Écartée",
            )
            self._arbre.insert(
                "",
                "end",
                iid=ligne.channel_id,
                values=valeurs,
                tags=("retenue" if ligne.statut == "retenue" else "ecartee",),
            )
            self._index[ligne.channel_id] = ligne

        retenues = sum(1 for ligne in lignes if ligne.statut == "retenue")
        if self._lignes:
            ecartees = len(lignes) - retenues
            self._compte_resultats.set(
                f"{len(lignes)} affichée(s) · {retenues} retenue(s) · {ecartees} écartée(s)"
            )
        else:
            self._compte_resultats.set("")

        if self._lignes:
            if self._vide_visible:
                self._vide.place_forget()
                self._vide_visible = False
        else:
            if not self._vide_visible:
                self._vide.place(relx=0.5, rely=0.5, anchor="center")
                self._vide_visible = True

        if not lignes:
            self._detail_initial(
                "Aucune chaîne ne correspond au filtre." if filtre else None
            )
        elif ancienne_selection and self._arbre.exists(ancienne_selection[0]):
            self._arbre.selection_set(ancienne_selection[0])
        else:
            self._detail_initial()

    def _vider_detail(self) -> None:
        for enfant in self._detail_corps.winfo_children():
            enfant.destroy()

    def _detail_label(
        self,
        texte: str,
        taille: int = 11,
        gras: bool = False,
        couleur: str = TEXTE,
        wrap: int = 356,
        pady=(0, 4),
    ) -> ctk.CTkLabel:
        label = ctk.CTkLabel(
            self._detail_corps,
            text=texte,
            font=(self._police, taille + 2, "bold" if gras else "normal"),
            text_color=couleur,
            wraplength=wrap,
            justify="left",
            anchor="w",
        )
        label.pack(fill="x", pady=pady, padx=(0, 4))
        return label

    def _detail_initial(self, message: str | None = None) -> None:
        self._vider_detail()
        self._detail_label(
            "Aucune chaîne sélectionnée",
            taille=13,
            gras=True,
            couleur=DOUX,
            pady=(6, 6),
        )
        self._detail_label(
            message or "Sélectionnez une chaîne pour voir sa description, son score, "
            "ses métriques et ses vidéos outliers.",
            taille=11,
            couleur=DOUX_FONCE,
            pady=(0, 0),
        )
        for bouton in (
            self._bouton_chaine,
            self._bouton_apercu,
            self._bouton_enregistrer,
            self._bouton_copier,
        ):
            bouton.configure(state="disabled")

    def _ligne_selectionnee(self) -> report.LigneRapport | None:
        selection = self._arbre.selection()
        if not selection:
            return None
        return self._index.get(selection[0])

    def _poser_vignette(
        self, parent: tk.Misc, video_id: str | None, largeur: int, hauteur: int
    ) -> ctk.CTkLabel:
        """Place une miniature, chargée en arrière-plan (jamais de gel de l'UI)."""
        label = ctk.CTkLabel(
            parent,
            text="",
            width=largeur,
            height=hauteur,
            corner_radius=7,
            fg_color=SURFACE_HAUTE,
            text_color=DOUX_FONCE,
            font=(self._police, 12),
        )
        try:
            label.pack_propagate(False)
        except tk.TclError:
            pass

        def poser(image) -> None:
            try:
                if not label.winfo_exists():
                    return
                if image is None:
                    label.configure(text="▶")
                    return
                photo = ctk.CTkImage(
                    light_image=image, dark_image=image, size=(largeur, hauteur)
                )
                self._vignettes_images[f"{video_id}-{largeur}x{hauteur}"] = photo
                label.configure(image=photo, text="")
            except tk.TclError:
                pass

        self._vignettes.demander(str(video_id or ""), poser)
        return label

    def _afficher_detail(self) -> None:
        ligne = self._ligne_selectionnee()
        if ligne is None:
            return
        for bouton in (
            self._bouton_chaine,
            self._bouton_apercu,
            self._bouton_enregistrer,
            self._bouton_copier,
        ):
            bouton.configure(state="normal")
        self._vider_detail()

        # Identité.
        self._detail_label(ligne.titre, taille=15, gras=True, pady=(2, 2), wrap=360)
        identite = f"@{ligne.handle.lstrip('@')}" if ligne.handle else ligne.channel_id
        self._detail_label(
            f"{identite} · {_nombre(ligne.abonnes)} abonnés · {_nombre(ligne.nb_videos)} vidéos",
            taille=10,
            couleur=DOUX_FONCE,
            pady=(0, 8),
        )

        # Repères factuels, tous déjà en base (aucun appel réseau).
        reperes = []
        if ligne.pays:
            reperes.append(f"Pays : {ligne.pays}")
        if ligne.vues_totales is not None:
            reperes.append(f"Vues cumulées : {_nombre(ligne.vues_totales)}")
        if ligne.cree_le:
            reperes.append(f"Chaîne créée le : {ligne.cree_le[:10]}")
        if ligne.est_seed:
            reperes.append("Chaîne de départ (seed)")
        if reperes:
            self._detail_label(
                " · ".join(reperes), taille=9, couleur=DOUX_FONCE, pady=(0, 8), wrap=360
            )

        # Score (LLM, sinon proximité locale calculée hors ligne).
        score = ligne.score_affiche
        if score is not None:
            badge = ctk.CTkFrame(
                self._detail_corps, fg_color=ACCENT_DOUX, corner_radius=10
            )
            badge.pack(fill="x", pady=(0, 8))
            rang = ctk.CTkFrame(badge, fg_color="transparent")
            rang.pack(fill="x", padx=12, pady=9)
            ctk.CTkLabel(
                rang,
                text=str(score),
                font=(self._police, 30, "bold"),
                text_color=ACCENT_TEXTE,
            ).pack(side="left")
            ctk.CTkLabel(
                rang, text="/100", font=(self._police, 13), text_color=DOUX
            ).pack(side="left", padx=(2, 10), pady=(8, 0))
            ctk.CTkLabel(
                rang,
                text=(
                    "score de proximité"
                    if ligne.score is not None
                    else "proximité locale (sans LLM)"
                ),
                font=(self._police, 12),
                text_color=DOUX,
            ).pack(side="left", pady=(8, 0))
            if ligne.score is not None:
                self._detail_label(
                    f"Recouvrement {ligne.recouvrement or '—'} · Format "
                    f"{ligne.format_detecte or '—'} · Langue {ligne.langue or '—'}",
                    taille=10,
                    couleur=DOUX,
                    pady=(0, 6),
                )
            else:
                self._detail_label(
                    "Calculé hors ligne à partir de ma chaîne et du positionnement.",
                    taille=10,
                    couleur=DOUX,
                    pady=(0, 6),
                )
                if ligne.termes_communs:
                    self._detail_label(
                        "Thèmes partagés : " + ", ".join(ligne.termes_communs[:6]),
                        taille=10,
                        couleur=DOUX_FONCE,
                        pady=(0, 4),
                    )
            if ligne.justification:
                self._detail_label(ligne.justification, taille=11, pady=(0, 4), wrap=360)
        else:
            self._detail_label(
                "Score de proximité : non évalué par le LLM",
                taille=11,
                gras=True,
                couleur=DOUX,
                pady=(0, 4),
            )
            if ligne.score_heuristique is not None:
                self._detail_label(
                    f"Recouvrement lexical estimé : {ligne.score_heuristique:.2f}",
                    taille=10,
                    couleur=DOUX_FONCE,
                )

        # Description de la chaîne : c'est elle qui manquait pour se repérer.
        self._titre_section(self._detail_corps, "Description", pady=(10, 4))
        description = (ligne.description or "").strip()
        if description:
            extrait = description[:1600] + ("…" if len(description) > 1600 else "")
            self._detail_label(extrait, taille=10, couleur=DOUX, pady=(0, 4), wrap=360)
        else:
            self._detail_label(
                "Aucune description enregistrée pour cette chaîne.",
                taille=10,
                couleur=DOUX_FONCE,
                pady=(0, 4),
            )

        # Mots-clés déclarés par la chaîne (facultatif).
        mots_cles = " ".join((ligne.mots_cles or "").replace('"', " ").split())
        if mots_cles:
            self._titre_section(self._detail_corps, "Mots-clés déclarés", pady=(10, 4))
            self._detail_label(mots_cles[:320], taille=9, couleur=DOUX_FONCE, wrap=360)

        # Risques.
        if ligne.risques:
            self._titre_section(self._detail_corps, "Risques et doutes", pady=(10, 4))
            for risque in ligne.risques:
                self._detail_label(f"• {risque}", taille=10, couleur=ALERTE, pady=(0, 3))

        # Métriques.
        metriques = ligne.metriques
        if metriques:
            self._titre_section(self._detail_corps, "Métriques", pady=(10, 6))
            stats = ctk.CTkFrame(self._detail_corps, fg_color="transparent")
            stats.pack(fill="x")
            stats.grid_columnconfigure(0, weight=1)
            stats.grid_columnconfigure(1, weight=1)
            self._stat(stats, 0, 0, "Médiane longues", _nombre(metriques.mediane_vues_longues))
            self._stat(stats, 0, 1, "Médiane Shorts", _nombre(metriques.mediane_vues_short))
            self._stat(
                stats,
                1,
                0,
                "Cadence",
                f"{_nombre(metriques.cadence_semaine, 1)}/sem",
            )
            self._stat(
                stats,
                1,
                1,
                "Dernière vidéo",
                f"il y a {_nombre(metriques.jours_depuis_publication)} j",
            )
            self._detail_label(
                f"{metriques.n_shorts} Shorts · {metriques.n_longues} longues "
                f"({metriques.mix_format})",
                taille=10,
                couleur=DOUX_FONCE,
                pady=(6, 0),
            )

        # Outliers, avec miniature, lien de lecture et enregistrement.
        outliers = data.outliers_de(self.racine, ligne.channel_id, 4)
        self._titre_section(self._detail_corps, "Meilleurs outliers", pady=(12, 6))
        if outliers:
            for outlier in outliers:
                bloc = ctk.CTkFrame(self._detail_corps, fg_color=SURFACE_HAUTE, corner_radius=9)
                bloc.pack(fill="x", pady=(0, 8))
                ligne_video = ctk.CTkFrame(bloc, fg_color="transparent")
                ligne_video.pack(fill="x", padx=10, pady=(9, 4))
                vignette = self._poser_vignette(
                    ligne_video, outlier.get("video_id"), 112, 63
                )
                vignette.pack(side="left", padx=(0, 10))
                textes = ctk.CTkFrame(ligne_video, fg_color="transparent")
                textes.pack(side="left", fill="both", expand=True)
                ctk.CTkLabel(
                    textes,
                    text=outlier.get("titre") or "",
                    font=(self._police, 12, "bold"),
                    text_color=TEXTE,
                    wraplength=210,
                    justify="left",
                    anchor="w",
                ).pack(anchor="w")
                duree = outlier.get("duree_s")
                details = (
                    f"×{_nombre(outlier.get('score_outlier'), 1)} · "
                    f"{_nombre(outlier.get('vues'))} vues · "
                    f"{'Short' if outlier.get('est_short') else 'longue'}"
                    + (f" · {_duree(duree)}" if duree else "")
                )
                ctk.CTkLabel(
                    textes,
                    text=details,
                    font=(self._police, 11),
                    text_color=ACCENT_TEXTE,
                    wraplength=210,
                    justify="left",
                    anchor="w",
                ).pack(anchor="w", pady=(2, 0))
                if outlier.get("explication"):
                    ctk.CTkLabel(
                        textes,
                        text=outlier["explication"],
                        font=(self._police, 11),
                        text_color=DOUX_FONCE,
                        wraplength=210,
                        justify="left",
                        anchor="w",
                    ).pack(anchor="w", pady=(2, 0))
                actions_video = ctk.CTkFrame(bloc, fg_color="transparent")
                actions_video.pack(fill="x", padx=10, pady=(0, 8))
                self._lien(
                    actions_video,
                    "Lire",
                    lambda v=outlier: self._ouvrir_video(v),
                    width=64,
                ).pack(side="left")
                self._lien(
                    actions_video,
                    "Enregistrer",
                    lambda v=outlier, l=ligne: self._enregistrer_video(v, l),
                    width=104,
                ).pack(side="left", padx=(6, 0))
        else:
            self._detail_label(
                "Aucune vidéo au-dessus de la médiane de son format.",
                taille=10,
                couleur=DOUX_FONCE,
                pady=(0, 4),
            )

        # Statut.
        self._titre_section(self._detail_corps, "Statut", pady=(10, 4))
        if ligne.statut == "retenue":
            if ligne.complement:
                self._detail_label(
                    "● Retenue de complément : minimum de chaînes à proposer.",
                    taille=10,
                    couleur=OK,
                )
            else:
                self._detail_label(
                    "● Retenue : proximité suffisante.", taille=10, couleur=OK
                )
        else:
            self._detail_label(
                f"○ Écartée : {ligne.raison or 'raison inconnue'}.", taille=10, couleur=ALERTE
            )
    def _apercu_chaine(self) -> None:
        """Fenêtre d'aperçu : description, stats et vidéos outliers en grand."""
        ligne = self._ligne_selectionnee()
        if ligne is None:
            return
        fenetre = ctk.CTkToplevel(self)
        fenetre.title(f"Aperçu — {ligne.titre}")
        fenetre.geometry("800x820")
        fenetre.minsize(660, 560)
        fenetre.configure(fg_color=FOND)
        try:
            fenetre.transient(self)
        except tk.TclError:
            pass

        zone = ctk.CTkScrollableFrame(
            fenetre,
            fg_color=FOND,
            corner_radius=0,
            scrollbar_button_color=SURFACE_HAUTE,
            scrollbar_button_hover_color=BORDURE,
        )
        zone.pack(fill="both", expand=True, padx=18, pady=18)

        identite = f"@{ligne.handle.lstrip('@')}" if ligne.handle else ligne.channel_id
        sous_titre = (
            f"{identite} · {_nombre(ligne.abonnes)} abonnés · "
            f"{_nombre(ligne.nb_videos)} vidéos · {ligne.statut}"
        )
        cadre, corps = self._carte(zone, ligne.titre, sous_titre)
        cadre.pack(fill="x")

        actions = ctk.CTkFrame(corps, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 12))
        self._bouton(
            actions,
            "Ouvrir sur YouTube",
            lambda l=ligne: webbrowser.open(
                f"https://www.youtube.com/channel/{l.channel_id}"
            ),
            width=160,
        ).pack(side="left")
        self._bouton(
            actions,
            "Enregistrer la chaîne",
            lambda l=ligne: self._enregistrer_chaine(l),
            width=172,
        ).pack(side="left", padx=(8, 0))

        self._titre_section(corps, "Description", pady=(4, 4))
        description = (ligne.description or "").strip()
        texte_description = ctk.CTkLabel(
            corps,
            text=description or "Aucune description enregistrée.",
            font=(self._police, 13),
            text_color=DOUX,
            justify="left",
            anchor="w",
            wraplength=660,
        )
        texte_description.pack(fill="x")
        self._auto_wrap(texte_description)

        self._titre_section(zone, "Vidéos outliers", pady=(18, 8))
        outliers = data.outliers_de(self.racine, ligne.channel_id, 12)
        if not outliers:
            ctk.CTkLabel(
                zone,
                text="Aucune vidéo au-dessus de la médiane de son format pour cette chaîne.",
                font=(self._police, 13),
                text_color=DOUX_FONCE,
                anchor="w",
            ).pack(fill="x", pady=(0, 8))
        for outlier in outliers:
            bloc = ctk.CTkFrame(
                zone, fg_color=SURFACE, corner_radius=6, border_width=1, border_color=BORDURE
            )
            bloc.pack(fill="x", pady=(0, 10))
            contenu = ctk.CTkFrame(bloc, fg_color="transparent")
            contenu.pack(fill="x", padx=14, pady=12)
            vignette = self._poser_vignette(contenu, outlier.get("video_id"), 176, 99)
            vignette.pack(side="left", padx=(0, 14))
            droite = ctk.CTkFrame(contenu, fg_color="transparent")
            droite.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(
                droite,
                text=outlier.get("titre") or "",
                font=(self._police, 14, "bold"),
                text_color=TEXTE,
                wraplength=500,
                justify="left",
                anchor="w",
            ).pack(anchor="w")
            duree = outlier.get("duree_s")
            details = (
                f"×{_nombre(outlier.get('score_outlier'), 1)} · "
                f"{_nombre(outlier.get('vues'))} vues · "
                f"{'Short' if outlier.get('est_short') else 'longue'}"
                + (f" · {_duree(duree)}" if duree else "")
                + (f" · publiée le {outlier['publie_le'][:10]}" if outlier.get("publie_le") else "")
            )
            ctk.CTkLabel(
                droite,
                text=details,
                font=(self._police, 12),
                text_color=ACCENT_TEXTE,
                anchor="w",
                justify="left",
                wraplength=500,
            ).pack(anchor="w", pady=(3, 6))
            if outlier.get("explication"):
                ctk.CTkLabel(
                    droite,
                    text=outlier["explication"],
                    font=(self._police, 11),
                    text_color=DOUX_FONCE,
                    wraplength=500,
                    justify="left",
                    anchor="w",
                ).pack(anchor="w", pady=(0, 6))
            actions_video = ctk.CTkFrame(droite, fg_color="transparent")
            actions_video.pack(anchor="w")
            self._bouton(
                actions_video,
                "Lire sur YouTube",
                lambda v=outlier: self._ouvrir_video(v),
                width=136,
            ).pack(side="left")
            self._bouton(
                actions_video,
                "Enregistrer",
                lambda v=outlier, l=ligne: self._enregistrer_video(v, l),
                width=108,
            ).pack(side="left", padx=(8, 0))

        # Peindre une première fois avant de lever la fenêtre : un `lift`
        # différé après la création laissait certains libellés vides.
        fenetre.update_idletasks()
        fenetre.lift()

    def _stat(self, parent: tk.Misc, ligne: int, colonne: int, libelle: str, valeur: str) -> None:
        bloc = ctk.CTkFrame(parent, fg_color=SURFACE_HAUTE, corner_radius=9)
        marge = (0, 6) if colonne == 0 else (0, 0)
        bloc.grid(row=ligne, column=colonne, sticky="nsew", padx=marge, pady=(0, 6))
        ctk.CTkLabel(
            bloc, text=valeur, font=(self._police, 16, "bold"), text_color=TEXTE, anchor="w"
        ).pack(anchor="w", padx=10, pady=(7, 0))
        ctk.CTkLabel(
            bloc, text=libelle, font=(self._police, 11), text_color=DOUX_FONCE, anchor="w"
        ).pack(anchor="w", padx=10, pady=(0, 7))

    def _ouvrir_chaine(self) -> None:
        selection = self._arbre.selection()
        if selection:
            webbrowser.open(f"https://www.youtube.com/channel/{selection[0]}")

    def _ouvrir_video(self, video: dict) -> None:
        video_id = str(video.get("video_id") or "").strip()
        if video_id:
            webbrowser.open(f"https://www.youtube.com/watch?v={video_id}")

    def _enregistrer_video(
        self, video: dict, ligne: report.LigneRapport | None = None
    ) -> None:
        """Ajoute une vidéo outlier à la playlist « À regarder »."""
        try:
            playlist.ajouter_video(self.racine, video, ligne.titre if ligne else "")
        except playlist.ErreurPlaylist as probleme:
            messagebox.showerror("Playlist", str(probleme), parent=self)
            return
        self._message.set(
            f"« {video.get('titre') or 'Vidéo'} » ajoutée à « À regarder »."
        )
        self._remplir_playlist()

    def _enregistrer_chaine(
        self, ligne: report.LigneRapport | None = None
    ) -> None:
        """Ajoute la chaîne sélectionnée à la playlist « À regarder »."""
        ligne = ligne or self._ligne_selectionnee()
        if ligne is None:
            return
        try:
            playlist.ajouter_chaine(
                self.racine,
                ligne.channel_id,
                ligne.titre,
                ligne.handle,
                ligne.abonnes,
                ligne.description,
            )
        except playlist.ErreurPlaylist as probleme:
            messagebox.showerror("Playlist", str(probleme), parent=self)
            return
        self._message.set(f"« {ligne.titre} » ajoutée à « À regarder ».")
        self._remplir_playlist()

    def _copier_id(self) -> None:
        selection = self._arbre.selection()
        if not selection:
            return
        self.clipboard_clear()
        self.clipboard_append(selection[0])
        self._message.set("Identifiant copié dans le presse-papiers.")

    # ------------------------------------------------------------------
    # Ouverture de fichiers, fermeture, erreurs
    # ------------------------------------------------------------------
    def _ouvrir(self, chemin: Path) -> None:
        if not chemin.exists():
            messagebox.showinfo(
                "Fichier absent",
                f"{chemin.name} n'existe pas encore.\nLancez d'abord une exécution.",
                parent=self,
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(chemin))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(chemin)])
            else:
                subprocess.Popen(["xdg-open", str(chemin)])
        except OSError as probleme:
            messagebox.showerror("Ouverture impossible", str(probleme), parent=self)

    def _raccourci_enregistrer(self) -> None:
        """Ctrl+S enregistre d'abord la note en cours, puis la configuration."""
        if self._note_dirty and not self._sauvegarder_note():
            return
        self._sauvegarder()

    def _fermer(self) -> None:
        if self._note_dirty:
            if not self._sauvegarder_note(silencieux=True):
                quitter = messagebox.askyesnocancel(
                    "Note non enregistrée",
                    "La note en cours n'a pas pu être enregistrée (titre vide ou erreur). "
                    "Quitter sans l'enregistrer ?",
                    parent=self,
                )
                if not quitter:
                    return
        if self._tache is not None:
            if not messagebox.askyesno(
                "Exécution en cours",
                "Une exécution est en cours. L'arrêter et quitter ?",
                parent=self,
            ):
                return
            self._tache.annuler()
        if self._modifie:
            reponse = messagebox.askyesnocancel(
                "Enregistrer ?",
                "Des modifications ne sont pas enregistrées. Les enregistrer avant de quitter ?",
                parent=self,
            )
            if reponse is None:
                return
            if reponse and not self._sauvegarder():
                return
        if self._preferences.get("demarrage") == "derniere":
            self._enregistrer_preference("derniere_page", self._page_courante)
        self.destroy()

    def _filtre_statut_change(self, _valeur: str) -> None:
        """Applique le filtre Toutes / Retenues / Écartées."""
        if hasattr(self, "_arbre"):
            self._remplir_arbre()

    def _erreur_callback(self, type_exception, valeur, trace) -> None:
        detail = "".join(traceback.format_exception(type_exception, valeur, trace))
        try:
            self._ecrire_journal("! Erreur interne de l'interface :", "erreur")
            for ligne in detail.splitlines():
                self._ecrire_journal("  " + ligne, "erreur")
            _journaliser_plantage(self.racine, detail)
        except Exception:
            pass
        # Pas de dialogue modal ici : une erreur de mise en page peut se
        # reproduire en rafale, et un modal empilé donnerait l'impression que
        # l'application est bloquée ou fermée. Le journal et la barre d'état
        # suffisent, sans interrompre le travail en cours.
        try:
            self._message.set(
                "Erreur interne consignée dans data/interface-erreur.log."
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Démarrage
# ---------------------------------------------------------------------------


def _journaliser_plantage(racine: Path, detail: str) -> Path | None:
    try:
        chemin = racine / "data" / "interface-erreur.log"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as fichier:
            fichier.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n{detail}\n")
        return chemin
    except OSError:
        return None


_FLUX_DIAGNOSTIC = None


def _journaliser_demarrage(racine: Path, etape: str) -> None:
    """Trace chaque étape du démarrage.

    Une fenêtre qui apparaît puis disparaît ne laisse aucune trace : Tk est
    déjà lancé, mais l'erreur peut survenir hors de tout `try` (icône Win32,
    boucle d'événements, plantage natif). Ce journal dit jusqu'où le démarrage
    est allé.
    """
    try:
        ligne = f"{datetime.now().isoformat(timespec='seconds')} · {etape}\n"
        if _FLUX_DIAGNOSTIC is not None:
            _FLUX_DIAGNOSTIC.write(ligne)
            _FLUX_DIAGNOSTIC.flush()
            return
        chemin = racine / "data" / "interface-demarrage.log"
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as fichier:
            fichier.write(ligne)
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="outly-gui",
        description="Interface graphique locale d'Outly.",
    )
    parseur.add_argument(
        "--racine", default=None, help="dossier du projet (défaut : dossier d'Outly)"
    )
    arguments = parseur.parse_args(argv)

    racine = (
        Path(arguments.racine).expanduser().resolve()
        if arguments.racine
        else racine_projet()
    )
    global _FLUX_DIAGNOSTIC
    try:
        (racine / "data").mkdir(parents=True, exist_ok=True)
        # Mode « w » : chaque lancement repart d'un journal vierge. Le
        # descripteur reste ouvert pour que faulthandler écrive au même endroit
        # (le fermer puis le rouvrir donnerait deux fichiers mélangés).
        _FLUX_DIAGNOSTIC = (racine / "data" / "interface-demarrage.log").open(
            "w", encoding="utf-8"
        )
    except OSError:
        _FLUX_DIAGNOSTIC = None
    _journaliser_demarrage(racine, "lancement")
    try:
        import faulthandler

        # Un plantage natif (accès mémoire dans Tk ou dans l'icône Win32) tue
        # le processus sans traceback : faulthandler écrit alors la pile ici.
        if _FLUX_DIAGNOSTIC is not None:
            faulthandler.enable(file=_FLUX_DIAGNOSTIC, all_threads=True)
    except (OSError, ImportError):
        pass

    try:
        application = Application(racine)
    except Exception as probleme:  # Tk absent, pas d'affichage, dossier invalide…
        detail = traceback.format_exc()
        _journaliser_plantage(racine, detail)
        _journaliser_demarrage(racine, f"échec de construction : {probleme}")
        try:
            from tkinter import Tk
            from tkinter import messagebox as boite

            fantome = Tk()
            fantome.withdraw()
            boite.showerror(
                "Outly",
                f"L'interface n'a pas pu démarrer.\n\n{probleme}\n\n"
                "Détail écrit dans data/interface-erreur.log",
            )
            fantome.destroy()
        except Exception:
            print(f"Interface impossible : {probleme}", file=sys.stderr)
        return 1
    _journaliser_demarrage(
        racine, f"fenêtre construite · pid {os.getpid()} · {sys.executable}"
    )
    import atexit

    atexit.register(_journaliser_demarrage, racine, "fin du processus (atexit)")
    application.after(
        1500, lambda: _journaliser_demarrage(racine, "toujours ouverte après 1,5 s")
    )
    _journaliser_demarrage(racine, "boucle d'événements démarrée")
    try:
        application.mainloop()
    except Exception:
        detail = traceback.format_exc()
        _journaliser_plantage(racine, detail)
        _journaliser_demarrage(racine, "boucle d'événements interrompue")
        return 1
    _journaliser_demarrage(racine, "fermeture")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
