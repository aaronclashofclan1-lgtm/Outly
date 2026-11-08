"""Tests hors réseau de la recherche documentaire.

Les transports Wikipédia et Exa sont remplacés par des fonctions locales : aucun
test ne consomme de quota, de crédit Exa ou d'appel LLM réel.
"""

from __future__ import annotations

import json

from outly import recherche
from outly.cli import construire_parseur
from outly.gui.runner import arguments_recherche
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


class FauxWiki:
    def __init__(self):
        self.appels: list[tuple[str, dict]] = []

    def __call__(self, url, params, timeout):
        self.appels.append((url, params))
        mot = params["gsrsearch"]
        return {
            "query": {
                "pages": [
                    {
                        "pageid": 1,
                        "title": f"Page {mot}",
                        "extract": f"Extrait documentaire pour {mot}.",
                        "fullurl": f"https://fr.wikipedia.org/wiki/{mot.replace(' ', '_')}",
                        "index": 1,
                    }
                ]
            }
        }


class FauxExa:
    def __init__(self, statut: int = 200):
        self.statut = statut
        self.appels: list[dict] = []

    def __call__(self, url, entetes, corps, timeout):
        self.appels.append({"url": url, "entetes": entetes, "corps": corps})
        if self.statut != 200:
            return self.statut, {}
        return 200, {
            "results": [
                {
                    "title": "Chaleur urbaine : données",
                    "url": "https://www.lemonde.fr/climat/chaleur-urbaine",
                    "publishedDate": "2025-07-01T00:00:00Z",
                    "highlights": [
                        "Les villes sont plus chaudes que les campagnes.",
                        "La végétalisation réduit la température ressentie.",
                    ],
                },
                {
                    "title": "Îlot de chaleur urbain",
                    "url": "https://www.insee.fr/fr/statistiques/chaleur",
                    "highlights": ["L'écart atteint plusieurs degrés la nuit."],
                },
                {
                    "title": "Urban heat island study",
                    "url": "https://www.nature.com/articles/urban-heat",
                    "highlights": ["Les surfaces minérales stockent la chaleur."],
                },
                {
                    "title": "Blog anonyme",
                    "url": "https://un-blog-inconnu.example/chaleur",
                    "highlights": ["Une affirmation sans autorité éditoriale."],
                },
            ]
        }


def _intention() -> dict:
    return {
        "sujet_reformule": "Pourquoi les villes sont plus chaudes que les campagnes",
        "public_attendu": ["Comprendre l'îlot de chaleur urbain", "Connaître des solutions"],
        "intention_createur": "Expliquer le phénomène sans le réduire à la canicule.",
        "questions_utiles": ["Quel est l'écart de température ?", "Quelles solutions existent ?"],
        "requetes": [
            "îlot de chaleur urbain chiffres",
            "végétalisation température ville",
            "température nocturne ville campagne étude",
        ],
    }


def _dossier() -> dict:
    return {
        "resume": (
            "Les sources convergent sur l'existence d'un écart thermique entre villes et "
            "campagnes. Les solutions de végétalisation sont documentées, mais leur effet "
            "dépend fortement du contexte local."
        ),
        "faits": [
            {
                "affirmation": "Les villes sont plus chaudes que les campagnes.",
                "precision": "L'écart est surtout marqué la nuit.",
                "sources": ["S1", "S2", "S3"],
            },
            {
                "affirmation": "La végétalisation réduit la température ressentie.",
                "precision": "L'effet dépend de l'essence et de l'arrosage.",
                "sources": ["S1", "S2"],
            },
            {
                "affirmation": "Cette affirmation n'a aucune source.",
                "precision": "",
                "sources": [],
            },
        ],
        "chiffres": [
            {
                "valeur": "plusieurs degrés",
                "contexte": "Écart nocturne documenté par les sources.",
                "sources": ["S1", "S2", "S3"],
            }
        ],
        "contradictions": [],
        "angles": [
            {
                "titre": "La ville a-t-elle vraiment plus chaud ?",
                "angle": "Partir d'un thermomètre et démonter le phénomène.",
                "pourquoi": "Le sujet est mesurable et visuel.",
                "sources": ["S1", "S2"],
            }
        ],
        "a_verifier": ["Le chiffre exact dépend de la ville étudiée."],
    }


def test_client_wikipedia_interroge_chaque_mot_cle_et_garde_les_extraits():
    transport = FauxWiki()
    client = recherche.ClientWikipedia(transport=transport)

    pages = client.rechercher("îlot de chaleur")

    assert len(pages) == 1
    assert pages[0]["titre"] == "Page îlot de chaleur"
    assert pages[0]["extrait"].startswith("Extrait documentaire")
    assert pages[0]["domaine"] == "wikipedia.org"
    assert transport.appels[0][1]["generator"] == "search"
    assert transport.appels[0][1]["explaintext"] == "1"


def test_client_exa_envoie_la_cle_et_extrait_les_highlights():
    transport = FauxExa()
    client = recherche.ClientExa("exa-secrete", transport=transport)

    resultats = client.rechercher("chaleur urbaine")

    assert len(resultats) == 4
    assert resultats[0]["domaine"] == "lemonde.fr"
    assert "villes sont plus chaudes" in resultats[0]["extrait"]
    assert transport.appels[0]["entetes"]["x-api-key"] == "exa-secrete"
    assert transport.appels[0]["corps"]["contents"]["highlights"]


