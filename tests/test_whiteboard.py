"""Géométrie du tableau blanc : formes ajoutées et découpe des traits."""
from __future__ import annotations

import math

from outly.gui.whiteboard_page import POLYGONES, _densifier, _distance_segment, _forme_points

NOUVELLES_FORMES = [
    "triangle",
    "losange",
    "pentagone",
    "hexagone",
    "octogone",
    "etoile",
    "parallelogramme",
    "trapeze",
    "croix",
    "double_fleche",
]


def test_dix_formes_geometriques_en_plus():
    assert len(NOUVELLES_FORMES) == 10
    assert set(NOUVELLES_FORMES) - {"double_fleche"} <= POLYGONES
    for code in NOUVELLES_FORMES:
        if code == "double_fleche":
            continue
        points = _forme_points(code, 10, 20, 110, 120)
        assert len(points) >= 6
        assert all(math.isfinite(valeur) for valeur in points)
        assert min(points[0::2]) >= 9.9
        assert max(points[0::2]) <= 110.1
        assert min(points[1::2]) >= 19.9
        assert max(points[1::2]) <= 120.1


def test_etoile_et_croix_ont_leurs_points_propres():
    assert len(_forme_points("etoile", 0, 0, 100, 100)) == 20
    assert len(_forme_points("croix", 0, 0, 100, 100)) == 24
    assert len(_forme_points("hexagone", 0, 0, 100, 100)) == 12


def test_densifier_conserve_les_extremites_et_ajoute_des_points():
    points = _densifier([[0, 0], [100, 0]], 10)
    assert len(points) == 11
    assert points[0] == [0, 0]
    assert points[-1] == [100.0, 0.0]
    assert points[5] == [50.0, 0.0]


def test_distance_au_segment():
    assert _distance_segment(5, 3, 0, 0, 10, 0) == 3
    assert _distance_segment(-2, 0, 0, 0, 10, 0) == 2
    assert _distance_segment(5, 4, 5, 5, 5, 5) == 1
