"""Édition des fichiers d'entrée de l'outil, sans perdre les commentaires.

L'interface écrit dans les deux fichiers que l'utilisateur remplit :
  * `.env`        : un `CLE=valeur` par ligne ;
  * `config.yaml` : réglages scalaires, section par section.

`config.yaml` est truffé de commentaires pédagogiques. Un passage par un
analyseur YAML les effacerait tous : c'est inacceptable pour un fichier que
l'utilisateur est invité à lire. L'écriture se fait donc ligne à ligne, en
remplaçant uniquement la valeur visée et en conservant le commentaire de fin
de ligne. Avant d'écrire, le résultat est relu par l'analyseur YAML : si la
modification produisait un fichier invalide, rien n'est écrit.

Aucune valeur de secret n'est journalisée : les messages d'erreur ne citent
que le nom de la variable.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

# Une clé d'environnement valide : lettres, chiffres et soulignés.
MOTIF_CLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# `export CLE=valeur` est accepté (certains utilisateurs exportent .env tel quel).
MOTIF_LIGNE_ENV = re.compile(
    r"^(?P<avant>\s*(?:export\s+)?)(?P<cle>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<egal>\s*=\s*)(?P<valeur>.*)$"
)
# Section YAML à la racine (colonne 0).
MOTIF_SECTION = re.compile(r"^(?P<nom>[A-Za-z_][\w-]*)\s*:\s*(?:#.*)?$")


class ErreurFichier(Exception):
    """Fichier illisible, non modifiable, ou modification refusée."""


# ---------------------------------------------------------------------------
# Entrées / sorties de bas niveau
# ---------------------------------------------------------------------------


def _lire_texte(chemin: Path) -> tuple[str, str]:
    """Renvoie (contenu, fin de ligne d'origine)."""
    try:
        brut = chemin.read_bytes()
    except OSError as probleme:
        raise ErreurFichier(f"Lecture impossible de {chemin.name} : {probleme}") from probleme
    try:
        texte = brut.decode("utf-8-sig")
    except UnicodeDecodeError as probleme:
        raise ErreurFichier(
            f"{chemin.name} n'est pas en UTF-8. Réenregistrez-le dans un éditeur de texte."
        ) from probleme
    return texte, ("\r\n" if "\r\n" in texte else "\n")


def _ecrire_atomique(chemin: Path, texte: str, fin_de_ligne: str = "\n") -> None:
    """Écrit via un fichier temporaire : jamais de fichier tronqué en cas d'échec."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(chemin.name + ".tmp")
    donnees = texte.replace("\n", fin_de_ligne).encode("utf-8")
    try:
        temporaire.write_bytes(donnees)
        os.replace(temporaire, chemin)
    except OSError as probleme:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass
        raise ErreurFichier(f"Écriture impossible de {chemin.name} : {probleme}") from probleme


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------


def _denude(brut: str) -> str:
    """Retire les guillemets et un commentaire de fin de ligne."""
    valeur = brut.strip()
    if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
        return valeur[1:-1]
    if " #" in valeur:  # commentaire de fin de ligne, hors guillemets
        valeur = valeur.split(" #", 1)[0].strip()
    return valeur


def lire_env(chemin: Path) -> dict[str, str]:
    """Lit `.env` sans passer par python-dotenv (et donc sans toucher os.environ)."""
    if not chemin.exists():
        return {}
    texte, _ = _lire_texte(chemin)
    valeurs: dict[str, str] = {}
    for brute in texte.splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne.startswith("[") and ligne.endswith("]"):  # marqueur de gabarit
            continue
        if ligne.startswith("export "):
            ligne = ligne[len("export ") :].strip()
        if "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        cle = cle.strip()
        if MOTIF_CLE.match(cle):
            valeurs[cle] = _denude(valeur)
    return valeurs


def _valeur_env(valeur: str) -> str:
    if valeur == "":
        return ""
    if re.search(r"[\s#\"']", valeur):
        return '"' + valeur.replace('"', '\\"') + '"'
    return valeur


def ecrire_env(chemin: Path, valeurs: dict[str, str | None]) -> list[str]:
    """Met à jour les clés fournies. `None` = ne pas toucher, `""` = vider.

    Renvoie la liste des clés réellement modifiées. Les commentaires, l'ordre
    des lignes et la fin de ligne d'origine sont conservés.
    """
    mises: dict[str, str] = {}
    for cle, valeur in valeurs.items():
        if valeur is None:
            continue
        if not MOTIF_CLE.match(cle):
            raise ErreurFichier(f"Nom de variable invalide : {cle!r}")
        texte = str(valeur)
        if "\n" in texte or "\r" in texte:
            raise ErreurFichier(f"La valeur de {cle} ne peut pas contenir de saut de ligne.")
        mises[cle] = texte

    if not mises:
        return []

    if chemin.exists():
        contenu, fin_de_ligne = _lire_texte(chemin)
        lignes = contenu.splitlines()
    else:
        fin_de_ligne, lignes = "\n", []

    restantes = dict(mises)
    modifiees: list[str] = []
    for index, ligne in enumerate(lignes):
        trouve = MOTIF_LIGNE_ENV.match(ligne)
        if not trouve:
            continue
        cle = trouve.group("cle")
        if cle in restantes:
            lignes[index] = (
                f"{trouve.group('avant')}{cle}{trouve.group('egal')}"
                f"{_valeur_env(restantes.pop(cle))}"
            )
            modifiees.append(cle)

    if restantes:
        if lignes and lignes[-1].strip():
            lignes.append("")
        for cle, valeur in restantes.items():
            lignes.append(f"{cle}={_valeur_env(valeur)}")
            modifiees.append(cle)

    _ecrire_atomique(chemin, "\n".join(lignes) + "\n", fin_de_ligne)
    return modifiees


# ---------------------------------------------------------------------------
# Préférences de l'interface (data/preferences.json)
# ---------------------------------------------------------------------------

THEMES = ("light", "dark")
THEME_DEFAUT = "dark"
TEXTES_TABLEAU = ("normal", "grand")
DEMARRAGES = ("accueil", "derniere")

# Valeurs par défaut : le sombre d'abord (le plus confortable), ombres actives,
# tableaux en taille normale, ouverture sur l'accueil, confirmations demandées.
PREFERENCES_DEFAUT: dict[str, Any] = {
    "theme": THEME_DEFAUT,
    "ombres": True,
    "texte_tableau": "normal",
    "demarrage": "accueil",
    "confirmer_suppression": True,
    "derniere_page": "accueil",
    "disposition": {},
    "navigation_groupes": {},
    "inversions": {},
}


def _chemin_preferences(racine: Path) -> Path:
    return Path(racine) / "data" / "preferences.json"


def _nettoyer_bool_dict(valeur: Any) -> dict[str, bool]:
    """Ne garde qu'un dictionnaire nom → booléen (volets, colonnes inversées)."""
    if not isinstance(valeur, dict):
        return {}
    propre: dict[str, bool] = {}
    for cle, drapeau in valeur.items():
        if isinstance(cle, str) and cle.strip() and isinstance(drapeau, bool):
            propre[cle.strip()] = drapeau
    return propre


def _nettoyer_disposition(valeur: Any) -> dict[str, dict[str, list[str]]]:
    """Ne garde qu'une structure page → zone → liste d'identifiants."""
    if not isinstance(valeur, dict):
        return {}
    propre: dict[str, dict[str, list[str]]] = {}
    for page, zones in valeur.items():
        if not isinstance(page, str) or not page.strip() or not isinstance(zones, dict):
            continue
        zones_propres: dict[str, list[str]] = {}
        for zone, ordre in zones.items():
            if not isinstance(zone, str) or not zone.strip() or not isinstance(ordre, list):
                continue
            identifiants: list[str] = []
            for identifiant in ordre:
                if (
                    isinstance(identifiant, str)
                    and identifiant.strip()
                    and identifiant not in identifiants
                ):
                    identifiants.append(identifiant.strip())
            if identifiants:
                zones_propres[zone.strip()] = identifiants[:60]
        if zones_propres:
            propre[page.strip()] = zones_propres
    return propre


def _lire_preferences_brutes(chemin: Path) -> dict:
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return donnees if isinstance(donnees, dict) else {}


def lire_preferences(racine: Path) -> dict[str, Any]:
    """Préférences de l'interface, complétées par les valeurs par défaut.

    Un fichier absent, illisible ou corrompu ne doit jamais empêcher
    l'interface de démarrer : chaque valeur retombe silencieusement sur son
    défaut.
    """
    donnees = _lire_preferences_brutes(_chemin_preferences(racine))
    preferences: dict[str, Any] = dict(PREFERENCES_DEFAUT)

    theme = str(donnees.get("theme", "")).strip().lower()
    if theme in THEMES:
        preferences["theme"] = theme
    for cle in ("ombres", "confirmer_suppression"):
        valeur = donnees.get(cle)
        if isinstance(valeur, bool):
            preferences[cle] = valeur
    texte = str(donnees.get("texte_tableau", "")).strip().lower()
    if texte in TEXTES_TABLEAU:
        preferences["texte_tableau"] = texte
    demarrage = str(donnees.get("demarrage", "")).strip().lower()
    if demarrage in DEMARRAGES:
        preferences["demarrage"] = demarrage
    page = donnees.get("derniere_page")
    if isinstance(page, str) and page.strip():
        preferences["derniere_page"] = page.strip()
    preferences["disposition"] = _nettoyer_disposition(donnees.get("disposition"))
    preferences["navigation_groupes"] = _nettoyer_bool_dict(
        donnees.get("navigation_groupes")
    )
    preferences["inversions"] = _nettoyer_bool_dict(donnees.get("inversions"))
    return preferences


def ecrire_preferences(racine: Path, **modifications: Any) -> dict[str, Any]:
    """Enregistre une ou plusieurs préférences, sans effacer les autres.

    Les valeurs sont validées avant écriture ; une préférence inconnue lève
    `ErreurFichier` plutôt que d'ajouter une clé inutilisée au fichier.
    """
    if not modifications:
        return lire_preferences(racine)
    theme = modifications.get("theme")
    if theme is not None and str(theme).strip().lower() not in THEMES:
        raise ErreurFichier(f"Thème inconnu : {theme!r}.")
    texte = modifications.get("texte_tableau")
    if texte is not None and str(texte).strip().lower() not in TEXTES_TABLEAU:
        raise ErreurFichier(f"Taille de texte inconnue : {texte!r}.")
    demarrage = modifications.get("demarrage")
    if demarrage is not None and str(demarrage).strip().lower() not in DEMARRAGES:
        raise ErreurFichier(f"Mode de démarrage inconnu : {demarrage!r}.")
    for cle in ("ombres", "confirmer_suppression"):
        if cle in modifications and not isinstance(modifications[cle], bool):
            raise ErreurFichier(f"La préférence {cle} doit être vraie ou fausse.")
    page = modifications.get("derniere_page")
    if page is not None and (not isinstance(page, str) or not page.strip()):
        raise ErreurFichier("Page de démarrage invalide.")
    for cle in modifications:
        if cle not in PREFERENCES_DEFAUT:
            raise ErreurFichier(f"Préférence inconnue : {cle!r}.")

    if "disposition" in modifications:
        brut = modifications["disposition"]
        if not isinstance(brut, dict):
            raise ErreurFichier("La disposition doit être un objet page → zone → liste.")
        modifications = dict(modifications)
        modifications["disposition"] = _nettoyer_disposition(brut)
    for cle in ("navigation_groupes", "inversions"):
        if cle in modifications:
            brut = modifications[cle]
            if not isinstance(brut, dict):
                raise ErreurFichier(f"La préférence {cle} doit être un objet nom → booléen.")
            modifications = dict(modifications)
            modifications[cle] = _nettoyer_bool_dict(brut)

    preferences = lire_preferences(racine)
    for cle, valeur in modifications.items():
        if isinstance(valeur, str):
            preferences[cle] = valeur.strip().lower() if cle != "derniere_page" else valeur.strip()
        else:
            preferences[cle] = valeur
    _ecrire_atomique(
        _chemin_preferences(racine),
        json.dumps(preferences, ensure_ascii=False, indent=2) + "\n",
    )
    return preferences


def lire_theme(racine: Path) -> str:
    """Thème enregistré (`light` ou `dark`), avec le sombre par défaut."""
    return str(lire_preferences(racine)["theme"])


def ecrire_theme(racine: Path, theme: str) -> None:
    """Enregistre le thème choisi, sans toucher au reste des préférences."""
    ecrire_preferences(racine, theme=theme)


# ---------------------------------------------------------------------------
# Fichiers de texte libre (positioning.md, seeds.txt, keywords.txt)
# ---------------------------------------------------------------------------


def lire_texte(chemin: Path) -> str:
    if not chemin.exists():
        return ""
    contenu, _ = _lire_texte(chemin)
    return contenu


def ecrire_texte(chemin: Path, contenu: str) -> None:
    if "\x00" in contenu:
        raise ErreurFichier(f"{chemin.name} contient un caractère nul : écriture refusée.")
    normalise = contenu.replace("\r\n", "\n")
    if normalise and not normalise.endswith("\n"):
        normalise += "\n"
    _ecrire_atomique(chemin, normalise, "\n")


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------


def lire_config(chemin: Path) -> dict:
    """Charge config.yaml en lecture seule (aucune variable d'environnement touchée)."""
    if not chemin.exists():
        return {}
    contenu, _ = _lire_texte(chemin)
    try:
        donnees = yaml.safe_load(contenu)
    except yaml.YAMLError as probleme:
        raise ErreurFichier(f"YAML invalide dans {chemin.name} : {probleme}") from probleme
    if donnees is None:
        return {}
    if not isinstance(donnees, dict):
        raise ErreurFichier(f"{chemin.name} doit contenir un objet YAML à la racine.")
    return donnees


def _valeur_yaml(valeur: Any) -> str:
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, int):
        return str(valeur)
    if isinstance(valeur, float):
        return repr(valeur)
    if isinstance(valeur, (list, tuple)):
        return "[" + ", ".join(_valeur_yaml(element) for element in valeur) + "]"
    texte = str(valeur)
    if texte == "":
        return '""'
    # « 08:00 » est un entier sexagésimal en YAML 1.1 : sans guillemets, PyYAML
    # le relirait comme 480. Les heures doivent rester des chaînes.
    if re.fullmatch(r"\d{1,2}:\d{2}", texte):
        return '"' + texte + '"'
    # Un scalaire YAML ne peut pas commencer par un indicateur réservé (@, -, %, …) :
    # « chaine: @handle » serait invalide. On n'accepte donc que ces têtes sûres.
    if re.fullmatch(r"[A-Za-z0-9_./][A-Za-z0-9_./:@+\-]*", texte):
        return texte
    return '"' + texte.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _remplacer_scalaire(texte: str, section: str, cle: str, valeur: str) -> str:
    """Remplace `section.cle` dans le texte YAML, commentaires intacts."""
    lignes = texte.splitlines()

    debut = None
    for index, ligne in enumerate(lignes):
        trouve = MOTIF_SECTION.match(ligne)
        if trouve and trouve.group("nom") == section:
            debut = index
            break

    if debut is None:  # section absente : on l'ajoute proprement en fin de fichier
        if lignes and lignes[-1].strip():
            lignes.append("")
        lignes.extend([f"{section}:", f"  {cle}: {valeur}"])
        return "\n".join(lignes)

    # Fin de section : première ligne de niveau racine rencontrée ensuite.
    fin = len(lignes)
    for index in range(debut + 1, len(lignes)):
        if MOTIF_SECTION.match(lignes[index]):
            fin = index
            break

    motif = re.compile(rf"^(?P<indent>\s+){re.escape(cle)}\s*:\s*(?P<reste>.*)$")
    for index in range(debut + 1, fin):
        trouve = motif.match(lignes[index])
        if not trouve:
            continue
        commentaire = ""
        reste = trouve.group("reste")
        if "#" in reste:
            # On conserve le commentaire tel quel, en normalisant l'espace avant.
            commentaire = "  " + reste[reste.index("#") :].rstrip()
        lignes[index] = f"{trouve.group('indent')}{cle}: {valeur}{commentaire}"
        return "\n".join(lignes)

    # Clé absente : insertion en fin de section, avant les lignes vides finales.
    position = fin
    while position > debut + 1 and not lignes[position - 1].strip():
        position -= 1
    lignes.insert(position, f"  {cle}: {valeur}")
    return "\n".join(lignes)


