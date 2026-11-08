"""Tests du cache local."""

from __future__ import annotations

from datetime import timedelta

from outly.cache import Cache


def test_empreinte_stable_quel_que_soit_l_ordre_des_parametres():
    premier = Cache.empreinte("channels.list", {"part": "snippet", "id": "UC1"})
    second = Cache.empreinte("channels.list", {"id": "UC1", "part": "snippet"})
    assert premier == second


def test_empreinte_depend_de_l_endpoint():
    assert Cache.empreinte("channels.list", {"id": "UC1"}) != Cache.empreinte(
        "videos.list", {"id": "UC1"}
    )


def test_lecture_ecriture(connexion, horloge):
    cache = Cache(connexion, horloge=horloge.maintenant)
    empreinte = cache.empreinte("videos.list", {"id": "a"})
    cache.ecrire(empreinte, "videos.list", {"items": [1, 2]}, ttl_jours=1)

    assert cache.lire(empreinte) == {"items": [1, 2]}
    assert cache.lire("inconnue") is None


def test_expiration(connexion, horloge):
    cache = Cache(connexion, horloge=horloge.maintenant)
    empreinte = cache.empreinte("videos.list", {"id": "a"})
    cache.ecrire(empreinte, "videos.list", {"ok": True}, ttl_jours=1)

    horloge.avancer(timedelta(hours=12).total_seconds())
    assert cache.lire(empreinte) is not None

    horloge.avancer(timedelta(days=1).total_seconds())
    assert cache.lire(empreinte) is None


def test_plafond_legal_de_30_jours(connexion, horloge):
    cache = Cache(connexion, horloge=horloge.maintenant, plafond_jours=30)
    empreinte = cache.empreinte("channels.list", {"id": "a"})
    # Même si l'appelant demande un an, on ne dépasse jamais 30 jours.
    cache.ecrire(empreinte, "channels.list", {"x": 1}, ttl_jours=365)

    horloge.avancer(timedelta(days=29).total_seconds())
    assert cache.lire(empreinte) is not None
    horloge.avancer(timedelta(days=2).total_seconds())
    assert cache.lire(empreinte) is None


def test_purge_par_anciennete(connexion, horloge):
    cache = Cache(connexion, horloge=horloge.maintenant)
    ancienne = cache.empreinte("videos.list", {"id": "vieux"})
    cache.ecrire(ancienne, "videos.list", {"x": 1}, ttl_jours=30)

    horloge.avancer(timedelta(days=10).total_seconds())
    recente = cache.empreinte("videos.list", {"id": "recent"})
    cache.ecrire(recente, "videos.list", {"x": 2}, ttl_jours=30)

    supprimees = cache.purger(5)
    assert supprimees == 1
    assert cache.lire(ancienne) is None
    assert cache.lire(recente) == {"x": 2}


def test_vider(connexion, horloge):
    cache = Cache(connexion, horloge=horloge.maintenant)
    cache.ecrire("a", "videos.list", {}, 1)
    cache.ecrire("b", "videos.list", {}, 1)
    assert cache.compter() == 2
    assert cache.vider() == 2
    assert cache.compter() == 0
