"""Tests de la construction des commandes lancées par l'interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from outly.gui import runner


def test_arguments_run_par_defaut():
    assert runner.arguments_run() == ["run"]


def test_arguments_run_complets():
    assert runner.arguments_run(max_recherches=5, avec_outliers=True, transcriptions=True) == [
        "run",
        "--max-searches",
        "5",
        "--with-outliers",
        "--transcripts",
    ]


def test_estimation_execution_suit_les_options():
    assert runner.estimation_execution(20) == "≈ 6 à 8 min"
    assert runner.estimation_execution(0) == "≈ 2 à 4 min"
    assert runner.estimation_execution(20, avec_outliers=True) == "≈ 7 à 9 min"
    assert runner.estimation_execution(20, transcriptions=True) == "≈ 7 à 9 min"
    assert runner.estimation_execution(20, avec_outliers=True, transcriptions=True) == "≈ 8 à 10 min"
    assert runner.estimation_execution("invalide") == "≈ 6 à 8 min"


def test_arguments_des_etapes():
    assert runner.arguments_etape("discover") == ["discover"]
    assert runner.arguments_etape("discover", max_recherches=3) == ["discover", "--max-searches", "3"]
    assert runner.arguments_etape("enrich") == ["enrich"]
    assert runner.arguments_etape("score") == ["score"]
    assert runner.arguments_etape("outliers", transcriptions=True) == [
        "analyze-outliers",
        "--transcripts",
    ]
    assert runner.arguments_etape("hooks") == ["hooks"]
    assert runner.arguments_etape("hooks", transcriptions=True) == ["hooks", "--transcripts"]
    assert runner.arguments_etape("report") == ["report"]


def test_etape_inconnue_refusee():
    with pytest.raises(ValueError):
        runner.arguments_etape("inventee")


def test_arguments_du_cerveau_ia():
    assert runner.arguments_brain() == ["brain"]
    assert runner.arguments_brain(
        question="  Quels angles ?  ", inclure_archives=True, forcer=True
    ) == ["brain", "--question", "Quels angles ?", "--inclure-archives", "--force"]


def test_arguments_des_idees_bornees():
    assert runner.arguments_idees() == ["idees", "--nombre", "5"]
    assert runner.arguments_idees(
        question="  Une série sur la preuve  ", nombre=12, forcer=True
    ) == ["idees", "--question", "Une série sur la preuve", "--nombre", "10", "--force"]


def test_doctor_avec_et_sans_sonde():
    assert runner.arguments_doctor(True) == ["doctor", "--probe"]
    assert runner.arguments_doctor(False) == ["doctor"]


def test_python_du_projet_existe():
    assert Path(runner.python_du_projet()).exists()
