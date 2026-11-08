"""Exécution des commandes de la CLI depuis l'interface.

Pourquoi un sous-processus plutôt qu'un appel direct des fonctions Python :

  * la CLI écrit sur stdout/stderr ; la capter sans toucher au `sys.stdout` du
    processus Tkinter évite tout problème de fil d'exécution ;
  * `.env` est relu à neuf à chaque exécution, donc une clé modifiée dans
    l'interface est réellement utilisée ;
  * une exécution peut être interrompue (TerminateProcess) sans laisser la
    base dans un état incohérent : la CLI termine son `run` en `finally`.

Rien n'est réimplémenté ici : les arguments produits sont exactement ceux de
la ligne de commande documentée dans le README.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

# Ce que signifie chaque code de sortie de la CLI.
CODES = {
    0: "Terminé.",
    2: "Configuration invalide ou clé refusée.",
    3: "Budget de quota atteint : arrêt propre, rapport partiel si possible.",
    4: "Fournisseur LLM indisponible.",
    5: "Aucune donnée exploitable : lancez d'abord la découverte et l'enrichissement.",
    6: "Envoi du rapport impossible : vérifiez le serveur et le mot de passe SMTP.",
    130: "Interrompu.",
}


@dataclass
class Fin:
    """Événement de fin d'exécution poussé dans la file."""

    code: int
    annule: bool = False


def environnement_cli(racine: Path | None = None) -> dict[str, str]:
    """Environnement du sous-processus, avec le paquet et le projet visés.

    En développement, `PYTHONPATH` pointe vers le dossier qui contient
    `outly/` : la CLI lancée par l'interface est toujours celle du même code.
    Empaquetée, l'application se relance elle-même et n'a besoin ni de Python
    ni de `PYTHONPATH`. `OUTLY_ROOT` indique le projet à éditer, sinon la CLI
    retomberait sur son propre dossier.
    """
    environnement = os.environ.copy()
    environnement["PYTHONUNBUFFERED"] = "1"
    environnement["PYTHONIOENCODING"] = "utf-8"
    if not getattr(sys, "frozen", False):
        paquet = str(Path(__file__).resolve().parents[2])
        existant = environnement.get("PYTHONPATH", "")
        environnement["PYTHONPATH"] = paquet + (os.pathsep + existant if existant else "")
    if racine is not None:
        environnement["OUTLY_ROOT"] = str(Path(racine).resolve())
    return environnement


def commande_cli(arguments: list[str], python: str | None = None) -> list[str]:
    """Commande à exécuter pour une sous-commande de la CLI.

    Empaquetée, l'exécutable se relance lui-même : aucun Python n'est supposé
    installé sur le poste. En développement, `python -m outly` reste la forme
    documentée.
    """
    if getattr(sys, "frozen", False):
        return [str(sys.executable), *arguments]
    return [python or python_du_projet(), "-m", "outly", *arguments]


def python_du_projet() -> str:
    """Interpréteur pour la CLI (jamais pythonw : il n'a pas de sorties)."""
    executable = Path(sys.executable)
    if executable.name.lower().startswith("pythonw"):
        candidat = executable.with_name(executable.name.replace("pythonw", "python", 1))
        if candidat.exists():
            return str(candidat)
    return str(executable)


@dataclass
class Validation:
    """Résultat du diagnostic lancé après un enregistrement.

    `lancee` distingue « la configuration est refusée » de « la relecture a
    échoué » (droits, disque, fichier illisible) : les deux ne méritent pas le
    même message, et le second ne doit jamais accuser les valeurs saisies.
    """

    reussie: bool
    sortie: str
    lancee: bool = True


