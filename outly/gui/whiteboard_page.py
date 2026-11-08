"""Tableau blanc local : dessin libre, formes et navigation infinie."""
from __future__ import annotations

import copy
import json
import math
import os
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog

import customtkinter as ctk

from . import app as ui_app

COULEURS = {
    "Accent": ui_app.ACCENT,
    "Corail": "#E4573D",
    "Orange": "#F08A24",
    "Ambre": "#D9A400",
    "Vert": ui_app.OK,
    "Turquoise": "#17A398",
    "Bleu": "#3B82F6",
    "Indigo": "#6366F1",
    "Violet": "#8B5CF6",
    "Rose": "#EC4899",
    "Brun": "#8B5E3C",
    "Blanc": "#FFFFFF",
}
OUTILS = [
    ("Stylo", "pen"),
    ("Ligne", "line"),
    ("Flèche", "arrow"),
    ("Post-it", "note"),
    ("Texte", "text"),
    ("Gomme", "erase"),
    ("Forme", "shape"),
]
FORMES = {
    "Rectangle": "rect",
    "Ellipse": "ellipse",
    "Triangle": "triangle",
    "Losange": "losange",
    "Pentagone": "pentagone",
    "Hexagone": "hexagone",
    "Octogone": "octogone",
    "Étoile": "etoile",
    "Parallélogramme": "parallelogramme",
    "Trapèze": "trapeze",
    "Croix": "croix",
    "Double flèche": "double_fleche",
}
POLYGONES = {
    "triangle",
    "losange",
    "pentagone",
    "hexagone",
    "octogone",
    "etoile",
    "parallelogramme",
    "trapeze",
    "croix",
}


def _chemin(racine):
    return Path(racine) / "data" / "tableau-blanc.json"


def _distance_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _densifier(points, pas):
    """Ajoute des points intermédiaires pour que la gomme coupe sans perdre un côté."""
    if len(points) < 2:
        return list(points)
    resultat = []
    for index, point in enumerate(points):
        if index == 0:
            resultat.append(point)
            continue
        precedent = points[index - 1]
        distance = math.hypot(float(point[0]) - float(precedent[0]), float(point[1]) - float(precedent[1]))
        etapes = max(1, math.ceil(distance / max(1.0, pas)))
        for etape in range(1, etapes + 1):
            t = etape / etapes
            resultat.append([
                float(precedent[0]) + (float(point[0]) - float(precedent[0])) * t,
                float(precedent[1]) + (float(point[1]) - float(precedent[1])) * t,
            ])
    return resultat


def _forme_points(code, x1, y1, x2, y2):
    """Points d'un polygone dans la boîte (x1,y1)-(x2,y2)."""
    gauche, droite = sorted((x1, x2))
    haut, bas = sorted((y1, y2))
    largeur, hauteur = droite - gauche, bas - haut
    cx, cy = gauche + largeur / 2, haut + hauteur / 2
    rx, ry = largeur / 2, hauteur / 2
    if code == "losange":
        return [cx, haut, droite, cy, cx, bas, gauche, cy]
    if code == "parallelogramme":
        return [
            gauche + largeur * 0.22, haut,
            droite, haut,
            droite - largeur * 0.22, bas,
            gauche, bas,
        ]
    if code == "trapeze":
        return [
            gauche + largeur * 0.22, haut,
            droite - largeur * 0.22, haut,
            droite, bas,
            gauche, bas,
        ]
    if code == "croix":
        dx, dy = largeur * 0.32, hauteur * 0.32
        return [
            gauche + dx, haut, droite - dx, haut, droite - dx, haut + dy,
            droite, haut + dy, droite, bas - dy, droite - dx, bas - dy,
            droite - dx, bas, gauche + dx, bas, gauche + dx, bas - dy,
            gauche, bas - dy, gauche, haut + dy, gauche + dx, haut + dy,
        ]
    if code == "etoile":
        points = []
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            facteur = 1.0 if index % 2 == 0 else 0.45
            points.extend([cx + math.cos(angle) * rx * facteur, cy + math.sin(angle) * ry * facteur])
        return points
    nombre = {"triangle": 3, "pentagone": 5, "hexagone": 6, "octogone": 8}.get(code, 4)
    points = []
    for index in range(nombre):
        angle = -math.pi / 2 + index * 2 * math.pi / nombre
        points.extend([cx + math.cos(angle) * rx, cy + math.sin(angle) * ry])
    return points


