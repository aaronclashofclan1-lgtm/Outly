"""Tests de la carte du cerveau : matière prise en compte et disposition stable."""

from __future__ import annotations

from pathlib import Path

from outly import db
from outly.gui import reseau


def _base(racine: Path):
    dossier = racine / "data"
    dossier.mkdir(parents=True, exist_ok=True)
    conn = db.connexion(dossier / "radar.sqlite3")
    db.migrer(conn)
    return conn


def test_carte_vide_sans_matiere(tmp_path):
    noeuds, liens = reseau.construire(tmp_path)
    assert noeuds == []
    assert liens == []


def test_carte_reprend_les_mots_cles(tmp_path):
    (tmp_path / "keywords.txt").write_text(
        "montage video\nstorytelling\n", encoding="utf-8"
    )
    noeuds, liens = reseau.construire(tmp_path)
    assert {noeud.categorie for noeud in noeuds} == {"mot"}
    assert liens == []


def test_carte_relie_note_mot_cle_et_chaine(tmp_path):
    (tmp_path / "keywords.txt").write_text("montage video\n", encoding="utf-8")
    conn = _base(tmp_path)
    conn.execute(
        "INSERT INTO notes (titre, contenu, type, statut, tags_json, epingle, "
        "cree_le, maj_le) VALUES ('Montage rapide', 'Le montage video est la base', "
        "'hook', 'brouillon', '[\"montage\"]', 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO channels (channel_id, titre, mots_cles_chaine, statut, "
        "ajoute_le, maj_le) VALUES ('UC1', 'Chaîne A', 'montage video,\"tutoriel\"', "
        "'scored', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    noeuds, liens = reseau.construire(tmp_path)
    categories = {noeud.categorie for noeud in noeuds}
    assert any(noeud.cle.startswith("note:") for noeud in noeuds)
    assert "mot" in categories
    assert "chaine" in categories
    assert liens, "les points doivent être reliés"

    reseau.disposer(noeuds)
    positions = [(round(noeud.x, 6), round(noeud.y, 6), round(noeud.z, 6)) for noeud in noeuds]
    reseau.disposer(noeuds)
    assert positions == [
        (round(noeud.x, 6), round(noeud.y, 6), round(noeud.z, 6)) for noeud in noeuds
    ]
    assert all(noeud.degre > 0 for noeud in noeuds if noeud.categorie != "chaine")
