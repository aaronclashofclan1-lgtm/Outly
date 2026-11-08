"""Comportement de l'application empaquetée : chemins et choix du mode."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from outly import cli, config, lanceur
from outly.gui import runner


@pytest.fixture
def app_empaquetee(monkeypatch, tmp_path):
    """Simule `Outly.exe` dans un dossier utilisateur temporaire."""
    dossier = tmp_path / "Outly"
    dossier.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(dossier / "Outly.exe"))
    config.dossier_executable.cache_clear()
    config.dossier_donnees.cache_clear()
    yield dossier
    config.dossier_executable.cache_clear()
    config.dossier_donnees.cache_clear()


@pytest.fixture
def interface_factice(monkeypatch):
    """Remplace la fenêtre par un espion : aucun affichage pendant les tests."""
    vus: dict[str, list[str]] = {}

    def main(argv: list[str]) -> int:
        vus["gui"] = argv
        return 0

    monkeypatch.setitem(sys.modules, "outly.gui.app", types.SimpleNamespace(main=main))
    return vus


def test_racine_empaquetee_est_le_dossier_de_l_executable(app_empaquetee):
    assert config.racine_projet() == app_empaquetee.resolve()


def test_outly_root_reste_prioritaire(app_empaquetee, monkeypatch, tmp_path):
    autre = tmp_path / "projet"
    monkeypatch.setenv("OUTLY_ROOT", str(autre))
    assert config.racine_projet() == autre.resolve()


def test_le_premier_lancement_cree_les_fichiers_de_depart(app_empaquetee, interface_factice):
    assert lanceur.main([]) == 0

    assert (app_empaquetee / "config.yaml").is_file()
    assert (app_empaquetee / ".env").is_file()
    assert (app_empaquetee / "positioning.md").is_file()
    assert (app_empaquetee / "data").is_dir()
    assert interface_factice["gui"] == []


def test_les_sous_commandes_preparent_le_dossier_vise(app_empaquetee, monkeypatch, tmp_path):
    """Avec OUTLY_ROOT, aucun fichier ne doit apparaître à côté de l'exe."""
    autre = tmp_path / "projet"
    monkeypatch.setenv("OUTLY_ROOT", str(autre))
    monkeypatch.setattr("outly.cli.main", lambda argv: 0)

    lanceur.main(["doctor"])

    assert (autre / "config.yaml").is_file()
    assert (autre / ".env").is_file()
    assert not (app_empaquetee / "config.yaml").exists()
    assert not (app_empaquetee / ".env").exists()


def test_un_fichier_existant_n_est_pas_ecrase(app_empaquetee, interface_factice):
    (app_empaquetee / "config.yaml").write_text("moi:\n  chaine: \"@moi\"\n", encoding="utf-8")

    lanceur.main([])

    assert "@moi" in (app_empaquetee / "config.yaml").read_text(encoding="utf-8")


def test_les_sous_commandes_vont_a_la_cli(app_empaquetee, monkeypatch):
    vus: dict[str, list[str]] = {}

    def main(argv: list[str]) -> int:
        vus["cli"] = argv
        return 7

    monkeypatch.setattr("outly.cli.main", main)
    assert lanceur.main(["doctor", "--offline"]) == 7
    assert vus["cli"] == ["doctor", "--offline"]


def test_les_options_globales_vont_a_la_cli(app_empaquetee, monkeypatch):
    vus: dict[str, list[str]] = {}

    def main(argv: list[str]) -> int:
        vus["cli"] = argv
        return 0

    monkeypatch.setattr("outly.cli.main", main)
    lanceur.main(["--version"])

    assert vus["cli"] == ["--version"]


def test_la_commande_hooks_est_reconnue():
    assert "hooks" in lanceur.COMMANDES


def test_la_commande_brain_est_reconnue():
    assert "brain" in lanceur.COMMANDES


def test_la_commande_recherche_est_reconnue_et_routée_vers_la_cli(
    app_empaquetee, monkeypatch
):
    vus: dict[str, list[str]] = {}

    def main(argv: list[str]) -> int:
        vus["cli"] = argv
        return 0

    monkeypatch.setattr("outly.cli.main", main)
    assert "recherche" in lanceur.COMMANDES
    assert lanceur.main(["recherche", "--titre", "Sujet", "--mots-cles", "mot"]) == 0
    assert vus["cli"][0] == "recherche"


