"""Carte 3D du cerveau : notes, hooks et mots-clés reliés.

Rien n'est envoyé au LLM : la carte est calculée localement, à partir des
notes et des hooks de la base, de `keywords.txt` et des mots-clés déclarés par
les chaînes concurrentes. Chaque point est une information ; un trait relie
deux points qui se répondent (tag commun, mot partagé, mot-clé observé sur une
chaîne).

La disposition est déterministe : chaque famille occupe une sphère de rayon
différent (notes au centre, hooks générés plus près, mots-clés puis chaînes
autour). On obtient une carte lisible sans simulation de forces coûteuse.
"""

from __future__ import annotations

import math
import random
import re
import sqlite3
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path

import customtkinter as ctk

from .. import db, hooks, notes

FOND_CANVAS = "#120A0F"
GRILLE = "#24131B"
GRILLE_FORTE = "#351B25"
TRAIT = "#5A3038"
TRAIT_ACTIF = "#F26B4F"
TEXTE = "#F7E9E1"
TEXTE_DOUX = "#D3ADA8"
ACCENT = "#F26B4F"
AMBRE = "#F0C070"
BORDURE_HUD = "#4A202A"

COULEURS = {
    "note": "#FFB09D",
    "hook": "#F0C070",
    "concept": "#74D6B4",
    "sujet": "#FF8B72",
    "idee": "#E3C7BF",
    "reference": "#BE938F",
    "mot": "#E8B86A",
    "chaine": "#74D6B4",
    "hook_genere": "#F0C070",
}

LIBELLES = {
    "note": "Notes",
    "mot": "Mots-clés",
    "chaine": "Chaînes",
    "hook_genere": "Hooks générés",
}

FAMILLES_NOTES = {"note", "hook", "concept", "sujet", "idee", "reference"}
RAYONS = {"note": 1.0, "hook_genere": 0.55, "mot": 1.5, "chaine": 1.85}

MOTIF_MOT = re.compile(r"\w{4,}", re.UNICODE)
MOTS_VIDES = {
    "dans", "avec", "pour", "leur", "votre", "vous", "nous", "cette", "plus",
    "sans", "être", "fait", "tout", "tous", "toute", "elles", "ils", "une",
    "des", "les", "que", "qui", "sur", "par", "est", "sont", "aux", "ses",
    "son", "sa", "mon", "mais", "comme", "aussi", "peut", "faire", "dont",
    "entre", "deux", "trois", "alors", "donc", "quand", "très", "bien",
    "chez", "cela", "c'est", "n'est", "qu'il", "qu'on", "être",
}


@dataclass
class Noeud:
    """Un point de la carte : sa nature, son libellé et sa position."""

    cle: str
    label: str
    categorie: str
    detail: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    degre: int = 0

    @property
    def famille(self) -> str:
        return "note" if self.categorie in FAMILLES_NOTES else self.categorie


def _jetons(texte: str) -> set[str]:
    return {
        mot.group(0)
        for mot in MOTIF_MOT.finditer((texte or "").casefold())
        if mot.group(0) not in MOTS_VIDES
    }


def _categorie(type_note: str) -> str:
    return type_note if type_note in COULEURS else "note"


