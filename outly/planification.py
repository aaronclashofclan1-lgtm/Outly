r"""Tâche planifiée Windows pour l'envoi conditionnel des rapports.

Une application de bureau ne peut pas envoyer un e-mail quand elle est fermée.
On inscrit donc une tâche Windows quotidienne, à l'heure choisie, qui lance,
selon le mode d'installation :

    pythonw -m outly mail --si-du        (développement)
    "…\Outly.exe" mail --si-du           (application empaquetée)

La commande vérifie elle-même si la fréquence configurée (quotidien,
hebdomadaire, mensuel) rend un envoi dû : la tâche se contente de réveiller
l'outil tous les jours. Changer la fréquence ne demande donc aucune
réinscription ; changer l'heure, si.

`-StartWhenAvailable` fait rattraper l'envoi si le PC était éteint à l'heure
prévue. Le script `.bat` redirige la sortie vers `out/envoi-rapports.log` :
avec `pythonw`, aucune fenêtre ne s'ouvre, et les erreurs restent lisibles.

Tout est testable sans toucher au planificateur : `contenu_script`,
`contenu_ps1` et les commandes sont des fonctions pures ; seul `creer_tache`
exécute PowerShell.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NOM_TACHE = "Outly — rapports e-mail"
FICHIER_SCRIPT = "envoyer-rapports.bat"
FICHIER_JOURNAL = Path("out") / "envoi-rapports.log"

MOTIF_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def python_sans_fenetre(executable: str | None = None) -> str:
    """Interpréteur `pythonw` : aucune console ne clignote à chaque envoi."""
    chemin = Path(executable or sys.executable)
    if chemin.name.lower().startswith("pythonw"):
        return str(chemin)
    candidat = chemin.with_name(chemin.name.replace("python", "pythonw", 1))
    return str(candidat) if candidat.exists() else str(chemin)


def chemin_script(racine: Path) -> Path:
    return Path(racine) / FICHIER_SCRIPT


def chemin_journal(racine: Path) -> Path:
    return Path(racine) / FICHIER_JOURNAL


def commande_envoi(python: str | None = None) -> str:
    """Commande écrite dans le `.bat`, selon le mode d'exécution.

    Empaquetée, l'application se relance elle-même : `Outly.exe mail --si-du`.
    En développement, on garde `pythonw -m outly`, la forme documentée. Un
    `-m outly` devant l'exécutable ouvrirait l'interface à chaque envoi.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" mail --si-du'
    return f'"{python_sans_fenetre(python)}" -m outly mail --si-du'


def contenu_script(python: str, racine: Path) -> str:
    """Contenu du `.bat` régénéré : chemins absolus, sortie journalisée."""
    journal = chemin_journal(racine).relative_to(Path(racine))
    lignes = [
        "@echo off",
        "rem Envoi conditionnel des rapports Outly (tâche planifiée Windows).",
        "rem Regenere par l'interface ; peut aussi etre lance a la main.",
        'cd /d "%~dp0"',
        f'{commande_envoi(python)} >> "{journal}" 2>&1',
        "",
    ]
    return "\r\n".join(lignes)


def ecrire_script(racine: Path, python: str | None = None) -> Path:
    racine = Path(racine)
    chemin_journal(racine).parent.mkdir(parents=True, exist_ok=True)
    script = chemin_script(racine)
    temporaire = script.with_name(f".{script.name}.{os.getpid()}.tmp")
    try:
        temporaire.write_text(contenu_script(python_sans_fenetre(python), racine), encoding="utf-8")
        os.replace(temporaire, script)
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass
    return script


def _ps(texte: str | Path) -> str:
    """Chaîne littérale PowerShell : apostrophes doublées, rien d'interprété."""
    return "'" + str(texte).replace("'", "''") + "'"


