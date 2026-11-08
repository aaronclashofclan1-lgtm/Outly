"""Petit indicateur de chargement animé, sans dépendance externe."""

from __future__ import annotations

import tkinter as tk


class Spinner(tk.Canvas):
    """Arc rotatif dessiné sur un Canvas.

    Tk ne gère pas la transparence : le fond est donc fourni par l'appelant et
    doit correspondre au conteneur. L'animation s'arrête d'elle-même quand le
    widget est détruit, ce qui évite les erreurs « invalid command name ».
    """

    def __init__(
        self,
        parent: tk.Misc,
        taille: int = 16,
        couleur: str = "#F26B4F",
        fond: str = "#21131A",
        epaisseur: int = 2,
    ):
        super().__init__(
            parent,
            width=taille,
            height=taille,
            bg=fond,
            highlightthickness=0,
            bd=0,
        )
        self._taille = max(10, int(taille))
        self._couleur = couleur
        self._fond = fond
        self._epaisseur = max(1, int(epaisseur))
        self._angle = 0
        self._after: str | None = None
        self._actif = False
        self.bind("<Destroy>", self._sur_destruction, add="+")
        self._dessiner()

    def demarrer(self) -> None:
        if self._actif:
            return
        self._actif = True
        self._tourner()

    def arreter(self) -> None:
        self._actif = False
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None

    def _sur_destruction(self, _event=None) -> None:
        self.arreter()

    def _tourner(self) -> None:
        if not self._actif:
            return
        self._angle = (self._angle + 30) % 360
        self._dessiner()
        try:
            self._after = self.after(80, self._tourner)
        except tk.TclError:
            self._actif = False

    def _dessiner(self) -> None:
        try:
            self.delete("all")
            marge = self._epaisseur + 1
            boite = (marge, marge, self._taille - marge, self._taille - marge)
            # Huit segments : les plus proches de la tête sont les plus vifs.
            for index in range(8):
                debut = (self._angle + index * 45) % 360
                force = 1.0 - index / 9
                couleur = self._melanger(self._couleur, self._fond, force)
                self.create_arc(
                    *boite,
                    start=debut,
                    extent=30,
                    style="arc",
                    outline=couleur,
                    width=self._epaisseur,
                )
        except tk.TclError:
            self._actif = False

    @staticmethod
    def _melanger(couleur: str, fond: str, force: float) -> str:
        try:
            a = tuple(int(couleur.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            b = tuple(int(fond.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
            return "#" + "".join(
                f"{round(b[i] + (a[i] - b[i]) * force):02x}" for i in range(3)
            )
        except (ValueError, IndexError):
            return couleur


__all__ = ["Spinner"]