def construire(
    racine: Path,
    limite_notes: int = 40,
    limite_mots: int = 16,
    limite_chaines: int = 10,
    limite_hooks: int = 5,
) -> tuple[list[Noeud], list[tuple[int, int]]]:
    """Assemble le petit réseau du cerveau, sans aucun appel réseau."""
    racine = Path(racine)
    noeuds: list[Noeud] = []
    index: dict[str, int] = {}
    liens: set[tuple[int, int]] = set()

    def ajouter(cle: str, label: str, categorie: str, detail: str = "") -> int:
        if cle in index:
            return index[cle]
        index[cle] = len(noeuds)
        noeuds.append(Noeud(cle=cle, label=label, categorie=categorie, detail=detail))
        return index[cle]

    def relier(a: int | None, b: int | None) -> None:
        if a is None or b is None or a == b:
            return
        liens.add((min(a, b), max(a, b)))

    # 1. Les notes : la matière principale.
    try:
        liste_notes = notes.lister(racine, inclure_archivees=True, limite=limite_notes)
    except Exception:
        liste_notes = []

    jetons_notes: dict[int, set[str]] = {}
    par_tag: dict[str, list[int]] = {}
    for note in liste_notes:
        position = ajouter(
            f"note:{note.id}",
            note.titre or "Note sans titre",
            _categorie(note.type),
            note.etiquette_type,
        )
        jetons_notes[position] = _jetons(
            " ".join([note.titre, note.contenu, " ".join(note.tags)])
        )
        for tag in note.tags:
            par_tag.setdefault(tag.casefold(), []).append(position)
    for positions in par_tag.values():
        for autre in positions[1:3]:
            relier(positions[0], autre)

    # 2. Les mots-clés de la veille.
    mots: dict[str, int] = {}
    try:
        contenu = (racine / "keywords.txt").read_text(encoding="utf-8-sig")
    except OSError:
        contenu = ""
    for brute in contenu.splitlines():
        mot = brute.strip()
        if not mot or mot.startswith("#"):
            continue
        cle = mot.casefold()
        if cle in mots:
            continue
        mots[cle] = ajouter(f"mot:{cle}", mot, "mot", "mot-clé de votre veille")
        if len(mots) >= limite_mots:
            break

    # 3. Les chaînes concurrentes, leurs mots-clés et les hooks générés.
    connexion = None
    try:
        fichier = racine / "data" / "radar.sqlite3"
        if fichier.exists():
            connexion = db.connexion(fichier)
            db.migrer(connexion)
            lignes = connexion.execute(
                """
                SELECT channel_id, titre, mots_cles_chaine
                FROM channels
                WHERE statut IN ('enrichie', 'scored', 'ecartee')
                ORDER BY COALESCE(score_heuristique, 0) DESC,
                         COALESCE(abonnes, 0) DESC
                LIMIT ?
                """,
                (limite_chaines,),
            ).fetchall()
            for ligne in lignes:
                chaine = ajouter(
                    f"chaine:{ligne['channel_id']}",
                    ligne["titre"] or "Chaîne",
                    "chaine",
                    "chaîne concurrente",
                )
                for morceau in re.split(r"[,;]", ligne["mots_cles_chaine"] or ""):
                    mot = morceau.strip().strip('"').strip()
                    if len(mot) < 3:
                        continue
                    cle = mot.casefold()
                    position = mots.get(cle)
                    if position is None:
                        position = ajouter(
                            f"mot:{cle}", mot, "mot", "mot-clé observé chez une chaîne"
                        )
                        mots[cle] = position
                    relier(chaine, position)

            jeu = hooks.dernier_jeu(connexion)
            for rang, variante in enumerate((jeu or {}).get("variantes") or []):
                if rang >= limite_hooks:
                    break
                texte = " ".join(str(variante.get("hook") or "").split())
                if len(texte) < 10:
                    continue
                label = texte[:54] + ("…" if len(texte) > 54 else "")
                position = ajouter(f"hook:{rang}", label, "hook_genere", "hook généré")
                jetons = _jetons(texte)
                meilleur, score = None, 0
                for note_position, autres in jetons_notes.items():
                    commun = len(jetons & autres)
                    if commun > score:
                        meilleur, score = note_position, commun
                if score >= 2:
                    relier(position, meilleur)
    except sqlite3.Error:
        pass
    finally:
        if connexion is not None:
            connexion.close()

    # 4. Un mot-clé connu de votre veille relie les notes qui le contiennent.
    for cle, position in mots.items():
        morceaux = _jetons(cle)
        if not morceaux:
            continue
        for note_position, jetons in jetons_notes.items():
            if morceaux <= jetons:
                relier(position, note_position)

    # 5. Degré de chaque point : il commande la taille du cercle.
    degres = [0] * len(noeuds)
    for a, b in liens:
        degres[a] += 1
        degres[b] += 1
    for noeud, degre in zip(noeuds, degres):
        noeud.degre = degre
    return noeuds, sorted(liens)


