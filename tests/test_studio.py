"""Tests du Studio YouTube : OAuth, rapports, rétention et cache.

Aucun appel réseau : les transports HTTP sont remplacés par des réponses
programmées, comme pour le reste du projet.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from outly import db, studio
from outly.gui import data
from outly.youtube_api import ReponseHttp


class TransportRapports:
    """Répond aux endpoints Data API et Analytics utilisés par `collecter`."""

    def __init__(self, impressions_ok: bool = True):
        self.impressions_ok = impressions_ok
        self.appels: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict, entetes: dict, timeout: int) -> ReponseHttp:
        self.appels.append((url, dict(params)))
        if url.endswith("/channels"):
            return ReponseHttp(
                200,
                {
                    "items": [
                        {
                            "id": "UC-skynt",
                            "snippet": {"title": "Skynt"},
                            "statistics": {
                                "subscriberCount": "1200",
                                "viewCount": "90000",
                                "videoCount": "42",
                            },
                        }
                    ]
                },
            )
        if url.endswith("/videos"):
            identifiants = str(params.get("id") or "").split(",")
            return ReponseHttp(
                200,
                {
                    "items": [
                        {
                            "id": identifiant,
                            "snippet": {
                                "title": f"Vidéo {identifiant}",
                                "publishedAt": "2026-10-01T10:00:00Z",
                            },
                            "contentDetails": {"duration": "PT12M30S"},
                        }
                        for identifiant in identifiants
                        if identifiant
                    ]
                },
            )
        dimensions = params.get("dimensions")
        metriques = str(params.get("metrics") or "")
        if "videoThumbnailImpressions" in metriques:
            if not self.impressions_ok:
                return ReponseHttp(
                    400,
                    {"error": {"message": "The query is not supported."}},
                )
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [
                        {"name": "video"},
                        {"name": "videoThumbnailImpressions"},
                        {"name": "videoThumbnailImpressionsClickRate"},
                    ],
                    "rows": [["v1", 1000, 5.0], ["v2", 500, 4.0]],
                },
            )
        if dimensions == "day":
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [
                        {"name": "day"},
                        {"name": "views"},
                        {"name": "estimatedMinutesWatched"},
                    ],
                    "rows": [["2026-11-01", 10, 30.0], ["2026-11-02", 20, 50.0]],
                },
            )
        if dimensions == "video":
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [
                        {"name": "video"},
                        {"name": "views"},
                        {"name": "estimatedMinutesWatched"},
                        {"name": "averageViewDuration"},
                        {"name": "averageViewPercentage"},
                        {"name": "likes"},
                        {"name": "comments"},
                        {"name": "shares"},
                    ],
                    "rows": [
                        ["v1", 100, 400.0, 240.0, 40.0, 5, 2, 1],
                        ["v2", 50, 100.0, 120.0, 30.0, 1, 0, 0],
                    ],
                },
            )
        if dimensions == "insightTrafficSourceType":
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [
                        {"name": "insightTrafficSourceType"},
                        {"name": "views"},
                    ],
                    "rows": [["YT_SEARCH", 90], ["RELATED_VIDEO", 40]],
                },
            )
        if dimensions == "deviceType":
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [{"name": "deviceType"}, {"name": "views"}],
                    "rows": [["MOBILE", 110], ["DESKTOP", 20]],
                },
            )
        if dimensions == "ageGroup,gender":
            return ReponseHttp(
                200,
                {
                    "columnHeaders": [
                        {"name": "ageGroup"},
                        {"name": "gender"},
                        {"name": "viewerPercentage"},
                    ],
                    "rows": [["age25-34", "male", 42.5], ["age18-24", "female", 31.0]],
                },
            )
        return ReponseHttp(
            200,
            {
                "columnHeaders": [
                    {"name": "views"},
                    {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"},
                    {"name": "averageViewPercentage"},
                    {"name": "subscribersGained"},
                    {"name": "subscribersLost"},
                    {"name": "likes"},
                    {"name": "comments"},
                    {"name": "shares"},
                ],
                "rows": [[150, 500.0, 200.0, 38.0, 10, 2, 6, 3, 1]],
            },
        )


def _client(transport: TransportRapports | None = None) -> studio.StudioClient:
    return studio.StudioClient("jeton", transport=transport or TransportRapports())


def test_url_autorisation_demande_offline_et_les_deux_portees():
    url = studio.url_autorisation(
        "client.apps.googleusercontent.com", "http://127.0.0.1:4567/", "etat-123"
    )
    params = parse_qs(urlparse(url).query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert params["state"] == ["etat-123"]
    assert "yt-analytics.readonly" in params["scope"][0]
    assert "youtube.readonly" in params["scope"][0]
    assert params["redirect_uri"] == ["http://127.0.0.1:4567/"]


def test_echange_code_refuse_un_jeton_sans_refresh_token():
    def poster(url, donnees, timeout):
        assert donnees["grant_type"] == "authorization_code"
        return ReponseHttp(200, {"access_token": "court"})

    with pytest.raises(studio.ErreurStudio, match="jeton durable"):
        studio.echanger_code("id", "secret", "code", "http://127.0.0.1:1/", poster=poster)


def test_echange_code_et_rafraichissement():
    appels: list[dict] = []

    def poster(url, donnees, timeout):
        appels.append(dict(donnees))
        return ReponseHttp(
            200, {"refresh_token": "durable", "access_token": "acces"}
        )

    donnees = studio.echanger_code(
        "id", "secret", "code", "http://127.0.0.1:1/", poster=poster
    )
    assert donnees["refresh_token"] == "durable"
    assert studio.rafraichir_jeton("id", "secret", "durable", poster=poster) == "acces"
    assert appels[0]["grant_type"] == "authorization_code"
    assert appels[1]["grant_type"] == "refresh_token"
    assert appels[1]["refresh_token"] == "durable"


def test_rafraichissement_refuse_explique_le_mode_test():
    def poster(url, donnees, timeout):
        return ReponseHttp(400, {"error": "invalid_grant"})

    with pytest.raises(studio.ErreurJeton, match="expiré"):
        studio.rafraichir_jeton("id", "secret", "durable", poster=poster)


def test_client_rafraichit_une_fois_sur_401():
    reponses = [
        ReponseHttp(401, {"error": {"message": "expired"}}),
        ReponseHttp(200, {"ok": True}),
    ]
    entetes_vus: list[dict] = []

    def transport(url, params, entetes, timeout):
        entetes_vus.append(dict(entetes))
        return reponses.pop(0)

    client = studio.StudioClient(
        "ancien", rafraichir=lambda: "nouveau", transport=transport
    )
    assert client._get("https://example.test", {}) == {"ok": True}
    assert client.jeton == "nouveau"
    assert entetes_vus[0]["Authorization"] == "Bearer ancien"
    assert entetes_vus[1]["Authorization"] == "Bearer nouveau"


def test_plage_se_termine_hier():
    debut, fin = studio.plage(28, date(2026, 11, 7))
    assert fin == "2026-11-06"
    assert debut == "2026-10-10"


def test_duree_iso():
    assert studio.duree_iso("PT12M30S") == 750
    assert studio.duree_iso("PT1H4M32S") == 3872
    assert studio.duree_iso("P1DT1M") == 86460
    assert studio.duree_iso("") == 0


def test_collecter_rassemble_les_indicateurs():
    transport = TransportRapports()
    tableau = studio.collecter(
        _client(transport), jours=28, max_videos=10, aujourd_hui=date(2026, 11, 7)
    )
    assert tableau.chaine == "Skynt"
    assert tableau.abonnes == 1200
    assert tableau.vues == 150
    assert tableau.minutes == 500.0
    assert tableau.pourcentage_vu == 38.0
    assert tableau.abonnes_gagnes == 10
    assert tableau.abonnes_perdus == 2
    assert tableau.debut == "2026-10-10"
    assert tableau.fin == "2026-11-06"
    assert len(tableau.serie_jours) == 2
    assert tableau.videos[0].titre == "Vidéo v1"
    assert tableau.videos[0].duree_s == 750
    assert tableau.videos[0].impressions == 1000
    assert tableau.videos[0].ctr == 5.0
    assert tableau.impressions == 1500
    assert tableau.ctr == pytest.approx((1000 * 5.0 + 500 * 4.0) / 1500)
    assert [source["cle"] for source in tableau.sources] == ["YT_SEARCH", "RELATED_VIDEO"]
    assert [appareil["cle"] for appareil in tableau.appareils] == ["MOBILE", "DESKTOP"]
    assert len(tableau.demographie) == 2
    assert tableau.notes == []
    jour = next(
        params
        for url, params in transport.appels
        if params.get("dimensions") == "day" and url.endswith("/reports")
    )
    assert jour["startDate"] == "2026-10-10"
    assert jour["endDate"] == "2026-11-06"


def test_collecter_tolere_des_impressions_refusees():
    tableau = studio.collecter(
        _client(TransportRapports(impressions_ok=False)),
        jours=7,
        aujourd_hui=date(2026, 11, 7),
    )
    assert tableau.vues == 150
    assert tableau.impressions is None
    assert tableau.videos[0].impressions is None
    assert any("Impressions" in note for note in tableau.notes)


def test_retention_trie_et_borne_les_points():
    def transport(url, params, entetes, timeout):
        assert params["filters"] == "video==v1"
        assert params["dimensions"] == "elapsedVideoTimeRatio"
        assert params["startDate"] == "2026-10-10"
        return ReponseHttp(
            200,
            {
                "columnHeaders": [
                    {"name": "elapsedVideoTimeRatio"},
                    {"name": "audienceWatchRatio"},
                    {"name": "relativeRetentionPerformance"},
                ],
                "rows": [
                    [0.5, 0.6, 0.1],
                    [0.0, 1.0, 0.0],
                    [0.5, 0.6, 0.1],
                    [1.0, 0.3, -0.2],
                    [1.4, 0.1, -0.4],
                ],
            },
        )

    points = studio.retention(
        studio.StudioClient("jeton", transport=transport), "v1", "2026-10-10", "2026-11-06"
    )
    assert [point["ratio"] for point in points] == [0.0, 0.5, 1.0]
    assert points[1]["maintien"] == 0.6


def test_cache_aller_retour(tmp_path: Path):
    tableau = studio.Tableau(
        chaine="Skynt",
        debut="2026-10-10",
        fin="2026-11-06",
        vues=12,
        videos=[studio.Video(identifiant="v1", titre="Une vidéo")],
    )
    chemin = studio.ecrire_cache(tmp_path, tableau)
    assert chemin == tmp_path / "data" / "studio.json"
    relu = studio.lire_cache(tmp_path)
    assert relu["chaine"] == "Skynt"
    assert relu["vues"] == 12
    assert relu["videos"][0]["titre"] == "Une vidéo"
    assert relu["notes"] == []
    assert json.loads(chemin.read_text(encoding="utf-8"))["fin"] == "2026-11-06"


def test_cache_illisible_est_ignore(tmp_path: Path):
    chemin = tmp_path / "data" / "studio.json"
    chemin.parent.mkdir(parents=True)
    chemin.write_text("{pas du json", encoding="utf-8")
    assert studio.lire_cache(tmp_path) is None


def test_matiere_cerveau_expose_recherches_et_idees(tmp_path: Path):
    dossier = {
        "sources": [
            {"domaine": "lemonde.fr"},
            {"domaine": "insee.fr"},
            {"domaine": "lemonde.fr"},
        ],
        "faits": [
            {"affirmation": "Fait A", "statut": "recoupe"},
            {"affirmation": "Fait B", "statut": "a_verifier"},
        ],
    }
    chemin = tmp_path / "data" / "radar.sqlite3"
    chemin.parent.mkdir(parents=True)
    conn = db.connexion(chemin)
    db.migrer(conn)
    conn.execute(
        """
        INSERT INTO research_sessions (
            titre, mots_cles_json, mode, empreinte, prompt_version,
            sources_json, pages_json, dossier_json, valide, cree_le
        ) VALUES ('La chaleur en ville', '[]', 'avec_ia', 'r1', 'recherche-v1',
                  '[]', '[]', ?, 1, '2026-11-02T10:00:00+00:00')
        """,
        (json.dumps(dossier),),
    )
    conn.execute(
        """
        INSERT INTO idea_sets (
            question, nombre, empreinte, prompt_version, introduction,
            idees_json, notes_json, valide, cree_le
        ) VALUES ('', 2, 'i1', 'ideas-v1', 'Deux pistes.',
                  ?, '[]', 1, '2026-11-03T10:00:00+00:00')
        """,
        (
            json.dumps(
                [
                    {"sujet": "La ville qui chauffe la nuit"},
                    {"sujet": "Trois arbres qui changent une rue"},
                ],
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    matiere = data.matiere_cerveau(tmp_path)
    assert len(matiere["recherches"]) == 1
    recherche = matiere["recherches"][0]
    assert recherche["titre"] == "La chaleur en ville"
    assert recherche["nb_sources"] == 3
    assert recherche["nb_faits"] == 1
    assert recherche["domaines"] == ["lemonde.fr", "insee.fr"]
    assert matiere["idees"][0]["nb_idees"] == 2
    assert "La ville qui chauffe la nuit" in matiere["idees"][0]["sujets"]
