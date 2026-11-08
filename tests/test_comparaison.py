from __future__ import annotations

from types import SimpleNamespace

from outly import comparaison


def ligne(score=70, outlier=2.0, cadence=1.0, jours=5, mediane=10_000, cid="a"):
    return SimpleNamespace(
        channel_id=cid,
        titre=f"Chaîne {cid}",
        score_affiche=score,
        metriques=SimpleNamespace(
            meilleur_outlier=outlier,
            cadence_semaine=cadence,
            jours_depuis_publication=jours,
            mediane_vues_longues=mediane,
            mediane_vues_short=None,
        ),
    )


def test_classement_par_priorite():
    scores = comparaison.classer(
        [
            ligne(score=90, outlier=3.5, cadence=2, jours=2, mediane=50_000, cid="forte"),
            ligne(score=60, outlier=1.2, cadence=0.4, jours=40, mediane=2_000, cid="faible"),
        ]
    )
    assert [score.channel_id for score in scores] == ["forte", "faible"]
    assert scores[0].score > scores[1].score
    assert scores[0].rang == 1
    assert scores[0].confiance == "élevée"


def test_donnees_manquantes_reduisent_la_confiance():
    scores = comparaison.classer(
        [
            SimpleNamespace(
                channel_id="seule",
                titre="Seule",
                score_affiche=80,
                metriques=None,
            )
        ]
    )
    assert scores[0].score == 80
    assert scores[0].confiance == "faible"
    assert scores[0].manques


def test_egalite_est_signalee():
    scores = comparaison.classer(
        [ligne(cid="a"), ligne(cid="b")]
    )
    assert scores[0].score == scores[1].score
    message = comparaison.recommandation(scores)
    assert "se valent" in message


def test_une_seule_chaine():
    assert comparaison.classer([]) == []
    scores = comparaison.classer([ligne(cid="unique")])
    assert scores[0].rang == 1
    assert "unique" in comparaison.recommandation(scores)


def test_les_raisons_sont_chiffrees():
    score = comparaison.classer([ligne(outlier=3.0, mediane=12_345)])[0]
    texte = " · ".join(score.raisons)
    assert "×3,0" in texte
    assert "12 345" in texte
