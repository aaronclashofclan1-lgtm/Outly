"""Tests du cœur de calcul : chaque fonction a au moins un cas limite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from outly.metrics import (
    cadence_semaine,
    calculer_metriques,
    est_short,
    jours_depuis,
    mediane,
    repartition,
    score_outlier,
)
from outly.models import Video

MAINTENANT = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Médiane
# ---------------------------------------------------------------------------


def test_mediane_nombre_impair():
    assert mediane([10, 30, 20]) == 20.0


def test_mediane_nombre_pair():
    assert mediane([10, 30, 20, 40]) == 25.0


def test_mediane_vide_ou_absente():
    assert mediane([]) is None
    assert mediane([None, None]) is None


def test_mediane_ignore_les_valeurs_absentes():
    assert mediane([None, 10, 40]) == 25.0


def test_mediane_gere_les_zeros():
    assert mediane([0, 0, 100]) == 0.0


# ---------------------------------------------------------------------------
# Score d'outlier
# ---------------------------------------------------------------------------


def test_score_outlier_simple():
    assert score_outlier(1000, 100) == 10.0


def test_score_outlier_arrondi():
    assert score_outlier(1000, 3) == 333.33


def test_score_outlier_mediane_nulle_ne_divise_pas():
    assert score_outlier(1000, 0) is None


def test_score_outlier_donnees_manquantes():
    assert score_outlier(None, 100) is None
    assert score_outlier(1000, None) is None


# ---------------------------------------------------------------------------
# Short / vidéo longue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duree", "attendu"),
    [(1, 1), (179, 1), (180, 1), (181, 0), (3600, 0)],
)
def test_detection_short_au_seuil_180(duree, attendu):
    assert est_short(duree, 180) == attendu


def test_detection_short_seuil_configurable():
    assert est_short(90, 60) == 0
    assert est_short(59, 60) == 1


def test_detection_short_duree_inconnue():
    assert est_short(None) is None


def test_detection_short_exclut_les_directs():
    assert est_short(120, 180, live="live") is None
    assert est_short(120, 180, live="upcoming") is None
    assert est_short(120, 180, live="none") == 1


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


def test_cadence_deux_videos_sur_deux_semaines():
    dates = [MAINTENANT, MAINTENANT - timedelta(days=14)]
    assert cadence_semaine(dates) == 1.0


def test_cadence_huit_videos_sur_quatre_semaines():
    dates = [MAINTENANT - timedelta(days=4 * rang) for rang in range(8)]
    assert cadence_semaine(dates) == 2.0


def test_cadence_indeterminable():
    assert cadence_semaine([]) is None
    assert cadence_semaine([MAINTENANT]) is None
    # Moins de sept jours d'amplitude : la division serait trompeuse.
    assert cadence_semaine([MAINTENANT, MAINTENANT - timedelta(days=5)]) is None


def test_jours_depuis():
    assert jours_depuis(MAINTENANT - timedelta(days=10), MAINTENANT) == 10
    assert jours_depuis(None, MAINTENANT) is None


def test_repartition():
    assert repartition(3, 0) == "shorts"
    assert repartition(0, 3) == "longues"
    assert repartition(2, 2) == "mixte"
    assert repartition(0, 0) == "indetermine"


# ---------------------------------------------------------------------------
# Agrégation par chaîne
# ---------------------------------------------------------------------------


def _video(video_id, duree, vues, jours, live="none"):
    return Video(
        video_id=video_id,
        channel_id="UC_test",
        titre=f"Vidéo {video_id}",
        publie_le=(MAINTENANT - timedelta(days=jours)).isoformat(timespec="seconds"),
        duree_s=duree,
        vues=vues,
        est_short=est_short(duree, 180, live),
        live=live,
    )


def test_metriques_separent_les_formats():
    """Un Short énorme ne doit pas gonfler la médiane des vidéos longues."""
    videos = [
        _video("s1", 120, 10_000, 1),
        _video("s2", 60, 30_000, 8),
        _video("l1", 600, 1_000, 15),
        _video("l2", 900, 3_000, 22),
    ]
    metriques = calculer_metriques("UC_test", videos, 180, 30, MAINTENANT)

    assert metriques.n_shorts == 2
    assert metriques.n_longues == 2
    assert metriques.mediane_vues_short == 20_000.0
    assert metriques.mediane_vues_longues == 2_000.0
    assert metriques.mix_format == "mixte"
    # x1,5 dans les deux formats (30 000/20 000 et 3 000/2 000)
    assert metriques.meilleur_outlier == 1.5


def test_metriques_ignorent_les_directs_et_les_durees_inconnues():
    videos = [
        _video("l1", 600, 5_000, 3),
        _video("l2", 700, 15_000, 10),
        _video("live", 600, 9_999_999, 1, live="live"),
        _video("flou", None, 8_888_888, 2),
    ]
    metriques = calculer_metriques("UC_test", videos, 180, 30, MAINTENANT)

    assert metriques.n_videos == 4
    assert metriques.n_longues == 2
    assert metriques.n_shorts == 0
    # Le direct et la durée inconnue n'entrent dans aucune des deux populations.
    assert metriques.n_inconnues == 2
    assert metriques.mediane_vues_longues == 10_000.0
    assert metriques.meilleur_outlier == 1.5


def test_metriques_chaîne_vide():
    metriques = calculer_metriques("UC_test", [], 180, 30, MAINTENANT)
    assert metriques.n_videos == 0
    assert metriques.mediane_vues_longues is None
    assert metriques.meilleur_outlier is None
    assert metriques.mix_format == "indetermine"
    assert metriques.part_shorts is None


def test_metriques_fenetre_limite_les_videos():
    videos = [_video(f"v{rang}", 600, 1_000, rang) for rang in range(50)]
    metriques = calculer_metriques("UC_test", videos, 180, 30, MAINTENANT)
    assert metriques.fenetre == 30
    assert metriques.n_videos == 30
