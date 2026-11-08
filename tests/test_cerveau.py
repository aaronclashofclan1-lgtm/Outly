"""Tests du cerveau IA : notes + outliers, cache et normalisation des réponses."""

from __future__ import annotations

import json
import sqlite3

from outly import cerveau
from outly.llm import prompts
from outly.llm.client import ReponseLlm


def _inserer_note(
    conn: sqlite3.Connection,
    titre: str,
    contenu: str = "Un contenu utile.",
    type_note: str = "concept",
    statut: str = "brouillon",
    tags: list[str] | None = None,
) -> int:
    curseur = conn.execute(
        """
        INSERT INTO notes (titre, contenu, type, statut, tags_json, epingle, cree_le, maj_le)
        VALUES (?, ?, ?, ?, ?, 0, '2026-11-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00')
        """,
        (titre, contenu, type_note, statut, json.dumps(tags or [])),
    )
    conn.commit()
    return int(curseur.lastrowid)


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


def _reponse(notes_ids: list[str] | None = None) -> dict:
    references = ["1"] if notes_ids is None else notes_ids
    return {
        "synthese": "Vos notes dessinent une tension entre preuve concrète et promesse ambitieuse.",
        "angles": [
            {
                "titre": "La promesse qui se retourne",
                "promesse": "Montrer que la promesse la plus séduisante est aussi la plus risquée.",
                "hook": "Plus tu promets, moins on te croit. Voici comment inverser ça.",
                "pourquoi": "Le concept de preuve et le hook précédent se contredisent utilement.",
                "notes": references,
            }
        ],
        "sujets": [
            {
                "titre": "La promesse qui te fait perdre des abonnés",
                "angle": "Opposer la promesse facile à la preuve difficile.",
                "notes": references,
            }
        ],
        "questions": ["Quelle preuve montrer dès la première seconde ?"],
        "liens": (
            [
                {
                    "notes": [references[0], references[0]],
                    "idee": "Une même note peut nourrir une promesse et sa réfutation.",
                }
            ]
            if references
            else []
        ),
    }


def test_le_schema_accepte_une_reponse_complete():
    schema = prompts.charger_schema("brain.schema.json")
    assert prompts.valider(_reponse(), schema) == []


def test_generer_relie_les_notes_et_les_met_en_cache(connexion, config):
    premier = _inserer_note(connexion, "La preuve avant la promesse", type_note="concept")
    second = _inserer_note(connexion, "Un hook qui interpelle", type_note="hook")
    service = FauxService([_reponse([str(premier), str(second)])])

    resume = cerveau.generer(connexion, config, service, question="Comment parler de preuve ?")

    assert resume.reussie
    assert len(resume.angles) == 1
    assert resume.angles[0]["notes"] == [str(premier), str(second)]
    assert service.appels == 1
    assert "La preuve avant la promesse" in service.dernier_prompt
    assert "Comment parler de preuve ?" in service.dernier_prompt

    cache = FauxService([])
    resume_cache = cerveau.generer(connexion, config, cache, question="Comment parler de preuve ?")
    assert resume_cache.reussie
    assert resume_cache.reutilisee
    assert cache.appels == 0
    assert cerveau.derniere_session(connexion) is not None


def test_aucune_note_ne_declenche_aucun_appel(connexion, config):
    service = FauxService([])
    resume = cerveau.generer(connexion, config, service)
    assert not resume.reussie
    assert service.appels == 0
    assert any("note" in message.lower() for message in resume.messages)


def test_les_identifiants_de_notes_inconnus_sont_remplaces(connexion, config):
    identifiant = _inserer_note(connexion, "Une seule note")
    service = FauxService([_reponse(["999"])])
    resume = cerveau.generer(connexion, config, service)
    assert resume.reussie
    assert resume.angles[0]["notes"] == [str(identifiant)]


def test_les_notes_archivees_sont_exclues_par_defaut(connexion, config):
    _inserer_note(connexion, "Note active", contenu="À garder")
    _inserer_note(connexion, "Note archivée", contenu="À cacher", statut="archive")
    service = FauxService([_reponse(["1"])])
    cerveau.generer(connexion, config, service)
    assert "Note active" in service.dernier_prompt
    assert "Note archivée" not in service.dernier_prompt


def _inserer_recherche(conn: sqlite3.Connection) -> None:
    dossier = {
        "resume": "Les sources convergent sur un écart thermique mesurable.",
        "faits": [
            {
                "affirmation": "Les villes sont plus chaudes que les campagnes.",
                "precision": "Surtout la nuit.",
                "sources": ["S1", "S2", "S3"],
                "domaines": ["lemonde.fr", "insee.fr", "nature.com"],
                "statut": "recoupe",
            }
        ],
        "chiffres": [],
        "contradictions": [],
        "angles": [],
        "a_verifier": ["Le chiffre exact dépend de la ville."],
        "pages_wiki": [
            {
                "titre": "Îlot de chaleur urbain",
                "url": "https://fr.wikipedia.org/wiki/%C3%8Elot_de_chaleur_urbain",
                "extrait": "Un îlot de chaleur urbain est une zone urbaine plus chaude.",
                "domaine": "wikipedia.org",
                "type": "wikipedia",
            }
        ],
        "sources": [
            {
                "id": "S1",
                "domaine": "lemonde.fr",
                "type": "web",
                "url": "https://www.lemonde.fr/climat",
            }
        ],
    }
    conn.execute(
        """
        INSERT INTO research_sessions (
            titre, mots_cles_json, mode, empreinte, prompt_version,
            intention_json, sources_json, pages_json, dossier_json,
            valide, cree_le
        ) VALUES ('La chaleur en ville', '["chaleur"]', 'avec_ia',
                  'recherche-cerveau', 'recherche-v1', '{}', '[]', '[]', ?, 1,
                  '2026-11-02T10:00:00+00:00')
        """,
        (json.dumps(dossier, ensure_ascii=False),),
    )
    conn.commit()


