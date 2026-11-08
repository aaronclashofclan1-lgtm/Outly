from __future__ import annotations

from pathlib import Path

from PIL import Image

from outly.gui import icones


def test_toutes_les_pages_ont_une_icone():
    for cle in icones.ICONES_PAGES:
        chemin = Path(icones.DOSSIER) / f"{icones.ICONES_PAGES[cle]}.png"
        assert chemin.is_file(), cle
        with Image.open(chemin) as image:
            assert image.mode == "RGBA"
            assert image.size == (96, 96)


def test_icone_inconnue_retourne_none():
    assert icones.image("icone-qui-nexiste-pas", 16) is None