def ecrire_config(chemin: Path, valeurs: dict[tuple[str, str], Any]) -> list[str]:
    """Écrit plusieurs réglages. Renvoie « section.cle » pour chaque écriture réussie.

    Le fichier n'est remplacé que si le YAML obtenu reste valide ET porte bien
    les nouvelles valeurs : une faute de frappe ne peut pas casser la config.
    """
    if not chemin.exists():
        raise ErreurFichier("config.yaml est introuvable. Lancez d'abord : python -m outly init")
    texte, fin_de_ligne = _lire_texte(chemin)
    modifiees: list[str] = []
    for (section, cle), valeur in valeurs.items():
        texte = _remplacer_scalaire(texte, section, cle, _valeur_yaml(valeur))
        modifiees.append(f"{section}.{cle}")

    try:
        relu = yaml.safe_load(texte)
    except yaml.YAMLError as probleme:
        raise ErreurFichier(
            f"La modification produirait un config.yaml invalide : {probleme}"
        ) from probleme

    for (section, cle), valeur in valeurs.items():
        lue = (relu.get(section) or {}).get(cle)
        if lue != valeur and str(lue) != str(valeur):
            raise ErreurFichier(
                f"Le réglage {section}.{cle} n'a pas pu être écrit "
                f"(valeur relue : {lue!r}). Vérifiez le fichier."
            )

    _ecrire_atomique(chemin, texte, fin_de_ligne)
    return modifiees
