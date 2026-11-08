"""Tests du score de proximité local et du minimum de chaînes retenues.

Le score local est le secours quand le LLM est indisponible : il ne doit
dépendre d'aucun réseau, classer les chaînes de la même niche avant les autres
et garantir un rapport exploitable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from outly import db, proximite, report
from outly.metrics import calculer_metriques
from outly.models import Chaine, ScoreChaine, Video

MAINTENANT = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _chaine(
    connexion,
    channel_id: str,
    titre: str,
    description: str = "",
    mots_cles: str | None = None,
    titres: list[str] | None = None,
    statut: str = "enrichie",
    score_llm: int | None = None,
) -> None:
    db.enregistrer_chaine(
        connexion,
        Chaine(
            channel_id=channel_id,
            titre=titre,
            handle=f"@{channel_id.lower()}",
            description=description,
            mots_cles_chaine=mots_cles,
            abonnes=5_000,
            nb_videos=4,
            playlist_uploads=f"UU{channel_id}",
            cree_le="2020-01-01T00:00:00+00:00",
        ),
    )
    titres = titres or [f"Vidéo {rang} de {titre}" for rang in range(4)]
    videos = [
        Video(
            video_id=f"{channel_id}-{rang}",
            channel_id=channel_id,
            titre=contenu,
            publie_le=(MAINTENANT - timedelta(days=2 + 7 * rang)).isoformat(timespec="seconds"),
            duree_s=600,
            vues=1_000,
            est_short=0,
            live="none",
            rang_uploads=rang,
        )
        for rang, contenu in enumerate(titres)
    ]
    db.enregistrer_videos(connexion, videos)
    db.enregistrer_metriques(
        connexion, calculer_metriques(channel_id, videos, 180, 30, MAINTENANT)
    )
    connexion.execute("UPDATE channels SET statut = ? WHERE channel_id = ?", (statut, channel_id))
    connexion.commit()

    if score_llm is not None:
        db.enregistrer_score(
            connexion,
            channel_id,
            None,
            f"empreinte-{channel_id}",
            "score-v1",
            "gemini",
            "gemini-3.5-flash-lite",
            ScoreChaine(
                score_llm,
                "moyen",
                "voix_off",
                "fr",
                "Le sujet se recoupe partiellement. Le format reste différent.",
                [],
            ),
            "{}",
            1,
            None,
            True,
        )
        db.marquer_statut(connexion, channel_id, "scored")


def _ma_chaine(connexion, tmp_path: Path) -> Chaine:
    (tmp_path / "positioning.md").write_text(
        "Chaîne consacrée à l'ergonomie du bureau : souris ergonomique, posture, "
        "prévention du canal carpien et douleurs de poignet.",
        encoding="utf-8",
    )
    (tmp_path / "keywords.txt").write_text(
        "ergonomie bureau\nsouris ergonomique\ncanal carpien\ndouleur poignet\n",
        encoding="utf-8",
    )
    chaine = Chaine(
        channel_id="UCmoi",
        titre="Skynt",
        handle="@skynt",
        description=(
            "Conseils d'ergonomie au bureau, souris ergonomique et prévention "
            "des douleurs de poignet."
        ),
        mots_cles_chaine='"canal carpien" "tapis de souris"',
        abonnes=112,
        nb_videos=20,
        cree_le="2020-01-01T00:00:00+00:00",
    )
    db.enregistrer_chaine(connexion, chaine)
    connexion.execute("UPDATE channels SET statut = 'moi' WHERE channel_id = ?", ("UCmoi",))
    connexion.commit()
    db.enregistrer_reference(
        connexion,
        chaine,
        [
            "Comment choisir une souris ergonomique",
            "Prévenir le canal carpien au bureau",
        ],
    )
    return chaine


def _semer_niche(connexion, tmp_path: Path) -> None:
    _ma_chaine(connexion, tmp_path)
    _chaine(
        connexion,
        "UCproche",
        "Bureau confort",
        description="Ergonomie au bureau et matériel pour éviter les douleurs.",
        titres=[
            "Test d'une souris ergonomique verticale",
            "Canal carpien : exercices et prévention au bureau",
            "Bien régler sa posture devant l'écran",
            "Douleurs de poignet au travail : que faire ?",
        ],
    )
    _chaine(
        connexion,
        "UCloin",
        "Cuisine facile",
        description="Recettes rapides pour tous les jours.",
        titres=[
            "Pizza maison en 20 minutes",
            "Voyage à Tokyo : mes bonnes adresses",
            "Tutoriel guitare pour débuter",
            "Meilleur smartphone de l'année",
        ],
    )


def test_profil_local_utilise_mots_cles_positionnement_et_titres(connexion, config, tmp_path):
    _ma_chaine(connexion, tmp_path)

    profil = proximite.construire_profil(connexion, config)

    assert profil is not None
    assert profil.titre == "Skynt"
    assert "canal carpien" in profil.phrases
    assert "souris ergonomique" in profil.phrases
    assert len(profil.titres_videos) == 2
    # « souris » ne doit pas être tronqué en « souri » par la simplification.
    assert "souris" in proximite.jetons("une souris ergonomique")
    assert proximite.jetons("des souris ergonomiques")


def test_score_local_classe_la_niche_avant_le_reste(connexion, config, tmp_path):
    _semer_niche(connexion, tmp_path)

    scores = proximite.scorer(connexion, config)

    assert scores["UCproche"].score > scores["UCloin"].score
    assert scores["UCproche"].score >= config.classement.seuil_local
    assert "canal" in scores["UCproche"].termes_communs or "poignet" in scores["UCproche"].termes_communs


def test_rapport_utilise_le_score_local_sans_llm(connexion, config, tmp_path):
    _semer_niche(connexion, tmp_path)

    lignes = report.charger_lignes(connexion, config)
    index = {ligne.channel_id: ligne for ligne in lignes}

    proche = index["UCproche"]
    assert proche.statut == "retenue"
    assert proche.source_score == "local"
    assert proche.score is None
    assert proche.score_local is not None and proche.score_local >= config.classement.seuil_local
    assert index["UCloin"].statut == "ecartee"
    assert "proximité locale" in (index["UCloin"].raison or "")


def test_score_llm_reste_prioritaire_sur_le_local(connexion, config, tmp_path):
    _ma_chaine(connexion, tmp_path)
    _chaine(
        connexion,
        "UCproche",
        "Bureau confort",
        description="Ergonomie au bureau et prévention du canal carpien.",
        titres=[
            "Souris ergonomique et posture au bureau",
            "Canal carpien : les bons gestes",
            "Régler son poste de travail",
            "Douleurs de poignet : prévenir",
        ],
        score_llm=30,
    )

    (ligne,) = [
        element for element in report.charger_lignes(connexion, config)
        if element.channel_id == "UCproche"
    ]

    assert ligne.source_score == "llm"
    assert ligne.statut == "ecartee"
    assert "30 < seuil 50" in (ligne.raison or "")


def test_minimum_de_cinq_retenues_meme_sans_score_suffisant(connexion, config, tmp_path):
    _ma_chaine(connexion, tmp_path)
    for rang in range(6):
        _chaine(
            connexion,
            f"UChors{rang}",
            f"Chaîne hors sujet {rang}",
            description="Recettes, voyage et jardinage.",
            titres=[f"Recette {rang}", f"Voyage {rang}", f"Jardin {rang}", f"Film {rang}"],
        )

    lignes = report.charger_lignes(connexion, config)
    retenues = [ligne for ligne in lignes if ligne.statut == "retenue"]

    assert len(retenues) == 5
    assert all(ligne.complement for ligne in retenues)
    assert all(ligne.score_local is not None for ligne in retenues)


def test_minimum_non_applique_si_le_corpus_est_trop_petit(connexion, config, tmp_path):
    _ma_chaine(connexion, tmp_path)
    _chaine(
        connexion,
        "UChors",
        "Chaîne hors sujet",
        description="Recettes de cuisine.",
        titres=["Pizza", "Voyage", "Guitare", "Jardin"],
    )

    lignes = report.charger_lignes(connexion, config)

    assert not any(ligne.statut == "retenue" for ligne in lignes)


def test_score_local_sans_ma_chaine_ne_plante_pas(connexion, config):
    assert proximite.scorer(connexion, config) == {}


def test_generation_rapport_indique_le_repli_local(connexion, config, tmp_path):
    _semer_niche(connexion, tmp_path)

    resultat = report.generer(connexion, config)
    contenu = Path(resultat["rapport"]).read_text(encoding="utf-8")

    assert resultat["retenues"] >= 1
    assert "proximité locale" in contenu.lower()
    assert "Proximité locale" in contenu
    assert "LLM indisponible" in contenu
