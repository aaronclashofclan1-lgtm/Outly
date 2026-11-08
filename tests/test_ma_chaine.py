"""Tests du mode « depuis ma chaîne » et de la recherche internationale.

Le client YouTube est remplacé par un faux objet : aucun réseau, aucun quota.
On vérifie surtout que ma chaîne sert de point de départ sans devenir un
concurrent, et que chaque requête est bien lancée deux fois (France + monde).
"""

from __future__ import annotations

import json

import pytest

from outly import db, discovery
from outly.llm.client import ReponseLlm
from outly.llm.queries import generer_requetes
from outly.models import Chaine

MOI = Chaine(
    channel_id="UC_moi",
    titre="Ma Chaîne",
    handle="@moi",
    abonnes=50_000,
    cree_le="2019-01-01T00:00:00Z",
    playlist_uploads="UUmoi",
    mots_cles_chaine='"montage video" "etalonnage"',
)


def _chaine(channel_id: str, titre: str) -> Chaine:
    return Chaine(
        channel_id=channel_id,
        titre=titre,
        abonnes=50_000,
        cree_le="2018-01-01T00:00:00Z",
    )


class ClientFactice:
    def __init__(self):
        self.recherches: list[tuple[str, str | None, str | None]] = []
        self.mentions_resolues: list[str] = []

    def chaine_par_handle(self, handle: str) -> Chaine:
        if handle == "moi":
            return MOI
        self.mentions_resolues.append(handle)
        return _chaine(f"UC_{handle}", f"Chaîne {handle}")

    def chaine_par_username(self, nom: str) -> Chaine:
        return _chaine(f"UC_{nom}", f"Chaîne {nom}")

    def chaines_par_ids(self, ids):
        return [_chaine(identifiant, f"Chaîne {identifiant}") for identifiant in ids]

    def elements_playlist(self, playlist_id: str, max_results: int):
        assert playlist_id == "UUmoi"
        return [
            {"snippet": {"title": "Mon titre 1", "resourceId": {"videoId": "v1"}}},
            {"snippet": {"title": "Mon titre 2", "resourceId": {"videoId": "v2"}}},
        ]

    def videos_infos(self, ids):
        return {
            "v1": {"snippet": {"description": "Merci à @ami pour la collab."}},
            "v2": {"snippet": {"description": "Voir https://www.youtube.com/@autre"}},
        }

    def sections_chaine(self, channel_id: str):
        if channel_id == "UC_moi":
            return ["UC_mise_en_avant"]
        return []

    def rechercher_chaines(self, requete: str, pays=None, langue=None):
        self.recherches.append((requete, pays, langue))
        index = len(self.recherches)
        resultat = [{"id": {"channelId": f"UC_cand{index}"}}]
        if index == 1:
            # L'API peut renvoyer ma propre chaîne : elle doit être écartée.
            resultat.append({"id": {"channelId": "UC_moi"}})
        return resultat


@pytest.fixture
def projet(config):
    (config.racine / "seeds.txt").write_text("", encoding="utf-8")
    (config.racine / "keywords.txt").write_text(
        "montage video pro\ncolorimetrie\n", encoding="utf-8"
    )
    config.moi.chaine = "@moi"
    config.moi.recherche_internationale = True
    config.youtube.max_search_calls_per_run = 4
    config.youtube.subscriber_min = 1000
    return config


def test_ma_chaine_sert_de_point_de_depart_sans_devenir_concurrente(connexion, projet):
    client = ClientFactice()

    resume = discovery.decouvrir(connexion, client, projet, max_recherches=4)

    assert resume.ma_chaine == "Ma Chaîne"
    assert resume.passes_recherche == 2
    assert resume.recherches == 4  # 2 requêtes × 2 passes

    # Ma chaîne est stockée avec un statut inerte.
    ligne = connexion.execute(
        "SELECT statut, source FROM channels WHERE channel_id = 'UC_moi'"
    ).fetchone()
    assert ligne["statut"] == "moi"
    assert ligne["source"] == "moi"

    # Elle n'apparaît pas parmi les candidates à analyser.
    candidates = {
        ligne["channel_id"]
        for ligne in connexion.execute(
            "SELECT channel_id FROM channels WHERE statut = 'candidate'"
        )
    }
    assert "UC_moi" not in candidates

    # Les @handles de ses descriptions et sa section ont été explorés.
    assert "ami" in client.mentions_resolues
    assert "autre" in client.mentions_resolues
    assert connexion.execute(
        "SELECT 1 FROM channels WHERE channel_id = 'UC_mise_en_avant'"
    ).fetchone()


