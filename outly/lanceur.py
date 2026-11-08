"""Point d'entrée unique de l'application empaquetée.

L'exécutable ne peut pas lancer `python -m outly` comme le fait le venv de
développement : il se relance lui-même avec les arguments de la CLI. Ce module
choisit donc le mode :

  * une sous-commande connue (`run`, `doctor`, `mail`…) → ligne de commande,
    exactement comme `python -m outly …` ;
  * aucun argument (double-clic) → interface graphique.

Au premier lancement, les fichiers de départ sont créés dans le dossier de
l'utilisateur : l'application doit fonctionner sans terminal.
"""

from __future__ import annotations

import sys

# Options globales de la CLI, sans sous-commande.
OPTIONS_CLI = frozenset({"--version", "-h", "--help"})

COMMANDES = frozenset(
    {
        "init",
        "doctor",
        "discover",
        "enrich",
        "score",
        "analyze-outliers",
        "hooks",
        "brain",
        "idees",
        "recherche",
        "audit",
        "strategie",
        "production",
        "report",
        "mail",
        "run",
        "quota",
        "show",
        "cache",
    }
)


def _preparer_donnees() -> None:
    """Crée config.yaml, les textes et `.env` s'ils manquent (exécutable seul).

    Le dossier visé est `racine_projet()` et non celui de l'exe : l'interface
    lance ses commandes avec `OUTLY_ROOT`, et le sous-processus ne doit pas
    semer ses fichiers de départ à côté de l'application.
    """
    if not getattr(sys, "frozen", False):
        return
    from .cli import preparer_dossier
    from .config import racine_projet

    try:
        preparer_dossier(racine_projet())
    except OSError:
        # Dossier protégé : `charger()` produira un message explicite.
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commande = arguments[0] if arguments else ""
    _preparer_donnees()

    if commande in COMMANDES or commande in OPTIONS_CLI:
        from .cli import main as cli_main

        return cli_main(arguments)

    from .gui.app import main as gui_main

    return gui_main(arguments)


__all__ = ["COMMANDES", "main"]
