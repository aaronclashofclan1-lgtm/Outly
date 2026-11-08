"""Interface graphique locale d'Outly (CustomTkinter, sur Tkinter).

CustomTkinter apporte les thèmes clair et sombre et les composants arrondis ;
Tkinter, lui, reste livré avec Python. L'interface ne réimplémente aucune
logique métier : elle édite les mêmes fichiers que la CLI et exécute les mêmes
commandes, dans un sous-processus.

`main` importe la fenêtre à la demande : les modules `settings`, `data` et
`runner` restent utilisables sans affichage ni CustomTkinter (tests, scripts).
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from .app import main as _main

    return _main(argv)


__all__ = ["main"]