def test_recherche_lancee_en_france_puis_dans_le_monde(connexion, projet):
    client = ClientFactice()

    discovery.decouvrir(connexion, client, projet, max_recherches=4)

    # Deux passes par requête, dans cet ordre.
    assert client.recherches[0][1:] == ("FR", "fr")
    assert client.recherches[1][1:] == (None, None)
    assert client.recherches[2][1:] == ("FR", "fr")
    assert client.recherches[3][1:] == (None, None)


def test_sans_international_une_seule_passe(connexion, projet):
    projet.moi.recherche_internationale = False
    client = ClientFactice()

    resume = discovery.decouvrir(connexion, client, projet, max_recherches=4)

    assert resume.passes_recherche == 1
    assert resume.recherches == 4  # 4 requêtes × 1 passe
    assert all(pays == "FR" for _, pays, _ in client.recherches)


def test_requetes_llm_complettent_sans_ecraser_les_mots_cles(connexion, projet):
    """Le LLM apporte l'anglais, mais keywords.txt garde sa place."""
    (projet.racine / "positioning.md").write_text(
        "Montage vidéo pour créateurs débutants, ton direct, formats longs.", encoding="utf-8"
    )
    service = ServiceFactice(
        json.dumps(
            {"requetes": ["video editing", "color grading", "creator tips", "montage pro"]}
        )
    )
    client = ClientFactice()

    resume = discovery.decouvrir(connexion, client, projet, max_recherches=8, service=service)

    assert resume.recherches == 8  # 4 requêtes × 2 passes
    requetes = {recherche[0] for recherche in client.recherches}
    assert "video editing" in requetes
    assert "montage video" in requetes  # mots-clés de ma chaîne


def test_passes_recherche_et_mots_cles_de_chaine(projet):
    assert discovery.passes_recherche(projet) == [("FR", "fr"), (None, None)]
    projet.moi.recherche_internationale = False
    assert discovery.passes_recherche(projet) == [("FR", "fr")]
    assert discovery.mots_cles_chaines('"a b" "c d" "a b"') == ["a b", "c d"]
    assert discovery.mots_cles_chaines(None) == []


# ---------------------------------------------------------------------------
# Génération des requêtes par le LLM
# ---------------------------------------------------------------------------


class ServiceFactice:
    def __init__(self, texte: str):
        self.texte = texte
        self.appels = 0

    def completer(self, messages):
        self.appels += 1
        return ReponseLlm(texte=self.texte, modele="modele-test", fournisseur="test")


def test_generer_requetes_valide_et_dedoublonne(projet):
    (projet.racine / "positioning.md").write_text(
        "Montage vidéo pour créateurs débutants, ton direct, formats longs.",
        encoding="utf-8",
    )
    service = ServiceFactice(
        json.dumps({"requetes": ["montage video", "video editing", "Montage video", "court"]})
    )

    requetes = generer_requetes(service, projet, None, [], limite=4)

    assert requetes == ["montage video", "video editing", "court"]
    assert service.appels == 1


def test_generer_requetes_json_invalide_renvoie_vide(projet):
    (projet.racine / "positioning.md").write_text("Un positionnement suffisant.", encoding="utf-8")
    service = ServiceFactice("Je ne peux pas répondre en JSON.")

    assert generer_requetes(service, projet, None, [], limite=4) == []
    assert service.appels == 2  # une seconde chance, puis abandon


def test_generer_requetes_sans_positionnement(projet):
    service = ServiceFactice('{"requetes": []}')

    assert generer_requetes(service, projet, None, [], limite=4) == []
    assert service.appels == 0
