"""Tests du plan éditorial et de la préférence d'apparence."""

from __future__ import annotations

import pytest

from outly import plan
from outly.gui import settings


def test_cycle_de_vie_dune_fiche(tmp_path):
    identifiant = plan.ajouter(
        tmp_path,
        titre="5 erreurs qui tuent votre première vidéo",
        date_prevue="02/11/2026",
        statut="Écriture",
        format="Short",
        notes="Tourner au bureau.",
    )
    assert identifiant > 0

    fiches = plan.lister(tmp_path)
    assert len(fiches) == 1
    fiche = fiches[0]
    assert fiche.titre.startswith("5 erreurs")
    assert fiche.date_prevue == "2026-11-02"
    assert fiche.date_affichable == "02/11/2026"
    assert fiche.etiquette_statut == "Écriture"
    assert fiche.etiquette_format == "Short"
    assert not fiche.publie

    plan.modifier(
        tmp_path,
        identifiant,
        titre="5 erreurs qui tuent votre première vidéo",
        date_prevue="2026-11-02",
        statut="publie",
        format="short",
        notes="Publiée.",
    )
    fiche = plan.lister(tmp_path)[0]
    assert fiche.publie
    assert fiche.notes == "Publiée."

    assert plan.supprimer(tmp_path, identifiant) is True
    assert plan.lister(tmp_path) == []


def test_titre_vide_refuse(tmp_path):
    with pytest.raises(plan.ErreurPlan):
        plan.ajouter(tmp_path, titre="   ")


def test_date_invalide_refusee(tmp_path):
    with pytest.raises(plan.ErreurPlan):
        plan.ajouter(tmp_path, titre="Idée", date_prevue="32/13/2026")


def test_les_fiches_sans_date_vont_a_la_fin(tmp_path):
    plan.ajouter(tmp_path, titre="Décembre", date_prevue="2026-12-01")
    plan.ajouter(tmp_path, titre="Sans date")
    plan.ajouter(tmp_path, titre="Novembre", date_prevue="05/11/2026")

    dates = [fiche.date_prevue for fiche in plan.lister(tmp_path)]
    assert dates == ["2026-11-05", "2026-12-01", None]


def test_modifier_une_fiche_absente_est_signale(tmp_path):
    with pytest.raises(plan.ErreurPlan):
        plan.modifier(tmp_path, 999, titre="Fantôme")


def test_theme_memorise(tmp_path):
    assert settings.lire_theme(tmp_path) == "dark"
    settings.ecrire_theme(tmp_path, "light")
    assert settings.lire_theme(tmp_path) == "light"
    with pytest.raises(settings.ErreurFichier):
        settings.ecrire_theme(tmp_path, "bleu")


def test_preferences_complete_et_fusionne(tmp_path):
    assert settings.lire_preferences(tmp_path)["theme"] == "dark"
    settings.ecrire_preferences(tmp_path, texte_tableau="grand", ombres=False)
    prefs = settings.lire_preferences(tmp_path)
    assert prefs["texte_tableau"] == "grand"
    assert prefs["ombres"] is False
    assert prefs["theme"] == "dark"

    settings.ecrire_preferences(tmp_path, theme="light")
    prefs = settings.lire_preferences(tmp_path)
    assert prefs["theme"] == "light"
    assert prefs["texte_tableau"] == "grand"
    assert prefs["demarrage"] == "accueil"


def test_preferences_refusent_les_valeurs_inconnues(tmp_path):
    for modification in (
        {"theme": "bleu"},
        {"texte_tableau": "enorme"},
        {"demarrage": "milieu"},
        {"ombres": "oui"},
        {"confirmer_suppression": 1},
        {"derniere_page": "   "},
        {"inconnue": 1},
    ):
        with pytest.raises(settings.ErreurFichier):
            settings.ecrire_preferences(tmp_path, **modification)


def test_theme_illisible_retombe_sur_le_sombre(tmp_path):
    chemin = tmp_path / "data" / "preferences.json"
    chemin.parent.mkdir(parents=True)
    chemin.write_text("{pas du json", encoding="utf-8")
    assert settings.lire_theme(tmp_path) == "dark"


def test_taches_dune_fiche(tmp_path):
    identifiant = plan.ajouter(tmp_path, titre="Vidéo avec tâches")
    tache = plan.ajouter_tache(tmp_path, identifiant, "  Préparer le plan  ")
    assert tache > 0

    taches = plan.lister_taches(tmp_path, identifiant)
    assert [t.libelle for t in taches] == ["Préparer le plan"]
    assert not taches[0].fait

    assert plan.basculer_tache(tmp_path, tache) is True
    assert plan.lister_taches(tmp_path, identifiant)[0].fait is True
    assert plan.basculer_tache(tmp_path, tache, False) is True
    assert plan.lister_taches(tmp_path, identifiant)[0].fait is False

    assert plan.supprimer_tache(tmp_path, tache) is True
    assert plan.lister_taches(tmp_path, identifiant) == []


def test_supprimer_fiche_supprime_ses_taches(tmp_path):
    identifiant = plan.ajouter(tmp_path, titre="À supprimer")
    plan.ajouter_tache(tmp_path, identifiant, "Tourner")
    assert plan.supprimer(tmp_path, identifiant) is True
    assert plan.lister_taches(tmp_path, identifiant) == []


def test_tache_vide_refusee(tmp_path):
    identifiant = plan.ajouter(tmp_path, titre="Fiche")
    with pytest.raises(plan.ErreurPlan):
        plan.ajouter_tache(tmp_path, identifiant, "   ")
