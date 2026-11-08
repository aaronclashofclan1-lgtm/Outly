"""Tests de la génération de hooks : transcriptions horodatées, cache, LLM."""

from __future__ import annotations

import json
import sqlite3

import pytest

from outly import db, hooks, transcripts
from outly.config import Config, charger
from outly.llm.client import ReponseLlm
from outly.llm import prompts


CHAMP = "UC" + "h" * 22
VIDEO = "hookvideo01"


def _preparer(conn: sqlite3.Connection, avec_transcription: bool = True) -> None:
    conn.execute(
        "INSERT INTO channels (channel_id, titre, statut, ajoute_le, maj_le) "
        "VALUES (?, ?, 'scored', '2026-10-01', '2026-10-01')",
        (CHAMP, "La Chaîne Test"),
    )
    conn.execute(
        "INSERT INTO videos (video_id, channel_id, titre, publie_le, duree_s, vues, "
        "est_short, live, recupere_le) VALUES (?, ?, ?, ?, ?, ?, 0, 'none', ?)",
        (VIDEO, CHAMP, "Une vidéo qui a explosé", "2026-10-01", 300, 150_000, "2026-10-02"),
    )
    conn.execute(
        "INSERT INTO channel_metrics (channel_id, calcule_le, fenetre, n_videos, n_shorts, "
        "n_longues, mediane_vues_longues) VALUES (?, '2026-10-02', 10, 5, 0, 5, 15000)",
        (CHAMP,),
    )
    if avec_transcription:
        transcripts.enregistrer(
            conn,
            transcripts.ResultatTranscription(
                VIDEO,
                "ok",
                langue="fr",
                texte="Attends, personne ne parle de ça. Voici comment doubler tes vues.",
                segments=[
                    {"debut": 0.0, "duree": 4.0, "texte": "Attends, personne ne parle de ça."},
                    {
                        "debut": 4.0,
                        "duree": 5.0,
                        "texte": "Voici comment doubler tes vues en 30 jours.",
                    },
                ],
            ),
        )


class FauxService:
    """Service LLM minimal : une réponse programmee par appel."""

    def __init__(self, reponses: list[dict]):
        self.reponses = [json.dumps(reponse, ensure_ascii=False) for reponse in reponses]
        self.appels = 0
        self.messages: list[str] = []
        self.dernier_prompt = ""
        self.nom_fournisseur = "test"
        self.nom_modele = "fake-modele"

    def completer(self, messages):
        self.appels += 1
        self.dernier_prompt = "\n".join(
            str(message.get("content")) for message in messages
        )
        self.messages.append(self.dernier_prompt)
        if not self.reponses:
            raise AssertionError("Aucune réponse LLM programmée")
        return ReponseLlm(
            texte=self.reponses.pop(0),
            modele=self.nom_modele,
            fournisseur=self.nom_fournisseur,
        )


def _intro_valide() -> dict:
    return {
        "intro_debut_s": 0,
        "intro_fin_s": 9,
        "hook_texte": "Attends, personne ne parle de ça.",
        "resume": "Une interruption directe, puis une promesse chiffrée qui crée une boucle ouverte.",
        "promesse": "Doubler ses vues en 30 jours.",
        "techniques": ["interpellation", "promesse chiffrée"],
    }


