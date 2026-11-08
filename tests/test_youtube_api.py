"""Tests du client YouTube : cache, reprises, erreurs et découpage en lots."""

from __future__ import annotations

import pytest

from outly.cache import Cache
from outly.quota import BudgetDepasse, CompteurQuota
from outly.youtube_api import (
    ClientYoutube,
    ErreurApi,
    ErreurQuota,
    ReponseHttp,
)
from tests.conftest import TransportFactice


def _chaine_json(channel_id: str = "UC1", titre: str = "Ma chaîne") -> dict:
    return {
        "items": [
            {
                "id": channel_id,
                "snippet": {
                    "title": titre,
                    "description": "Montage vidéo",
                    "publishedAt": "2020-01-01T00:00:00Z",
                    "customUrl": "@machaine",
                },
                "statistics": {
                    "subscriberCount": "1000",
                    "videoCount": "10",
                    "viewCount": "50000",
                },
                "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
            }
        ]
    }


def _client(config, connexion, horloge, transport, **budgets):
    compteur = CompteurQuota(connexion, None, horloge=horloge.maintenant, **budgets)
    cache = Cache(connexion, horloge=horloge.maintenant)
    client = ClientYoutube(
        "cle-test",
        compteur,
        cache,
        config,
        transport=transport,
        dormir=lambda secondes: None,
    )
    return client, compteur


def test_un_appel_compte_une_unite(config, connexion, horloge):
    transport = TransportFactice([ReponseHttp(200, _chaine_json())])
    client, compteur = _client(config, connexion, horloge, transport)

    chaine = client.chaine_par_handle("@machaine")

    assert chaine is not None
    assert chaine.channel_id == "UC1"
    assert chaine.abonnes == 1000
    assert chaine.playlist_uploads == "UU1"
    assert compteur.totaux_jour().unites_principal == 1
    # La clé API ne doit jamais entrer dans les paramètres du cache.
    assert transport.appels[0][1]["key"] == "cle-test"


def test_le_second_appel_est_servi_par_le_cache(config, connexion, horloge):
    transport = TransportFactice([ReponseHttp(200, _chaine_json())])
    client, compteur = _client(config, connexion, horloge, transport)

    client.chaine_par_handle("machaine")
    client.chaine_par_handle("@machaine")

    assert len(transport.appels) == 1
    assert compteur.totaux_jour().unites_principal == 1


def test_reprise_apres_429(config, connexion, horloge):
    dormies: list[float] = []
    transport = TransportFactice(
        [
            ReponseHttp(429, {"error": {"message": "trop de requêtes"}}, {"Retry-After": "3"}),
            ReponseHttp(200, _chaine_json()),
        ]
    )
    compteur = CompteurQuota(connexion, None, horloge=horloge.maintenant)
    cache = Cache(connexion, horloge=horloge.maintenant)
    client = ClientYoutube(
        "cle", compteur, cache, config, transport=transport, dormir=dormies.append
    )

    assert client.chaine_par_handle("x") is not None
    assert dormies == [3.0]
    # Un 429 n'est pas facturé : une seule unité au total.
    assert compteur.totaux_jour().unites_principal == 1


def test_quota_epuise_leve_une_erreur_dediee(config, connexion, horloge):
    transport = TransportFactice(
        [
            ReponseHttp(
                403,
                {"error": {"errors": [{"reason": "quotaExceeded"}], "message": "quota"}},
            )
        ]
    )
    client, compteur = _client(config, connexion, horloge, transport)

    with pytest.raises(ErreurQuota) as capture:
        client.chaine_par_handle("x")
    assert "Quota YouTube épuisé" in str(capture.value)
    assert compteur.totaux_jour().unites_principal == 0


def test_requete_invalide_est_facturee(config, connexion, horloge):
    transport = TransportFactice(
        [
            ReponseHttp(
                400,
                {
                    "error": {
                        "errors": [{"reason": "invalidCriteria"}],
                        "message": "plusieurs filtres",
                    }
                },
            )
        ]
    )
    client, compteur = _client(config, connexion, horloge, transport)

    with pytest.raises(ErreurApi):
        client.chaine_par_handle("x")
    # La documentation Google indique que les requêtes invalides sont facturées.
    assert compteur.totaux_jour().unites_principal == 1


def test_le_budget_est_verifie_avant_l_appel(config, connexion, horloge):
    transport = TransportFactice()
    client, _ = _client(config, connexion, horloge, transport, budget_unites_jour=0)

    with pytest.raises(BudgetDepasse):
        client.chaine_par_handle("x")
    assert transport.appels == []


def test_chaines_par_ids_par_lots_de_50(config, connexion, horloge):
    premier = {"items": [{"id": f"UC{rang}", "snippet": {"title": f"T{rang}"}} for rang in range(50)]}
    second = {"items": [{"id": "UC_dernier", "snippet": {"title": "Dernier"}}]}
    transport = TransportFactice([ReponseHttp(200, premier), ReponseHttp(200, second)])
    client, compteur = _client(config, connexion, horloge, transport)

    chaines = client.chaines_par_ids([f"UC{rang}" for rang in range(51)])

    assert len(transport.appels) == 2
    assert len(chaines) == 51
    assert compteur.totaux_jour().unites_principal == 2


def test_recherche_internationale_omet_region_et_langue(config, connexion, horloge):
    transport = TransportFactice([ReponseHttp(200, {"items": []})])
    client, _ = _client(config, connexion, horloge, transport)

    client.rechercher_chaines("montage video")

    params = transport.appels[0][1]
    assert "regionCode" not in params
    assert "relevanceLanguage" not in params
    assert params["type"] == "channel"


def test_recherche_locale_envoie_region_et_langue(config, connexion, horloge):
    transport = TransportFactice([ReponseHttp(200, {"items": []})])
    client, _ = _client(config, connexion, horloge, transport)

    client.rechercher_chaines("montage video", pays="FR", langue="fr")

    params = transport.appels[0][1]
    assert params["regionCode"] == "FR"
    assert params["relevanceLanguage"] == "fr"


def test_sections_de_chaine(config, connexion, horloge):
    transport = TransportFactice(
        [
            ReponseHttp(
                200,
                {
                    "items": [
                        {"contentDetails": {"channels": ["UC1", "UC2", "UC1"]}},
                        {"contentDetails": {}},
                    ]
                },
            )
        ]
    )
    client, _ = _client(config, connexion, horloge, transport)

    assert client.sections_chaine("UC_seed") == ["UC1", "UC2"]


def test_extraire_erreur_tolere_une_forme_inattendue():
    from outly.youtube_api import _extraire_erreur

    raison, message = _extraire_erreur({"error": {"errors": "pas une liste"}})
    assert raison is None
    assert "sans message" in message

    raison, message = _extraire_erreur({"error": "refus brut"})
    assert raison is None
    assert message == "refus brut"
