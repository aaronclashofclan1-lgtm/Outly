"""Tests de la robustesse JSON : extraction, schéma, seconde tentative."""

from __future__ import annotations

import json

import pytest

from outly.llm import prompts
from outly.llm.client import ReponseLlm
from outly.llm.scoring import demander_json

JUSTIFICATION = (
    "La chaîne traite exactement le même sujet que le positionnement. "
    "Son format en voix-off et son audience débutante recoupent la promesse."
)

VALIDE = {
    "score_proximite": 70,
    "recouvrement_audience": "fort",
    "format_detecte": "voix_off",
    "langue": "fr",
    "justification": JUSTIFICATION,
    "risques": ["aborde parfois un sujet exclu"],
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extraction_json_brut():
    assert prompts.extraire_json(json.dumps(VALIDE)) == VALIDE


def test_extraction_json_avec_cloture_markdown():
    texte = "```json\n" + json.dumps(VALIDE) + "\n```"
    assert prompts.extraire_json(texte) == VALIDE


def test_extraction_json_entoure_de_prose():
    texte = "Voici mon analyse :\n" + json.dumps(VALIDE) + "\nJ'espère que cela aide."
    assert prompts.extraire_json(texte) == VALIDE


def test_extraction_respecte_les_accolades_dans_les_chaines():
    charge = dict(VALIDE, justification='Il dit "attention {au sens}" puis conclut. Autre phrase.')
    texte = "Préambule " + json.dumps(charge) + " fin"
    assert prompts.extraire_json(texte) == charge


def test_extraction_echoue_proprement():
    assert prompts.extraire_json(None) is None
    assert prompts.extraire_json("") is None
    assert prompts.extraire_json("aucune donnée exploitable") is None
    assert prompts.extraire_json('{"score_proximite": 70') is None
    assert prompts.extraire_json("[1, 2, 3]") is None


# ---------------------------------------------------------------------------
# Validation par schéma
# ---------------------------------------------------------------------------


def test_schema_accepte_une_reponse_valide():
    schema = prompts.charger_schema("channel_score.schema.json")
    assert prompts.valider(VALIDE, schema) == []


def test_schema_refuse_une_valeur_hors_bornes():
    schema = prompts.charger_schema("channel_score.schema.json")
    erreurs = prompts.valider(dict(VALIDE, score_proximite=120), schema)
    assert erreurs and any("120" in message or "maximum" in message for message in erreurs)


def test_schema_refuse_une_propriete_en_trop():
    schema = prompts.charger_schema("channel_score.schema.json")
    erreurs = prompts.valider(dict(VALIDE, commentaire="bonus"), schema)
    assert erreurs


def test_schema_refuse_une_enumeration_inconnue():
    schema = prompts.charger_schema("channel_score.schema.json")
    erreurs = prompts.valider(dict(VALIDE, recouvrement_audience="total"), schema)
    assert erreurs


def test_schema_refuse_une_justification_trop_courte():
    schema = prompts.charger_schema("channel_score.schema.json")
    erreurs = prompts.valider(dict(VALIDE, justification="Trop court."), schema)
    assert erreurs


def test_transformation_vers_modele():
    score = prompts.transformer_score(VALIDE)
    assert score.score_proximite == 70
    assert score.risques == ["aborde parfois un sujet exclu"]


def test_controle_souple_des_deux_phrases():
    assert prompts.deux_phrases("Première phrase. Deuxième phrase.")
    assert not prompts.deux_phrases("Une seule phrase sans point final")


# ---------------------------------------------------------------------------
# Reprise après JSON invalide
# ---------------------------------------------------------------------------


class FauxService:
    """Imite l'interface minimale attendue par `demander_json`."""

    def __init__(self, textes: list[str]):
        self._textes = list(textes)
        self.conversations: list[list[dict]] = []

    def completer(self, messages: list[dict]) -> ReponseLlm:
        self.conversations.append(list(messages))
        return ReponseLlm(
            texte=self._textes.pop(0), modele="modele-test", fournisseur="fournisseur-test"
        )


def test_deuxieme_tentative_apres_reponse_invalide():
    service = FauxService(["je ne sais pas", json.dumps(VALIDE)])
    schema = prompts.charger_schema("channel_score.schema.json")

    donnees, brut, tentatives, erreur = demander_json(
        service,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "question"}],
        schema,
    )

    assert donnees == VALIDE
    assert tentatives == 2
    assert erreur is None
    assert brut == json.dumps(VALIDE)
    assert len(service.conversations) == 2
    dernier_message = service.conversations[1][-1]["content"]
    assert "invalide" in dernier_message
    # La conversation de réparation rappelle le contexte initial.
    assert service.conversations[1][0]["role"] == "system"


def test_echec_propre_apres_deux_reponses_invalides():
    service = FauxService(["pas du json", '{"score_proximite": "beaucoup"}'])
    schema = prompts.charger_schema("channel_score.schema.json")

    donnees, brut, tentatives, erreur = demander_json(
        service,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "question"}],
        schema,
    )

    assert donnees is None
    assert tentatives == 2
    assert erreur
    assert brut == '{"score_proximite": "beaucoup"}'


def test_schema_outlier_valide_une_structure_complete():
    schema = prompts.charger_schema("outlier_analysis.schema.json")
    charge = {
        "accroche": "Une promesse chiffrée dès la première seconde.",
        "structure": [
            {"etape": "Promesse", "description": "Annonce du résultat attendu."},
            {"etape": "Démonstration", "description": "Le résultat est montré à l'écran."},
        ],
        "explication": (
            "Le titre promet un gain concret et la vidéo le montre immédiatement. "
            "Cela explique un ratio nettement supérieur à la médiane de la chaîne."
        ),
        "reutilisable": ["Montrer le résultat avant d'expliquer la méthode"],
    }
    assert prompts.valider(charge, schema) == []
    assert prompts.valider({"accroche": "court"}, schema)