def contenu_ps1(script: Path, heure: str, nom: str = NOM_TACHE) -> str:
    description = "Rapports Outly envoyés par e-mail selon la fréquence configurée."
    return "\n".join(
        [
            '$ErrorActionPreference = "Stop"',
            f"$action = New-ScheduledTaskAction -Execute {_ps(script)} "
            f"-WorkingDirectory {_ps(Path(script).parent)}",
            f"$trigger = New-ScheduledTaskTrigger -Daily -At {_ps(heure)}",
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit (New-TimeSpan -Minutes 30)",
            f"Register-ScheduledTask -TaskName {_ps(nom)} -Action $action "
            f"-Trigger $trigger -Settings $settings -Description {_ps(description)} "
            "-Force | Out-Null",
            "",
        ]
    )


def commande_powershell(script_ps1: Path, commande: str | None = None) -> list[str]:
    base = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
    ]
    if commande is not None:
        return base + ["-Command", commande]
    return base + ["-File", str(script_ps1)]


def _executer(commande: list[str], executeur=None) -> tuple[int, str]:
    options: dict = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 60,
    }
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        resultat = (executeur or subprocess.run)(commande, **options)
    except (OSError, subprocess.TimeoutExpired) as probleme:
        return -1, str(probleme)
    sortie = ((resultat.stdout or "") + (resultat.stderr or "")).strip()
    return int(getattr(resultat, "returncode", -1)), sortie


def verifier_windows() -> str | None:
    if os.name != "nt":
        return "La planification automatique n'est disponible que sous Windows."
    return None


def creer_tache(
    racine: Path,
    heure: str,
    python: str | None = None,
    executeur=None,
    nom: str = NOM_TACHE,
) -> tuple[bool, str]:
    """Inscrit (ou met à jour) la tâche quotidienne. Sans privilège admin."""
    probleme = verifier_windows()
    if probleme:
        return False, probleme
    if not MOTIF_HEURE.match(heure):
        return False, f"Heure invalide : {heure!r}. Format attendu : HH:MM."

    script = ecrire_script(racine, python)
    contenu = contenu_ps1(script, heure, nom)
    try:
        with tempfile.TemporaryDirectory(prefix="outly-") as dossier:
            ps1 = Path(dossier) / "planifier.ps1"
            # BOM : PowerShell 5 lit correctement les accents du chemin et du nom.
            ps1.write_text(contenu, encoding="utf-8-sig")
            code, sortie = _executer(commande_powershell(ps1), executeur)
    except OSError as probleme_ecriture:
        return False, f"Impossible d'écrire le script de planification : {probleme_ecriture}"

    if code != 0:
        return False, sortie or f"PowerShell a renvoyé le code {code}."
    return True, f"Tâche « {nom} » programmée tous les jours à {heure}."


def commande_etat(nom: str = NOM_TACHE) -> list[str]:
    commande = (
        f"$t = Get-ScheduledTask -TaskName {_ps(nom)} -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) { Write-Output 'non' } else { Write-Output 'oui' }"
    )
    return commande_powershell(Path("."), commande)


def tache_programmee(executeur=None, nom: str = NOM_TACHE) -> bool:
    if verifier_windows():
        return False
    code, sortie = _executer(commande_etat(nom), executeur)
    return code == 0 and sortie.strip().lower().endswith("oui")


def supprimer_tache(executeur=None, nom: str = NOM_TACHE) -> tuple[bool, str]:
    if verifier_windows():
        return False, verifier_windows() or ""
    if not tache_programmee(executeur, nom):
        return True, "Aucune tâche programmée : rien à retirer."
    commande = (
        "$ErrorActionPreference = 'Stop'; "
        f"Unregister-ScheduledTask -TaskName {_ps(nom)} -Confirm:$false | Out-Null"
    )
    code, sortie = _executer(commande_powershell(Path("."), commande), executeur)
    if code != 0:
        return False, sortie or f"PowerShell a renvoyé le code {code}."
    return True, "Tâche retirée : plus aucun envoi automatique."
