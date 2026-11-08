"""Tests de la lecture d'état pour l'interface (base SQLite temporaire)."""

from __future__ import annotations

import json
import os

from outly import db
from outly.gui import data
from outly.quota import jour_pacifique

CHAMP_ID = "UC" + "x" * 22


def _preparer_projet(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "youtube:\n"
        "  max_search_calls_per_run: 20\n"
        "  max_search_calls_per_day: 90\n"
        "  max_units_per_run: 2000\n"
        "  max_units_per_day: 8000\n"
        "metrics:\n"
        "  min_videos_for_metrics: 3\n"
        "llm:\n"
        "  min_score_keep: 50\n",
        encoding="utf-8",
    )
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    conn.execute(
        "INSERT INTO channels (channel_id, handle, titre, abonnes, nb_videos, cree_le, "
        "est_seed, statut, ajoute_le, maj_le) VALUES (?, ?, ?, ?, ?, ?, 1, 'scored', ?, ?)",
        (CHAMP_ID, "@voisin", "Le Voisin", 12000, 120, "2019-01-01T00:00:00Z",
         "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
    )
    for index, vues in enumerate((1000, 1000, 5000)):
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, titre, publie_le, duree_s, vues, "
            "est_short, live, recupere_le) VALUES (?, ?, ?, ?, ?, ?, 0, 'none', ?)",
            (f"vid{index}", CHAMP_ID, f"Vidéo {index}", "2026-10-20T00:00:00Z", 900, vues,
             "2026-10-21T00:00:00Z"),
        )
    conn.execute(
        "INSERT INTO channel_metrics (channel_id, calcule_le, fenetre, n_videos, n_shorts, "
        "n_longues, mediane_vues_short, mediane_vues_longues, cadence_semaine, "
        "jours_depuis_publication, meilleur_outlier, mix_format) "
        "VALUES (?, ?, 30, 3, 0, 3, NULL, 1000, 2.0, 3, 5.0, '100 % longues')",
        (CHAMP_ID, "2026-10-21T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO channel_scores (channel_id, empreinte_prompt, prompt_version, "
        "score_proximite, recouvrement_audience, format_detecte, langue, justification, "
        "risques_json, valide, cree_le) VALUES (?, 'e', 'v1', 72, 'fort', 'voix-off', 'fr', "
        "'Même promesse.', '[\"prix\"]', 1, ?)",
        (CHAMP_ID, "2026-10-21T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO api_calls (jour, ts, endpoint, compartiment, unites, depuis_cache) "
        "VALUES (?, ?, 'channels.list', 'principal', 5, 0)",
        (jour_pacifique(), "2026-10-29T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def test_resultats_lit_score_et_risques(tmp_path):
    _preparer_projet(tmp_path)
    lignes = data.resultats(tmp_path)
    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne.channel_id == CHAMP_ID
    assert ligne.score == 72
    assert ligne.risques == ["prix"]
    assert ligne.statut == "retenue"
    assert ligne.source_score == "llm"


def test_outliers_de_ne_renvoie_que_les_vraies_performances(tmp_path):
    _preparer_projet(tmp_path)
    outliers = data.outliers_de(tmp_path, CHAMP_ID, 3)
    assert len(outliers) == 1
    assert outliers[0]["video_id"] == "vid2"
    assert outliers[0]["score_outlier"] == 5.0


def test_jeu_hooks_lit_les_variantes_et_les_intros(tmp_path):
    _preparer_projet(tmp_path)
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    conn.execute(
        "INSERT INTO intro_analyses (video_id, channel_id, prompt_version, empreinte, "
        "intro_debut_s, intro_fin_s, hook_texte, resume, promesse, techniques_json, "
        "valide, cree_le) VALUES ('vid2', ?, 'intro-v1', 'e', 0, 12, 'Citation.', "
        "'Résumé.', 'Promesse.', ?, 1, '2026-10-21')",
        (CHAMP_ID, json.dumps(["question"])),
    )
    conn.execute(
        "INSERT INTO hook_sets (sujet, empreinte, prompt_version, fournisseur, modele, "
        "analyse, variantes_json, sources_json, valide, cree_le) "
        "VALUES ('Mon sujet', 'emp', 'hooks-v1', 'test', 'fake', 'Analyse.', ?, ?, 1, "
        "'2026-10-21')",
        (
            json.dumps([{"titre": "Variante", "hook": "Une accroche."}]),
            json.dumps([{"video_id": "vid2", "titre": "Vidéo 2"}]),
        ),
    )
    conn.commit()
    conn.close()

    jeu = data.jeu_hooks(tmp_path)

    assert jeu is not None
    assert jeu["sujet"] == "Mon sujet"
    assert jeu["variantes"][0]["titre"] == "Variante"
    assert jeu["intros"][0]["video_id"] == "vid2"
    assert jeu["intros"][0]["hook_texte"] == "Citation."


def test_derniere_session_cerveau_lit_les_notes_utilisees(tmp_path):
    _preparer_projet(tmp_path)
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    reponse = {
        "synthese": "Une synthèse.",
        "angles": [
            {
                "titre": "Un angle",
                "promesse": "Une promesse.",
                "hook": "Un hook suffisamment long.",
                "pourquoi": "Parce que les notes se répondent.",
                "notes": ["4"],
            }
        ],
        "sujets": [],
        "questions": [],
    }
    conn.execute(
        "INSERT INTO brain_sessions (question, empreinte, prompt_version, fournisseur, "
        "modele, reponse_json, notes_json, valide, cree_le) "
        "VALUES ('Ma question', 'e', 'brain-v1', 'test', 'fake', ?, ?, 1, '2026-11-01')",
        (
            json.dumps(reponse),
            json.dumps([{"id": 4, "titre": "Une note", "type": "concept"}]),
        ),
    )
    conn.commit()
    conn.close()

    session = data.derniere_session_cerveau(tmp_path)

    assert session is not None
    assert session["question"] == "Ma question"
    assert session["reponse"]["angles"][0]["titre"] == "Un angle"
    assert session["notes"][0]["titre"] == "Une note"


def test_dernieres_idees_lit_les_sujets(tmp_path):
    _preparer_projet(tmp_path)
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    conn.execute(
        "INSERT INTO idea_sets (nombre, empreinte, prompt_version, introduction, "
        "idees_json, notes_json, valide, cree_le) "
        "VALUES (5, 'e', 'ideas-v1', 'Une introduction.', ?, '[]', 1, '2026-11-01')",
        (
            json.dumps(
                [
                    {
                        "sujet": "Un sujet",
                        "type": "sujet",
                        "pourquoi": "Parce que.",
                        "sources": ["preuve"],
                        "notes": [],
                        "angles": [
                            {"angle": "Récit", "titre": "Un titre"},
                            {"angle": "Guide", "titre": "Un autre titre"},
                            {"angle": "Analyse", "titre": "Un troisième titre"},
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()

    session = data.dernieres_idees(tmp_path)

    assert session is not None
    assert session["idees"][0]["sujet"] == "Un sujet"
    assert len(session["idees"][0]["angles"]) == 3


def test_instantane_compte_chaines_outliers_et_quota(tmp_path):
    _preparer_projet(tmp_path)
    instantane = data.instantane(tmp_path)
    assert instantane.erreur_config is None
    assert instantane.nb_chaines == 1
    assert instantane.nb_retenues == 1
    assert instantane.nb_outliers == 1
    assert instantane.unites_jour == 5
    assert instantane.recherches_jour == 0
    assert instantane.max_recherches_run == 20
    assert instantane.derniere_execution is None
    assert instantane.pret is False  # ni clés ni positionnement dans ce projet de test


def test_instantane_accepte_ma_chaine_comme_seule_source(tmp_path):
    """Le mode « depuis ma chaîne » ne doit pas exiger de seeds ni de mots-clés."""
    sans_source = data.Instantane(racine=tmp_path)
    assert "aucune source : ni seed, ni mot-clé, ni ma chaîne" in sans_source.problemes

    avec_chaine = data.Instantane(racine=tmp_path, ma_chaine="@studio")
    assert not any(probleme.startswith("aucune source") for probleme in avec_chaine.problemes)


def test_instantane_lit_ma_chaine_et_la_portee(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "moi:\n  chaine: \"@studio\"\n  recherche_internationale: false\n",
        encoding="utf-8",
    )
    instantane = data.instantane(tmp_path)
    assert instantane.ma_chaine == "@studio"
    assert instantane.international is False


def test_instantane_supporte_un_projet_vide(tmp_path):
    instantane = data.instantane(tmp_path)
    assert instantane.erreur_config is not None  # config.yaml absent
    assert instantane.nb_chaines == 0
    assert instantane.problemes


def test_charger_respecte_outly_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTLY_ROOT", str(tmp_path))
    (tmp_path / "config.yaml").write_text("youtube:\n  videos_per_channel: 9\n", encoding="utf-8")

    from outly.config import charger

    assert charger().racine == tmp_path.resolve()
    assert charger().youtube.videos_per_channel == 9


def test_charger_config_ne_laisse_pas_de_secret_dans_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text("youtube:\n  videos_per_channel: 12\n", encoding="utf-8")
    (tmp_path / ".env").write_text("YOUTUBE_API_KEY=cle_de_test_locale\n", encoding="utf-8")

    config = data.charger_config(tmp_path)

    assert config.cle_youtube == "cle_de_test_locale"
    assert "YOUTUBE_API_KEY" not in os.environ


def test_instantane_signale_une_base_illisible(tmp_path):
    dossier = tmp_path / "data"
    dossier.mkdir()
    (dossier / "radar.sqlite3").write_bytes(b"ceci n'est pas une base SQLite")

    instantane = data.instantane(tmp_path)

    assert instantane.erreur_base
    assert any(probleme.startswith("base SQLite illisible") for probleme in instantane.problemes)
    assert instantane.pret is False
