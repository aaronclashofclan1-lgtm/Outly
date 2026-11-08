"""Le comparateur doit ignorer le filtre de la page Chaînes.

Le tableau des résultats ne garde dans `_index` que les lignes visibles. Une
chaîne choisie dans le comparateur doit pourtant rester affichée, même si un
filtre la masque.
"""

from __future__ import annotations

from types import SimpleNamespace

from outly.gui import app


class Choix:
    def __init__(self, valeur: str):
        self.valeur = valeur

    def get(self) -> str:
        return self.valeur


def test_lignes_comparateur_utilise_l_index_complet():
    alpha = object()
    beta = object()
    ui = SimpleNamespace(
        _comparateur_choix=[Choix("Alpha"), Choix("— aucune —"), Choix("Beta")],
        _comparateur_labels={"Alpha": "UC-a", "Beta": "UC-b"},
        _comparateur_index={"UC-a": alpha, "UC-b": beta},
    )

    assert app.Application._lignes_comparateur(ui) == [alpha, None, beta]