def test_le_parseur_brain_accepte_les_options():
    args = cli.construire_parseur().parse_args(
        ["brain", "--question", "Quels angles ?", "--inclure-archives", "--force"]
    )
    assert args.fonction is cli.cmd_brain
    assert args.question == "Quels angles ?"
    assert args.inclure_archives
    assert args.force


def test_la_commande_relance_l_executable(app_empaquetee):
    assert runner.commande_cli(["run"]) == [str(Path(sys.executable)), "run"]


def test_en_developpement_la_commande_reste_python_m_outly():
    assert runner.commande_cli(["run"], "python.exe") == ["python.exe", "-m", "outly", "run"]


# ---------------------------------------------------------------------------
# Vérification après enregistrement (bogue : `-m outly` devant l'exe)
# ---------------------------------------------------------------------------


def test_valider_config_accepte_un_projet_neuf(tmp_path):
    """Le diagnostic lancé par l'interface doit réussir sur une config neuve."""
    cli.preparer_dossier(tmp_path)

    validation = runner.valider_config(tmp_path)

    assert validation.reussie, validation.sortie
    assert validation.lancee


def test_valider_config_refuse_une_config_hors_bornes(tmp_path):
    cli.preparer_dossier(tmp_path)
    chemin = tmp_path / "config.yaml"
    chemin.write_text(
        chemin.read_text(encoding="utf-8").replace("batch_size: 50", "batch_size: 999"),
        encoding="utf-8",
    )

    validation = runner.valider_config(tmp_path)

    assert not validation.reussie
    assert validation.lancee
    assert "batch_size" in validation.sortie


def test_valider_config_empaquetee_ne_relance_pas_l_executable(app_empaquetee, monkeypatch):
    """Empaquetée, la validation est en mémoire : aucun `Outly.exe` relancé."""
    cli.preparer_dossier(app_empaquetee)

    def interdit(*_args, **_options):
        raise AssertionError("valider_config ne doit lancer aucun sous-processus")

    monkeypatch.setattr(runner.subprocess, "run", interdit)

    validation = runner.valider_config(app_empaquetee)

    assert validation.reussie, validation.sortie


def test_valider_config_distingue_une_relecture_impossible(tmp_path, monkeypatch):
    """Un fichier illisible ne doit pas accuser la configuration saisie."""
    cli.preparer_dossier(tmp_path)

    def refuse(*_args, **_options):
        raise OSError("Accès refusé")

    monkeypatch.setattr("outly.config.charger", refuse)

    validation = runner.valider_config(tmp_path)

    assert not validation.reussie
    assert not validation.lancee
    assert "Accès refusé" in validation.sortie


def test_valider_config_ne_laisse_aucune_cle_dans_l_environnement(tmp_path, monkeypatch):
    """Les clés de `.env` ne doivent pas rester dans l'environnement de l'interface."""
    cli.preparer_dossier(tmp_path)
    (tmp_path / ".env").write_text("OUTLY_TEST_SECRET=abc\n", encoding="utf-8")
    monkeypatch.delenv("OUTLY_TEST_SECRET", raising=False)

    validation = runner.valider_config(tmp_path)

    assert validation.reussie, validation.sortie
    assert "OUTLY_TEST_SECRET" not in os.environ


def test_le_script_planifie_lance_l_executable_empaquete(tmp_path, monkeypatch):
    """La tâche Windows doit appeler l'exe avec `mail`, pas `-m outly`."""
    from outly import planification

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Outly.exe"))

    script = planification.contenu_script(r"C:\Python\pythonw.exe", tmp_path)

    assert f'"{tmp_path / "Outly.exe"}" mail --si-du' in script
    assert "-m outly" not in script


def test_le_script_planifie_reste_python_en_developpement(tmp_path):
    from outly import planification

    script = planification.contenu_script(r"C:\Python\pythonw.exe", tmp_path)

    assert '"C:\\Python\\pythonw.exe" -m outly mail --si-du' in script


def test_la_commande_de_doctor_empaquetee_ne_contient_pas_m(app_empaquetee):
    """Garde-fou du lanceur : `Outly.exe doctor`, jamais `-m outly`."""
    commande = runner.commande_cli(["doctor"])
    assert "-m" not in commande
    assert commande[1:] == ["doctor"]
    assert Path(commande[0]).name == "Outly.exe"
