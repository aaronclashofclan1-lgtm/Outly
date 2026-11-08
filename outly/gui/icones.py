"""Icônes Tabler (Iconify) teintées à la volée.

Les PNG sont générés une seule fois dans ``assets/icones`` à partir des SVG
Tabler (licence MIT). Aucune dépendance d'exécution n'est ajoutée : Pillow,
déjà utilisé pour les miniatures, sert uniquement à colorer le masque alpha.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
from PIL import Image

DOSSIER = Path(__file__).resolve().parent / "assets" / "icones"

ICONES_PAGES = {
    "accueil": "accueil",
    "audit": "audit",
    "studio": "studio",
    "plan": "plan",
    "lancer": "lancer",
    "resultats": "resultats",
    "comparateur": "comparateur",
    "hooks": "hooks",
    "cerveau": "cerveau",
    "idees": "idees",
    "recherche": "recherche",
    "notes": "notes",
    "regarder": "regarder",
    "contenu": "contenu",
    "cles": "cles",
    "reglages": "reglages",
    "email": "email",
    "strategie": "strategie",
    "schemas": "schemas",
    "tableau": "tableau",
    "diagnostic": "audit",
    "production": "studio",
    "veille": "comparateur",
    "carnet": "notes",
    "atelier": "cerveau",
    "configuration": "reglages",
}

ICONES_GROUPES = {
    "PILOTAGE": "pilotage",
    "ANALYSE": "analyse",
    "BIBLIOTHÈQUE": "bibliotheque",
    "CONFIGURATION": "configuration",
}

_cache: dict[tuple[str, int, str], ctk.CTkImage | None] = {}


def _chemin(nom: str) -> Path | None:
    propre = Path(str(nom)).name
    if propre.endswith(".png"):
        propre = propre[:-4]
    candidat = DOSSIER / f"{propre}.png"
    return candidat if candidat.is_file() else None


def image(nom: str, taille: int = 20, couleur="#FFF6EE"):
    """Renvoie une CTkImage teintée, ou None si l'icône manque.

    Une icône absente ne doit jamais empêcher une page de s'afficher : les
    appelants retombent simplement sur leur texte. Une couleur de thème peut
    être passée sous forme de couple (clair, sombre) ; on résout alors la
    variante correspondant à l'apparence active.
    """
    if isinstance(couleur, (tuple, list)):
        sombre = ctk.get_appearance_mode() == "Dark"
        couleur = couleur[1] if sombre and len(couleur) > 1 else couleur[0]
    couleur = str(couleur)
    cle = (str(nom), int(taille), couleur)
    if cle in _cache:
        return _cache[cle]
    chemin = _chemin(nom)
    if chemin is None:
        _cache[cle] = None
        return None
    try:
        masque = Image.open(chemin).convert("RGBA")
        teinte = Image.new("RGBA", masque.size, couleur)
        if not couleur.startswith("#") or len(couleur) not in (7, 9):
            raise ValueError(f"couleur invalide : {couleur!r}")
        teinte.putalpha(masque.getchannel("A"))
        icone = ctk.CTkImage(
            light_image=teinte, dark_image=teinte, size=(int(taille), int(taille))
        )
    except (OSError, ValueError):
        icone = None
    _cache[cle] = icone
    return icone


def page(cle: str, taille: int = 18, couleur: str = "#FFF6EE"):
    return image(ICONES_PAGES.get(cle, cle), taille, couleur)


def groupe(nom: str, taille: int = 15, couleur: str = "#E3C7BF"):
    return image(ICONES_GROUPES.get(nom.upper(), "pilotage"), taille, couleur)


__all__ = ["ICONES_GROUPES", "ICONES_PAGES", "DOSSIER", "groupe", "image", "page"]
