"""Tests du compteur de quota : deux compartiments, arrêt propre, jour Pacifique."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from outly.quota import (
    BudgetDepasse,
    CompteurQuota,
    compartiment_et_cout,
    jour_pacifique,
)

UTC = timezone.utc


def test_jour_pacifique_bascule_a_minuit_local():
    # Le 30 octobre 2026, Los Angeles est encore en heure d'été (UTC-7).
    assert jour_pacifique(datetime(2026, 10, 30, 6, 59, tzinfo=UTC)) == "2026-10-29"
    assert jour_pacifique(datetime(2026, 10, 30, 7, 0, tzinfo=UTC)) == "2026-10-30"


def test_jour_pacifique_independant_du_fuseau_de_la_machine():
    # 23 h à Paris le 29 = encore le 29 en Californie.
    instant = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)
    assert jour_pacifique(instant) == "2026-10-29"


def test_compartiments():
    assert compartiment_et_cout("search.list")[0] == "search"
    assert compartiment_et_cout("search.list")[1] == 1
    assert compartiment_et_cout("channels.list")[0] == "principal"
    assert compartiment_et_cout("videos.list") == ("principal", 1)


def test_les_deux_compartiments_sont_comptes_separement(connexion, horloge):
    compteur = CompteurQuota(connexion, None, horloge=horloge.maintenant)
    compteur.enregistrer("search.list", 1)
    compteur.enregistrer("channels.list", 1)
    compteur.enregistrer("playlistItems.list", 1)

    totaux = compteur.totaux_jour()
    assert totaux.unites_recherche == 1
    assert totaux.unites_principal == 2
    assert totaux.total == 3


def test_un_appel_servi_par_le_cache_ne_coute_rien(connexion, horloge):
    compteur = CompteurQuota(connexion, None, horloge=horloge.maintenant)
    compteur.enregistrer("channels.list", 0, depuis_cache=True)

    totaux = compteur.totaux_jour()
    assert totaux.unites_principal == 0
    # Et il ne doit pas être refusé pour cause de budget.
    compteur.verifier("channels.list")
    # Une lecture en cache n'écrit pas dans le journal : c'est ce qui évite
    # des milliers de commits SQLite inutiles pendant une découverte.
    assert connexion.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == 0


def test_budget_journalier_de_recherches(connexion, horloge):
    compteur = CompteurQuota(
        connexion, None, budget_recherches_jour=2, budget_recherches_run=99, horloge=horloge.maintenant
    )
    compteur.enregistrer("search.list", 1)
    compteur.enregistrer("search.list", 1)

    with pytest.raises(BudgetDepasse) as capture:
        compteur.verifier("search.list")
    assert capture.value.compartiment == "search"
    assert capture.value.portee == "jour"
    assert "2/2" in str(capture.value)


def test_budget_par_execution(connexion, horloge):
    compteur = CompteurQuota(
        connexion, 1, budget_recherches_jour=90, budget_recherches_run=1, horloge=horloge.maintenant
    )
    compteur.enregistrer("search.list", 1)

    with pytest.raises(BudgetDepasse) as capture:
        compteur.verifier("search.list")
    assert capture.value.portee == "run"


def test_budget_du_pool_principal(connexion, horloge):
    compteur = CompteurQuota(connexion, None, budget_unites_jour=1, budget_unites_run=1, horloge=horloge.maintenant)
    compteur.enregistrer("channels.list", 1)

    with pytest.raises(BudgetDepasse):
        compteur.verifier("videos.list")


def test_les_totaux_d_execution_sont_isoles(connexion, horloge):
    compteur_run_1 = CompteurQuota(connexion, 1, horloge=horloge.maintenant)
    compteur_run_1.enregistrer("channels.list", 1)

    compteur_run_2 = CompteurQuota(connexion, 2, horloge=horloge.maintenant)
    compteur_run_2.enregistrer("channels.list", 1)

    assert compteur_run_1.totaux_run().unites_principal == 1
    assert compteur_run_2.totaux_run().unites_principal == 1
    # Le journal du jour, lui, additionne les deux exécutions.
    assert compteur_run_1.totaux_jour().unites_principal == 2


def test_le_journal_change_de_jour(connexion, horloge):
    compteur = CompteurQuota(connexion, 1, horloge=horloge.maintenant)
    compteur.enregistrer("channels.list", 1)
    assert compteur.totaux_jour().unites_principal == 1

    # 25 heures plus tard : nouvelle journée Pacifique.
    horloge.avancer(25 * 3600)
    assert compteur.totaux_jour().unites_principal == 0
    assert compteur.totaux_run().unites_principal == 1


def test_rapport_lisible(connexion, horloge):
    compteur = CompteurQuota(connexion, 3, horloge=horloge.maintenant)
    compteur.enregistrer("channels.list", 1)
    rapport = compteur.rapport()
    assert "pool principal" in rapport
    assert "recherches" in rapport
    assert "Cette exécution" in rapport