def test_client_exa_refuse_une_cle_invalide():
    client = recherche.ClientExa("mauvaise", transport=FauxExa(statut=401))
    try:
        client.rechercher("sujet")
    except recherche.ErreurRecherche as probleme:
        assert "refuse la clé" in str(probleme)
    else:
        raise AssertionError("Une clé refusée doit lever une ErreurRecherche")


def test_mode_sans_ia_lit_wikipedia_et_ne_fait_aucun_appel_llm(connexion, config):
    transport = FauxWiki()
    client = recherche.ClientWikipedia(transport=transport)

    resume = recherche.generer(
        connexion,
        config,
        titre="La chaleur en ville",
        mots_cles="îlot de chaleur, végétalisation",
        mode="sans_ia",
        client_wikipedia=client,
    )

    assert resume.reussie
    assert resume.appels_llm == 0
    assert len(resume.dossier["pages_wiki"]) == 2
    assert {source["id"] for source in resume.dossier["sources"]} == {"W1", "W2"}
    assert len(transport.appels) == 2


def test_mode_ia_recoupe_trois_domaines_et_degrade_le_reste(connexion, config):
    config.exa_cle = "exa-test"
    service = FauxService([_intention(), _dossier()])
    wiki = FauxWiki()
    exa = FauxExa()

    resume = recherche.generer(
        connexion,
        config,
        titre="La chaleur en ville",
        mots_cles="îlot de chaleur, végétalisation",
        mode="avec_ia",
        service=service,
        client_wikipedia=recherche.ClientWikipedia(transport=wiki),
        client_exa=recherche.ClientExa("exa-test", transport=exa),
    )

    assert resume.reussie
    assert service.appels == 2
    faits = {fait["affirmation"]: fait for fait in resume.dossier["faits"]}
    assert faits["Les villes sont plus chaudes que les campagnes."]["statut"] == "recoupe"
    assert faits["La végétalisation réduit la température ressentie."]["statut"] == "a_verifier"
    assert "Cette affirmation n'a aucune source." not in faits
    assert resume.dossier["chiffres"][0]["statut"] == "recoupe"
    assert any("aucune source réelle" in element for element in resume.dossier["a_verifier"])
    assert "S1" in resume.dossier["faits"][0]["sources"]
    assert resume.dossier["faits"][0]["sources"] != ["W1"]


def test_le_dossier_ia_est_relu_depuis_le_cache_sans_appel_reseau(connexion, config):
    config.exa_cle = "exa-test"
    service = FauxService([_intention(), _dossier()])
    premier = recherche.generer(
        connexion,
        config,
        titre="La chaleur en ville",
        mots_cles="îlot de chaleur, végétalisation",
        mode="avec_ia",
        service=service,
        client_wikipedia=recherche.ClientWikipedia(transport=FauxWiki()),
        client_exa=recherche.ClientExa("exa-test", transport=FauxExa()),
    )
    assert premier.reussie

    appels = service.appels
    second = recherche.generer(
        connexion,
        config,
        titre="La chaleur en ville",
        mots_cles="îlot de chaleur, végétalisation",
        mode="avec_ia",
        service=service,
        client_wikipedia=recherche.ClientWikipedia(
            transport=lambda *_: (_ for _ in ()).throw(AssertionError("réseau inattendu"))
        ),
        client_exa=recherche.ClientExa(
            "exa-test",
            transport=lambda *_: (_ for _ in ()).throw(AssertionError("réseau inattendu")),
        ),
    )

    assert second.reussie
    assert second.depuis_cache
    assert service.appels == appels
    assert second.dossier["faits"][0]["statut"] == "recoupe"


def test_mode_ia_sans_cle_exa_est_refuse_avant_tout_appel(connexion, config):
    try:
        recherche.generer(
            connexion,
            config,
            titre="La chaleur en ville",
            mots_cles="chaleur",
            mode="avec_ia",
            service=FauxService([]),
        )
    except recherche.ErreurRecherche as probleme:
        assert "EXA_API_KEY" in str(probleme)
    else:
        raise AssertionError("Le mode IA sans clé Exa doit être refusé")


def test_la_cli_expose_les_deux_modes_et_les_mots_cles():
    sans_ia = construire_parseur().parse_args(
        ["recherche", "--titre", "Un sujet", "--mots-cles", "un, deux"]
    )
    avec_ia = construire_parseur().parse_args(
        ["recherche", "--titre", "Un sujet", "--mots-cles", "un", "--ia", "--force"]
    )

    assert sans_ia.ia is False
    assert avec_ia.ia is True
    assert avec_ia.force is True
    assert sans_ia.mots_cles == "un, deux"


def test_arguments_gui_ne_passent_aucun_secret():
    arguments = arguments_recherche(
        "Un sujet", ["un", "deux"], avec_ia=True, forcer=True
    )

    assert arguments[:2] == ["recherche", "--titre"]
    assert "--ia" in arguments
    assert "--force" in arguments
    assert "EXA_API_KEY" not in arguments
