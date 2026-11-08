"""Tests de la découverte : entrées, normalisation, pré-filtre, mentions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from outly import discovery
from outly.discovery import (
    classifier_seed,
    extraire_mentions,
    filtrer_et_classer,
    lire_lignes,
    normaliser,
    score_heuristique,
)
from outly.models import Chaine

MAINTENANT = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("entree", "attendu"),
    [
        ("@montagevideo", ("handle", "montagevideo")),
        ("montagevideo", ("handle", "montagevideo")),
        ("https://www.youtube.com/@montagevideo", ("handle", "montagevideo")),
        ("https://www.youtube.com/@montagevideo/videos", ("handle", "montagevideo")),
        ("https://www.youtube.com/@théo", ("handle", "théo")),
        ("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw", ("id", "UC_x5XG1OV2P6uZZ5FSM9Ttw")),
        ("UC_x5XG1OV2P6uZZ5FSM9Ttw", ("id", "UC_x5XG1OV2P6uZZ5FSM9Ttw")),
        ("https://www.youtube.com/user/ancien_nom", ("username", "ancien_nom")),
        ("https://www.youtube.com/c/nom-personnalise", ("c", "nom-personnalise")),
        ("https://www.youtube.com/watch?v=abcdefghijk", ("inconnu", "https://www.youtube.com/watch?v=abcdefghijk")),
    ],
)
def test_classification_des_seeds(entree, attendu):
    assert classifier_seed(entree) == attendu


def test_lire_lignes_ignore_commentaires_et_vides(tmp_path):
    fichier = tmp_path / "seeds.txt"
    fichier.write_text(
        "# un commentaire\n@premier\n\n  @second  # commentaire en ligne\n", encoding="utf-8"
    )
    assert lire_lignes(fichier) == ["@premier", "@second"]


def test_lire_lignes_fichier_absent(tmp_path):
    assert lire_lignes(tmp_path / "absent.txt") == []


def test_normaliser_retire_accents_et_ponctuation():
    assert normaliser("Étalonnage Vidéo, niveau AVANCÉ !") == "etalonnage video niveau avance"


def test_score_heuristique_part_de_mots_cles():
    chaine = Chaine(
        channel_id="UC1",
        titre="Monteur vidéo freelance",
        description="Je fais du montage vidéo et de l'étalonnage sur DaVinci.",
    )
    score = score_heuristique(chaine, ["montage vidéo", "étalonnage", "audio"])
    assert score == pytest.approx(2 / 3, abs=0.001)


def test_score_heuristique_sans_mots_cles():
    assert score_heuristique(Chaine(channel_id="UC1", titre="Peu importe"), []) == 0.0


def test_extraire_mentions_evite_les_emails():
    texte = (
        "Merci à @chaine_amie et à https://www.youtube.com/@autre-chaine. "
        "Écrivez à contact@site.com. "
        "Voir aussi https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"
    )
    mentions = extraire_mentions(texte)
    assert "@chaine_amie" in mentions
    assert "@autre-chaine" in mentions
    assert not any("site.com" in mention for mention in mentions)
    assert "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw" in mentions


def test_extraire_mentions_texte_vide():
    assert extraire_mentions(None) == []


def _chaine(channel_id, abonnes, cree_le=None, est_seed=False, description=""):
    return Chaine(
        channel_id=channel_id,
        titre=f"Chaîne {channel_id}",
        abonnes=abonnes,
        cree_le=cree_le or (MAINTENANT - timedelta(days=1000)).isoformat(),
        est_seed=est_seed,
        description=description,
    )


def test_prefiltre_exclut_les_chaines_hors_bande(config):
    candidats = [
        _chaine("petite", 10),
        _chaine("bonne", 50_000),
        _chaine("enorme", 9_000_000),
    ]
    retenus = filtrer_et_classer(candidats, config, [], MAINTENANT)
    assert [chaine.channel_id for chaine in retenus] == ["bonne"]


def test_prefiltre_exclut_les_chaines_trop_recentes(config):
    recente = _chaine("recente", 50_000, cree_le=(MAINTENANT - timedelta(days=30)).isoformat())
    ancienne = _chaine("ancienne", 50_000)
    retenus = filtrer_et_classer([recente, ancienne], config, [], MAINTENANT)
    assert [chaine.channel_id for chaine in retenus] == ["ancienne"]


def test_les_seeds_echappent_aux_filtres(config):
    # 5 abonnés et une création récente : deux motifs d'exclusion pour une non-seed.
    seed = _chaine("seed", 5, est_seed=True)
    recente = _chaine(
        "recente", 5, cree_le=(MAINTENANT - timedelta(days=1)).isoformat(), est_seed=True
    )
    retenus = filtrer_et_classer([recente, seed], config, [], MAINTENANT)
    assert {chaine.channel_id for chaine in retenus} == {"seed", "recente"}


def test_classement_seed_puis_score(config):
    faible = _chaine("faible", 50_000, description="sujet sans rapport")
    fort = _chaine("fort", 50_000, description="montage vidéo")
    seed = _chaine("seed", 50_000, est_seed=True, description="montage vidéo")
    retenus = filtrer_et_classer([faible, fort, seed], config, ["montage vidéo"], MAINTENANT)
    assert retenus[0].channel_id == "seed"
    assert retenus[1].channel_id == "fort"


def test_exiger_un_mot_cle(config):
    config.decouverte.require_keyword_hit = True
    sans_rapport = _chaine("sans", 50_000, description="cuisine et pâtisserie")
    avec = _chaine("avec", 50_000, description="montage vidéo pro")
    retenus = filtrer_et_classer([sans_rapport, avec], config, ["montage vidéo"], MAINTENANT)
    assert [chaine.channel_id for chaine in retenus] == ["avec"]


def test_plafond_de_candidats(config):
    config.youtube.max_candidates_per_run = 2
    candidats = [_chaine(f"c{rang}", 50_000) for rang in range(10)]
    assert len(filtrer_et_classer(candidats, config, [], MAINTENANT)) == 2


class ClientMentions:
    def __init__(self):
        self.handles: list[str] = []

    def chaine_par_handle(self, handle: str) -> Chaine:
        self.handles.append(handle)
        return Chaine(channel_id=f"UC_{handle}", titre=f"Chaîne {handle}", handle=f"@{handle}")


def _inserer_video(connexion, video_id: str, description: str) -> None:
    connexion.execute(
        "INSERT INTO videos (video_id, channel_id, titre, description, recupere_le) "
        "VALUES (?, ?, ?, ?, ?)",
        (video_id, "UC_source", "Titre", description, "2026-01-01T00:00:00Z"),
    )
    connexion.commit()


def test_expansion_descriptions_ne_relit_pas_les_videos_deja_scannees(connexion, config):
    _inserer_video(connexion, "v1", "Merci à @ami pour la collab.")
    client = ClientMentions()

    assert discovery.expandre_depuis_descriptions(
        connexion, client, config, discovery.ResumeDecouverte()
    ) == 1
    assert client.handles == ["ami"]
    assert connexion.execute(
        "SELECT mentions_scannees_le FROM videos WHERE video_id = 'v1'"
    ).fetchone()[0]

    # Deuxième passage : aucune description à relire, aucun handle résolu.
    assert discovery.expandre_depuis_descriptions(
        connexion, client, config, discovery.ResumeDecouverte()
    ) == 0
    assert client.handles == ["ami"]

    # Une description modifiée invalide le cache et sera rescannée.
    connexion.execute(
        "UPDATE videos SET description = ? WHERE video_id = 'v1'",
        ("Merci à @autre cette fois.",),
    )
    connexion.commit()
    assert discovery.expandre_depuis_descriptions(
        connexion, client, config, discovery.ResumeDecouverte()
    ) == 1
    assert client.handles == ["ami", "autre"]


def test_mention_d_un_handle_deja_connu_ne_coute_aucun_appel(connexion, config):
    connexion.execute(
        "INSERT INTO channels (channel_id, titre, handle, ajoute_le, maj_le) "
        "VALUES (?, ?, ?, ?, ?)",
        ("UC_connu", "Chaîne connue", "@connu", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    _inserer_video(connexion, "v2", "Voir @connu pour la suite.")
    client = ClientMentions()

    assert discovery.expandre_depuis_descriptions(
        connexion, client, config, discovery.ResumeDecouverte()
    ) == 0
    assert client.handles == []
