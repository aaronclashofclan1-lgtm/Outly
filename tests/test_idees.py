"""Tests du générateur d'idées : trois angles, sources et cache LLM."""

from __future__ import annotations

import json
import sqlite3

from outly import idees
from outly.llm import prompts
from outly.llm.client import ReponseLlm


class FauxService:
    def __init__(self, reponses: list[dict]):
        self.reponses = [json.dumps(reponse, ensure_ascii=False) for reponse in reponses]
        self.appels = 0
        self.messages: list[str] = []
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


def _inserer_note(
    conn: sqlite3.Connection,
    titre: str,
    note: int | None = None,
    contenu: str = "Une matière concrète, avec une preuve.",
    type_note: str = "concept",
) -> int:
    curseur = conn.execute(
        """
        INSERT INTO notes (
            titre, contenu, type, statut, tags_json, epingle, note_sur_10, cree_le, maj_le
        ) VALUES (?, ?, ?, 'brouillon', '[]', 0, ?,
                  '2026-11-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """,
        (titre, contenu, type_note, note),
    )
    conn.commit()
    return int(curseur.lastrowid)


def _inserer_chaine(conn: sqlite3.Connection, titre: str, mots_cles: str) -> None:
    conn.execute(
        """
        INSERT INTO channels (
            channel_id, titre, mots_cles_chaine, sujets_json, statut, ajoute_le, maj_le
        ) VALUES (?, ?, ?, '[]', 'enrichie',
                  '2026-11-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """,
        (titre.replace(" ", "-").lower(), titre, mots_cles),
    )
    conn.commit()


def _inserer_cerveau(conn: sqlite3.Connection) -> None:
    reponse = {
        "synthese": "Vos notes opposent la promesse facile à la preuve difficile.",
        "angles": [
            {
                "titre": "La preuve avant la promesse",
                "promesse": "Montrer une preuve concrète avant toute promesse.",
                "hook": "Voici ce que personne ne montre avant de promettre.",
                "pourquoi": "La note centrale parle de preuve.",
                "notes": [],
            }
        ],
        "sujets": [],
        "questions": ["Quelle preuve montrer dès la première seconde ?"],
    }
    conn.execute(
        """
        INSERT INTO brain_sessions (
            question, empreinte, prompt_version, fournisseur, modele,
            reponse_json, notes_json, valide, cree_le
        ) VALUES ('', 'empreinte-cerveau', 'brain-v2', 'test', 'fake', ?, '[]', 1,
                  '2026-11-01T11:00:00+00:00')
        """,
        (json.dumps(reponse, ensure_ascii=False),),
    )
    conn.commit()


def _inserer_video_mise_de_cote(
    conn: sqlite3.Connection, element_id: str, titre: str, note: int | None
) -> None:
    conn.execute(
        """
        INSERT INTO watch_later (
            element_id, type, titre, sous_titre, url, note_sur_10, ajoute_le
        ) VALUES (?, 'video', ?, '', ?, ?, '2026-11-01T10:00:00+00:00')
        """,
        (element_id, titre, f"https://www.youtube.com/watch?v={element_id}", note),
    )
    conn.commit()


def _reponse(notes_ids: list[str] | None = None, angles: int = 3) -> dict:
    references = notes_ids or []
    declinaisons = [
        {"angle": "Démonstration", "titre": "La preuve en direct qui change tout"},
        {"angle": "Contre-intuition", "titre": "Pourquoi promettre moins attire plus"},
        {"angle": "Enquête", "titre": "Ce que les meilleures chaînes cachent encore"},
    ][:angles]
    return {
        "introduction": "Votre matière oppose la preuve concrète à la promesse facile.",
        "idees": [
            {
                "sujet": "La preuve avant la promesse",
                "type": "concept",
                "pourquoi": "Les notes et les outliers convergent vers une demande de preuve.",
                "sources": ["preuve", "Chaîne Proche", "Vidéo forte"],
                "notes": references,
                "angles": declinaisons,
            },
            {
                "sujet": "Le premier échec qui enseigne plus qu'un succès",
                "type": "sujet",
                "pourquoi": "Les concurrents documentent leurs réussites, jamais leurs échecs.",
                "sources": ["abandon"],
                "notes": references,
                "angles": [
                    {"angle": "Récit", "titre": "J'ai échoué trois fois avant de comprendre"},
                    {"angle": "Analyse", "titre": "Les trois erreurs qui reviennent chez tous"},
                    {"angle": "Guide", "titre": "Reprendre une vidéo qui n'a pas marché"},
                ],
            },
        ],
    }


