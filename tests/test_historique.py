from __future__ import annotations

from outly import db, historique, studio


def test_serie_cumulee_agrege_les_jours_et_ignore_les_dates_invalides():
    points = historique.serie_cumulee(
        [
            {"publie_le": "2026-01-01T10:00:00Z", "vues": 100},
            {"publie_le": "2026-01-01T18:00:00Z", "vues": 50},
            {"publie_le": "2026-01-03", "vues": 25},
            {"publie_le": "", "vues": 999},
            {"publie_le": "2026-01-04", "vues": None},
        ]
    )
    assert [point["jour"] for point in points] == ["2026-01-01", "2026-01-03"]
    assert [point["cumul"] for point in points] == [150, 175]
    assert points[0]["videos"] == 2


def test_chaines_et_serie_excluent_ma_chaine(tmp_path):
    conn = db.connexion(tmp_path / "radar.sqlite3")
    db.migrer(conn)
    for channel_id, titre, statut, total, source, est_seed in (
        ("UC-moi", "Moi", "moi", 9999, "moi", 1),
        ("UC-a", "Alpha", "scored", 1000, "mot-cle:montage", 0),
        ("UC-b", "Beta", "enrichie", None, "section", 0),
        ("UC-seed", "Seed", "enrichie", 500, "seed", 1),
        ("UC-manuel", "Manuel", "enrichie", 500, None, 0),
    ):
        conn.execute(
            "INSERT INTO channels (channel_id, titre, statut, vues_totales, source, est_seed,"
            " ajoute_le, maj_le) VALUES (?, ?, ?, ?, ?, ?, '2026-01-01', '2026-01-01')",
            (channel_id, titre, statut, total, source, est_seed),
        )
    for video_id, channel_id, date, vues, likes, commentaires in (
        ("v1", "UC-a", "2026-01-01", 100, 12, 3),
        ("v2", "UC-a", "2026-01-02", 200, 20, 4),
        ("v3", "UC-b", "2026-01-01", 10, 1, 0),
        ("v4", "UC-moi", "2026-01-01", 500, 50, 8),
        ("v5", "UC-seed", "2026-01-01", 400, 40, 6),
        ("v6", "UC-manuel", "2026-01-01", 300, 30, 5),
    ):
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, titre, publie_le, vues, likes, "
            "commentaires, recupere_le) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-01-02')",
            (video_id, channel_id, video_id, date, vues, likes, commentaires),
        )
    conn.commit()

    chaines = historique.chaines(conn)
    # Seules les chaînes trouvées par la veille sont proposées : les seeds,
    # les fiches sans source et « ma chaîne » restent hors du sélecteur.
    assert [chaine["channel_id"] for chaine in chaines] == ["UC-a", "UC-b"]
    serie = historique.serie_chaine(conn, "UC-a")
    assert serie["total_connu"] == 300
    assert serie["couverture"] == 30.0
    assert serie["points"][-1]["cumul"] == 300
    assert [video["video_id"] for video in serie["videos"]] == ["v2", "v1"]
    assert serie["videos"][0]["likes"] == 20
    assert serie["videos"][0]["commentaires"] == 4
    assert historique.serie_chaine(conn, "inconnue") is None
    conn.close()


def test_classement_des_formats():
    assert studio.type_video({"duree_s": 60, "live": "none"}) == "short"
    assert studio.type_video({"duree_s": 600, "live": "none"}) == "longue"
    assert studio.type_video({"duree_s": 600, "live": "was_live"}) == "autre"
    assert studio.type_video({"duree_s": 0}) == "autre"
    videos = [
        {"duree_s": 600, "live": "none"},
        {"duree_s": 60, "live": "none"},
        {"duree_s": 600, "live": "live"},
    ]
    assert len(studio.filtrer_videos(videos, "longue")) == 1
    assert len(studio.filtrer_videos(videos, "short")) == 1
    assert len(studio.filtrer_videos(videos, "autre")) == 1
