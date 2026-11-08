"""Tests de l'édition des fichiers d'entrée par l'interface.

Le point critique : réécrire `config.yaml` sans perdre ses commentaires, et
modifier `.env` sans réordonner ni casser le fichier.
"""

from __future__ import annotations

import pytest
import yaml

from outly.gui import settings


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def test_lire_env_ignore_commentaires_et_marqueur(tmp_path):
    chemin = tmp_path / ".env"
    chemin.write_text(
        "# un commentaire\n"
        "[TEMPLATE]\n"
        "\n"
        "export YOUTUBE_API_KEY=\"une cle avec espaces\"\n"
        "LLM_MODEL='gemini-3.5-flash-lite'\n"
        "LLM_BASE_URL=https://exemple.test/v1  # fin de ligne\n",
        encoding="utf-8",
    )
    valeurs = settings.lire_env(chemin)
    assert valeurs["YOUTUBE_API_KEY"] == "une cle avec espaces"
    assert valeurs["LLM_MODEL"] == "gemini-3.5-flash-lite"
    assert valeurs["LLM_BASE_URL"] == "https://exemple.test/v1"


def test_ecrire_env_conserve_ordre_et_commentaires(tmp_path):
    chemin = tmp_path / ".env"
    chemin.write_text(
        "# en-tête\nYOUTUBE_API_KEY=ancienne\nLLM_MODEL=modele  # inline\nCLE_INCHANGEE=1\n",
        encoding="utf-8",
    )
    modifiees = settings.ecrire_env(chemin, {"YOUTUBE_API_KEY": "nouvelle"})
    contenu = chemin.read_text(encoding="utf-8")
    assert modifiees == ["YOUTUBE_API_KEY"]
    assert contenu.startswith("# en-tête\nYOUTUBE_API_KEY=nouvelle\n")
    assert "LLM_MODEL=modele  # inline" in contenu
    assert "CLE_INCHANGEE=1" in contenu


def test_ecrire_env_ajoute_les_cles_absentes_et_vide(tmp_path):
    chemin = tmp_path / ".env"
    chemin.write_text("A=1\nB=2\n", encoding="utf-8")
    settings.ecrire_env(chemin, {"A": "", "NOUVELLE": "valeur avec espaces"})
    valeurs = settings.lire_env(chemin)
    assert valeurs["A"] == ""
    assert valeurs["B"] == "2"
    assert valeurs["NOUVELLE"] == "valeur avec espaces"


def test_ecrire_env_conserve_les_fins_de_ligne_windows(tmp_path):
    chemin = tmp_path / ".env"
    chemin.write_bytes(b"A=1\r\nB=2\r\n")
    settings.ecrire_env(chemin, {"A": "3"})
    assert chemin.read_bytes() == b"A=3\r\nB=2\r\n"


def test_ecrire_env_refuse_un_saut_de_ligne(tmp_path):
    chemin = tmp_path / ".env"
    with pytest.raises(settings.ErreurFichier):
        settings.ecrire_env(chemin, {"A": "ligne1\nligne2"})


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------


def test_ecrire_config_conserve_les_commentaires(tmp_path):
    chemin = tmp_path / "config.yaml"
    chemin.write_text(
        "# Configuration\n"
        "youtube:\n"
        "  max_units_per_run: 2000    # marge\n"
        "  videos_per_channel: 30\n"
        "\n"
        "discovery:\n"
        "  use_channel_sections: true   # ok\n",
        encoding="utf-8",
    )
    settings.ecrire_config(
        chemin,
        {("youtube", "max_units_per_run"): 500, ("discovery", "use_channel_sections"): False},
    )
    contenu = chemin.read_text(encoding="utf-8")
    assert "# Configuration" in contenu
    assert "max_units_per_run: 500  # marge" in contenu
    assert "use_channel_sections: false  # ok" in contenu
    assert "videos_per_channel: 30" in contenu


def test_ecrire_config_insere_une_cle_dans_la_bonne_section(tmp_path):
    chemin = tmp_path / "config.yaml"
    chemin.write_text("youtube:\n  videos_per_channel: 30\n\ndiscovery:\n  actif: true\n",
                      encoding="utf-8")
    settings.ecrire_config(chemin, {("youtube", "nouvelle_cle"): 7})
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    assert donnees["youtube"]["nouvelle_cle"] == 7
    assert donnees["youtube"]["videos_per_channel"] == 30
    assert donnees["discovery"]["actif"] is True


def test_ecrire_config_cree_une_section_absente(tmp_path):
    chemin = tmp_path / "config.yaml"
    chemin.write_text("youtube:\n  videos_per_channel: 30\n", encoding="utf-8")
    settings.ecrire_config(chemin, {("output", "report_top"): 15})
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    assert donnees["output"]["report_top"] == 15


def test_ecrire_config_accepte_un_handle_commencant_par_arobase(tmp_path):
    """« chaine: @handle » est invalide en YAML brut : la valeur doit être citée."""
    chemin = tmp_path / "config.yaml"
    chemin.write_text(
        "moi:\n  chaine: \"\"  # @handle, URL complète ou identifiant UC…\n"
        "  recherche_internationale: true\n",
        encoding="utf-8",
    )

    settings.ecrire_config(chemin, {("moi", "chaine"): "@studio-montage"})

    assert settings.lire_config(chemin)["moi"]["chaine"] == "@studio-montage"
    assert "# @handle" in chemin.read_text(encoding="utf-8")


def test_ecrire_config_cite_une_heure(tmp_path):
    """« 08:00 » sans guillemets serait relu comme l'entier sexagésimal 480."""
    chemin = tmp_path / "config.yaml"
    chemin.write_text('email:\n  heure: ""\n', encoding="utf-8")

    settings.ecrire_config(chemin, {("email", "heure"): "08:00"})

    assert settings.lire_config(chemin)["email"]["heure"] == "08:00"
    assert 'heure: "08:00"' in chemin.read_text(encoding="utf-8")


def test_ecrire_config_exige_un_fichier_existant(tmp_path):
    with pytest.raises(settings.ErreurFichier):
        settings.ecrire_config(tmp_path / "absent.yaml", {("youtube", "videos_per_channel"): 5})


def test_lire_config_fichier_absent(tmp_path):
    assert settings.lire_config(tmp_path / "absent.yaml") == {}


def test_ecrire_texte_normalise_la_fin_de_fichier(tmp_path):
    chemin = tmp_path / "seeds.txt"
    settings.ecrire_texte(chemin, "@a\r\n@b")
    assert chemin.read_text(encoding="utf-8") == "@a\n@b\n"
