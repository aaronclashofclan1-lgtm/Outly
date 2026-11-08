from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from outly import db, production, strategie
from outly.plan import Fiche


def video(vid="v1", vues=1000, titre="Une vidéo", publie="2020-01-01", short=False):
    return {
        "video_id": vid,
        "titre": titre,
        "publie_le": publie,
        "duree_s": 60 if short else 600,
        "est_short": short,
        "vues": vues,
        "pourcentage_vu": 35.0,
        "ctr": 3.0,
        "impressions": 5000,
    }


def contexte(contexte_videos=None):
    return {
        "source": "studio",
        "chaine": "Test",
        "videos": contexte_videos or [video()],
        "medianes": {"longue": 2000.0, "short": None},
    }


def test_analyse_locale_sous_performance():
    analyse = strategie.analyser_local(contexte([video(vues=500)]))
    assert analyse[0]["verdict_local"] == "sous-performe"
    assert any(signal["code"] == "vues_faibles" for signal in analyse[0]["signaux"])


def test_video_trop_recente_nest_pas_condamnee():
    analyse = strategie.analyser_local(
        contexte([video(publie="2099-01-01", vues=1)])
    )
    assert analyse[0]["verdict_local"] == "trop récent"


def test_nettoyage_rejette_les_videos_inconnues():
    contexte_local = strategie.analyser_local(contexte([video()]))
    donnees = {
        "synthese": "Une synthèse suffisamment longue pour être acceptée.",
        "videos": [
            {
                "video_id": "inconnue",
                "titre": "Inconnue",
                "verdict": "fragile",
                "causes": [
                    {
                        "cause": "miniature",
                        "preuve": "CTR 1 %",
                        "hypothese": False,
                        "test": "Tester une autre miniature",
                    }
                ],
                "action_prioritaire": "Refaire la miniature",
            }
        ],
        "ajustements_strategie": [
            {
                "axe": "Miniature",
                "avant": "Texte long",
                "apres": "Un seul mot",
                "pourquoi": "CTR faible",
                "effort": "faible",
            }
        ],
        "tests_prochains": [
            {"test": "Nouvelle miniature", "critere_reussite": "CTR > 4 %", "delai": "7 jours"}
        ],
        "limites": ["Pas de courbe de rétention."],
    }
    assert strategie._nettoyer(donnees, {"videos": contexte_local}) is None


def test_schema_strategie_valide():
    schema = json.loads(
        Path("outly/schemas/strategie.schema.json").read_text(encoding="utf-8")
    )
    contexte_local = strategie.analyser_local(contexte([video()]))
    propre = strategie._nettoyer(
        {
            "synthese": "Une synthèse suffisamment longue pour être acceptée.",
            "videos": [
                {
                    "video_id": "v1",
                    "titre": "Une vidéo",
                    "verdict": "sous-performe",
                    "causes": [
                        {
                            "cause": "miniature",
                            "preuve": "CTR 1 %",
                            "hypothese": False,
                            "test": "Tester une autre miniature",
                        }
                    ],
                    "action_prioritaire": "Refaire la miniature",
                }
            ],
            "ajustements_strategie": [
                {
                    "axe": "Miniature",
                    "avant": "Texte long",
                    "apres": "Un seul mot",
                    "pourquoi": "CTR faible",
                    "effort": "faible",
                }
            ],
            "tests_prochains": [
                {"test": "Nouvelle miniature", "critere_reussite": "CTR > 4 %", "delai": "7 jours"}
            ],
            "limites": ["Pas de courbe de rétention."],
        },
        {"videos": contexte_local},
    )
    jsonschema.validate(propre, schema)


def test_cache_strategie(tmp_path):
    conn = db.connexion(tmp_path / "radar.sqlite3")
    db.migrer(conn)
    contexte_local = contexte()
    signature = strategie.empreinte(contexte_local)
    strategie._enregistrer(
        conn,
        None,
        signature,
        None,
        {
            "synthese": "Synthèse",
            "videos": [],
            "ajustements_strategie": [],
            "tests_prochains": [],
            "limites": [],
        },
        contexte_local,
        None,
    )
    assert strategie._lire_session(conn, signature)["donnees"]["synthese"] == "Synthèse"
    conn.close()


def test_estimation_locale_valide():
    fiche = Fiche(
        identifiant=1,
        titre="Ma vidéo",
        notes="Quelques notes",
        date_prevue="2026-12-01",
        statut="idee",
        format="longue",
    )
    estimation = production.estimation_locale(fiche)
    schema = json.loads(
        Path("outly/schemas/production.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(estimation, schema)
    assert estimation["temps_total_heures"] == 12.0
    assert estimation["materiel"]
    assert estimation["footage"]