def valider_config(racine: Path) -> Validation:
    """Relit `config.yaml` avec le même analyseur que la CLI, en mémoire.

    Un sous-processus `doctor` serait fidèle, mais dans l'exécutable il faudrait
    relancer `Outly.exe` : 5 à 8 secondes de gel de la fenêtre à chaque
    enregistrement. La validation utile ici — les valeurs sont-elles dans les
    bornes ? — est celle de `config.charger`, appelée directement.

    `charger` lit `.env` dans `os.environ` sans écraser l'existant : les clés
    lues sont donc mises de côté puis rendues à l'identique, pour qu'une clé
    modifiée dans l'interface soit bien relue par le prochain sous-processus
    et que l'interface ne garde aucun secret en mémoire d'environnement.
    """
    from ..config import ErreurConfig, charger
    from .settings import lire_env

    racine = Path(racine).expanduser().resolve()
    valeurs = lire_env(racine / ".env")
    avant = {cle: os.environ.get(cle) for cle in valeurs}
    os.environ.update(valeurs)
    try:
        charger(racine, exiger_cles=False)
    except ErreurConfig as probleme:
        return Validation(False, str(probleme))
    except Exception as probleme:  # droits, disque, fichier illisible…
        return Validation(False, str(probleme), lancee=False)
    finally:
        for cle, valeur in avant.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
    return Validation(True, "")


# ---------------------------------------------------------------------------
# Construction des commandes
# ---------------------------------------------------------------------------


def arguments_run(
    *,
    max_recherches: int | None = None,
    avec_outliers: bool = False,
    transcriptions: bool = False,
) -> list[str]:
    arguments = ["run"]
    if max_recherches is not None:
        arguments.extend(["--max-searches", str(int(max_recherches))])
    if avec_outliers:
        arguments.append("--with-outliers")
    if transcriptions:
        arguments.append("--transcripts")
    return arguments


def estimation_execution(
    max_recherches: int = 20,
    avec_outliers: bool = False,
    transcriptions: bool = False,
) -> str:
    """Estimation indicative de la durée d'une veille complète.

    La découverte et les appels réseau dominent ; l'enrichissement, les
    outliers et les sous-titres ajoutent des passes. La fourchette reste
    volontairement large : le réseau, le LLM et le nombre de chaînes peuvent
    la faire varier. Elle ne remplace pas la mesure du journal.
    """
    try:
        recherches = max(0, min(100, int(max_recherches)))
    except (TypeError, ValueError):
        recherches = 20
    bas = 2.0 + 0.2 * recherches
    if avec_outliers:
        bas += 0.8
    if transcriptions:
        bas += 1.0
    haut = bas + 2.0
    return f"≈ {round(bas):d} à {round(haut):d} min"


def arguments_brain(
    *, question: str = "", inclure_archives: bool = False, forcer: bool = False
) -> list[str]:
    """Commande du cerveau IA, sans passer de secret en argument."""
    arguments = ["brain"]
    question = " ".join(str(question or "").split())
    if question:
        arguments.extend(["--question", question])
    if inclure_archives:
        arguments.append("--inclure-archives")
    if forcer:
        arguments.append("--force")
    return arguments


def arguments_idees(
    *, question: str = "", nombre: int = 5, forcer: bool = False
) -> list[str]:
    """Commande de recherche d'idées, bornée aux valeurs acceptées par la CLI."""
    arguments = ["idees"]
    question = " ".join(str(question or "").split())
    if question:
        arguments.extend(["--question", question])
    arguments.extend(["--nombre", str(max(1, min(int(nombre or 5), 10)))])
    if forcer:
        arguments.append("--force")
    return arguments


def arguments_recherche(
    titre: str,
    mots_cles: list[str] | tuple[str, ...],
    *,
    avec_ia: bool = False,
    forcer: bool = False,
) -> list[str]:
    """Commande de recherche documentaire, sans secret en argument."""
    arguments = [
        "recherche",
        "--titre",
        " ".join(str(titre or "").split()),
        "--mots-cles",
        "; ".join(" ".join(str(mot).split()) for mot in mots_cles if str(mot).strip()),
        "--ia" if avec_ia else "--sans-ia",
    ]
    if forcer:
        arguments.append("--force")
    return arguments