def test_le_schema_accepte_une_reponse_complete():
    schema = prompts.charger_schema("idea_set.schema.json")
    assert prompts.valider(_reponse(), schema) == []


def test_generer_croise_notes_mots_cles_concurrents_et_cerveau(connexion, config):
    identifiant = _inserer_note(connexion, "La preuve avant la promesse", note=9)
    _inserer_chaine(connexion, "Chaîne Proche", '"preuve" "promesse" "abandon"')
    _inserer_cerveau(connexion)
    _inserer_video_mise_de_cote(connexion, "vid-forte", "Vidéo forte", 8)
    (config.racine / "keywords.txt").write_text("preuve concrète\n", encoding="utf-8")
    (config.racine / "seeds.txt").write_text("preuve\n", encoding="utf-8")

    service = FauxService([_reponse([str(identifiant)])])
    resume = idees.generer(
        connexion, config, service, question="Une série sur la preuve", nombre=5
    )

    assert resume.reussie
    assert len(resume.idees) == 2
    assert all(len(idee["angles"]) == 3 for idee in resume.idees)
    assert resume.idees[0]["notes"] == [str(identifiant)]
    assert resume.idees[0]["angles"][0]["angle"] == "Démonstration"

    prompt = service.dernier_prompt
    for attendu in (
        "La preuve avant la promesse",
        "preuve concrète",
        "Chaîne Proche",
        "abandon",
        "Vos notes opposent la promesse facile",
        "Vidéo forte",
        "classement 8/10",
        "Une série sur la preuve",
    ):
        assert attendu in prompt
    assert "POSITIONNEMENT DU CRÉATEUR" in prompt

    cache = FauxService([])
    second = idees.generer(
        connexion, config, cache, question="Une série sur la preuve", nombre=5
    )
    assert second.reussie and second.reutilisee
    assert cache.appels == 0
    assert idees.derniere_session(connexion)["idees"][0]["sujet"] == (
        "La preuve avant la promesse"
    )


def test_une_note_reclassee_invalide_le_cache(connexion, config):
    identifiant = _inserer_note(connexion, "Une note classée", note=2)
    (config.racine / "keywords.txt").write_text("preuve\n", encoding="utf-8")

    assert idees.generer(
        connexion, config, FauxService([_reponse([str(identifiant)])])
    ).reussie
    connexion.execute("UPDATE notes SET note_sur_10 = 9 WHERE id = ?", (identifiant,))
    connexion.commit()

    service = FauxService([_reponse([str(identifiant)])])
    resume = idees.generer(connexion, config, service)
    assert resume.reussie
    assert not resume.reutilisee
    assert service.appels == 1


def test_une_idee_sans_trois_angles_distincts_est_ecartee(connexion, config):
    _inserer_note(connexion, "Une note")
    propre = idees._nettoyer(_reponse(["999"], angles=3), [{"id": 1}], 5)
    # Deux titres identiques ne comptent que pour un seul angle.
    doublon = _reponse(angles=3)
    doublon["idees"][0]["angles"][1]["titre"] = doublon["idees"][0]["angles"][0]["titre"]
    propre = idees._nettoyer(doublon, [{"id": 1}], 5)
    assert len(propre["idees"]) == 1  # la seconde idée reste complète
    assert propre["idees"][0]["sujet"].startswith("Le premier échec")
    assert propre["idees"][0]["notes"] == []

    # Le schéma impose trois entrées ; seule la duplication de titres est
    # détectable localement, et c'est elle qui doit écarter l'idée.
    incomplet = _reponse(angles=3)
    incomplet["idees"] = incomplet["idees"][:1]
    incomplet["idees"][0]["angles"][1]["titre"] = incomplet["idees"][0]["angles"][0][
        "titre"
    ]
    service = FauxService([incomplet])
    resume = idees.generer(connexion, config, service)
    assert not resume.reussie
    assert service.appels == 1
    assert any("trois angles" in message for message in resume.messages)


def test_la_commande_idees_est_branchee():
    from outly.cli import construire_parseur

    args = construire_parseur().parse_args(["idees", "--nombre", "3", "--force"])
    assert args.nombre == 3
    assert args.force is True
    assert args.fonction.__name__ == "cmd_idees"
