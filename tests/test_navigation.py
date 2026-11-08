from __future__ import annotations

from outly.gui import settings


def test_volets_par_defaut_et_persistance(tmp_path):
    prefs = settings.lire_preferences(tmp_path)
    assert prefs["navigation_groupes"] == {}
    settings.ecrire_preferences(tmp_path, navigation_groupes={"ANALYSE": False})
    prefs = settings.lire_preferences(tmp_path)
    assert prefs["navigation_groupes"] == {"ANALYSE": False}


def test_volets_invalides_ignores(tmp_path):
    settings.ecrire_preferences(
        tmp_path,
        navigation_groupes={"ANALYSE": False, "MAUVAIS": "non", 4: True},
    )
    prefs = settings.lire_preferences(tmp_path)
    assert prefs["navigation_groupes"] == {"ANALYSE": False}


def test_regroupements_en_onglets_coherents():
    from outly.gui import app

    pages = [page for _titre, entrees in app.HUBS_ONGLETS.values() for page, _lib in entrees]
    assert len(pages) == len(set(pages)), "une page ne doit appartenir qu'à un seul groupe"
    assert set(app.PAGE_VERS_HUB) == set(pages)
    assert set(app.PAGE_VERS_HUB.values()) == set(app.HUBS_ONGLETS)
    assert all(page in app.PAGES for page in pages)


def test_navigation_ne_montre_que_les_groupes():
    from outly.gui import app

    entrees = [cle for _groupe, pages in app.NAVIGATION_GROUPES for cle, _lib in pages]
    assert set(entrees) == set(app.HUBS_ONGLETS) | {"accueil", "lancer", "email", "contenu"}
    assert len(entrees) == len(set(entrees))