def _inserer_idee(conn: sqlite3.Connection) -> None:
    idees = [
        {
            "sujet": "La ville qui chauffe la nuit",
            "type": "sujet",
            "pourquoi": "Les sources documentaires donnent un angle mesurable.",
            "sources": ["recherche"],
            "notes": [],
            "angles": [
                {"angle": "Démonstration", "titre": "Pourquoi la nuit est le pire moment"},
                {"angle": "Enquête", "titre": "Ce que mesure vraiment un thermomètre"},
                {"angle": "Solution", "titre": "Trois arbres qui changent une rue"},
            ],
        }
    ]
    conn.execute(
        """
        INSERT INTO idea_sets (
            question, nombre, empreinte, prompt_version, introduction,
            idees_json, notes_json, valide, cree_le
        ) VALUES ('', 1, 'idees-cerveau', 'ideas-v1',
                  'Une piste thermique déjà proposée.', ?, '[]', 1,
                  '2026-11-03T10:00:00+00:00')
        """,
        (json.dumps(idees, ensure_ascii=False),),
    )
    conn.commit()


def _inserer_plan(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO editorial_plan (
            titre, notes, date_prevue, statut, format, cree_le, maj_le
        ) VALUES ('La ville à hauteur d''arbre', 'Prévoir un thermomètre.',
                  '2026-12-01', 'ecriture', 'longue',
                  '2026-11-04T10:00:00+00:00', '2026-11-04T10:00:00+00:00')
        """
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


def test_les_videos_classees_nourrissent_le_prompt_et_le_cache(connexion, config):
    _inserer_note(connexion, "Une note de base")
    _inserer_video_mise_de_cote(connexion, "vid-1", "Vidéo moyenne", 3)
    _inserer_video_mise_de_cote(connexion, "vid-2", "Vidéo forte", 9)

    service = FauxService([_reponse()])
    assert cerveau.generer(connexion, config, service).reussie
    assert "VIDÉOS ET CHAÎNES MISE DE CÔTÉ" in service.dernier_prompt
    assert "classement 9/10" in service.dernier_prompt
    assert service.dernier_prompt.index("Vidéo forte") < service.dernier_prompt.index(
        "Vidéo moyenne"
    )

    # Changer un classement change l'empreinte : la session n'est pas resservie.
    connexion.execute("UPDATE watch_later SET note_sur_10 = 4 WHERE element_id = 'vid-2'")
    connexion.commit()
    service2 = FauxService([_reponse()])
    resume = cerveau.generer(connexion, config, service2)
    assert resume.reussie
    assert not resume.reutilisee
    assert service2.appels == 1


def test_recherches_et_idees_nourrissent_le_prompt_sans_note(connexion, config):
    _inserer_recherche(connexion)
    _inserer_idee(connexion)
    _inserer_plan(connexion)

    service = FauxService([_reponse([])])
    resume = cerveau.generer(connexion, config, service)

    assert resume.reussie
    assert resume.angles[0]["notes"] == []
    prompt = service.dernier_prompt
    assert "RECHERCHES DOCUMENTAIRES RÉCENTES" in prompt
    assert "Les villes sont plus chaudes que les campagnes" in prompt
    assert "IDÉES DÉJÀ GÉNÉRÉES" in prompt
    assert "La ville qui chauffe la nuit" in prompt
    assert "Trois arbres qui changent une rue" in prompt
    assert "PLAN ÉDITORIAL" in prompt
    assert "La ville à hauteur d'arbre" in prompt
    assert "à vérifier" in prompt.lower()


def test_une_nouvelle_recherche_invalide_le_cache_du_cerveau(connexion, config):
    _inserer_note(connexion, "Une note de base")
    assert cerveau.generer(connexion, config, FauxService([_reponse()])).reussie

    _inserer_recherche(connexion)
    service = FauxService([_reponse()])
    resume = cerveau.generer(connexion, config, service)

    assert resume.reussie
    assert not resume.reutilisee
    assert service.appels == 1
    assert "La chaleur en ville" in service.dernier_prompt


def test_forcer_regenere_une_session_identique(connexion, config):
    _inserer_note(connexion, "Une note")
    assert cerveau.generer(connexion, config, FauxService([_reponse()])).reussie
    service = FauxService([_reponse()])
    resume = cerveau.generer(connexion, config, service, forcer=True)
    assert resume.reussie
    assert not resume.reutilisee
    assert service.appels == 1
