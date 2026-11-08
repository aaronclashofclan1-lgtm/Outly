"""Tests de l'audit complet : profil mesuré, prompt, stratégie et cache."""

from __future__ import annotations

import json
import sqlite3

from outly import audit
from outly.cli import construire_parseur
from outly.gui import data as gui_data
from outly.gui.runner import arguments_audit
from outly.llm import prompts
from outly.llm.client import ReponseLlm


class FauxService:
    def __init__(self, reponses: list[dict]):
        self.reponses = [json.dumps(reponse, ensure_ascii=False) for reponse in reponses]
        self.appels = 0
        self.dernier_prompt = ""
        self.nom_fournisseur = "test"
        self.nom_modele = "fake-modele"

    def completer(self, messages):
        self.appels += 1
        self.dernier_prompt = "\n".join(str(message.get("content")) for message in messages)
        if not self.reponses:
            raise AssertionError("Aucune réponse LLM programmée")
        return ReponseLlm(
            texte=self.reponses.pop(0),
            modele=self.nom_modele,
            fournisseur=self.nom_fournisseur,
        )


def _inserer_ma_chaine(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO channels (
            channel_id, handle, titre, description, abonnes, nb_videos, vues_totales,
            mots_cles_chaine, statut, ajoute_le, maj_le
        ) VALUES ('UC-moi', '@moi', 'Ma Chaîne', 'Je montre des preuves concrètes.',
                  4200, 6, 120000, '"preuve" "méthode"', 'moi',
                  '2026-10-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """
    )
    videos = [
        ("v1", "La preuve qui change tout", "2026-10-28T10:00:00+00:00", 900, 4000, 0),
        ("v2", "Pourquoi promettre moins", "2026-10-21T10:00:00+00:00", 800, 1000, 0),
        ("v3", "Le test en direct", "2026-10-14T10:00:00+00:00", 700, 500, 0),
        ("v4", "Trois erreurs de méthode", "2026-10-07T10:00:00+00:00", 1200, 900, 0),
        ("v5", "Le raccourci en 30 secondes", "2026-10-26T10:00:00+00:00", 30, 600, 1),
        ("v6", "Une preuve par jour", "2026-10-19T10:00:00+00:00", 45, 150, 1),
    ]
    for video_id, titre, publie, duree, vues, short in videos:
        conn.execute(
            """
            INSERT INTO videos (video_id, channel_id, titre, publie_le, duree_s, vues,
                                est_short, live, recupere_le)
            VALUES (?, 'UC-moi', ?, ?, ?, ?, ?, 'none', '2026-11-01T10:00:00+00:00')
            """,
            (video_id, titre, publie, duree, vues, short),
        )
    conn.execute(
        """
        INSERT INTO channel_metrics (
            channel_id, calcule_le, fenetre, n_videos, n_shorts, n_longues,
            mediane_vues_short, mediane_vues_longues, cadence_semaine,
            jours_depuis_publication, meilleur_outlier, mix_format
        ) VALUES ('UC-moi', '2026-11-01T10:00:00+00:00', 6, 6, 2, 4,
                  375, 950, 1.5, 3, 4.21, '2 Shorts / 4 longues')
        """
    )
    conn.commit()


def _inserer_concurrent(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO channels (
            channel_id, handle, titre, description, abonnes, nb_videos,
            mots_cles_chaine, langue_estimee, statut, ajoute_le, maj_le
        ) VALUES ('UC-proche', '@proche', 'Chaîne Proche',
                  'Des expériences filmées et des preuves.', 18000, 8,
                  '"preuve" "expérience"', 'fr', 'enrichie',
                  '2026-10-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO channel_metrics (
            channel_id, calcule_le, fenetre, n_videos, n_shorts, n_longues,
            mediane_vues_short, mediane_vues_longues, cadence_semaine,
            jours_depuis_publication, meilleur_outlier, mix_format
        ) VALUES ('UC-proche', '2026-11-01T10:00:00+00:00', 8, 8, 3, 5,
                  900, 2400, 2.0, 2, 3.4, '3 Shorts / 5 longues')
        """
    )
    for rang, titre in enumerate(
        ("Titre concurrent principal", "Deuxième preuve filmée", "Troisième essai")
    ):
        conn.execute(
            """
            INSERT INTO videos (video_id, channel_id, titre, publie_le, duree_s, vues,
                                est_short, live, recupere_le)
            VALUES (?, 'UC-proche', ?, ?, 600, 5000, 0, 'none',
                    '2026-11-01T10:00:00+00:00')
            """,
            (f"c{rang}", titre, f"2026-10-0{rang + 1}T10:00:00+00:00"),
        )
    conn.execute(
        """
        INSERT INTO channel_scores (
            channel_id, empreinte_prompt, prompt_version, score_proximite,
            valide, cree_le
        ) VALUES ('UC-proche', 'e', 'score-v1', 82, 1, '2026-11-01T10:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO notes (titre, contenu, type, statut, tags_json, epingle,
                           note_sur_10, cree_le, maj_le)
        VALUES ('La preuve avant la promesse', 'Toujours montrer avant de promettre.',
                'concept', 'brouillon', '[]', 1, 9,
                '2026-11-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """
    )
    conn.commit()


def _reponse(idees: int = 3) -> dict:
    constat = {"constat": "La preuve domine vos titres.", "preuve": "4 titres sur 6."}
    return {
        "secteur": {
            "secteur": "Création de contenu",
            "sous_niche": "Preuve et méthode",
            "audience": "Créateurs débutants qui veulent des résultats vérifiables.",
            "promesse": "Montrer des preuves concrètes plutôt que des promesses.",
            "formats": "Longues et Shorts",
            "langue": "Français",
        },
        "diagnostic": {
            "forces": [constat],
            "faiblesses": [{"constat": "Cadence irrégulière.", "preuve": "1,5 vidéo/semaine."}],
            "opportunites": [{"constat": "Les concurrents ne montrent pas leurs échecs.",
                              "preuve": "Aucun titre sur l'échec."}],
        },
        "concurrents": {
            "paysage": "Les chaînes proches misent sur des expériences filmées et des titres courts.",
            "pratiques": [{
                "pratique": "Filmer la preuve dès l'ouverture.",
                "exemples": "« Titre concurrent principal ».",
                "pourquoi_ca_marche": "La démonstration retient avant l'explication.",
            }],
            "manques": ["Personne ne documente ses ratés."],
        },
        "strategie": {
            "positionnement": "Le créateur qui prouve chaque conseil par une expérience filmée.",
            "promesse": "Chaque vidéo apporte une preuve reproductible.",
            "piliers": [{
                "nom": "Preuves filmées",
                "promesse": "Montrer le résultat avant de l'expliquer.",
                "formats": "Longues de 8 minutes",
                "exemples_de_titres": ["La preuve qui change tout"],
            }],
            "formats": [{"format": "Longue", "frequence": "1/semaine",
                         "objectif": "Installer la confiance."}],
            "cadence": "Une longue par semaine et deux Shorts.",
            "series": ["Une preuve par jour"],
            "premiers_30_jours": [
                {"semaine": "Semaine 1", "objectif": "Poser la promesse.",
                 "actions": "Publier la preuve fondatrice et deux Shorts."},
                {"semaine": "Semaine 2", "objectif": "Installer le rythme.",
                 "actions": "Une longue et un Short de démonstration."},
            ],
            "kpis": [
                {"indicateur": "Rétention à 30 s", "cible": "60 %",
                 "pourquoi": "Vérifier que la preuve accroche."},
                {"indicateur": "Vues médianes", "cible": "1 200",
                 "pourquoi": "Mesurer la progression réelle."},
            ],
        },
        "idees": [
            {"titre": f"La preuve numéro {rang}", "angle": "Démonstration filmée",
             "pourquoi": "Répond à un manque concurrent.", "format": "Longue"}
            for rang in range(1, idees + 1)
        ],
        "limites": ["Pas de données de rétention."],
    }


def test_le_schema_accepte_une_reponse_complete():
    schema = prompts.charger_schema("audit.schema.json")
    assert prompts.valider(_reponse(), schema) == []


def test_profil_resume_les_mesures_de_ma_chaine(connexion, config):
    _inserer_ma_chaine(connexion)
    profil = audit.profil_de_ma_chaine(connexion, config)
    assert profil is not None
    assert profil.titre == "Ma Chaîne"
    assert profil.source_videos == "api"
    stats = profil.statistiques
    assert stats["formats"] == {"longue": 4, "short": 2}
    assert stats["titres_avec_chiffre"] == 1
    assert stats["mediane_vues_longue"] == 950
    assert stats["meilleures_videos"][0]["titre"] == "La preuve qui change tout"
    assert stats["meilleures_videos"][0]["ratio"] == 4.21
    assert profil.metriques["cadence_semaine"] == 1.5


def test_generer_croise_profil_concurrents_notes_et_mots_cles(connexion, config):
    _inserer_ma_chaine(connexion)
    _inserer_concurrent(connexion)
    (config.racine / "keywords.txt").write_text("preuve\nméthode\n", encoding="utf-8")
    (config.racine / "positioning.md").write_text(
        "Je montre des preuves concrètes, filmées, reproductibles.", encoding="utf-8"
    )
    service = FauxService([_reponse()])

    resume = audit.generer(connexion, config, service, nombre_idees=3)

    assert resume.reussie
    assert resume.reutilisee is False
    assert resume.secteur["sous_niche"] == "Preuve et méthode"
    assert len(resume.idees) == 3
    assert resume.strategie["piliers"][0]["nom"] == "Preuves filmées"
    assert "Chaîne Proche" in service.dernier_prompt
    assert "« Titre concurrent principal »" in service.dernier_prompt
    assert "La preuve avant la promesse" in service.dernier_prompt
    assert "950" in service.dernier_prompt
    assert "preuve" in service.dernier_prompt
    assert audit.derniere_session(connexion) is not None


def test_un_audit_identique_est_servi_par_le_cache(connexion, config):
    _inserer_ma_chaine(connexion)
    _inserer_concurrent(connexion)
    premier = FauxService([_reponse()])
    audit.generer(connexion, config, premier, nombre_idees=3)
    assert premier.appels == 1

    second = FauxService([])
    resume = audit.generer(connexion, config, second, nombre_idees=3)

    assert resume.reutilisee
    assert resume.reussie
    assert second.appels == 0
    assert len(resume.idees) == 3


def test_forcer_regenere_malgre_le_cache(connexion, config):
    _inserer_ma_chaine(connexion)
    _inserer_concurrent(connexion)
    audit.generer(connexion, config, FauxService([_reponse()]), nombre_idees=3)
    service = FauxService([_reponse(idees=4)])

    resume = audit.generer(
        connexion, config, service, forcer=True, nombre_idees=3
    )

    assert service.appels == 1
    assert resume.reutilisee is False
    assert len(resume.idees) == 3


def test_une_reponse_incomplete_est_rejetee(connexion, config):
    _inserer_ma_chaine(connexion)
    incomplete = _reponse()
    incomplete["strategie"].pop("piliers")
    service = FauxService([incomplete, incomplete])

    resume = audit.generer(connexion, config, service, nombre_idees=3)

    assert not resume.reussie
    assert resume.messages
    assert "invalide" in resume.messages[0].lower()
    assert audit.derniere_session(connexion) is None


def test_ma_chaine_inconnue_donne_un_message_clair(connexion, config):
    service = FauxService([])
    resume = audit.generer(connexion, config, service)
    assert not resume.reussie
    assert "Ma chaîne n'est pas connue" in resume.messages[0]
    assert service.appels == 0


def test_enrichir_ma_chaine_restaure_le_statut_moi(connexion, config):
    from outly import enrichment
    from outly.youtube_api import ErreurApi

    _inserer_ma_chaine(connexion)

    class ClientEnPanne:
        def chaines_par_ids(self, identifiants):
            raise ErreurApi("panne simulée")

    resume = enrichment.enrichir_ma_chaine(connexion, ClientEnPanne(), config)

    assert resume.erreurs == 1
    statut = connexion.execute(
        "SELECT statut FROM channels WHERE channel_id = 'UC-moi'"
    ).fetchone()["statut"]
    assert statut == "moi"


def test_cli_et_runner_exposent_la_commande_audit():
    args = construire_parseur().parse_args(["audit", "--sans-recuperation", "--force"])
    assert args.fonction.__name__ == "cmd_audit"
    assert args.recuperation is False
    assert args.force is True
    assert arguments_audit() == ["audit"]
    assert arguments_audit(forcer=True, recuperation=False) == [
        "audit", "--force", "--sans-recuperation",
    ]


def test_derniere_audit_sans_base_renvoie_none(tmp_path):
    assert gui_data.derniere_audit(tmp_path) is None