class TableauBlanc(ctk.CTkFrame):
    def __init__(self, ui, parent):
        super().__init__(parent, fg_color="transparent")
        self.ui, self.racine = ui, Path(ui.racine)
        self.outil = tk.StringVar(value="pen")
        self.forme = tk.StringVar(value="Rectangle")
        self.couleur = tk.StringVar(value="Accent")
        self.epaisseur = tk.StringVar(value="3")
        self.objets, self._undo, self._redo = [], [], []
        self._actif = None
        self._debut = self._pan = None
        self._ox, self._oy, self._zoom = 80.0, 70.0, 1.0
        self._timer = None
        self._gomme_snapshot = False
        self._btn_annuler = self._btn_retablir = None
        self._construire_barre()
        self.canvas = tk.Canvas(
            self,
            bg=ui_app._couleur(ui_app.SURFACE),
            highlightthickness=1,
            highlightbackground=ui_app._couleur(ui_app.BORDURE),
            cursor="crosshair",
            takefocus=True,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._presse)
        self.canvas.bind("<B1-Motion>", self._glisse)
        self.canvas.bind("<ButtonRelease-1>", self._relache)
        self.canvas.bind("<ButtonPress-2>", self._pan_debut)
        self.canvas.bind("<B2-Motion>", self._pan_glisse)
        self.canvas.bind("<ButtonRelease-2>", self._pan_fin)
        self.canvas.bind("<ButtonPress-3>", self._pan_debut)
        self.canvas.bind("<B3-Motion>", self._pan_glisse)
        self.canvas.bind("<ButtonRelease-3>", self._pan_fin)
        self.canvas.bind("<MouseWheel>", self._molette)
        self.canvas.bind("<Control-z>", lambda _e: self.annuler())
        self.canvas.bind("<Control-y>", lambda _e: self.retablir())
        self.canvas.bind("<Control-Shift-Z>", lambda _e: self.retablir())
        self.canvas.bind("<Configure>", lambda _e: self.redessiner())
        self.charger()
        self.redessiner()
        self._maj_historique()

    def _construire_barre(self):
        barre = ctk.CTkFrame(self, fg_color=ui_app.SURFACE, corner_radius=6)
        barre.pack(fill="x", pady=(0, 8))
        outils = ctk.CTkFrame(barre, fg_color="transparent")
        outils.pack(fill="x", padx=8, pady=(7, 3))
        for libelle, valeur in OUTILS:
            ctk.CTkRadioButton(
                outils,
                text=libelle,
                variable=self.outil,
                value=valeur,
                font=(self.ui._police, 11),
                text_color=ui_app.TEXTE,
                fg_color=ui_app.ACCENT,
                hover_color=ui_app.ACCENT_SURVOL,
                border_color=ui_app.BORDURE,
            ).pack(side="left", padx=(0, 7))
        reglages = ctk.CTkFrame(barre, fg_color="transparent")
        reglages.pack(fill="x", padx=8, pady=(0, 7))
        ctk.CTkLabel(
            reglages, text="Couleur", font=(self.ui._police, 10), text_color=ui_app.DOUX
        ).pack(side="left", padx=(0, 3))
        ctk.CTkOptionMenu(
            reglages,
            values=list(COULEURS),
            variable=self.couleur,
            width=104,
            height=26,
            fg_color=ui_app.CHAMP,
            button_color=ui_app.SURFACE_HAUTE,
            button_hover_color=ui_app.BORDURE,
            text_color=ui_app.TEXTE,
            font=(self.ui._police, 10),
        ).pack(side="left", padx=(0, 7))
        ctk.CTkLabel(
            reglages, text="Trait", font=(self.ui._police, 10), text_color=ui_app.DOUX
        ).pack(side="left", padx=(0, 3))
        ctk.CTkOptionMenu(
            reglages,
            values=["2", "3", "5", "8", "12"],
            variable=self.epaisseur,
            width=58,
            height=26,
            fg_color=ui_app.CHAMP,
            button_color=ui_app.SURFACE_HAUTE,
            button_hover_color=ui_app.BORDURE,
            text_color=ui_app.TEXTE,
            font=(self.ui._police, 10),
        ).pack(side="left", padx=(0, 7))
        ctk.CTkLabel(
            reglages, text="Forme", font=(self.ui._police, 10), text_color=ui_app.DOUX
        ).pack(side="left", padx=(0, 3))
        ctk.CTkOptionMenu(
            reglages,
            values=list(FORMES),
            variable=self.forme,
            command=self._choisir_forme,
            width=142,
            height=26,
            fg_color=ui_app.CHAMP,
            button_color=ui_app.SURFACE_HAUTE,
            button_hover_color=ui_app.BORDURE,
            text_color=ui_app.TEXTE,
            font=(self.ui._police, 10),
        ).pack(side="left", padx=(0, 7))
        self._btn_annuler = ctk.CTkButton(
            reglages, text="↶ Annuler", command=self.annuler, width=76, height=26,
            fg_color="transparent", hover_color=ui_app.SURFACE_HAUTE, border_width=1,
            border_color=ui_app.BORDURE, text_color=ui_app.DOUX,
            font=(self.ui._police, 10),
        )
        self._btn_annuler.pack(side="left", padx=2)
        self._btn_retablir = ctk.CTkButton(
            reglages, text="↷ Rétablir", command=self.retablir, width=80, height=26,
            fg_color="transparent", hover_color=ui_app.SURFACE_HAUTE, border_width=1,
            border_color=ui_app.BORDURE, text_color=ui_app.DOUX,
            font=(self.ui._police, 10),
        )
        self._btn_retablir.pack(side="left", padx=2)
        for texte, action in (("Recentrer", self.recentrer), ("Effacer", self.effacer)):
            ctk.CTkButton(
                reglages, text=texte, command=action, width=78, height=26,
                fg_color="transparent", hover_color=ui_app.SURFACE_HAUTE, border_width=1,
                border_color=ui_app.BORDURE, text_color=ui_app.DOUX,
                font=(self.ui._police, 10),
            ).pack(side="left", padx=2)

    def _choisir_forme(self, _valeur=None):
        self.outil.set("shape")

    def _ecran(self, x, y):
        return x * self._zoom + self._ox, y * self._zoom + self._oy

    def _monde(self, x, y):
        return (x - self._ox) / self._zoom, (y - self._oy) / self._zoom

    def _couleur_active(self):
        return self.couleur.get()

    def _epaisseur_active(self):
        try:
            return max(1, int(self.epaisseur.get()))
        except ValueError:
            return 3

    def _forme_code(self):
        return FORMES.get(self.forme.get(), "rect")

    def charger(self):
        try:
            charge = json.loads(_chemin(self.racine).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            charge = {}
        if not isinstance(charge, dict):
            charge = {}
        self.objets = [o for o in charge.get("objets") or [] if isinstance(o, dict)]
        self._ox = float(charge.get("ox") or 80)
        self._oy = float(charge.get("oy") or 70)
        self._zoom = max(0.25, min(3.0, float(charge.get("zoom") or 1)))

    def sauvegarder(self):
        chemin = _chemin(self.racine)
        temporaire = chemin.with_name(f".{chemin.name}.{os.getpid()}.tmp")
        try:
            chemin.parent.mkdir(parents=True, exist_ok=True)
            temporaire.write_text(
                json.dumps(
                    {"objets": self.objets, "ox": self._ox, "oy": self._oy, "zoom": self._zoom},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporaire, chemin)
        except OSError as probleme:
            try:
                temporaire.unlink(missing_ok=True)
            except OSError:
                pass
            self.ui._message.set(f"Sauvegarde du tableau impossible : {probleme}")

    def _planifier(self):
        if self._timer:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
        self._timer = self.after(350, self.sauvegarder)

    def _memo(self):
        self._undo.append(copy.deepcopy(self.objets))
        self._undo = self._undo[-60:]
        self._redo.clear()
        self._maj_historique()

    def _maj_historique(self):
        if self._btn_annuler is not None:
            self._btn_annuler.configure(state="normal" if self._undo else "disabled")
        if self._btn_retablir is not None:
            self._btn_retablir.configure(state="normal" if self._redo else "disabled")

    def annuler(self):
        if not self._undo:
            return
        self._redo.append(copy.deepcopy(self.objets))
        self.objets = self._undo.pop()
        self.redessiner()
        self._planifier()
        self._maj_historique()

    def retablir(self):
        if not self._redo:
            return
        self._undo.append(copy.deepcopy(self.objets))
        self.objets = self._redo.pop()
        self.redessiner()
        self._planifier()
        self._maj_historique()

    def effacer(self):
        if self.objets:
            self._memo()
            self.objets.clear()
            self.redessiner()
            self._planifier()

    def recentrer(self):
        self._ox, self._oy, self._zoom = 80.0, 70.0, 1.0
        self.redessiner()
        self._planifier()

    def redessiner(self):
        self.canvas.delete("all")
        largeur = max(1, self.canvas.winfo_width())
        hauteur = max(1, self.canvas.winfo_height())
        pas = 40 * self._zoom
        if pas >= 9:
            x = self._ox % pas
            while x < largeur:
                self.canvas.create_line(x, 0, x, hauteur, fill=ui_app._couleur(ui_app.BORDURE))
                x += pas
            y = self._oy % pas
            while y < hauteur:
                self.canvas.create_line(0, y, largeur, y, fill=ui_app._couleur(ui_app.BORDURE))
                y += pas
        for objet in self.objets:
            self._dessiner(objet)
        self.canvas.create_text(
            14,
            hauteur - 14,
            anchor="sw",
            text="Clic droit : déplacer · molette : zoomer · gomme : glisser sur le trait",
            fill=ui_app._couleur(ui_app.DOUX),
            font=(self.ui._police, 10),
        )

    def _couleur_objet(self, objet):
        couleur = objet.get("couleur") or "Accent"
        if isinstance(couleur, (tuple, list)):
            return couleur[1] if ctk.get_appearance_mode() == "Dark" and len(couleur) > 1 else couleur[0]
        if couleur in COULEURS:
            return ui_app._couleur(COULEURS[couleur])
        return couleur if isinstance(couleur, str) else ui_app._couleur(ui_app.ACCENT)

    def _dessiner(self, objet):
        typ = objet.get("type")
        points = objet.get("points") or []
        couleur = self._couleur_objet(objet)
        ep = max(1, int(objet.get("epaisseur") or 3))
        ecran = [self._ecran(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
        if not ecran:
            return
        if typ == "pen" and len(ecran) >= 2:
            self.canvas.create_line(
                [c for p in ecran for c in p],
                fill=couleur,
                width=ep,
                smooth=True,
                capstyle="round",
            )
        elif typ in ("line", "arrow", "double_fleche") and len(ecran) >= 2:
            fleche = "both" if typ == "double_fleche" else ("last" if typ == "arrow" else None)
            self.canvas.create_line(
                *ecran[0], *ecran[-1], fill=couleur, width=ep, arrow=fleche
            )
        elif typ in ("rect", "ellipse", "note"):
            if len(ecran) < 2:
                return
            x1, y1 = ecran[0]
            x2, y2 = ecran[-1]
            if typ == "ellipse":
                self.canvas.create_oval(x1, y1, x2, y2, outline=couleur, width=ep)
            elif typ == "note":
                self.canvas.create_rectangle(
                    x1, y1, x2, y2, fill="#F6D98B", outline="#B89B52", width=ep
                )
                if objet.get("texte"):
                    self.canvas.create_text(
                        x1 + 10,
                        y1 + 10,
                        anchor="nw",
                        text=objet["texte"],
                        fill="#2B2114",
                        width=max(40, abs(x2 - x1) - 20),
                        font=(self.ui._police, 12),
                    )
            else:
                self.canvas.create_rectangle(x1, y1, x2, y2, outline=couleur, width=ep)
        elif typ in POLYGONES and len(ecran) >= 2:
            x1, y1 = ecran[0]
            x2, y2 = ecran[-1]
            self.canvas.create_polygon(
                _forme_points(typ, x1, y1, x2, y2),
                fill="",
                outline=couleur,
                width=ep,
            )
        elif typ == "text" and objet.get("texte"):
            self.canvas.create_text(
                *ecran[0],
                anchor="nw",
                text=objet["texte"],
                fill=couleur,
                font=(self.ui._police, 14),
                width=520,
            )

    def _objet_touche(self, objet, x, y, rayon):
        typ = objet.get("type")
        points = [p for p in objet.get("points") or [] if len(p) >= 2]
        if not points:
            return False
        if typ in ("line", "arrow", "double_fleche") and len(points) >= 2:
            return (
                _distance_segment(
                    x, y,
                    float(points[0][0]), float(points[0][1]),
                    float(points[-1][0]), float(points[-1][1]),
                )
                <= rayon
            )
        if typ == "text":
            px, py = float(points[0][0]), float(points[0][1])
            return px - rayon <= x <= px + 420 and py - rayon <= y <= py + 90
        if len(points) < 2:
            return math.hypot(x - float(points[0][0]), y - float(points[0][1])) <= rayon
        x1, y1 = float(points[0][0]), float(points[0][1])
        x2, y2 = float(points[-1][0]), float(points[-1][1])
        return (
            min(x1, x2) - rayon <= x <= max(x1, x2) + rayon
            and min(y1, y2) - rayon <= y <= max(y1, y2) + rayon
        )

    def _effacer_au_point(self, x, y):
        """Gomme réelle : retire la partie du tracé sous le pointeur."""
        rayon = max(6.0, self._epaisseur_active() + 6.0)
        nouveaux = []
        modifie = False
        for objet in self.objets:
            if objet.get("type") != "pen":
                if self._objet_touche(objet, x, y, rayon):
                    modifie = True
                else:
                    nouveaux.append(objet)
                continue
            morceaux = []
            courant = []
            points = _densifier(objet.get("points") or [], max(2.0, rayon / 2))
            for point in points:
                if len(point) < 2:
                    continue
                if math.hypot(x - float(point[0]), y - float(point[1])) <= rayon:
                    if len(courant) >= 2:
                        morceaux.append(courant)
                    courant = []
                    modifie = True
                else:
                    courant.append(point)
            if len(courant) >= 2:
                morceaux.append(courant)
            for morceau in morceaux:
                copie = dict(objet)
                copie["points"] = morceau
                nouveaux.append(copie)
        if modifie:
            if not self._gomme_snapshot:
                self._memo()
                self._gomme_snapshot = True
            self.objets = nouveaux
        return modifie

    def _presse(self, event):
        if event.num in (2, 3):
            return
        self.canvas.focus_set()
        x, y = self._monde(event.x, event.y)
        outil = self.outil.get()
        if outil == "erase":
            self._gomme_snapshot = False
            if self._effacer_au_point(x, y):
                self.redessiner()
                self._planifier()
            return
        if outil == "text":
            texte = simpledialog.askstring("Texte", "Texte à placer :", parent=self)
            if texte and texte.strip():
                self._memo()
                self.objets.append({
                    "type": "text",
                    "points": [[x, y]],
                    "couleur": self._couleur_active(),
                    "epaisseur": self._epaisseur_active(),
                    "texte": texte.strip(),
                })
                self.redessiner()
                self._planifier()
            return
        texte = ""
        if outil == "note":
            texte = simpledialog.askstring("Post-it", "Texte du post-it :", parent=self)
            if texte is None:
                return
        self._debut = (x, y)
        self._memo()
        if outil == "pen":
            self._actif = {
                "type": "pen",
                "points": [[x, y]],
                "couleur": self._couleur_active(),
                "epaisseur": self._epaisseur_active(),
            }
        else:
            self._actif = {
                "type": self._forme_code() if outil == "shape" else outil,
                "points": [[x, y], [x, y]],
                "couleur": self._couleur_active(),
                "epaisseur": self._epaisseur_active(),
            }
            if outil == "note":
                self._actif["texte"] = texte
        self.objets.append(self._actif)
        self.redessiner()

    def _glisse(self, event):
        x, y = self._monde(event.x, event.y)
        if self.outil.get() == "erase" and self._actif is None:
            if self._effacer_au_point(x, y):
                self.redessiner()
                self._planifier()
            return
        if self._actif is None or self._debut is None:
            return
        if self._actif["type"] == "pen":
            self._actif["points"].append([x, y])
        else:
            self._actif["points"][-1] = [x, y]
        self.redessiner()

    def _relache(self, _event):
        if self.outil.get() == "erase":
            self._gomme_snapshot = False
            return
        if self._actif is not None:
            if len(self._actif.get("points") or []) < 2:
                self.objets.remove(self._actif)
            self._actif = self._debut = None
            self.redessiner()
            self._planifier()

    def _pan_debut(self, event):
        self._pan = (event.x, event.y)

    def _pan_glisse(self, event):
        if self._pan is None:
            return
        dx, dy = event.x - self._pan[0], event.y - self._pan[1]
        self._ox += dx
        self._oy += dy
        self._pan = (event.x, event.y)
        self.redessiner()

    def _pan_fin(self, _event):
        self._pan = None
        self._planifier()

    def _molette(self, event):
        avant = self._monde(event.x, event.y)
        facteur = 1.12 if event.delta > 0 else 1 / 1.12
        self._zoom = max(0.25, min(3.0, self._zoom * facteur))
        apres = self._ecran(*avant)
        self._ox += event.x - apres[0]
        self._oy += event.y - apres[1]
        self.redessiner()
        self._planifier()


def construire(ui):
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["tableau"] = page
    entete = ctk.CTkFrame(page, fg_color="transparent")
    entete.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        entete,
        text="Tableau blanc",
        font=(ui._police, 20, "bold"),
        text_color=ui_app.TEXTE,
        anchor="w",
    ).pack(side="left")
    ui._bouton(
        entete, "Retour aux notes", lambda: ui._afficher_page("notes"), width=140
    ).pack(side="right")
    ui._bandeau_parcours(
        page,
        "Tableau blanc",
        [
            ("Choisir un outil", None),
            ("Dessiner ou poser une forme", None),
            ("Clic droit pour explorer", None),
        ],
        lien=("Ouvrir mes notes", lambda: ui._afficher_page("notes")),
    )
    ui._tableau_blanc = TableauBlanc(ui, page)
    ui._tableau_blanc.pack(fill="both", expand=True)