def _jeu_valide() -> dict:
    return {
        "analyse": "Les intros cassent le rythme puis posent une promesse mesurable et vérifiable.",
        "variantes": [
            {
                "titre": "L'interpellation",
                "hook": "Attends. Ce que tu fais depuis six mois explique peut-être pourquoi ta chaîne stagne.",
                "duree_secondes": 8,
                "technique": "interpellation directe",
                "inspire_de": [VIDEO],
                "pourquoi": "Reprend la rupture de rythme observée dans l'outlier.",
            },
            {
                "titre": "La promesse chiffrée",
                "hook": "En trente jours, tu peux doubler tes vues sans publier une seule vidéo de plus.",
                "duree_secondes": 9,
                "technique": "promesse chiffrée",
                "inspire_de": [VIDEO],
                "pourquoi": "Transforme le bénéfice de l'outlier en délai concret.",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Transcriptions horodatées
# ---------------------------------------------------------------------------


def test_transcription_conserve_les_reperes_et_les_formate(connexion):
    transcripts.enregistrer(
        connexion,
        transcripts.ResultatTranscription(
            "v1",
            "ok",
            texte="Bonjour. Voici la suite.",
            segments=[
                {"debut": 0, "duree": 2, "texte": "Bonjour."},
                {"debut": 65.2, "duree": 3, "texte": "Voici la suite."},
            ],
        ),
    )

    relu = transcripts.depuis_cache(connexion, "v1", avec_segments=True)
    assert relu is not None
    assert relu.segments and len(relu.segments) == 2
    texte = transcripts.texte_horodate(relu.segments)
    assert texte.startswith("[0:00] Bonjour.")
    assert "[1:05] Voici la suite." in texte
    assert transcripts.formater_horodatage(3725) == "1:02:05"


def test_une_transcription_ancienne_sans_reperes_est_ignoree(connexion):
    connexion.execute(
        "INSERT INTO transcripts (video_id, langue, texte, caracteres, statut, recupere_le) "
        "VALUES ('ancienne', 'fr', 'Un texte sans horodatage.', 25, 'ok', datetime('now'))"
    )
    connexion.commit()

    assert transcripts.depuis_cache(connexion, "ancienne") is not None
    assert transcripts.depuis_cache(connexion, "ancienne", avec_segments=True) is None


def test_migration_ajoute_les_reperes_a_une_base_ancienne(tmp_path):
    chemin = tmp_path / "ancienne.sqlite3"
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE transcripts (video_id TEXT PRIMARY KEY, langue TEXT, texte TEXT, "
        "caracteres INTEGER, statut TEXT NOT NULL, erreur TEXT, recupere_le TEXT NOT NULL)"
    )
    db.migrer(conn)

    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(transcripts)")}
    assert "segments_json" in colonnes
    conn.close()


# ---------------------------------------------------------------------------
# Schémas et prompts
# ---------------------------------------------------------------------------


def test_les_schemas_acceptent_les_reponses_attendues():
    assert prompts.valider(_intro_valide(), prompts.charger_schema("intro_analysis.schema.json")) == []
    assert prompts.valider(_jeu_valide(), prompts.charger_schema("hook_variants.schema.json")) == []


def test_le_schema_de_variantes_refuse_un_hook_trop_court():
    donnees = _jeu_valide()
    donnees["variantes"][0]["hook"] = "Court"
    erreurs = prompts.valider(donnees, prompts.charger_schema("hook_variants.schema.json"))
    assert erreurs


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------


def test_generer_analyse_les_intros_puis_ecrit_des_variantes(connexion, config):
    _preparer(connexion)
    config.hooks.sujet = "Comment doubler mes vues en 30 jours"
    config.hooks.variants = 4
    service = FauxService([_intro_valide(), _jeu_valide()])

    resume = hooks.generer(connexion, config, service)

    assert resume.reussie
    assert resume.intros_analysees == 1
    assert resume.variantes == 2
    assert service.appels == 2
    jeu = hooks.dernier_jeu(connexion)
    assert jeu is not None
    assert jeu["sujet"] == config.hooks.sujet
    assert len(jeu["variantes"]) == 2
    assert jeu["sources"][0]["video_id"] == VIDEO
    intros = hooks.intros_du_jeu(connexion, jeu)
    assert intros and intros[0]["intro_fin_s"] == 9
    assert "promesse chiffrée" in intros[0]["techniques"]


def test_un_second_appel_identique_ne_recoûte_rien(connexion, config):
    _preparer(connexion)
    config.hooks.sujet = "Un sujet précis"
    premier = FauxService([_intro_valide(), _jeu_valide()])
    assert hooks.generer(connexion, config, premier).reussie

    second = FauxService([])
    resume = hooks.generer(connexion, config, second)

    assert resume.reussie
    assert resume.jeu_reutilise
    assert resume.variantes == 2
    assert second.appels == 0


def test_forcer_regenere_les_variantes_sans_relancer_les_intros(connexion, config):
    _preparer(connexion)
    config.hooks.sujet = "Un sujet précis"
    assert hooks.generer(
        connexion, config, FauxService([_intro_valide(), _jeu_valide()])
    ).reussie

    service = FauxService([_jeu_valide()])
    resume = hooks.generer(connexion, config, service, forcer=True)

    assert resume.reussie
    assert not resume.jeu_reutilise
    assert service.appels == 1


def test_sans_transcription_aucun_appel_llm_n_est_fait(connexion, config):
    _preparer(connexion, avec_transcription=False)
    config.hooks.sujet = "Un sujet"
    service = FauxService([])

    resume = hooks.generer(connexion, config, service)

    assert not resume.reussie
    assert resume.sans_transcription == 1
    assert service.appels == 0
    assert any("transcription" in message.lower() for message in resume.messages)


def test_un_sujet_vide_est_refuse_avant_tout_appel(connexion, config):
    service = FauxService([])
    resume = hooks.generer(connexion, config, service)
    assert not resume.reussie
    assert service.appels == 0
    assert any("sujet" in message.lower() for message in resume.messages)


def test_les_notes_personnelles_nourrissent_les_hooks_et_invalident_le_cache(connexion, config):
    _preparer(connexion)
    connexion.execute(
        "INSERT INTO notes (titre, contenu, type, statut, tags_json, epingle, cree_le, maj_le) "
        "VALUES (?, ?, 'concept', 'valide', '[]', 1, '2026-11-01', '2026-11-01')",
        ("Ma conviction", "La preuve concrète bat la promesse abstraite."),
    )
    connexion.commit()
    config.hooks.sujet = "Comment prouver ce que j'avance"
    premier = FauxService([_intro_valide(), _jeu_valide()])

    assert hooks.generer(connexion, config, premier).reussie
    assert "La preuve concrète bat la promesse abstraite." in premier.dernier_prompt

    connexion.execute(
        "UPDATE notes SET contenu = 'Une nouvelle conviction à tester.', maj_le = '2026-11-01T11:00:00'"
    )
    connexion.commit()
    second = FauxService([_jeu_valide()])
    assert hooks.generer(connexion, config, second).reussie
    assert "Une nouvelle conviction à tester." in second.dernier_prompt


def test_une_intro_incoherente_ne_produit_pas_de_variantes(connexion, config):
    _preparer(connexion)
    config.hooks.sujet = "Un sujet"
    intro = _intro_valide()
    intro["intro_debut_s"] = 20
    intro["intro_fin_s"] = 5
    service = FauxService([intro])

    resume = hooks.generer(connexion, config, service)

    assert not resume.reussie
    assert resume.intros_analysees == 0
    assert any("incohérentes" in message for message in resume.messages)


def test_une_inspiration_inconnue_est_rattachee_a_une_source_reelle(connexion, config):
    _preparer(connexion)
    config.hooks.sujet = "Un sujet"
    jeu = _jeu_valide()
    jeu["variantes"][0]["inspire_de"] = ["video-inconnue"]
    service = FauxService([_intro_valide(), jeu])

    resume = hooks.generer(connexion, config, service)

    assert resume.reussie
    variantes = hooks.dernier_jeu(connexion)["variantes"]
    assert variantes[0]["inspire_de"] == [VIDEO]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_la_configuration_lit_les_hooks(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "hooks:\n"
        "  enabled: true\n"
        "  sujet: \"Mon titre de vidéo\"\n"
        "  variants: 4\n"
        "  max_videos: 8\n",
        encoding="utf-8",
    )

    configuration = charger(tmp_path, exiger_cles=False)

    assert configuration.hooks.enabled is True
    assert configuration.hooks.sujet == "Mon titre de vidéo"
    assert configuration.hooks.variants == 4
    assert configuration.hooks.max_videos == 8
    assert configuration.hooks.max_chars_transcription == 12000


def test_les_hooks_sont_desactives_par_defaut(tmp_path):
    (tmp_path / "config.yaml").write_text("youtube:\n  videos_per_channel: 5\n", encoding="utf-8")
    configuration = charger(tmp_path, exiger_cles=False)
    assert configuration.hooks.enabled is False
    assert configuration.hooks.sujet == ""
