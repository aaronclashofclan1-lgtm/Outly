"""Test d'intégration du pipeline complet, sans réseau ni quota réel.

Il vérifie trois choses que les tests unitaires ne couvrent pas :
  * le câblage entre découverte, enrichissement, scoring et rapport ;
  * la comptabilité de quota de bout en bout ;
  * le fait qu'aucune étape ne consomme `search.list` pour lister des vidéos.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from outly import db, discovery, enrichment, report
from outly.cache import Cache
from outly.llm.client import ReponseLlm
from outly.llm.scoring import scorer
from outly.quota import CompteurQuota
from outly.youtube_api import ClientYoutube, ReponseHttp

MAINTENANT = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

SCORE_VALIDE = {
    "score_proximite": 80,
    "recouvrement_audience": "fort",
    "format_detecte": "voix_off",
    "langue": "fr",
    "justification": (
        "Le sujet et la promesse recoupent exactement le positionnement de la marque. "
        "Le format en voix-off et l'audience débutante confirment la concurrence directe."
    ),
    "risques": ["aborde parfois un sujet exclu"],
}


def _chaine(channel_id: str, titre: str, abonnes: int = 50_000) -> dict:
    return {
        "id": channel_id,
        "snippet": {
            "title": titre,
            "description": "Montage vidéo et étalonnage pour créateurs débutants.",
            "publishedAt": "2020-01-01T00:00:00Z",
            "customUrl": f"@{channel_id.lower()}",
        },
        "statistics": {
            "subscriberCount": str(abonnes),
            "videoCount": "4",
            "viewCount": "100000",
        },
        "contentDetails": {"relatedPlaylists": {"uploads": f"UU{channel_id}"}},
    }


def _videos_de(channel_id: str) -> list[dict]:
    dates = [
        (MAINTENANT - timedelta(days=7 * rang)).isoformat(timespec="seconds") for rang in range(4)
    ]
    return [
        {
            "video_id": f"{channel_id}_v{rang}",
            "titre": f"Vidéo {rang} de {channel_id}",
            "date": dates[rang],
            "vues": [10_000, 1_000, 1_000, 5_000][rang],
            "duree": "PT10M" if rang < 3 else "PT2M",
        }
        for rang in range(4)
    ]


class RouteurYoutube:
    """Répond selon l'endpoint, comme le ferait l'API réelle."""

    def __init__(self):
        self.appels: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict, timeout: int) -> ReponseHttp:
        self.appels.append((url, dict(params)))

        if "channelSections" in url:
            return ReponseHttp(200, {"items": []})
        if "channels" in url:
            if "forHandle" in params:
                return ReponseHttp(200, {"items": [_chaine("UC_seed", "Chaîne seed")]})
            identifiants = params["id"].split(",")
            return ReponseHttp(
                200,
                {"items": [_chaine(identifiant, f"Chaîne {identifiant}", 20_000) for identifiant in identifiants]},
            )
        if "search" in url:
            return ReponseHttp(
                200, {"items": [{"id": {"channelId": "UC_new1"}, "snippet": {"title": "Nouvelle"}}]}
            )
        if "playlistItems" in url:
            channel_id = params["playlistId"][2:]  # enlève le préfixe « UU »
            return ReponseHttp(
                200,
                {
                    "items": [
                        {
                            "snippet": {
                                "title": video["titre"],
                                "publishedAt": video["date"],
                                "resourceId": {"videoId": video["video_id"]},
                            }
                        }
                        for video in _videos_de(channel_id)
                    ]
                },
            )
        if "/videos" in url:
            details = {}
            for identifiant in params["id"].split(","):
                channel_id, rang = identifiant.rsplit("_v", 1)
                video = next(v for v in _videos_de(channel_id) if v["video_id"] == identifiant)
                details[identifiant] = {
                    "id": identifiant,
                    "snippet": {
                        "title": video["titre"],
                        "publishedAt": video["date"],
                        "liveBroadcastContent": "none",
                    },
                    "statistics": {"viewCount": str(video["vues"])},
                    "contentDetails": {"duration": video["duree"]},
                }
            return ReponseHttp(200, {"items": list(details.values())})

        raise AssertionError(f"Endpoint inattendu : {url}")


class FauxServiceLlm:
    """Service LLM déterministe, sans réseau."""

    def __init__(self, textes: list[str] | None = None):
        self.appels = 0
        self.messages: list[str] = []
        self.nom_fournisseur = "faux"
        self.nom_modele = "modele-test"
        self._textes = list(textes or [])

    def completer(self, messages: list[dict]) -> ReponseLlm:
        self.appels += 1
        texte = self._textes.pop(0) if self._textes else json.dumps(SCORE_VALIDE)
        return ReponseLlm(texte=texte, modele="modele-test", fournisseur="faux")


@pytest.fixture
def config_pipeline(config):
    config.racine.mkdir(parents=True, exist_ok=True)
    (config.racine / "positioning.md").write_text(
        "Client idéal : créateur débutant qui monte ses vidéos seul. "
        "Promesse : des tutos concrets de montage et d'étalonnage. "
        "Format : voix-off. Sujets exclus : jeux vidéo.",
        encoding="utf-8",
    )
    (config.racine / "seeds.txt").write_text("@chainedeseed\n", encoding="utf-8")
    (config.racine / "keywords.txt").write_text("montage vidéo\n", encoding="utf-8")
    config.youtube.videos_per_channel = 4
    config.metriques.min_videos_for_metrics = 3
    return config


def test_pipeline_complet(config_pipeline, connexion, horloge):
    config = config_pipeline
    routeur = RouteurYoutube()
    compteur = CompteurQuota(connexion, 1, horloge=horloge.maintenant)
    cache = Cache(connexion, horloge=horloge.maintenant)
    client = ClientYoutube("cle-test", compteur, cache, config, transport=routeur)

    # --- Découverte -------------------------------------------------------
    resume = discovery.decouvrir(connexion, client, config)
    assert resume.seeds_resolus == 1
    assert resume.candidats_analysables == 2
    # Deux passes par requête (France puis international) : 1 requête = 2 appels.
    assert resume.recherches == 2
    assert resume.requetes_utilisees == 1

    # --- Enrichissement ---------------------------------------------------
    enrichissement = enrichment.enrichir(connexion, client, config)
    assert enrichissement.chaines_traitees == 2
    assert enrichissement.videos_ajoutees == 8
    assert enrichissement.erreurs == 0

    # --- Métriques --------------------------------------------------------
    metriques = db.lire_metriques(connexion, "UC_new1")
    assert metriques is not None
    assert metriques.mediane_vues_longues == 1_000.0
    assert metriques.mediane_vues_short == 5_000.0
    assert metriques.n_shorts == 1
    assert metriques.n_longues == 3

    # --- Scoring ----------------------------------------------------------
    service = FauxServiceLlm()
    resume_score = scorer(connexion, config, service, run_id=1)
    assert resume_score.valides == 2
    assert resume_score.invalides == 0
    assert service.appels == 2

    ligne = connexion.execute(
        "SELECT score_proximite, justification FROM channel_scores WHERE channel_id = 'UC_seed'"
    ).fetchone()
    assert ligne["score_proximite"] == 80
    assert "positionnement" in ligne["justification"]

    # --- Rapport ----------------------------------------------------------
    resultat = report.generer(connexion, config)
    assert resultat["evaluees"] == 2
    assert resultat["retenues"] == 2
    # Une seule vidéo par chaîne dépasse la médiane de son format.
    assert resultat["nb_outliers"] == 2

    contenu = (config.dossier_sortie / "report.md").read_text(encoding="utf-8")
    assert "Chaîne seed" in contenu
    assert "80/100" in contenu

    # --- Quota ------------------------------------------------------------
    # 1 handle + 1 sections + 1 lot de chaînes + 2 playlists + 2 lots de vidéos.
    # Les 2 recherches (France puis monde) sont comptées à part.
    totaux = compteur.totaux_jour()
    assert totaux.unites_recherche == 2
    assert totaux.unites_principal == 7

    # Aucune vidéo n'a été listée via search.list.
    recherches = [appel for appel in routeur.appels if "search" in appel[0]]
    assert len(recherches) == 2
    assert all(appel[1]["type"] == "channel" for appel in recherches)


def test_pipeline_ne_repaie_pas_le_scoring(config_pipeline, connexion, horloge):
    """Relancer le scoring sur des données inchangées ne doit coûter aucun appel."""
    config = config_pipeline
    routeur = RouteurYoutube()
    compteur = CompteurQuota(connexion, 1, horloge=horloge.maintenant)
    cache = Cache(connexion, horloge=horloge.maintenant)
    client = ClientYoutube("cle-test", compteur, cache, config, transport=routeur)

    discovery.decouvrir(connexion, client, config)
    enrichment.enrichir(connexion, client, config)

    premier = FauxServiceLlm()
    scorer(connexion, config, premier, run_id=1)
    assert premier.appels == 2

    second = FauxServiceLlm()
    resume = scorer(connexion, config, second, run_id=2)
    assert resume.deja_scorees == 2
    assert second.appels == 0
