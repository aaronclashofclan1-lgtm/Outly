"""Tests du client LLM : messages d'erreur réels et sonde GET /models.

Gemini renvoie certaines erreurs sous forme de tableau. Sans déballage, le
message devenait « réponse vide » et masquait la cause (palier gratuit
indisponible, clé refusée), ce qui a rendu le diagnostic impossible en
production.
"""

from __future__ import annotations

import pytest

from outly.config import Config
from outly.llm import client

ERREUR_GEMINI = [
    {
        "error": {
            "code": 403,
            "message": (
                "The Gemini API free tier is not available for this project. "
                "To use the Gemini API, please set up billing to upgrade to the paid tier."
            ),
            "status": "PERMISSION_DENIED",
        }
    }
]


def _config(tmp_path) -> Config:
    config = Config(
        racine=tmp_path,
        fichier_db=tmp_path / "data" / "test.sqlite3",
        dossier_sortie=tmp_path / "out",
    )
    config.llm.max_retries = 0
    return config


def test_message_erreur_deballe_la_liste_gemini():
    message = client._message_erreur(ERREUR_GEMINI)

    assert "free tier" in message
    assert "réponse vide" not in message


def test_403_explique_le_palier_gratuit_indisponible(tmp_path):
    def transport(url, entetes, corps, timeout):
        return client.ReponseHttpLlm(403, ERREUR_GEMINI)

    fournisseur = client.FournisseurLlm(
        client.Fournisseur("principal", "https://exemple.test/v1beta/openai", "gemini-3.5-flash-lite", "cle"),
        _config(tmp_path),
        transport=transport,
    )

    with pytest.raises(client.ErreurLlm) as erreur:
        fournisseur.completer([{"role": "user", "content": "Bonjour"}])

    texte = str(erreur.value)
    assert "403" in texte
    assert "free tier" in texte
    assert "palier gratuit" in texte
    assert "LLM_FALLBACK" in texte


def test_instantane_considere_ollama_local_comme_complet(tmp_path, monkeypatch):
    from outly.gui import data

    for nom in data.NOMS_ENV:
        monkeypatch.delenv(nom, raising=False)
    (tmp_path / "config.yaml").write_text("moi:\n  chaine: ''\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "LLM_BASE_URL=http://localhost:11434/v1\nLLM_MODEL=gpt-oss:20b\n",
        encoding="utf-8",
    )

    instantane = data.instantane(tmp_path)

    assert instantane.llm_complet is True
    assert "fournisseur LLM incomplet" not in instantane.problemes


def test_service_accepte_ollama_local_sans_cle(tmp_path):
    config = _config(tmp_path)
    config.llm_base_url = "http://localhost:11434/v1"
    config.llm_modele = "gpt-oss:20b"
    config.llm_cle = None

    service = client.construire_service(config)

    assert service.nom_modele == "gpt-oss:20b"


def test_service_refuse_un_fournisseur_distant_sans_cle(tmp_path):
    config = _config(tmp_path)
    config.llm_base_url = "https://ollama.com/v1"
    config.llm_modele = "gemma4:31b"
    config.llm_cle = None

    with pytest.raises(client.ErreurLlm) as erreur:
        client.construire_service(config)

    assert "LLM_API_KEY" in str(erreur.value)


def test_ollama_local_omet_l_en_tete_authorization(tmp_path):
    requetes: list[dict] = []

    def transport(url, entetes, corps, timeout):
        requetes.append({"url": url, "entetes": dict(entetes), "corps": corps})
        return client.ReponseHttpLlm(
            200, {"choices": [{"message": {"content": "{\"ok\": true}"}}]}
        )

    fournisseur = client.FournisseurLlm(
        client.Fournisseur("principal", "http://localhost:11434/v1", "gpt-oss:20b", ""),
        _config(tmp_path),
        transport=transport,
    )

    fournisseur.completer([{"role": "user", "content": "Bonjour"}])

    assert requetes[0]["url"].endswith("/chat/completions")
    assert "Authorization" not in requetes[0]["entetes"]


def test_degradation_de_response_format_sur_un_400(tmp_path):
    appels: list[dict] = []

    def transport(url, entetes, corps, timeout):
        appels.append(corps)
        if len(appels) == 1:
            return client.ReponseHttpLlm(
                400, {"error": {"message": "response_format is not supported"}}
            )
        return client.ReponseHttpLlm(
            200, {"choices": [{"message": {"content": "{\"ok\": true}"}}]}
        )

    fournisseur = client.FournisseurLlm(
        client.Fournisseur("principal", "https://exemple.test/v1", "modele", "cle"),
        _config(tmp_path),
        transport=transport,
    )

    reponse = fournisseur.completer([{"role": "user", "content": "Bonjour"}])

    assert reponse.texte == "{\"ok\": true}"
    assert "response_format" in appels[0]
    assert "response_format" not in appels[1]


def test_liste_des_modeles_en_get_et_identifiants_normalises(tmp_path):
    appels: list = []

    def transport(url, entetes, corps, timeout):
        appels.append(corps)
        return client.ReponseHttpLlm(
            200,
            {
                "data": [
                    {"id": "models/gemini-3.5-flash-lite"},
                    {"id": "gemini-2.5-flash"},
                ]
            },
        )

    fournisseur = client.FournisseurLlm(
        client.Fournisseur("principal", "https://exemple.test/v1", "gemini-3.5-flash-lite", "cle"),
        _config(tmp_path),
        transport=transport,
    )

    modeles = fournisseur.lister_modeles()

    assert modeles == ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
    # None signifie GET : un POST sur /models renvoie 404 chez Gemini.
    assert appels == [None]


def test_liste_des_modeles_accepte_une_reponse_en_tableau(tmp_path):
    def transport(url, entetes, corps, timeout):
        return client.ReponseHttpLlm(
            200,
            [{"id": "models/gemini-3.5-flash-lite"}, "gemini-2.5-flash", 42],
        )

    fournisseur = client.FournisseurLlm(
        client.Fournisseur("principal", "https://exemple.test/v1", "gemini-3.5-flash-lite", "cle"),
        _config(tmp_path),
        transport=transport,
    )

    assert fournisseur.lister_modeles() == ["gemini-3.5-flash-lite", "gemini-2.5-flash"]
