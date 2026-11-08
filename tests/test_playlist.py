"""Tests de la playlist locale « À regarder plus tard ».

Aucun réseau : la playlist ne lit que la base SQLite du projet. Les ajouts
répétés doivent mettre à jour l'entrée existante, jamais créer de doublon.
"""

from __future__ import annotations

import pytest

from outly import db, playlist


def _video(video_id: str = "vid-1", **extra):
    return {
        "video_id": video_id,
        "channel_id": "UC_a",
        "titre": "Une vidéo outlier",
        "vues": 12_345,
        "duree_s": 125,
        "est_short": False,
        "score_outlier": 4.2,
        "publie_le": "2026-10-01T10:00:00+00:00",
        **extra,
    }


def test_playlist_vide(tmp_path):
    assert playlist.lister(tmp_path) == []


def test_ajout_video_et_chaine(tmp_path):
    playlist.ajouter_video(tmp_path, _video(), "Chaîne A")
    playlist.ajouter_chaine(
        tmp_path, "UC_a", "Chaîne A", "@chainea", 12_000, "Description de la chaîne"
    )

    elements = playlist.lister(tmp_path)
    assert len(elements) == 2

    video = next(element for element in elements if element.type == "video")
    assert video.element_id == "vid-1"
    assert video.url == "https://www.youtube.com/watch?v=vid-1"
    assert "Chaîne A" in video.sous_titre
    assert "×4,2" in video.sous_titre
    assert video.meta["duree_s"] == 125

    chaine = next(element for element in elements if element.type == "channel")
    assert chaine.etiquette_type == "Chaîne"
    assert chaine.url == "https://www.youtube.com/channel/UC_a"
    assert "@chainea" in chaine.sous_titre
    assert "12 000 abonnés" in chaine.sous_titre
    assert chaine.meta["description"] == "Description de la chaîne"


def test_doublon_met_a_jour_sans_dupliquer(tmp_path):
    playlist.ajouter_video(tmp_path, _video(), "Chaîne A")
    playlist.ajouter_video(tmp_path, _video(titre="Titre corrigé", vues=99_999), "Chaîne B")

    elements = playlist.lister(tmp_path)
    assert len(elements) == 1
    assert elements[0].titre == "Titre corrigé"
    assert "99 999" in elements[0].sous_titre


def test_retirer_et_vider(tmp_path):
    playlist.ajouter_video(tmp_path, _video("vid-1"), "Chaîne A")
    playlist.ajouter_video(tmp_path, _video("vid-2"), "Chaîne A")
    playlist.ajouter_chaine(tmp_path, "UC_a", "Chaîne A")

    assert playlist.contient(tmp_path, "vid-1", "video")
    assert playlist.retirer(tmp_path, "vid-1", "video")
    assert not playlist.retirer(tmp_path, "vid-1", "video")
    assert not playlist.contient(tmp_path, "vid-1", "video")
    assert len(playlist.lister(tmp_path)) == 2

    assert playlist.vider(tmp_path) == 2
    assert playlist.lister(tmp_path) == []


def test_classement_sur_dix_et_priorite(tmp_path):
    playlist.ajouter_video(tmp_path, _video("vid-1"), "Chaîne A")
    playlist.ajouter_video(tmp_path, _video("vid-2"), "Chaîne A")

    assert playlist.noter(tmp_path, "vid-2", "video", "8/10")
    # Réenregistrer la vidéo ne doit pas effacer son classement.
    playlist.ajouter_video(tmp_path, _video("vid-2", titre="Titre mis à jour"), "Chaîne A")

    elements = playlist.lister(tmp_path)
    assert elements[0].element_id == "vid-2"
    assert elements[0].note_sur_10 == 8
    assert elements[0].note_lisible == "8/10"
    assert elements[1].note_sur_10 is None

    assert playlist.noter(tmp_path, "vid-1", "video", None)
    assert playlist.lister(tmp_path)[1].note_sur_10 is None
    with pytest.raises(playlist.ErreurPlaylist):
        playlist.noter(tmp_path, "vid-1", "video", 12)


def test_contexte_pour_le_cerveau_classe_les_videos(tmp_path):
    playlist.ajouter_video(tmp_path, _video("vid-1", titre="Vidéo moyenne"), "Chaîne A")
    playlist.ajouter_video(tmp_path, _video("vid-2", titre="Vidéo forte"), "Chaîne A")
    playlist.noter(tmp_path, "vid-1", "video", 3)
    playlist.noter(tmp_path, "vid-2", "video", 9)

    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    bloc, instantane = playlist.contexte_pour_cerveau(conn)
    conn.close()

    assert "classement 9/10" in bloc
    assert bloc.index("Vidéo forte") < bloc.index("Vidéo moyenne")
    assert [element["titre"] for element in instantane][0] == "Vidéo forte"
    assert instantane[0]["note_sur_10"] == 9


def test_type_invalide_refuse(tmp_path):
    with pytest.raises(playlist.ErreurPlaylist):
        playlist.ajouter(
            tmp_path,
            element_id="x",
            type="podcast",
            titre="Inconnu",
        )


def test_video_sans_identifiant_refusee(tmp_path):
    with pytest.raises(playlist.ErreurPlaylist):
        playlist.ajouter_video(tmp_path, {"titre": "Sans identifiant"})