def arguments_audit(*, forcer: bool = False, recuperation: bool = True) -> list[str]:
    """Commande d'audit complet de ma chaîne, sans secret en argument."""
    arguments = ["audit"]
    if forcer:
        arguments.append("--force")
    if not recuperation:
        arguments.append("--sans-recuperation")
    return arguments


def arguments_strategie(*, jours: int = 10, forcer: bool = False) -> list[str]:
    """Commande du diagnostic de stratégie, sans secret en argument."""
    arguments = ["strategie", "--jours", str(max(1, min(int(jours or 10), 30)))]
    if forcer:
        arguments.append("--force")
    return arguments


def arguments_production(plan_id: int, *, forcer: bool = False) -> list[str]:
    """Commande d'estimation de production d'une fiche."""
    arguments = ["production", "--plan", str(int(plan_id))]
    if forcer:
        arguments.append("--force")
    return arguments


def arguments_doctor(sonde: bool = True) -> list[str]:
    return ["doctor", "--probe"] if sonde else ["doctor"]


def arguments_etape(etape: str, *, max_recherches: int | None = None, transcriptions: bool = False) -> list[str]:
    """Commande d'une étape séparée, telle qu'elle est documentée."""
    if etape == "discover":
        arguments = ["discover"]
        if max_recherches is not None:
            arguments.extend(["--max-searches", str(int(max_recherches))])
        return arguments
    if etape == "enrich":
        return ["enrich"]
    if etape == "score":
        return ["score"]
    if etape == "outliers":
        return ["analyze-outliers"] + (["--transcripts"] if transcriptions else [])
    if etape == "hooks":
        return ["hooks"] + (["--transcripts"] if transcriptions else [])
    if etape == "report":
        return ["report"]
    raise ValueError(f"Étape inconnue : {etape}")


# ---------------------------------------------------------------------------
# Tâche
# ---------------------------------------------------------------------------


class Tache:
    """Une commande `python -m outly ...` suivie ligne à ligne."""

    def __init__(self, racine: Path, arguments: list[str], python: str | None = None):
        self.racine = Path(racine)
        self.arguments = list(arguments)
        self.python = python or python_du_projet()
        self.file: queue.Queue[str | Fin] = queue.Queue()
        self._processus: subprocess.Popen | None = None
        self._annule = False

    # --- cycle de vie -------------------------------------------------------
    def demarrer(self) -> "Tache":
        commande = commande_cli(self.arguments, self.python)
        environnement = environnement_cli(self.racine)

        options: dict = {}
        if sys.platform == "win32":
            # Aucune fenêtre de console ne doit surgir derrière l'interface.
            options["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._processus = subprocess.Popen(
            commande,
            cwd=str(self.racine),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environnement,
            **options,
        )
        threading.Thread(target=self._lire, name="outly-sortie", daemon=True).start()
        return self

    def _lire(self) -> None:
        processus = self._processus
        if processus is None or processus.stdout is None:
            self.file.put(Fin(-1))
            return
        try:
            for ligne in processus.stdout:
                self.file.put(ligne.rstrip("\r\n"))
        except Exception as probleme:  # tube fermé brutalement
            self.file.put(f"! Lecture de la sortie interrompue : {probleme}")
        finally:
            try:
                code = processus.wait(timeout=10)
            except subprocess.TimeoutExpired:
                code = -1
            self.file.put(Fin(code, self._annule))

    def annuler(self) -> None:
        """Demande l'arrêt. La CLI reçoit un arrêt immédiat, pas un signal doux."""
        self._annule = True
        processus = self._processus
        if processus is not None and processus.poll() is None:
            try:
                processus.terminate()
            except OSError:
                pass

    @property
    def actif(self) -> bool:
        processus = self._processus
        return processus is not None and processus.poll() is None
