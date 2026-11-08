"""Tests des sorties : CSV lisibles par Excel, rapport et règles d'exclusion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from outly import db, report
from outly.metrics import calculer_metriques
from outly.models import Chaine, ScoreChaine, Video

MAINTENANT = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _semer(
    connexion,
    channel_id: str,
    titre: str,
    score: int | None = None,
    heuristique: float | None = None,
    n_videos: int = 4,
    age_jours: int = 0,
    vues: int = 1_000,
    description: str | None = None,
):
    """Insère une chaîne complète (vidéos, métriques, score éventuel)."""
    db.enregistrer_chaine(
        connexion,
        Chaine(
            channel_id=channel_id,
            titre=titre,
            handle=f"@{titre.lower().replace(' ', '')}",
            abonnes=10_000,
            nb_videos=n_videos,
            playlist_uploads=f"UU{channel_id}",
            cree_le="2020-01-01T00:00:00+00:00",
            score_heuristique=heuristique,
            description=description,
        ),
    )
    videos = [
        Video(
            video_id=f"{channel_id}-v{rang}",
            channel_id=channel_id,
            titre=f"Vidéo {rang} de {titre}",
            publie_le=(MAINTENANT - timedelta(days=age_jours + 7 * rang)).isoformat(
                timespec="seconds"
            ),
            duree_s=600,
            vues=vues * 10 if rang == 0 else vues,
            est_short=0,
            live="none",
            rang_uploads=rang,
        )
        for rang in range(n_videos)
    ]
    db.enregistrer_videos(connexion, videos)
    db.enregistrer_metriques(
        connexion, calculer_metriques(channel_id, videos, 180, 30, MAINTENANT)
    )
    if score is not None:
        db.enregistrer_score(
            connexion,
            channel_id,
            None,
            f"empreinte-{channel_id}",
            "score-v1",
            "gemini",
            "gemini-3.5-flash-lite",
            ScoreChaine(
                score,
                "fort",
                "voix_off",
                "fr",
                "Le sujet recoupe exactement le positionnement. Le format et l'audience concordent.",
                ["aborde un sujet exclu"],
            ),
            "{}",
            1,
            None,
            True,
        )
        db.marquer_statut(connexion, channel_id, "scored")
    else:
        db.marquer_statut(connexion, channel_id, "enrichie")


def test_classement_et_raisons(connexion, config):
    _semer(connexion, "UC_a", "Chaîne A", score=82)
    _semer(connexion, "UC_b", "Chaîne B", score=30)
    _semer(connexion, "UC_c", "Chaîne C", n_videos=1)

    lignes = report.charger_lignes(connexion, config)

    assert [ligne.channel_id for ligne in lignes] == ["UC_a", "UC_b", "UC_c"]
    assert lignes[0].statut == "retenue"
    assert lignes[0].source_score == "llm"
    assert lignes[1].statut == "ecartee"
    assert "30 < seuil 50" in lignes[1].raison
    assert lignes[2].statut == "ecartee"
    assert "données insuffisantes" in lignes[2].raison


def test_repli_heuristique_quand_le_llm_est_absent(connexion, config):
    _semer(connexion, "UC_d", "Chaîne D", heuristique=0.8)
    (_ligne,) = report.charger_lignes(connexion, config)
    assert _ligne.statut == "retenue"
    assert _ligne.source_score == "heuristique"


def test_chaine_inactive_ecartee(connexion, config):
    _semer(connexion, "UC_e", "Chaîne E", score=90, age_jours=400)
    (ligne,) = report.charger_lignes(connexion, config)
    assert ligne.statut == "ecartee"
    assert "inactive" in ligne.raison


def test_generation_des_trois_fichiers(connexion, config):
    _semer(connexion, "UC_a", "Chaîne A", score=82)
    _semer(connexion, "UC_b", "Chaîne B", score=30)

    resultat = report.generer(connexion, config)

    assert resultat["evaluees"] == 2
    assert resultat["retenues"] == 1
    # Une seule vidéo dépasse réellement la médiane de son format (×10).
    assert resultat["nb_outliers"] == 1
    for cle in ("channels", "outliers_csv", "rapport"):
        assert Path(resultat[cle]).exists()


def test_csv_compatible_excel_francais(connexion, config):
    _semer(connexion, "UC_a", "Chaîne A", score=82)
    resultat = report.generer(connexion, config)

    brut = Path(resultat["channels"]).read_bytes()
    assert brut.startswith(b"\xef\xbb\xbf"), "le BOM UTF-8 est nécessaire pour Excel"
    lignes = brut.decode("utf-8-sig").splitlines()
    assert lignes[0].startswith("rang;statut;")
    assert "Chaîne A" in lignes[1]
    # Aucune ligne vide parasite.
    assert all(ligne.strip() for ligne in lignes)


def test_csv_des_outliers_relie_la_video_a_la_chaine(connexion, config):
    _semer(connexion, "UC_a", "Chaîne A", score=82, vues=1_000)
    resultat = report.generer(connexion, config)

    with Path(resultat["outliers_csv"]).open(
        encoding=config.sortie.csv_encodage, newline=""
    ) as fichier:
        lignes = list(__import__("csv").reader(fichier, delimiter=config.sortie.csv_separateur))

    entetes = lignes[0]
    index_score = entetes.index("score_outlier")
    index_titre = entetes.index("titre_video")
    index_chaine = entetes.index("chaine")

    assert lignes[1][index_chaine] == "Chaîne A"
    assert lignes[1][index_titre] == "Vidéo 0 de Chaîne A"
    # 10 000 vues face à une médiane de 1 000 → ×10, à la décimale près.
    assert float(lignes[1][index_score]) == 10.0


def test_une_video_sous_la_mediane_n_est_pas_un_outlier(connexion, config):
    """Une vidéo à ×1,0 n'a rien d'exceptionnel : elle ne doit pas être exportée."""
    _semer(connexion, "UC_a", "Chaîne A", score=82)
    resultat = report.generer(connexion, config)

    contenu = Path(resultat["outliers_csv"]).read_text(encoding="utf-8-sig")
    assert "Vidéo 0 de Chaîne A" in contenu
    assert "Vidéo 1 de Chaîne A" not in contenu


def test_rapport_markdown_contient_les_sections_attendues(connexion, config):
    _semer(connexion, "UC_a", "Chaîne A", score=82)
    _semer(connexion, "UC_b", "Chaîne B", score=30)
    resultat = report.generer(connexion, config)

    contenu = Path(resultat["rapport"]).read_text(encoding="utf-8")
    assert "# Veille concurrentielle YouTube" in contenu
    assert "## Synthèse" in contenu
    assert "## Top 1 chaînes" in contenu
    assert "### 1. Chaîne A — 82/100" in contenu
    assert "Meilleurs outliers" in contenu
    assert "## Chaînes écartées" in contenu
    assert "Chaîne B" in contenu
    assert "score de proximité 30 < seuil 50" in contenu
    assert "## Méthode" in contenu
    # La justification et les risques du LLM sont visibles.
    assert "Le sujet recoupe exactement le positionnement." in contenu
    assert "aborde un sujet exclu" in contenu


def test_description_de_la_chaine_est_exposee(connexion, config):
    _semer(
        connexion,
        "UC_a",
        "Chaîne A",
        score=82,
        description="Je parle de design et de vidéo, chaque semaine.",
    )
    (ligne,) = report.charger_lignes(connexion, config)
    assert ligne.description == "Je parle de design et de vidéo, chaque semaine."


def test_rapport_sans_aucune_chaine(connexion, config):
    resultat = report.generer(connexion, config)
    contenu = Path(resultat["rapport"]).read_text(encoding="utf-8")
    assert resultat["evaluees"] == 0
    assert "Aucune chaîne ne dépasse le seuil" in contenu


def test_ecriture_atomique_preserve_l_ancien_fichier_en_cas_d_echec(tmp_path):
    cible = tmp_path / "report.md"
    cible.write_text("ancien contenu\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        with report._ecriture_atomique(cible) as fichier:
            fichier.write("nouveau contenu\n")
            raise RuntimeError("panne simulée")

    assert cible.read_text(encoding="utf-8") == "ancien contenu\n"
    assert not list(tmp_path.glob(".report.md.*.tmp"))
