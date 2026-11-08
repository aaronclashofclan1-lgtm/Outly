"""Page « Studio YouTube » : le tableau de bord de sa propre chaîne.

La page ne calcule aucun indicateur : `outly.studio` interroge la YouTube
Analytics API et la Data API, met le résultat en cache, puis l'interface
l'affiche. Le réseau tourne dans un fil de travail et communique par une file,
jamais par un accès direct aux widgets.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

import customtkinter as ctk

from .. import studio
from . import app as ui_app
from . import data, settings

SOURCES = {
    "YT_SEARCH": "Recherche YouTube",
    "RELATED_VIDEO": "Vidéos suggérées",
    "YT_CHANNEL": "Page de la chaîne",
    "SUBSCRIBER": "Abonnés",
    "NO_LINK_OTHER": "Accès direct",
    "EXT_URL": "Sites externes",
    "PLAYLIST": "Playlists",
    "YT_OTHER_PAGE": "Autres pages YouTube",
    "NOTIFICATION": "Notifications",
    "SHORTS_FEED": "Fil Shorts",
    "YT_HOME": "Accueil YouTube",
    "YT_SUBSCRIPTION_FEED": "Fil des abonnements",
    "YT_PLAYLIST_PAGE": "Page playlist",
    "END_SCREEN": "Écrans de fin",
    "CARD": "Cartes",
    "HASHTAGS": "Hashtags",
}
APPAREILS = {
    "MOBILE": "Mobile",
    "DESKTOP": "Ordinateur",
    "TV": "Télévision",
    "TABLET": "Tablette",
    "GAME_CONSOLE": "Console de jeu",
    "WEARABLE": "Montre connectée",
    "UNKNOWN_PLATFORM": "Inconnu",
}
AGES = {
    "age13-17": "13–17 ans",
    "age18-24": "18–24 ans",
    "age25-34": "25–34 ans",
    "age35-44": "35–44 ans",
    "age45-54": "45–54 ans",
    "age55-64": "55–64 ans",
    "age65-": "65 ans et +",
}
GENRES = {"male": "Hommes", "female": "Femmes", "other": "Autre"}


def _texte(ui, parent, valeur, taille: int = 13, couleur=None, gras: bool = False, pady=(0, 4)):
    libelle = ctk.CTkLabel(
        parent,
        text=str(valeur or ""),
        font=(ui._police, taille, "bold" if gras else "normal"),
        text_color=couleur or ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=820,
    )
    libelle.pack(anchor="w", fill="x", pady=pady)
    ui._auto_wrap(libelle)
    return libelle


def _etiquette(ui, parent, texte, couleur=None, fond=None) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=str(texte or ""),
        font=(ui._police, 10, "bold"),
        text_color=couleur or ui_app.ACCENT_TEXTE,
        fg_color=fond or ui_app.ACCENT_DOUX,
        corner_radius=9,
        padx=8,
        pady=2,
    )


def _pourcent(valeur, decimales: int = 1) -> str:
    if valeur is None:
        return "—"
    return f"{float(valeur):.{decimales}f} %".replace(".", ",")


def _date_courte(valeur: str | None) -> str:
    texte = str(valeur or "")
    if len(texte) >= 10 and texte[4:5] == "-" and texte[7:8] == "-":
        return texte[8:10] + "/" + texte[5:7]
    return texte


def _libelle_source(cle: str) -> str:
    return SOURCES.get(cle, cle.replace("_", " ").capitalize() or "Inconnu")


def _libelle_appareil(cle: str) -> str:
    return APPAREILS.get(cle, cle.replace("_", " ").capitalize() or "Inconnu")


def _jours(ui) -> int:
    try:
        return int(str(ui._studio_jours.get()).split()[0])
    except (TypeError, ValueError, IndexError):
        return 28


# ---------------------------------------------------------------------------
# Graphiques (Canvas natif : aucune dépendance de tracé)
# ---------------------------------------------------------------------------


class Graphique(tk.Canvas):
    """Courbe, histogramme ou barres horizontales, redessinés au redimensionnement."""

    def __init__(self, ui, parent: tk.Misc, hauteur: int = 170):
        super().__init__(
            parent,
            height=hauteur,
            bg=ui_app._couleur(ui_app.SURFACE),
            highlightthickness=0,
            bd=0,
        )
        self._ui = ui
        self._mode = "vide"
        self._data = None
        self._message = ""
        self.bind("<Configure>", lambda _event: self._redessiner())
        ui._studio_graphiques.append(self)

    # --- API ---------------------------------------------------------
    def vide(self, message: str = "Aucune donnée.") -> None:
        self._mode, self._data, self._message = "vide", None, message
        self._redessiner()

    def courbe(self, points: list[dict]) -> None:
        self._mode, self._data = "courbe", list(points or [])
        self._redessiner()

    def histogramme(self, valeurs: list[float], etiquettes: list[str] | None = None) -> None:
        self._mode = "histogramme"
        self._data = (list(valeurs or []), list(etiquettes or []))
        self._redessiner()

    def barres(self, donnees: list[tuple[str, float]]) -> None:
        self._mode = "barres"
        self._data = list(donnees or [])
        self.configure(height=max(60, 18 + 28 * max(1, len(self._data))))
        self._redessiner()

    def ligne(self, points: list[dict], titre: str = "") -> None:
        """Courbe cumulative libre : points {jour, cumul}."""
        self._mode = "ligne"
        self._data = (list(points or []), str(titre or ""))
        self._redessiner()

    def rafraichir(self) -> None:
        try:
            self.configure(bg=ui_app._couleur(ui_app.SURFACE))
        except tk.TclError:
            return
        self._redessiner()

    # --- Tracé -------------------------------------------------------
    def _redessiner(self) -> None:
        try:
            self.delete("all")
            largeur = self.winfo_width()
            hauteur = self.winfo_height()
        except tk.TclError:
            return
        if largeur < 120 or hauteur < 60:
            return
        texte = ui_app._couleur(ui_app.DOUX)
        doux = ui_app._couleur(ui_app.DOUX_FONCE)
        grille = ui_app._couleur(ui_app.BORDURE)
        accent = ui_app._couleur(ui_app.ACCENT_CLAIR)
        fond_barre = ui_app._couleur(ui_app.ACCENT_DOUX)
        if self._mode == "vide" or not self._data:
            self.create_text(
                largeur / 2,
                hauteur / 2,
                text=self._message or "Aucune donnée.",
                fill=texte,
                font=(self._ui._police, 12),
            )
            return
        if self._mode == "courbe":
            self._courbe(largeur, hauteur, texte, doux, grille, accent, fond_barre)
        elif self._mode == "histogramme":
            self._histogramme(largeur, hauteur, texte, doux, grille, accent)
        elif self._mode == "barres":
            self._barres(largeur, texte, doux, grille, accent)
        elif self._mode == "ligne":
            self._ligne(largeur, hauteur, texte, doux, grille, accent, fond_barre)

    def _courbe(self, largeur, hauteur, texte, doux, grille, accent, fond_barre) -> None:
        points = [
            point
            for point in self._data
            if isinstance(point, dict) and point.get("maintien") is not None
        ]
        if not points:
            self.create_text(
                largeur / 2,
                hauteur / 2,
                text="Pas encore assez de vues pour tracer la rétention.",
                fill=texte,
                font=(self._ui._police, 12),
            )
            return
        gauche, droite, haut, bas = 50, largeur - 18, 16, hauteur - 30
        maximum = max(0.05, max(float(point["maintien"]) for point in points))
        plafond = max(1.0, maximum * 1.08)
        for rang in range(5):
            ratio = rang / 4
            y = bas - ratio * (bas - haut)
            self.create_line(gauche, y, droite, y, fill=grille)
            self.create_text(
                gauche - 8,
                y,
                text=f"{int(ratio * plafond * 100)} %",
                fill=doux,
                anchor="e",
                font=(self._ui._police, 10),
            )
        for rang in range(5):
            ratio = rang / 4
            x = gauche + ratio * (droite - gauche)
            self.create_text(
                x,
                bas + 12,
                text=f"{int(ratio * 100)} %",
                fill=doux,
                font=(self._ui._police, 10),
            )
        coordonnees: list[float] = []
        for point in points:
            ratio = min(1.0, max(0.0, float(point["ratio"])))
            x = gauche + ratio * (droite - gauche)
            y = bas - (float(point["maintien"]) / plafond) * (bas - haut)
            coordonnees.extend([x, y])
        if len(coordonnees) >= 4:
            polygone = [gauche, bas, *coordonnees, coordonnees[-2], bas]
            self.create_polygon(polygone, fill=fond_barre, outline="")
            self.create_line(*coordonnees, fill=accent, width=2, smooth=True)
        self.create_text(
            gauche,
            4,
            text="Part de l'audience encore présente",
            fill=texte,
            anchor="nw",
            font=(self._ui._police, 10, "bold"),
        )

    def _histogramme(self, largeur, hauteur, texte, doux, grille, accent) -> None:
        valeurs, etiquettes = self._data
        valeurs = [max(0.0, float(valeur or 0)) for valeur in valeurs]
        if not valeurs:
            self.create_text(
                largeur / 2,
                hauteur / 2,
                text="Aucune vue sur la période.",
                fill=texte,
                font=(self._ui._police, 12),
            )
            return
        gauche, droite, haut, bas = 46, largeur - 14, 14, hauteur - 26
        maximum = max(valeurs) or 1
        for rang in range(4):
            ratio = rang / 3
            y = bas - ratio * (bas - haut)
            self.create_line(gauche, y, droite, y, fill=grille)
            self.create_text(
                gauche - 7,
                y,
                text=ui_app._nombre(maximum * ratio),
                fill=doux,
                anchor="e",
                font=(self._ui._police, 9),
            )
        pas = (droite - gauche) / max(1, len(valeurs))
        barre = max(1.0, pas * 0.72)
        for index, valeur in enumerate(valeurs):
            x = gauche + index * pas + (pas - barre) / 2
            y = bas - (valeur / maximum) * (bas - haut)
            self.create_rectangle(x, y, x + barre, bas, fill=accent, outline="")
        if etiquettes:
            self.create_text(
                gauche,
                bas + 10,
                text=etiquettes[0],
                fill=doux,
                anchor="w",
                font=(self._ui._police, 9),
            )
            self.create_text(
                droite,
                bas + 10,
                text=etiquettes[-1],
                fill=doux,
                anchor="e",
                font=(self._ui._police, 9),
            )

    def _ligne(self, largeur, hauteur, texte, doux, grille, accent, fond_barre) -> None:
        points, titre = self._data
        points = [
            point
            for point in points
            if isinstance(point, dict) and point.get("cumul") is not None
        ]
        if not points:
            self.create_text(
                largeur / 2,
                hauteur / 2,
                text="Aucune vidéo datée pour reconstruire la courbe.",
                fill=texte,
                font=(self._ui._police, 12),
            )
            return
        gauche, droite, haut, bas = 58, largeur - 18, 26, hauteur - 30
        maximum = max(1.0, max(float(point["cumul"]) for point in points))
        for rang in range(5):
            ratio = rang / 4
            y = bas - ratio * (bas - haut)
            self.create_line(gauche, y, droite, y, fill=grille)
            self.create_text(
                gauche - 8,
                y,
                text=ui_app._nombre(maximum * ratio),
                fill=doux,
                anchor="e",
                font=(self._ui._police, 9),
            )
        coordonnees: list[float] = []
        for index, point in enumerate(points):
            x = gauche + (index / max(1, len(points) - 1)) * (droite - gauche)
            y = bas - (float(point["cumul"]) / maximum) * (bas - haut)
            coordonnees.extend([x, y])
        if len(coordonnees) >= 4:
            self.create_polygon(
                [gauche, bas, *coordonnees, coordonnees[-2], bas],
                fill=fond_barre,
                outline="",
            )
            self.create_line(*coordonnees, fill=accent, width=2, smooth=True)
        if len(coordonnees) == 2:
            self.create_oval(
                coordonnees[0] - 3, coordonnees[1] - 3,
                coordonnees[0] + 3, coordonnees[1] + 3,
                fill=accent, outline="",
            )
        self.create_text(
            gauche, 4, text=titre or "Vues cumulées", fill=texte,
            anchor="nw", font=(self._ui._police, 10, "bold"),
        )
        self.create_text(
            gauche, bas + 12, text=_date_courte(points[0].get("jour")),
            fill=doux, anchor="w", font=(self._ui._police, 9),
        )
        self.create_text(
            droite, bas + 12, text=_date_courte(points[-1].get("jour")),
            fill=doux, anchor="e", font=(self._ui._police, 9),
        )
        self.create_text(
            droite, haut - 2, text=ui_app._nombre(maximum) + " vues",
            fill=accent, anchor="ne", font=(self._ui._police, 10, "bold"),
        )

    def _barres(self, largeur, texte, doux, grille, accent) -> None:
        donnees = [(str(libelle), max(0.0, float(valeur or 0))) for libelle, valeur in self._data]
        if not donnees:
            self.create_text(
                largeur / 2,
                30,
                text="Aucune donnée.",
                fill=texte,
                font=(self._ui._police, 12),
            )
            return
        maximum = max(valeur for _libelle, valeur in donnees) or 1
        gauche = min(190, max(110, largeur // 3))
        droite = largeur - 58
        for index, (libelle, valeur) in enumerate(donnees):
            y = 8 + index * 28
            self.create_text(
                gauche - 10,
                y + 8,
                text=(libelle[:26] + "…") if len(libelle) > 27 else libelle,
                fill=doux,
                anchor="e",
                font=(self._ui._police, 11),
            )
            self.create_rectangle(gauche, y, droite, y + 16, fill=grille, outline="")
            longueur = (valeur / maximum) * (droite - gauche)
            if longueur > 1:
                self.create_rectangle(gauche, y, gauche + longueur, y + 16, fill=accent, outline="")
            self.create_text(
                largeur - 8,
                y + 8,
                text=ui_app._nombre(valeur),
                fill=texte,
                anchor="e",
                font=(self._ui._police, 11, "bold"),
            )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def construire(ui) -> None:
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["studio"] = page

    zone = ui._zone_defilante(page)
    contenu = ctk.CTkFrame(zone, fg_color="transparent")
    contenu.pack(fill="x")

    env = settings.lire_env(ui.racine / ".env")
    identifiants = studio.identifiants(env)
    ui._studio_file = queue.Queue()
    ui._studio_jours = tk.StringVar(value="28 jours")
    ui._studio_etat = tk.StringVar(
        value="Données en cache." if studio.lire_cache(ui.racine) else "Aucune donnée pour l'instant."
    )
    ui._studio_donnees = studio.lire_cache(ui.racine)
    ui._studio_client = None
    ui._studio_chargement = False
    ui._studio_retention: dict[str, list[dict]] = {}
    ui._studio_retention_en_cours: set[str] = set()
    ui._studio_videos: dict[str, dict] = {}
    ui._studio_video_courant: dict | None = None
    ui._studio_graphiques: list[Graphique] = []
    ui._studio_arbre = None
    ui._studio_retention_graph: Graphique | None = None
    ui._studio_retention_titre = tk.StringVar(value="Sélectionnez une vidéo pour voir sa rétention.")
    ui._studio_client_id = tk.StringVar(value=identifiants["client_id"])
    ui._studio_client_secret = tk.StringVar(value=identifiants["client_secret"])
    ui._studio_secret_visible = tk.BooleanVar(value=False)
    ui._studio_onglet = tk.StringVar(value="Vidéos longues")
    ui._studio_concurrents: list[dict] = []
    ui._studio_concurrent_choix = tk.StringVar()
    ui._studio_concurrent_map: dict[str, str] = {}
    ui._studio_concurrent_graph: Graphique | None = None
    ui._studio_concurrent_pancartes = None
    ui._studio_concurrent_etat = tk.StringVar(value="")

    ui._studio_zone = ctk.CTkFrame(contenu, fg_color="transparent")
    ui._studio_zone.pack(fill="x")
    _remplir(ui)
    ui.after(200, lambda: _pomper(ui))


def _pomper(ui) -> None:
    """Vide la file du fil de travail, dans le fil Tkinter uniquement."""
    try:
        while True:
            genre, charge = ui._studio_file.get_nowait()
            if genre == "etat":
                ui._studio_etat.set(str(charge))
            elif genre == "erreur":
                ui._studio_chargement = False
                ui._studio_etat.set(str(charge))
                ui._message.set(str(charge))
            elif genre == "tableau":
                client, tableau = charge
                ui._studio_chargement = False
                ui._studio_client = client
                ui._studio_donnees = studio.lire_cache(ui.racine)
                ui._studio_etat.set(
                    f"{tableau.vues:,} vues · {tableau.minutes:,.0f} minutes vues · "
                    f"du {tableau.debut} au {tableau.fin}".replace(",", " ")
                )
                _remplir(ui)
                ui._rafraichir_tableau_de_bord()
            elif genre == "retention":
                video_id, points, probleme = charge
                ui._studio_retention_en_cours.discard(video_id)
                if probleme:
                    ui._studio_retention_titre.set(str(probleme))
                    continue
                ui._studio_retention[video_id] = points
                courant = ui._studio_video_courant or {}
                if str(courant.get("identifiant") or "") == video_id:
                    _afficher_retention(ui, points)
    except queue.Empty:
        pass
    try:
        if ui.winfo_exists() and ui._pages.get("studio") is not None:
            ui.after(150, lambda: _pomper(ui))
    except tk.TclError:
        pass


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------


def _remplir(ui) -> None:
    if not hasattr(ui, "_studio_zone"):
        return
    if ui._pages.get("studio") is None:
        return
    donnees = studio.lire_cache(ui.racine)
    ui._studio_donnees = donnees
    for enfant in ui._studio_zone.winfo_children():
        enfant.destroy()
    ui._studio_graphiques = []
    ui._studio_arbre = None
    ui._studio_retention_graph = None
    ui._studio_concurrent_graph = None
    ui._studio_concurrents = data.concurrents_historique(ui.racine)
    _carte_onglets(ui, ui._studio_zone)
    ui._studio_entete_zone = ctk.CTkFrame(ui._studio_zone, fg_color="transparent")
    ui._studio_entete_zone.pack(fill="x")
    ui._studio_onglet_contenu = ctk.CTkFrame(ui._studio_zone, fg_color="transparent")
    ui._studio_onglet_contenu.pack(fill="x")
    _rendre_onglet(ui)


def _carte_onglets(ui, parent) -> None:
    cadre, corps = ui._carte(
        parent,
        "Explorer le Studio",
        "Quatre vues : formats longs, Shorts, autres contenus et concurrents. "
        "Le choix reste actif pendant la session.",
        cle="studio-onglets",
    )
    cadre.pack(fill="x", pady=(0, 12))
    ctk.CTkSegmentedButton(
        corps,
        values=["Vidéos longues", "Shorts", "Autres contenus", "Concurrents"],
        variable=ui._studio_onglet,
        command=lambda valeur: _changer_onglet(ui, valeur),
        height=34,
        corner_radius=8,
        selected_color=ui_app.ACCENT,
        selected_hover_color=ui_app.ACCENT_SURVOL,
        unselected_color=ui_app.SURFACE_HAUTE,
        unselected_hover_color=ui_app.BORDURE,
        text_color=ui_app.TEXTE,
        font=(ui._police, 12),
    ).pack(fill="x")


def _changer_onglet(ui, valeur: str) -> None:
    ui._studio_onglet.set(valeur)
    _rendre_onglet(ui)


def _rendre_onglet(ui) -> None:
    parent = getattr(ui, "_studio_onglet_contenu", None)
    if parent is None:
        return
    for enfant in parent.winfo_children():
        enfant.destroy()
    ui._studio_arbre = None
    ui._studio_retention_graph = None
    ui._studio_concurrent_graph = None
    ui._studio_concurrent_pancartes = None
    ui._studio_videos = {}
    onglet = ui._studio_onglet.get()
    donnees = ui._studio_donnees or {}
    entete = getattr(ui, "_studio_entete_zone", None)
    if entete is not None:
        for enfant in entete.winfo_children():
            enfant.destroy()
        if onglet == "Concurrents":
            entete.pack_forget()
        else:
            entete.pack(fill="x", before=parent)
            _carte_entete(ui, entete)
    if onglet == "Concurrents":
        _carte_concurrents(ui, parent)
        return
    if not donnees:
        _carte_vide(ui, parent)
        _carte_guide(ui, parent)
        return
    videos = [video for video in donnees.get("videos") or [] if isinstance(video, dict)]
    if onglet == "Vidéos longues":
        filtrees = studio.filtrer_videos(videos, "longue")
        _carte_kpis_videos(ui, parent, filtrees, "Vidéos longues", donnees)
        _carte_videos(ui, parent, donnees, filtrees, "studio-videos-longues")
        _carte_vues(ui, parent, donnees)
        _carte_repartitions(ui, parent, donnees)
    elif onglet == "Shorts":
        filtrees = studio.filtrer_videos(videos, "short")
        _carte_kpis_videos(ui, parent, filtrees, "Shorts", donnees)
        _carte_videos(ui, parent, donnees, filtrees, "studio-videos-shorts")
    else:
        _carte_autres(ui, parent, videos)
        _carte_notes(ui, parent, donnees)
        _carte_guide(ui, parent)


def _carte_entete(ui, parent) -> None:
    cadre, corps = ui._carte(
        parent,
        "Ma chaîne YouTube",
        "Les indicateurs de YouTube Studio : vues, temps de visionnage, rétention, "
        "sources de trafic et audience. Les rapports ont un décalage de 24 à 48 h.",
        cle="studio-entete",
    )
    cadre.pack(fill="x", pady=(0, 12))

    ligne = ctk.CTkFrame(corps, fg_color="transparent")
    ligne.pack(fill="x", pady=(0, 10))
    connecte = bool(
        studio.identifiants(settings.lire_env(ui.racine / ".env"))["refresh_token"]
    )
    _etiquette(
        ui,
        ligne,
        "AUTORISÉ" if connecte else "NON CONNECTÉ",
        ui_app.OK if connecte else ui_app.ALERTE,
        ui_app.OK_DOUX if connecte else ui_app.ALERTE_DOUX,
    ).pack(side="left")
    ctk.CTkLabel(
        ligne,
        textvariable=ui._studio_etat,
        font=(ui._police, 11),
        text_color=ui_app.DOUX_FONCE,
        anchor="w",
        justify="left",
        wraplength=560,
    ).pack(side="left", padx=(10, 0), fill="x", expand=True)

    actions = ctk.CTkFrame(corps, fg_color="transparent")
    actions.pack(fill="x")
    ctk.CTkSegmentedButton(
        actions,
        values=["7 jours", "28 jours", "90 jours"],
        variable=ui._studio_jours,
        height=32,
        corner_radius=7,
        selected_color=ui_app.ACCENT,
        selected_hover_color=ui_app.ACCENT_SURVOL,
        unselected_color=ui_app.SURFACE_HAUTE,
        unselected_hover_color=ui_app.BORDURE,
        text_color=ui_app.TEXTE,
        font=(ui._police, 12),
    ).pack(side="left")
    ui._bouton_accent(
        actions, "Actualiser", lambda: _charger(ui), width=120
    ).pack(side="left", padx=(10, 0))
    if connecte:
        ui._bouton(
            actions, "Déconnecter", lambda: _deconnecter(ui), width=120
        ).pack(side="left", padx=(8, 0))
    ui._lien(actions, "Guide", lambda: _ouvrir_guide(ui), width=82).pack(
        side="left", padx=(8, 0)
    )

    ctk.CTkLabel(
        corps,
        text="IDENTIFIANT CLIENT OAuth — application de bureau",
        font=(ui._police, 10, "bold"),
        text_color=ui_app.DOUX,
        anchor="w",
    ).pack(anchor="w", pady=(14, 0))
    ctk.CTkEntry(
        corps,
        textvariable=ui._studio_client_id,
        height=34,
        corner_radius=7,
        fg_color=ui_app.CHAMP,
        border_color=ui_app.BORDURE,
        border_width=1,
        text_color=ui_app.TEXTE,
        placeholder_text="1234567890-abc.apps.googleusercontent.com",
        font=(ui._police, 12),
    ).pack(fill="x", pady=(4, 0))

    ctk.CTkLabel(
        corps,
        text="SECRET CLIENT OAuth",
        font=(ui._police, 10, "bold"),
        text_color=ui_app.DOUX,
        anchor="w",
    ).pack(anchor="w", pady=(10, 0))
    secret = ctk.CTkEntry(
        corps,
        textvariable=ui._studio_client_secret,
        height=34,
        corner_radius=7,
        fg_color=ui_app.CHAMP,
        border_color=ui_app.BORDURE,
        border_width=1,
        text_color=ui_app.TEXTE,
        placeholder_text="GOCSPX-…",
        show="*",
        font=(ui._police, 12),
    )
    secret.pack(fill="x", pady=(4, 0))

    options = ctk.CTkFrame(corps, fg_color="transparent")
    options.pack(fill="x", pady=(10, 0))
    ctk.CTkCheckBox(
        options,
        text="Afficher le secret",
        variable=ui._studio_secret_visible,
        command=lambda: secret.configure(
            show="" if ui._studio_secret_visible.get() else "*"
        ),
        font=(ui._police, 11),
        text_color=ui_app.DOUX,
        fg_color=ui_app.ACCENT,
        hover_color=ui_app.ACCENT_SURVOL,
        border_color=ui_app.BORDURE,
    ).pack(side="left")
    ui._bouton(
        options, "Enregistrer les identifiants", lambda: _enregistrer_identifiants(ui), width=210
    ).pack(side="right", padx=(8, 0))
    ui._bouton_accent(
        options, "Connecter ma chaîne", lambda: _connecter(ui), width=170
    ).pack(side="right")


def _carte_vide(ui, parent) -> None:
    cadre, corps = ui._carte(
        parent,
        "Aucune donnée pour l'instant",
        "Autorisez votre compte Google propriétaire de la chaîne, puis lancez la "
        "première lecture. Aucune donnée n'est envoyée à un tiers : Outly interroge "
        "directement les API Google depuis ce PC.",
        cle="studio-vide",
    )
    cadre.pack(fill="x", pady=(0, 12))
    chaine = ""
    try:
        configuration = settings.lire_config(ui.racine / "config.yaml")
        chaine = str((configuration.get("moi") or {}).get("chaine") or "")
    except settings.ErreurFichier:
        chaine = ""
    _texte(
        ui,
        corps,
        "Chaîne déclarée dans Outly : "
        + (chaine or "(renseignez « Ma chaîne » dans Mon contenu)")
        + ". C'est le compte Google propriétaire de cette chaîne qu'il faut autoriser.",
        taille=12,
        couleur=ui_app.DOUX,
    )


def _carte_kpis(ui, parent, donnees: dict) -> None:
    cadre, corps = ui._carte(
        parent,
        "Indicateurs de la période",
        f"{donnees.get('debut', '')} → {donnees.get('fin', '')} · "
        f"{donnees.get('chaine') or 'chaîne'}",
        cle="studio-kpis",
    )
    cadre.pack(fill="x", pady=(0, 12))
    grille = ctk.CTkFrame(corps, fg_color="transparent")
    grille.pack(fill="x")
    for colonne in range(4):
        grille.grid_columnconfigure(colonne, weight=1, uniform="kpi")

    abonnes_net = int(donnees.get("abonnes_gagnes") or 0) - int(
        donnees.get("abonnes_perdus") or 0
    )
    impressions = donnees.get("impressions")
    ctr = donnees.get("ctr")
    cartes = [
        ("Vues", ui_app._nombre(donnees.get("vues")), "sur la période"),
        (
            "Temps de visionnage",
            f"{float(donnees.get('minutes') or 0) / 60:,.1f} h".replace(",", " "),
            f"{ui_app._nombre(donnees.get('minutes'))} minutes",
        ),
        (
            "Durée moyenne vue",
            ui_app._duree(float(donnees.get("duree_moyenne_s") or 0)),
            "par vue",
        ),
        (
            "Visionnage moyen",
            _pourcent(donnees.get("pourcentage_vu")),
            "de la vidéo",
        ),
        (
            "Abonnés",
            ("+" if abonnes_net >= 0 else "") + ui_app._nombre(abonnes_net),
            f"+{ui_app._nombre(donnees.get('abonnes_gagnes'))} / "
            f"−{ui_app._nombre(donnees.get('abonnes_perdus'))}",
        ),
        ("J'aime", ui_app._nombre(donnees.get("jaime")), "sur la période"),
        ("Commentaires", ui_app._nombre(donnees.get("commentaires")), "sur la période"),
        ("Partages", ui_app._nombre(donnees.get("partages")), "sur la période"),
        (
            "Impressions",
            ui_app._nombre(impressions) if impressions is not None else "—",
            "miniatures affichées",
        ),
        (
            "Taux de clic",
            _pourcent(ctr) if ctr is not None else "—",
            "impressions → vues",
        ),
        (
            "Abonnés (total)",
            ui_app._nombre(donnees.get("abonnes")),
            "chaîne",
        ),
        (
            "Vidéos publiées",
            ui_app._nombre(donnees.get("nombre_videos")),
            "chaîne",
        ),
    ]
    for index, (titre, valeur, detail) in enumerate(cartes):
        ligne, colonne = divmod(index, 4)
        bloc = ctk.CTkFrame(grille, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
        bloc.grid(row=ligne, column=colonne, sticky="nsew", padx=3, pady=3)
        ctk.CTkLabel(
            bloc,
            text=titre.upper(),
            font=(ui._police, 9, "bold"),
            text_color=ui_app.DOUX,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkLabel(
            bloc,
            text=str(valeur),
            font=(ui._police, 20, "bold"),
            text_color=ui_app.TEXTE,
            anchor="w",
        ).pack(anchor="w", padx=10)
        ctk.CTkLabel(
            bloc,
            text=str(detail),
            font=(ui._police, 10),
            text_color=ui_app.DOUX_FONCE,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(0, 8))


def _carte_kpis_videos(ui, parent, videos: list[dict], libelle: str, donnees: dict) -> None:
    cadre, corps = ui._carte(
        parent,
        f"{libelle} · indicateurs",
        f"{donnees.get('debut', '')} → {donnees.get('fin', '')} · "
        "somme des vidéos connues sur la période.",
        cle="studio-kpis-" + libelle.lower().replace(" ", "-"),
    )
    cadre.pack(fill="x", pady=(0, 12))
    vues = sum(int(video.get("vues") or 0) for video in videos)
    minutes = sum(float(video.get("minutes") or 0) for video in videos)
    impressions = sum(int(video.get("impressions") or 0) for video in videos)
    poids = sum(int(video.get("vues") or 0) for video in videos)
    if poids:
        pourcentage = sum(
            float(video.get("pourcentage_vu") or 0) * float(video.get("vues") or 0)
            for video in videos
        ) / poids
        ctr = sum(
            float(video.get("ctr") or 0) * float(video.get("impressions") or 0)
            for video in videos
        ) / impressions if impressions else None
    else:
        pourcentage = None
        ctr = None
    cartes = [
        ("Vidéos", ui_app._nombre(len(videos)), libelle.lower()),
        ("Vues", ui_app._nombre(vues), "sur la période"),
        ("Temps de visionnage", f"{minutes / 60:,.1f} h".replace(",", " "), f"{ui_app._nombre(minutes)} min"),
        ("Visionnage moyen", _pourcent(pourcentage) if pourcentage is not None else "—", "pondéré par les vues"),
        ("J'aime", ui_app._nombre(sum(int(video.get('jaime') or 0) for video in videos)), "sur la période"),
        ("Commentaires", ui_app._nombre(sum(int(video.get('commentaires') or 0) for video in videos)), "sur la période"),
        ("Impressions", ui_app._nombre(impressions) if impressions else "—", "miniatures affichées"),
        ("Taux de clic", _pourcent(ctr) if ctr is not None else "—", "impressions → vues"),
    ]
    grille = ctk.CTkFrame(corps, fg_color="transparent")
    grille.pack(fill="x")
    for colonne in range(4):
        grille.grid_columnconfigure(colonne, weight=1, uniform="kpi")
    for index, (titre, valeur, detail) in enumerate(cartes):
        ligne, colonne = divmod(index, 4)
        bloc = ctk.CTkFrame(grille, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
        bloc.grid(row=ligne, column=colonne, sticky="nsew", padx=3, pady=3)
        ctk.CTkLabel(
            bloc, text=titre.upper(), font=(ui._police, 9, "bold"),
            text_color=ui_app.DOUX, anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 0))
        ctk.CTkLabel(
            bloc, text=str(valeur), font=(ui._police, 20, "bold"),
            text_color=ui_app.TEXTE, anchor="w",
        ).pack(anchor="w", padx=10)
        ctk.CTkLabel(
            bloc, text=str(detail), font=(ui._police, 10),
            text_color=ui_app.DOUX_FONCE, anchor="w",
        ).pack(anchor="w", padx=10, pady=(0, 8))
    if not videos:
        _texte(
            ui, corps,
            "Aucune vidéo de ce type dans la période. Essayez 90 jours ou vérifiez "
            "que la lecture Studio a bien récupéré les métadonnées des vidéos.",
            taille=11, couleur=ui_app.DOUX, pady=(10, 0),
        )


def _carte_autres(ui, parent, videos: list[dict]) -> None:
    autres = studio.filtrer_videos(videos, "autre")
    directs = [video for video in autres if str(video.get("live") or "none") != "none"]
    sans_duree = [video for video in autres if video not in directs]
    cadre, corps = ui._carte(
        parent,
        "Autres contenus",
        "Directs, rediffusions et vidéos dont la durée est inconnue. Les formats "
        "communautaires sont signalés plus bas avec leurs limites d'API.",
        cle="studio-autres",
    )
    cadre.pack(fill="x", pady=(0, 12))
    if not autres:
        _texte(
            ui, corps,
            "Aucun direct, replay ou contenu de durée inconnue dans les données "
            "récupérées. Les vidéos longues et les Shorts ont leurs propres onglets.",
            taille=12, couleur=ui_app.DOUX,
        )
    else:
        colonnes = (
            ("titre", "Contenu", 340, "w"),
            ("type", "Type", 100, "w"),
            ("date", "Publié", 90, "w"),
            ("duree", "Durée", 80, "e"),
            ("vues", "Vues", 80, "e"),
            ("jaime", "J'aime", 70, "e"),
        )
        arbre = ttk.Treeview(
            corps, columns=[colonne[0] for colonne in colonnes], show="headings",
            style="Outly.Treeview", height=min(12, max(3, len(autres))),
        )
        for cle, libelle, largeur, ancre in colonnes:
            arbre.heading(cle, text=libelle)
            arbre.column(cle, width=largeur, anchor=ancre, stretch=cle == "titre")
        arbre.pack(fill="x")
        for video in autres:
            live = str(video.get("live") or "none")
            type_ = "Direct" if live == "live" else "Replay" if live == "was_live" else "Durée inconnue"
            arbre.insert("", "end", values=(
                str(video.get("titre") or video.get("identifiant") or "Contenu"),
                type_,
                _date_courte(video.get("publie_le")),
                ui_app._duree(float(video.get("duree_s") or 0)),
                ui_app._nombre(video.get("vues")),
                ui_app._nombre(video.get("jaime")),
            ))
        if sans_duree:
            _texte(
                ui, corps, f"{len(sans_duree)} contenu(s) sans durée exploitable.",
                taille=10, couleur=ui_app.DOUX, pady=(8, 0),
            )
    if directs:
        _texte(
            ui, corps,
            f"{len(directs)} direct(s) ou replay(s) détecté(s) via liveStreamingDetails.",
            taille=10, couleur=ui_app.OK, pady=(8, 0),
        )

    cadre, corps = ui._carte(
        parent,
        "Posts, sondages et questionnaires",
        "YouTube ne les expose pas dans les API Data et Analytics utilisées par Outly.",
        cle="studio-autres-limites",
    )
    cadre.pack(fill="x", pady=(0, 12))
    for limite in (
        "Les publications de communauté, sondages, quiz et questionnaires ne font "
        "partie ni de la Data API v3 ni de la YouTube Analytics API.",
        "Ils ne peuvent donc pas être listés ni mesurés ici sans import manuel ou "
        "une autre source. Aucun chiffre n'est inventé pour combler ce manque.",
        "Les directs et rediffusions sont reconnus quand YouTube renvoie "
        "liveBroadcastContent ou liveStreamingDetails.",
    ):
        _texte(ui, corps, "•  " + limite, taille=11, couleur=ui_app.DOUX)
    ui._lien(
        corps, "Ouvrir YouTube Studio",
        lambda: webbrowser.open("https://studio.youtube.com/"), width=190,
    ).pack(anchor="w", pady=(8, 0))


def _carte_concurrents(ui, parent) -> None:
    cadre, corps = ui._carte(
        parent,
        "Évolution des vues d'un concurrent",
        "Choisissez une chaîne trouvée par la veille : la courbe cumule les vues "
        "actuelles des vidéos connues, par date de publication. C'est une "
        "reconstruction, pas un historique quotidien.",
        cle="studio-concurrents",
    )
    cadre.pack(fill="x", pady=(0, 12))
    concurrents = ui._studio_concurrents or []
    if not concurrents:
        _texte(
            ui, corps,
            "Aucune chaîne trouvée par la veille concurrentielle avec des vidéos. "
            "Lancez une veille et un enrichissement, puis revenez ici. Les seeds "
            "et les fiches ajoutées à la main ne sont pas proposés.",
            taille=12, couleur=ui_app.DOUX,
        )
        ui._lien(
            corps, "Voir les chaînes", lambda: ui._afficher_page("resultats"), width=130,
        ).pack(anchor="w", pady=(8, 0))
        return
    labels: list[str] = []
    correspondance: dict[str, str] = {}
    for chaine in concurrents:
        base = f"{chaine.get('titre')} · {chaine.get('videos_connues')} vidéos"
        label = base
        numero = 2
        while label in correspondance:
            label = f"{base} ({numero})"
            numero += 1
        labels.append(label)
        correspondance[label] = str(chaine.get("channel_id") or "")
    ui._studio_concurrent_map = correspondance
    if ui._studio_concurrent_choix.get() not in correspondance:
        ui._studio_concurrent_choix.set(labels[0])
    ctk.CTkOptionMenu(
        corps, values=labels, variable=ui._studio_concurrent_choix,
        command=lambda _valeur: _choisir_concurrent(ui),
        height=32, corner_radius=7, fg_color=ui_app.CHAMP,
        button_color=ui_app.ACCENT, button_hover_color=ui_app.ACCENT_SURVOL,
        dropdown_fg_color=ui_app.SURFACE, dropdown_hover_color=ui_app.ACCENT_DOUX,
        dropdown_text_color=ui_app.TEXTE, text_color=ui_app.TEXTE,
        font=(ui._police, 12),
    ).pack(fill="x")
    ctk.CTkLabel(
        corps, textvariable=ui._studio_concurrent_etat,
        font=(ui._police, 11), text_color=ui_app.DOUX_FONCE,
        anchor="w", justify="left", wraplength=800,
    ).pack(fill="x", pady=(8, 2))
    graphique = Graphique(ui, corps, 250)
    graphique.pack(fill="x")
    ui._studio_concurrent_graph = graphique
    ui._studio_concurrent_pancartes = ctk.CTkFrame(corps, fg_color="transparent")
    ui._studio_concurrent_pancartes.pack(fill="x")
    _choisir_concurrent(ui)


def _choisir_concurrent(ui) -> None:
    graphique = ui._studio_concurrent_graph
    if graphique is None:
        return
    channel_id = ui._studio_concurrent_map.get(ui._studio_concurrent_choix.get(), "")
    if not channel_id:
        graphique.vide("Chaîne introuvable.")
        _pancartes_concurrentes(ui, {})
        return
    serie = data.serie_historique(ui.racine, channel_id)
    if not serie:
        graphique.vide("Aucune vidéo datée pour cette chaîne.")
        ui._studio_concurrent_etat.set("Aucune donnée exploitable.")
        _pancartes_concurrentes(ui, {})
        return
    points = serie.get("points") or []
    if not points:
        graphique.vide("Aucune vidéo datée pour cette chaîne.")
        ui._studio_concurrent_etat.set("Aucune vidéo datée dans le cache local.")
        return
    graphique.ligne(points, "Vues cumulées des vidéos connues")
    couverture = serie.get("couverture")
    texte = (
        f"{ui_app._nombre(serie.get('total_connu'))} vues cumulées · "
        f"{len(points)} point(s) · {serie.get('premiere')} → {serie.get('derniere')}"
    )
    if couverture is not None:
        texte += f" · couverture {couverture:.0f} % des vues totales annoncées"
    elif serie.get("chaine", {}).get("vues_totales"):
        texte += " · couverture inconnue"
    texte += " · vues actuelles, pas un historique quotidien"
    ui._studio_concurrent_etat.set(texte)
    _pancartes_concurrentes(ui, serie)


def _pancartes_concurrentes(ui, serie: dict) -> None:
    """Petites cartes des vidéos de la chaîne sélectionnée."""
    conteneur = ui._studio_concurrent_pancartes
    if conteneur is None:
        return
    for enfant in conteneur.winfo_children():
        enfant.destroy()
    videos = [video for video in serie.get("videos") or [] if isinstance(video, dict)]
    if not videos:
        return
    titre = ctk.CTkFrame(conteneur, fg_color="transparent")
    titre.pack(fill="x", pady=(14, 6))
    ctk.CTkLabel(
        titre, text="VIDÉOS POSTÉES", font=(ui._police, 10, "bold"),
        text_color=ui_app.DOUX, anchor="w",
    ).pack(side="left")
    ctk.CTkLabel(
        titre,
        text=f"{len(videos)} vidéo(s) connue(s) · les plus récentes d'abord",
        font=(ui._police, 10), text_color=ui_app.DOUX_FONCE, anchor="w",
    ).pack(side="right")
    ctk.CTkLabel(
        conteneur,
        text="Les dislikes ne sont plus exposés publiquement par YouTube depuis 2021 ; "
        "ils ne peuvent donc pas être affichés. J'aime et commentaires sont les "
        "interactions publiques disponibles.",
        font=(ui._police, 10), text_color=ui_app.ALERTE,
        anchor="w", justify="left", wraplength=820,
    ).pack(fill="x", pady=(0, 8))
    grille = ctk.CTkFrame(conteneur, fg_color="transparent")
    grille.pack(fill="x")
    for colonne in range(3):
        grille.grid_columnconfigure(colonne, weight=1, uniform="video-concurrent")
    for index, video in enumerate(videos):
        ligne, colonne = divmod(index, 3)
        bloc = ctk.CTkFrame(
            grille, fg_color=ui_app.SURFACE, corner_radius=7,
            border_width=1, border_color=ui_app.BORDURE,
        )
        bloc.grid(
            row=ligne, column=colonne, sticky="nsew",
            padx=(0, 8) if colonne < 2 else 0, pady=(0, 8),
        )
        live = str(video.get("live") or "none")
        genre = (
            "Direct" if live == "live"
            else "Replay" if live == "was_live"
            else "Short" if video.get("est_short") == 1
            else "Vidéo"
        )
        ctk.CTkLabel(
            bloc, text=genre.upper(), font=(ui._police, 9, "bold"),
            text_color=ui_app.ACCENT_TEXTE, fg_color=ui_app.ACCENT_DOUX,
            corner_radius=7, padx=7, pady=1,
        ).pack(anchor="w", padx=10, pady=(9, 3))
        titre_video = ctk.CTkLabel(
            bloc, text=str(video.get("titre") or video.get("video_id") or "Vidéo"),
            font=(ui._police, 12, "bold"), text_color=ui_app.TEXTE,
            anchor="w", justify="left", wraplength=225,
        )
        titre_video.pack(fill="x", padx=10)
        ui._auto_wrap(titre_video)
        ctk.CTkLabel(
            bloc,
            text=str(video.get("publie_le") or "date inconnue")[:10]
            + " · " + ui_app._duree(float(video.get("duree_s") or 0)),
            font=(ui._police, 10), text_color=ui_app.DOUX,
            anchor="w", justify="left",
        ).pack(fill="x", padx=10, pady=(2, 0))
        ctk.CTkLabel(
            bloc, text=ui_app._nombre(video.get("vues")) + " vues",
            font=(ui._police, 16, "bold"), text_color=ui_app.TEXTE, anchor="w",
        ).pack(fill="x", padx=10, pady=(5, 0))
        likes = video.get("likes")
        commentaires = video.get("commentaires")
        ctk.CTkLabel(
            bloc,
            text=f"J'aime {ui_app._nombre(likes) if likes is not None else '—'} · "
            f"Comm. {ui_app._nombre(commentaires) if commentaires is not None else '—'}",
            font=(ui._police, 10), text_color=ui_app.DOUX_FONCE,
            anchor="w", justify="left", wraplength=225,
        ).pack(fill="x", padx=10)
        try:
            interactions = int(video.get("likes") or 0) + int(video.get("commentaires") or 0)
        except (TypeError, ValueError):
            interactions = 0
        try:
            vues = int(video.get("vues") or 0)
        except (TypeError, ValueError):
            vues = 0
        if vues:
            ctk.CTkLabel(
                bloc,
                text=f"Taux d'interaction {interactions / vues * 100:.2f} %",
                font=(ui._police, 10), text_color=ui_app.DOUX,
                anchor="w",
            ).pack(fill="x", padx=10, pady=(1, 0))
        ui._lien(
            bloc, "Ouvrir la vidéo",
            lambda vid=str(video.get("video_id") or ""): webbrowser.open(
                "https://www.youtube.com/watch?v=" + vid
            ) if vid else None,
            width=130,
        ).pack(anchor="w", padx=10, pady=(6, 9))


def _carte_vues(ui, parent, donnees: dict) -> None:
    serie = donnees.get("serie_jours") or []
    cadre, corps = ui._carte(
        parent,
        "Vues jour par jour",
        "Le rythme de la chaîne sur la période sélectionnée.",
        cle="studio-vues",
    )
    cadre.pack(fill="x", pady=(0, 12))
    graphique = Graphique(ui, corps, 170)
    graphique.pack(fill="x")
    if serie:
        graphique.histogramme(
            [float(ligne.get("vues") or 0) for ligne in serie],
            [_date_courte(serie[0].get("jour")), _date_courte(serie[-1].get("jour"))],
        )
    else:
        graphique.vide("Aucune vue enregistrée sur la période.")


def _carte_videos(ui, parent, donnees: dict, videos: list[dict] | None = None, cle: str = "studio-videos") -> None:
    if videos is None:
        videos = [
            video for video in donnees.get("videos") or [] if isinstance(video, dict)
        ]
    cadre, corps = ui._carte(
        parent,
        "Vidéos et rétention",
        "Sélectionnez une vidéo pour afficher sa courbe de rétention. La courbe "
        "compare la part d'audience encore présente au temps écoulé de la vidéo.",
        cle=cle,
    )
    cadre.pack(fill="x", pady=(0, 12))
    if not videos:
        _texte(
            ui,
            corps,
            "Aucune vidéo avec des vues sur cette période. Essayez une période plus longue.",
            taille=12,
            couleur=ui_app.DOUX,
        )
        return

    colonnes = (
        ("titre", "Vidéo", 300, "w"),
        ("vues", "Vues", 70, "e"),
        ("minutes", "Minutes", 80, "e"),
        ("duree", "Durée", 70, "e"),
        ("pct", "% vu", 68, "e"),
        ("jaime", "J'aime", 68, "e"),
        ("commentaires", "Comm.", 64, "e"),
        ("impressions", "Impr.", 70, "e"),
        ("ctr", "CTR", 60, "e"),
    )
    arbre = ttk.Treeview(
        corps,
        columns=[colonne[0] for colonne in colonnes],
        show="headings",
        style="Outly.Treeview",
        height=min(14, max(4, len(videos))),
    )
    for cle, libelle, largeur, ancre in colonnes:
        arbre.heading(cle, text=libelle)
        arbre.column(cle, width=largeur, anchor=ancre, stretch=cle == "titre")
    arbre.pack(fill="x")
    ui._studio_arbre = arbre
    ui._studio_videos = {}
    for video in videos:
        iid = arbre.insert(
            "",
            "end",
            values=(
                str(video.get("titre") or video.get("identifiant") or "Vidéo"),
                ui_app._nombre(video.get("vues")),
                ui_app._nombre(video.get("minutes")),
                ui_app._duree(float(video.get("duree_s") or 0)),
                _pourcent(video.get("pourcentage_vu")),
                ui_app._nombre(video.get("jaime")),
                ui_app._nombre(video.get("commentaires")),
                ui_app._nombre(video.get("impressions"))
                if video.get("impressions") is not None
                else "—",
                _pourcent(video.get("ctr")) if video.get("ctr") is not None else "—",
            ),
        )
        ui._studio_videos[iid] = video
    arbre.bind("<<TreeviewSelect>>", lambda _event: _choisir_video(ui, arbre))

    ctk.CTkLabel(
        corps,
        textvariable=ui._studio_retention_titre,
        font=(ui._police, 12, "bold"),
        text_color=ui_app.DOUX,
        anchor="w",
        justify="left",
        wraplength=780,
    ).pack(anchor="w", fill="x", pady=(12, 2))
    ui._studio_retention_graph = Graphique(ui, corps, 190)
    ui._studio_retention_graph.pack(fill="x")
    ui._studio_retention_graph.vide(
        "Sélectionnez une vidéo dans le tableau pour afficher sa courbe."
    )

    premier = next(iter(ui._studio_videos))
    arbre.selection_set(premier)
    arbre.focus(premier)
    _choisir_video(ui, arbre)


def _choisir_video(ui, arbre) -> None:
    selection = arbre.selection()
    if not selection:
        return
    video = ui._studio_videos.get(selection[0])
    if not video:
        return
    ui._studio_video_courant = video
    video_id = str(video.get("identifiant") or "")
    titre = str(video.get("titre") or video_id)
    ui._studio_retention_titre.set(f"Rétention · {titre}")
    points = ui._studio_retention.get(video_id)
    if points is not None:
        _afficher_retention(ui, points)
        return
    graphique = ui._studio_retention_graph
    if graphique is not None:
        graphique.vide("Lecture de la courbe de rétention…")
    _charger_retention(ui, video)


def _afficher_retention(ui, points: list[dict]) -> None:
    graphique = ui._studio_retention_graph
    if graphique is None:
        return
    if points:
        graphique.courbe(points)
    else:
        graphique.vide(
            "YouTube ne fournit pas encore de courbe pour cette vidéo "
            "(trop peu de vues ou données trop récentes)."
        )


def _carte_repartitions(ui, parent, donnees: dict) -> None:
    grille = ctk.CTkFrame(parent, fg_color="transparent")
    grille.pack(fill="x", pady=(0, 12))
    grille.grid_columnconfigure(0, weight=1, uniform="repartition")
    grille.grid_columnconfigure(1, weight=1, uniform="repartition")

    cadre, corps = ui._carte(grille, "Sources de trafic", cle="studio-sources")
    cadre.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    graphique = Graphique(ui, corps, 120)
    graphique.pack(fill="x")
    sources = sorted(
        donnees.get("sources") or [], key=lambda ligne: float(ligne.get("valeur") or 0), reverse=True
    )
    if sources:
        graphique.barres(
            [
                (_libelle_source(str(ligne.get("cle") or "")), float(ligne.get("valeur") or 0))
                for ligne in sources[:8]
            ]
        )
    else:
        graphique.vide("Aucune source pour la période.")

    cadre, corps = ui._carte(grille, "Appareils", cle="studio-appareils")
    cadre.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    graphique = Graphique(ui, corps, 120)
    graphique.pack(fill="x")
    appareils = sorted(
        donnees.get("appareils") or [],
        key=lambda ligne: float(ligne.get("valeur") or 0),
        reverse=True,
    )
    if appareils:
        graphique.barres(
            [
                (_libelle_appareil(str(ligne.get("cle") or "")), float(ligne.get("valeur") or 0))
                for ligne in appareils[:8]
            ]
        )
    else:
        graphique.vide("Aucun appareil pour la période.")

    demographie = sorted(
        donnees.get("demographie") or [],
        key=lambda ligne: float(ligne.get("pourcentage") or 0),
        reverse=True,
    )
    cadre, corps = ui._carte(
        parent,
        "Audience : âge et genre",
        "Part des spectateurs identifiés par YouTube sur la période.",
        cle="studio-demographie",
    )
    cadre.pack(fill="x", pady=(0, 12))
    graphique = Graphique(ui, corps, 100)
    graphique.pack(fill="x")
    if demographie:
        donnees_barres = []
        for ligne in demographie[:10]:
            age = AGES.get(str(ligne.get("age") or ""), str(ligne.get("age") or "?"))
            genre = GENRES.get(str(ligne.get("genre") or ""), str(ligne.get("genre") or ""))
            donnees_barres.append(
                (f"{age} · {genre}".strip(" ·"), float(ligne.get("pourcentage") or 0))
            )
        graphique.barres(donnees_barres)
    else:
        graphique.vide("Données démographiques indisponibles pour cette période.")


def _carte_notes(ui, parent, donnees: dict) -> None:
    notes = [str(note) for note in donnees.get("notes") or [] if str(note).strip()]
    if not notes:
        return
    cadre, corps = ui._carte(
        parent,
        "Ce que YouTube n'a pas fourni",
        "Ces rapports sont facultatifs : le reste du tableau reste exploitable.",
        cle="studio-notes",
    )
    cadre.pack(fill="x", pady=(0, 12))
    for note in notes[:8]:
        ctk.CTkLabel(
            corps,
            text="•  " + note,
            font=(ui._police, 11),
            text_color=ui_app.ALERTE,
            anchor="w",
            justify="left",
            wraplength=780,
        ).pack(anchor="w", fill="x", pady=1)


def _carte_guide(ui, parent) -> None:
    cadre, corps = ui._carte(
        parent,
        "Guide d'autorisation — à faire une seule fois",
        "La rétention, les impressions et l'audience ne sont visibles que par le "
        "propriétaire de la chaîne. YouTube exige donc une autorisation OAuth "
        "en lecture seule.",
        cle="studio-guide",
    )
    cadre.pack(fill="x", pady=(0, 12))

    etapes = (
        "Créez un projet sur console.cloud.google.com, puis activez "
        "« YouTube Data API v3 » et « YouTube Analytics API » dans la bibliothèque d'API.",
        "Dans « Écran de consentement OAuth », choisissez Type « Externe », "
        "ajoutez votre compte Google comme utilisateur test. En mode Test, "
        "l'autorisation expire au bout de 7 jours ; publiez l'application pour "
        "une durée illimitée.",
        "Dans « Identifiants », créez un identifiant OAuth de type "
        "« Application de bureau ». Copiez l'identifiant client et le secret.",
        "Collez-les ci-dessus, cliquez sur « Enregistrer les identifiants », puis "
        "sur « Connecter ma chaîne ». Autorisez le compte propriétaire de la chaîne "
        "dans le navigateur.",
    )
    for numero, etape in enumerate(etapes, start=1):
        ligne = ctk.CTkFrame(corps, fg_color="transparent")
        ligne.pack(fill="x", pady=3)
        ctk.CTkLabel(
            ligne,
            text=str(numero),
            width=22,
            font=(ui._police, 11, "bold"),
            text_color=ui_app.ACCENT_TEXTE,
            fg_color=ui_app.ACCENT_DOUX,
            corner_radius=11,
        ).pack(side="left", anchor="n")
        libelle = ctk.CTkLabel(
            ligne,
            text=etape,
            font=(ui._police, 12),
            text_color=ui_app.TEXTE,
            anchor="w",
            justify="left",
            wraplength=760,
        )
        libelle.pack(side="left", padx=(8, 0), fill="x", expand=True)
        ui._auto_wrap(libelle)

    ctk.CTkLabel(
        corps,
        text="Portées demandées : lecture des rapports Analytics et lecture de la "
        "chaîne. Outly ne peut ni modifier une vidéo, ni publier, ni lire vos "
        "messages. Seul un jeton de rafraîchissement est conservé dans .env, sur ce PC.",
        font=(ui._police, 11),
        text_color=ui_app.DOUX,
        anchor="w",
        justify="left",
        wraplength=800,
    ).pack(anchor="w", fill="x", pady=(10, 6))

    liens = ctk.CTkFrame(corps, fg_color="transparent")
    liens.pack(fill="x", pady=(2, 0))
    for libelle, url in (
        ("Console Google Cloud", "https://console.cloud.google.com/"),
        (
            "Bibliothèque d'API",
            "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
        ),
        ("Identifiants OAuth", "https://console.cloud.google.com/apis/credentials"),
    ):
        ui._lien(liens, libelle, lambda u=url: webbrowser.open(u), width=190).pack(
            side="left", padx=(0, 6)
        )
    ui._lien(
        liens,
        "Guide complet",
        lambda: _ouvrir_guide(ui),
        width=150,
    ).pack(side="left")


def _ouvrir_guide(ui) -> None:
    import os
    import subprocess
    import sys

    chemin = Path(ui.racine) / "guides" / "youtube-studio.md"
    try:
        if sys.platform == "win32":
            os.startfile(str(chemin))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(chemin)])
        else:
            subprocess.Popen(["xdg-open", str(chemin)])
    except OSError as probleme:
        ui._studio_etat.set(f"Ouverture du guide impossible : {probleme}")


# ---------------------------------------------------------------------------
# Actions réseau
# ---------------------------------------------------------------------------


def _enregistrer_identifiants(ui) -> bool:
    client_id = ui._studio_client_id.get().strip()
    secret = ui._studio_client_secret.get().strip()
    if not client_id or not secret:
        ui._studio_etat.set(
            "Renseignez l'identifiant client et le secret OAuth avant d'enregistrer."
        )
        return False
    try:
        settings.ecrire_env(
            ui.racine / ".env",
            {
                "YOUTUBE_OAUTH_CLIENT_ID": client_id,
                "YOUTUBE_OAUTH_CLIENT_SECRET": secret,
            },
        )
    except settings.ErreurFichier as probleme:
        ui._studio_etat.set(f"Enregistrement impossible : {probleme}")
        return False
    ui._studio_etat.set("Identifiants OAuth enregistrés dans .env.")
    ui._message.set("Identifiants OAuth enregistrés.")
    return True


def _connecter(ui) -> None:
    if ui._studio_chargement:
        ui._studio_etat.set("Une lecture est déjà en cours.")
        return
    client_id = ui._studio_client_id.get().strip()
    secret = ui._studio_client_secret.get().strip()
    if not client_id or not secret:
        messagebox.showwarning(
            "Identifiants OAuth manquants",
            "Renseignez l'identifiant client et le secret client de type "
            "« Application de bureau », puis réessayez.\n\n"
            "Le guide ci-dessous détaille les quatre étapes.",
            parent=ui,
        )
        return
    if not _enregistrer_identifiants(ui):
        return
    jours = _jours(ui)

    def travail() -> None:
        flux = studio.FluxOauth(client_id, secret)
        try:
            flux.demarrer()
            ui._studio_file.put(
                ("etat", "Autorisez Outly dans le navigateur, puis revenez ici.")
            )
            jeton = flux.attendre()
        except Exception as probleme:  # message déjà rédigé pour l'utilisateur
            ui._studio_file.put(("erreur", f"Connexion impossible : {probleme}"))
            return
        finally:
            flux.fermer()
        try:
            settings.ecrire_env(
                ui.racine / ".env", {"YOUTUBE_REFRESH_TOKEN": jeton}
            )
        except settings.ErreurFichier as probleme:
            ui._studio_file.put(
                ("erreur", f"Autorisation obtenue mais non enregistrée : {probleme}")
            )
            return
        ui._studio_file.put(
            ("etat", "Autorisation enregistrée. Lecture des données YouTube…")
        )
        _lancer_lecture(
            ui,
            client_id=client_id,
            secret=secret,
            refresh_token=jeton,
            jours=jours,
        )

    threading.Thread(target=travail, name="outly-studio-oauth", daemon=True).start()


def _charger(ui) -> None:
    env = settings.lire_env(ui.racine / ".env")
    identifiants = studio.identifiants(env)
    if not identifiants["refresh_token"]:
        ui._studio_etat.set(
            "Connectez d'abord votre chaîne : l'autorisation OAuth est nécessaire "
            "pour lire les statistiques privées."
        )
        return
    if not identifiants["client_id"] or not identifiants["client_secret"]:
        ui._studio_etat.set(
            "Identifiants OAuth manquants dans .env : renseignez-les puis enregistrez."
        )
        return
    if ui._studio_chargement:
        ui._studio_etat.set("Une lecture est déjà en cours.")
        return
    ui._studio_chargement = True
    ui._studio_etat.set("Lecture des données YouTube…")
    _lancer_lecture(
        ui,
        client_id=identifiants["client_id"],
        secret=identifiants["client_secret"],
        refresh_token=identifiants["refresh_token"],
        jours=_jours(ui),
    )


def _lancer_lecture(
    ui, *, client_id: str, secret: str, refresh_token: str, jours: int
) -> None:
    ui._studio_chargement = True

    def rafraichir() -> str:
        return studio.rafraichir_jeton(client_id, secret, refresh_token)

    def travail() -> None:
        try:
            jeton = rafraichir()
            client = studio.StudioClient(jeton, rafraichir=rafraichir)
            tableau = studio.collecter(client, jours=jours)
            studio.ecrire_cache(ui.racine, tableau)
        except Exception as probleme:  # toute erreur doit revenir à l'interface
            ui._studio_file.put(("erreur", f"Lecture impossible : {probleme}"))
            return
        ui._studio_file.put(("tableau", (client, tableau)))

    threading.Thread(target=travail, name="outly-studio-lecture", daemon=True).start()


def _charger_retention(ui, video: dict) -> None:
    client = ui._studio_client
    donnees = ui._studio_donnees or {}
    video_id = str(video.get("identifiant") or "")
    if client is None or not video_id:
        graphique = ui._studio_retention_graph
        if graphique is not None:
            graphique.vide("Reconnectez la chaîne pour lire la rétention.")
        return
    debut = str(donnees.get("debut") or "")
    fin = str(donnees.get("fin") or "")
    if not debut or not fin:
        return
    if video_id in ui._studio_retention_en_cours:
        return
    ui._studio_retention_en_cours.add(video_id)

    def travail() -> None:
        try:
            points = studio.retention(client, video_id, debut, fin)
        except Exception as probleme:
            ui._studio_file.put(
                ("retention", (video_id, [], f"Rétention indisponible : {probleme}"))
            )
            return
        ui._studio_file.put(("retention", (video_id, points, None)))

    threading.Thread(target=travail, name="outly-studio-retention", daemon=True).start()


def _deconnecter(ui) -> None:
    if not messagebox.askyesno(
        "Déconnecter YouTube",
        "Retirer l'autorisation enregistrée sur ce PC ?\n\n"
        "Les données déjà téléchargées restent affichées. Vous pourrez reconnecter "
        "la chaîne à tout moment.",
        parent=ui,
    ):
        return
    try:
        settings.ecrire_env(ui.racine / ".env", {"YOUTUBE_REFRESH_TOKEN": ""})
    except settings.ErreurFichier as probleme:
        ui._studio_etat.set(f"Déconnexion impossible : {probleme}")
        return
    ui._studio_client = None
    ui._studio_etat.set("Chaîne déconnectée. Le cache local reste disponible.")
    ui._message.set("Autorisation YouTube retirée de .env.")
    _remplir(ui)
