"""Tests de l'ordre personnalisable des blocs (sans interface graphique)."""

from __future__ import annotations

import json

import pytest

from outly.gui import disposition, settings


def test_ordre_par_defaut_et_filtrage(tmp_path):
    assert disposition.defaut("accueil") == {
        "gauche": ["infos", "veille"],
        "droite": ["audit", "quota", "execution", "rapport"],
    }

    ordre = disposition.ordre(
        tmp_path, "accueil", "droite", ["quota", "audit", "inconnu"]
    )
    assert ordre == ["quota", "audit"]


def test_ordre_sauvegarde_est_respecte_et_complete(tmp_path):
    disposition.enregistrer_zones(
        tmp_path, "accueil", {"droite": ["rapport", "audit"]}
    )

    ordre = disposition.ordre(
        tmp_path, "accueil", "droite", ["audit", "quota", "execution", "rapport"]
    )
    assert ordre == ["rapport", "audit", "quota", "execution"]


def test_les_autres_zones_et_pages_sont_conservees(tmp_path):
    disposition.enregistrer_zones(
        tmp_path, "accueil", {"droite": ["audit", "quota", "execution", "rapport"]}
    )
    disposition.enregistrer_zones(
        tmp_path, "accueil", {"gauche": ["veille", "infos"]}
    )

    ordre_gauche = disposition.ordre(tmp_path, "accueil", "gauche", ["infos", "veille"])
    ordre_droite = disposition.ordre(
        tmp_path, "accueil", "droite", ["audit", "quota", "execution", "rapport"]
    )
    assert ordre_gauche == ["veille", "infos"]
    assert ordre_droite == ["audit", "quota", "execution", "rapport"]


def test_ordre_accepte_un_bloc_deplace_dans_une_autre_zone(tmp_path):
    disposition.enregistrer_zones(
        tmp_path,
        "accueil",
        {
            "droite": ["audit", "quota", "execution"],
            "gauche": ["infos", "veille", "rapport"],
        },
    )

    assert disposition.ordre(
        tmp_path, "accueil", "gauche", ["infos", "veille", "rapport"]
    ) == ["infos", "veille", "rapport"]
    assert disposition.ordre(
        tmp_path, "accueil", "droite", ["audit", "quota", "execution"]
    ) == ["audit", "quota", "execution"]


def test_zone_retrouve_la_colonne_du_bloc(tmp_path):
    disposition.enregistrer_zones(
        tmp_path, "accueil", {"gauche": ["infos", "veille", "rapport"]}
    )

    assert disposition.zone(tmp_path, "accueil", "rapport", "droite") == "gauche"
    assert disposition.zone(tmp_path, "accueil", "audit", "droite") == "droite"


def test_doublons_et_identifiants_invalides_sont_ecartes(tmp_path):
    disposition.enregistrer_zones(
        tmp_path,
        "accueil",
        {"droite": ["audit", "audit", "", None, "quota"]},  # type: ignore[list-item]
    )

    ordre = disposition.ordre(tmp_path, "accueil", "droite", ["audit", "quota"])
    assert ordre == ["audit", "quota"]


def test_reinitialisation_dune_page_puis_de_tout(tmp_path):
    disposition.enregistrer_zones(tmp_path, "accueil", {"droite": ["rapport"]})
    disposition.enregistrer_zones(tmp_path, "audit", {"principal": ["diagnostic"]})

    disposition.reinitialiser(tmp_path, "accueil")
    assert disposition.ordre(
        tmp_path, "accueil", "droite", ["audit", "quota"]
    ) == ["audit", "quota"]
    assert disposition.ordre(
        tmp_path, "audit", "principal", ["diagnostic"]
    ) == ["diagnostic"]

    disposition.reinitialiser(tmp_path)
    assert settings.lire_preferences(tmp_path)["disposition"] == {}


def test_une_disposition_invalide_ne_casse_pas_les_preferences(tmp_path):
    chemin = tmp_path / "data" / "preferences.json"
    chemin.parent.mkdir(parents=True)
    chemin.write_text(
        json.dumps(
            {
                "theme": "light",
                "disposition": {
                    "accueil": {
                        "droite": ["audit", "audit", 3],
                        "gauche": "pas une liste",
                    },
                    "audit": {"principal": ["diagnostic", "diagnostic"]},
                    "page_invalide": "pas un objet",
                },
                "inconnue": "ignorée",
            }
        ),
        encoding="utf-8",
    )

    preferences = settings.lire_preferences(tmp_path)

    assert preferences["theme"] == "light"
    assert preferences["disposition"] == {
        "accueil": {"droite": ["audit"]},
        "audit": {"principal": ["diagnostic"]},
    }
    assert "inconnue" not in preferences


def test_ecriture_refuse_une_disposition_non_dict(tmp_path):
    with pytest.raises(settings.ErreurFichier):
        settings.ecrire_preferences(tmp_path, disposition=["pas", "un", "dict"])


def test_ecriture_conserve_les_autres_preferences(tmp_path):
    settings.ecrire_preferences(tmp_path, theme="light", ombres=False)
    disposition.enregistrer_zones(tmp_path, "accueil", {"gauche": ["veille", "infos"]})

    preferences = settings.lire_preferences(tmp_path)
    assert preferences["theme"] == "light"
    assert preferences["ombres"] is False
    assert preferences["disposition"]["accueil"]["gauche"] == ["veille", "infos"]