def disposer(noeuds: list[Noeud]) -> None:
    """Répartit les points en coquilles concentriques, sans hasard subi."""
    if not noeuds:
        return
    generateur = random.Random(7)
    familles: dict[str, list[Noeud]] = {}
    for noeud in noeuds:
        familles.setdefault(noeud.famille, []).append(noeud)
    for famille, liste in familles.items():
        rayon = RAYONS.get(famille, 1.0)
        for index, noeud in enumerate(liste):
            part = (index + 0.5) / max(1, len(liste))
            phi = math.acos(max(-1.0, min(1.0, 1 - 2 * part)))
            theta = math.pi * (1 + 5**0.5) * index
            noeud.x = rayon * math.sin(phi) * math.cos(theta) + generateur.uniform(-0.05, 0.05)
            noeud.y = rayon * math.cos(phi) * 0.8 + generateur.uniform(-0.05, 0.05)
            noeud.z = rayon * math.sin(phi) * math.sin(theta) + generateur.uniform(-0.05, 0.05)


class CarteCerveau(ctk.CTkFrame):
    """Carte tournable : glisser pour pivoter, molette pour zoomer."""

    def __init__(
        self,
        parent: tk.Misc,
        police: str,
        racine: Path,
        hauteur: int = 400,
        mono: str | None = None,
    ):
        super().__init__(parent, fg_color="transparent")
        self._police_ui = police
        self._mono = mono or "Consolas"
        self._racine = Path(racine)
        self._noeuds: list[Noeud] = []
        self._liens: list[tuple[int, int]] = []
        self._proj: list[tuple[float, float, float, float]] = []
        self._yaw = 0.55
        self._pitch = 0.32
        self._zoom = 1.0
        self._selection: int | None = None
        self._survol: int | None = None
        self._origine: tuple[int, int] | None = None
        self._bouge = False
        self._derniere_taille = (0, 0)

        barre = ctk.CTkFrame(self, fg_color="transparent")
        barre.pack(fill="x", pady=(0, 4))
        self._compte = tk.StringVar(value="Calcul de la carte…")
        ctk.CTkLabel(
            barre,
            textvariable=self._compte,
            font=(police, 12),
            text_color=("#7A4A55", "#D3ADA8"),
            anchor="w",
        ).pack(side="left")
        ctk.CTkButton(
            barre,
            text="Recentrer",
            command=self._recentrer,
            width=92,
            height=26,
            corner_radius=5,
            fg_color="transparent",
            hover_color=("#FBE7DF", "#3A1D22"),
            border_width=1,
            border_color=("#EBD8CA", "#402530"),
            text_color=("#9C2F22", "#FFB09D"),
            font=(police, 12),
        ).pack(side="right")
        ctk.CTkButton(
            barre,
            text="Recalculer",
            command=self.rafraichir,
            width=96,
            height=26,
            corner_radius=5,
            fg_color="transparent",
            hover_color=("#FBE7DF", "#3A1D22"),
            border_width=1,
            border_color=("#EBD8CA", "#402530"),
            text_color=("#9C2F22", "#FFB09D"),
            font=(police, 12),
        ).pack(side="right", padx=(0, 8))

        self._canvas = tk.Canvas(
            self, height=hauteur, background=FOND_CANVAS, highlightthickness=0, bd=0
        )
        self._canvas.pack(fill="both", expand=True, pady=(6, 0))
        self._canvas.bind("<Configure>", self._redimensionner)
        self._canvas.bind("<ButtonPress-1>", self._presser)
        self._canvas.bind("<B1-Motion>", self._glisser)
        self._canvas.bind("<ButtonRelease-1>", self._relacher)
        self._canvas.bind("<Motion>", self._survoler)
        self._canvas.bind("<Leave>", lambda _: self._effacer_survol())
        self._canvas.bind("<MouseWheel>", self._zoomer)
        self._canvas.bind("<Button-4>", self._zoomer)
        self._canvas.bind("<Button-5>", self._zoomer)

        self._detail = tk.StringVar(
            value="Faites glisser la carte pour la faire tourner · molette pour zoomer."
        )
        ctk.CTkLabel(
            self,
            textvariable=self._detail,
            font=(police, 11),
            text_color=("#5E3540", "#BE938F"),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(6, 0))

        legende = ctk.CTkFrame(self, fg_color="transparent")
        legende.pack(fill="x", pady=(2, 0))
        for categorie, libelle, symbole in (
            ("note", "Notes", "●"),
            ("hook_genere", "Hooks", "◆"),
            ("mot", "Mots-clés", "■"),
            ("chaine", "Chaînes", "▲"),
        ):
            ctk.CTkLabel(
                legende,
                text=f"{symbole}  {libelle}",
                font=(police, 11),
                text_color=COULEURS[categorie],
            ).pack(side="left", padx=(0, 16))

        self.rafraichir()

    # --- données ------------------------------------------------------
    def rafraichir(self) -> None:
        """Relit la base et les fichiers, puis redessine la carte."""
        self._noeuds, self._liens = construire(self._racine)
        disposer(self._noeuds)
        self._selection = None
        self._survol = None
        if not self._noeuds:
            self._compte.set("Rien à cartographier pour l'instant.")
            self._canvas.delete("all")
            return
        compte: dict[str, int] = {}
        for noeud in self._noeuds:
            compte[noeud.famille] = compte.get(noeud.famille, 0) + 1
        resume = " · ".join(
            f"{nombre} {LIBELLES.get(famille, famille).lower()}"
            for famille, nombre in sorted(compte.items())
        )
        self._compte.set(
            f"{len(self._noeuds)} points · {len(self._liens)} liens · {resume}"
        )
        self._dessiner()

    # --- dessin -------------------------------------------------------
    def _dessiner_fond(
        self, toile: tk.Canvas, largeur: int, hauteur: int, cx: float, cy: float
    ) -> None:
        """Grille, anneaux et repères : le cadre « radar » de la carte."""
        pas = 56
        x = cx % pas
        while x < largeur:
            toile.create_line(x, 0, x, hauteur, fill=GRILLE)
            x += pas
        y = cy % pas
        while y < hauteur:
            toile.create_line(0, y, largeur, y, fill=GRILLE)
            y += pas

        toile.create_line(cx, 14, cx, hauteur - 14, fill=GRILLE_FORTE, dash=(2, 8))
        toile.create_line(14, cy, largeur - 14, cy, fill=GRILLE_FORTE, dash=(2, 8))

        rayon = min(largeur, hauteur) * 0.46
        for ratio, aplat, dash in (
            (1.0, 0.60, (2, 8)),
            (0.74, 0.60, (2, 6)),
            (0.48, 0.60, (2, 5)),
        ):
            r = rayon * ratio
            toile.create_oval(
                cx - r,
                cy - r * aplat,
                cx + r,
                cy + r * aplat,
                outline=GRILLE_FORTE,
                dash=dash,
            )

        r = rayon * 0.66
        toile.create_arc(
            cx - r,
            cy - r * 0.60,
            cx + r,
            cy + r * 0.60,
            start=205,
            extent=38,
            style="arc",
            outline=BORDURE_HUD,
            width=2,
        )
        toile.create_arc(
            cx - r,
            cy - r * 0.60,
            cx + r,
            cy + r * 0.60,
            start=25,
            extent=26,
            style="arc",
            outline=BORDURE_HUD,
            width=2,
        )

        marge, longueur = 18, 26
        coins = (
            ((marge, marge + longueur), (marge, marge), (marge + longueur, marge)),
            ((largeur - marge - longueur, marge), (largeur - marge, marge),
             (largeur - marge, marge + longueur)),
            ((marge, hauteur - marge - longueur), (marge, hauteur - marge),
             (marge + longueur, hauteur - marge)),
            ((largeur - marge - longueur, hauteur - marge),
             (largeur - marge, hauteur - marge),
             (largeur - marge, hauteur - marge - longueur)),
        )
        for a, sommet, b in coins:
            toile.create_line(*a, *sommet, fill=BORDURE_HUD, width=2)
            toile.create_line(*sommet, *b, fill=BORDURE_HUD, width=2)

        toile.create_text(
            48,
            22,
            text="RÉSEAU COGNITIF",
            anchor="nw",
            fill=ACCENT,
            font=(self._mono, 9, "bold"),
        )
        toile.create_text(
            48,
            38,
            text="LOCAL · VUE 3D",
            anchor="nw",
            fill=TEXTE_DOUX,
            font=(self._mono, 8),
        )
        toile.create_text(
            largeur - 44,
            hauteur - 30,
            text=f"{len(self._noeuds):02d} NŒUDS // {len(self._liens):02d} LIENS",
            anchor="se",
            fill=TEXTE_DOUX,
            font=(self._mono, 8),
        )

    def _reticle(self, toile: tk.Canvas, x: float, y: float, rayon: float) -> None:
        """Quatre équerres autour d'un point, comme une cible de visée."""
        longueur = max(4.0, rayon * 0.45)
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            coin_x = x + dx * rayon
            coin_y = y + dy * rayon
            toile.create_line(
                coin_x,
                coin_y,
                coin_x - dx * longueur,
                coin_y,
                fill=ACCENT,
                width=2,
            )
            toile.create_line(
                coin_x,
                coin_y,
                coin_x,
                coin_y - dy * longueur,
                fill=ACCENT,
                width=2,
            )

    def _etiquette(
        self,
        toile: tk.Canvas,
        x: float,
        y: float,
        rayon: float,
        texte: str,
        couleur: str,
    ) -> None:
        """Nom du point survolé ou sélectionné, dans une bulle discrète."""
        texte = " ".join(texte.split())
        if not texte:
            return
        if len(texte) > 34:
            texte = texte[:33] + "…"
        a_droite = x < toile.winfo_width() * 0.62
        ancre = "w" if a_droite else "e"
        tx = x + rayon + 8 if a_droite else x - rayon - 8
        identifiant = toile.create_text(
            tx,
            y - 2,
            text=texte,
            anchor=ancre,
            fill=TEXTE,
            font=(self._mono, 9, "bold"),
        )
        x1, y1, x2, y2 = toile.bbox(identifiant)
        fond = toile.create_rectangle(
            x1 - 6,
            y1 - 3,
            x2 + 6,
            y2 + 3,
            fill="#1B1016",
            outline=couleur,
            width=1,
        )
        toile.tag_lower(fond, identifiant)

    def _dessiner(self) -> None:
        toile = self._canvas
        toile.delete("all")
        largeur, hauteur = toile.winfo_width(), toile.winfo_height()
        if largeur < 40 or hauteur < 40:
            return
        centre_x, centre_y = largeur / 2, hauteur / 2
        self._dessiner_fond(toile, largeur, hauteur, centre_x, centre_y)
        echelle = min(largeur, hauteur) * 0.36 * self._zoom
        cos_y, sin_y = math.cos(self._yaw), math.sin(self._yaw)
        cos_p, sin_p = math.cos(self._pitch), math.sin(self._pitch)

        projection: list[tuple[float, float, float, float]] = []
        for noeud in self._noeuds:
            x = noeud.x * cos_y + noeud.z * sin_y
            z = -noeud.x * sin_y + noeud.z * cos_y
            y = noeud.y * cos_p - z * sin_p
            profondeur = noeud.y * sin_p + z * cos_p
            facteur = 2.8 / (2.8 + profondeur * 0.75)
            projection.append(
                (
                    profondeur,
                    centre_x + x * echelle * facteur,
                    centre_y + y * echelle * facteur,
                    facteur,
                )
            )
        self._proj = projection

        actifs = {index for index in (self._selection, self._survol) if index is not None}
        for a, b in self._liens:
            _, x1, y1, _ = projection[a]
            _, x2, y2, _ = projection[b]
            actif = a in actifs or b in actifs
            toile.create_line(
                x1,
                y1,
                x2,
                y2,
                fill=TRAIT_ACTIF if actif else TRAIT,
                width=2 if actif else 1,
            )

        for index in sorted(range(len(self._noeuds)), key=lambda i: projection[i][0]):
            _, px, py, facteur = projection[index]
            noeud = self._noeuds[index]
            rayon = max(4.0, min(14.0, (4.2 + math.sqrt(noeud.degre) * 2.5) * facteur))
            couleur = COULEURS.get(noeud.categorie, COULEURS["note"])
            famille = noeud.famille

            if index == self._selection:
                self._reticle(toile, px, py, rayon + 9)
            toile.create_oval(
                px - rayon - 4,
                py - rayon - 4,
                px + rayon + 4,
                py + rayon + 4,
                outline="#3A1D22",
                width=2,
            )

            if famille == "hook_genere":
                toile.create_polygon(
                    px, py - rayon, px + rayon, py, px, py + rayon, px - rayon, py,
                    fill=couleur,
                    outline="",
                )
            elif famille == "mot":
                toile.create_rectangle(
                    px - rayon * 0.9,
                    py - rayon * 0.9,
                    px + rayon * 0.9,
                    py + rayon * 0.9,
                    fill=couleur,
                    outline="",
                )
            elif famille == "chaine":
                toile.create_polygon(
                    px,
                    py - rayon,
                    px + rayon * 0.9,
                    py + rayon * 0.8,
                    px - rayon * 0.9,
                    py + rayon * 0.8,
                    fill=couleur,
                    outline="",
                )
            else:
                toile.create_oval(
                    px - rayon,
                    py - rayon,
                    px + rayon,
                    py + rayon,
                    fill=couleur,
                    outline="",
                )

            if rayon >= 5.5:
                centre = rayon * 0.28
                toile.create_oval(
                    px - centre,
                    py - centre,
                    px + centre,
                    py + centre,
                    fill=FOND_CANVAS,
                    outline="",
                )

        etiquettes: list[int] = []
        for index in (self._selection, self._survol):
            if index is None or not 0 <= index < len(projection):
                continue
            if index in etiquettes:
                continue
            etiquettes.append(index)
            _, px, py, facteur = projection[index]
            noeud = self._noeuds[index]
            rayon = max(4.0, min(14.0, (4.2 + math.sqrt(noeud.degre) * 2.5) * facteur))
            self._etiquette(
                toile,
                px,
                py,
                rayon + 2,
                noeud.label,
                COULEURS.get(noeud.categorie, COULEURS["note"]),
            )

    def _proche(self, x: int, y: int) -> int | None:
        meilleur, distance = None, 14.0
        for index, (_z, px, py, _f) in enumerate(self._proj):
            ecart = math.hypot(px - x, py - y)
            if ecart < distance:
                meilleur, distance = index, ecart
        return meilleur

    # --- interactions -------------------------------------------------
    def _decrire(self, index: int | None) -> str:
        if index is None:
            return "Faites glisser la carte pour la faire tourner · molette pour zoomer."
        noeud = self._noeuds[index]
        return f"{LIBELLES.get(noeud.famille, noeud.categorie)} · {noeud.label}"

    def _presser(self, evenement) -> None:
        self._origine = (evenement.x, evenement.y)
        self._bouge = False

    def _glisser(self, evenement) -> None:
        if self._origine is None:
            return
        dx = evenement.x - self._origine[0]
        dy = evenement.y - self._origine[1]
        if abs(dx) + abs(dy) > 3:
            self._bouge = True
        self._origine = (evenement.x, evenement.y)
        self._yaw += dx * 0.012
        self._pitch = max(-1.35, min(1.35, self._pitch + dy * 0.012))
        self._dessiner()

    def _relacher(self, evenement) -> None:
        self._origine = None
        if self._bouge:
            self._bouge = False
            return
        index = self._proche(evenement.x, evenement.y)
        if index != self._selection:
            self._selection = index
            self._detail.set(self._decrire(index))
            self._dessiner()

    def _survoler(self, evenement) -> None:
        index = self._proche(evenement.x, evenement.y)
        if index == self._survol:
            return
        self._survol = index
        self._detail.set(self._decrire(index))
        self._dessiner()

    def _effacer_survol(self) -> None:
        if self._survol is None:
            return
        self._survol = None
        self._detail.set(self._decrire(self._selection))
        self._dessiner()

    def _zoomer(self, evenement) -> None:
        if getattr(evenement, "num", None) == 4 or getattr(evenement, "delta", 0) > 0:
            self._zoom = min(2.4, self._zoom * 1.12)
        else:
            self._zoom = max(0.55, self._zoom / 1.12)
        self._dessiner()

    def _recentrer(self) -> None:
        self._yaw, self._pitch, self._zoom = 0.55, 0.32, 1.0
        self._selection = None
        self._detail.set(self._decrire(None))
        self._dessiner()

    def _redimensionner(self, evenement) -> None:
        taille = (evenement.width, evenement.height)
        if taille == self._derniere_taille:
            return
        self._derniere_taille = taille
        self._dessiner()


__all__ = ["CarteCerveau", "Noeud", "construire", "disposer"]
