"""Plan éditorial local : les vidéos à venir et leur avancement.

Une seule table (`editorial_plan`) stocke les fiches : titre, date prévue,
étape de production, format et notes de travail. Aucun appel réseau : le
calendrier reste utilisable quand YouTube, le LLM ou le quota sont
indisponibles.

Les dates sont enregistrées en `AAAA-MM-JJ` (TEXT) ; l'interface accepte aussi
l'écriture française `JJ/MM/AAAA` et l'ISO complet, puis normalise avant
écriture. Une date vide est permise : une idée sans créneau reste une idée.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import db
from .models import iso_utc

# Étapes proposées, dans l'ordre de production. Le code est stocké en base,
# le libellé est ce que l'utilisateur voit : une faute de frappe dans
# l'interface ne peut donc pas créer une étape fantôme.
STATUTS = {
    "idee": "Idée",
    "ecriture": "Écriture",
    "tournage": "Tournage",
    "montage": "Montage",
    "publie": "Publié",
}
FORMATS = {"longue": "Vidéo longue", "short": "Short"}
STATUTS_INVERSES = {valeur: cle for cle, valeur in STATUTS.items()}
FORMATS_INVERSES = {valeur: cle for cle, valeur in FORMATS.items()}

TITRE_MAX = 200
NOTES_MAX = 20_000


class ErreurPlan(Exception):
    """Fiche invalide ou écriture impossible, avec un message affichable."""


def _cle(texte: str) -> str:
    """Clé insensible à la casse et aux accents (« Idee » → idee)."""
    decompose = unicodedata.normalize("NFKD", str(texte))
    return "".join(c for c in decompose if not unicodedata.combining(c)).strip().casefold()


STATUTS_CLES = {_cle(libelle): code for libelle, code in STATUTS_INVERSES.items()}
FORMATS_CLES = {_cle(libelle): code for libelle, code in FORMATS_INVERSES.items()}


@dataclass
class Fiche:
    """Une vidéo planifiée, telle que l'interface l'affiche."""

    identifiant: int
    titre: str
    notes: str
    date_prevue: str | None
    statut: str
    format: str
    cree_le: str = ""
    maj_le: str = ""

    @property
    def etiquette_statut(self) -> str:
        return STATUTS.get(self.statut, self.statut.title())

    @property
    def etiquette_format(self) -> str:
        return FORMATS.get(self.format, self.format.title())

    @property
    def publie(self) -> bool:
        return self.statut == "publie"

    @property
    def date(self) -> date | None:
        if not self.date_prevue:
            return None
        try:
            return date.fromisoformat(self.date_prevue)
        except ValueError:
            return None

    @property
    def date_affichable(self) -> str:
        jour = self.date
        return jour.strftime("%d/%m/%Y") if jour else ""

    @property
    def jours_restants(self) -> int | None:
        jour = self.date
        if jour is None:
            return None
        return (jour - date.today()).days


def _chemin(racine: Path) -> Path:
    return Path(racine) / "data" / "radar.sqlite3"


def _connexion(racine: Path) -> sqlite3.Connection:
    conn = db.connexion(_chemin(racine))
    db.migrer(conn)
    return conn


@contextmanager
def _base(racine: Path):
    """Connexion courte, toujours fermée (même en cas d'erreur)."""
    conn = _connexion(racine)
    try:
        yield conn
    finally:
        conn.close()


def normaliser_date(texte: str | None) -> str | None:
    """Renvoie `AAAA-MM-JJ`, ou None si la date est vide.

    Accepte l'écriture française (02/11/2026), l'ISO court et l'ISO complet.
    Une date impossible est refusée avec un message clair, jamais tronquée.
    """
    valeur = str(texte or "").strip()
    if not valeur:
        return None
    for format_ in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(valeur, format_).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(valeur).date().isoformat()
    except ValueError as erreur:
        raise ErreurPlan(
            f"Date invalide : {texte!r}. Utilisez JJ/MM/AAAA (ou laissez vide)."
        ) from erreur


def _normaliser_statut(valeur: str | None) -> str:
    texte = str(valeur or "idee").strip()
    if texte in STATUTS:
        return texte
    if texte in STATUTS_INVERSES:
        return STATUTS_INVERSES[texte]
    if _cle(texte) in STATUTS_CLES:
        return STATUTS_CLES[_cle(texte)]
    raise ErreurPlan(f"Étape inconnue : {valeur!r}.")


def _normaliser_format(valeur: str | None) -> str:
    texte = str(valeur or "longue").strip()
    if texte in FORMATS:
        return texte
    if texte in FORMATS_INVERSES:
        return FORMATS_INVERSES[texte]
    if _cle(texte) in FORMATS_CLES:
        return FORMATS_CLES[_cle(texte)]
    raise ErreurPlan(f"Format inconnu : {valeur!r}.")


def _verifier(titre: str, notes: str) -> tuple[str, str]:
    titre_propre = " ".join(str(titre or "").split())
    if not titre_propre:
        raise ErreurPlan("Donnez un titre à la fiche : c'est ce que vous retrouverez dans la liste.")
    if len(titre_propre) > TITRE_MAX:
        raise ErreurPlan(f"Le titre dépasse {TITRE_MAX} caractères.")
    notes_propres = str(notes or "")
    if len(notes_propres) > NOTES_MAX:
        raise ErreurPlan(f"Les notes dépassent {NOTES_MAX} caractères.")
    return titre_propre, notes_propres


def ajouter(
    racine: Path,
    *,
    titre: str,
    date_prevue: str | None = "",
    statut: str = "idee",
    format: str = "longue",
    notes: str = "",
) -> int:
    """Crée une fiche et renvoie son identifiant."""
    titre_propre, notes_propres = _verifier(titre, notes)
    maintenant = iso_utc()
    try:
        with _base(racine) as conn:
            curseur = conn.execute(
                """
                INSERT INTO editorial_plan (
                    titre, notes, date_prevue, statut, format, cree_le, maj_le
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    titre_propre,
                    notes_propres,
                    normaliser_date(date_prevue),
                    _normaliser_statut(statut),
                    _normaliser_format(format),
                    maintenant,
                    maintenant,
                ),
            )
            conn.commit()
            return int(curseur.lastrowid or 0)
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Écriture du plan impossible : {probleme}") from probleme


def modifier(
    racine: Path,
    identifiant: int,
    *,
    titre: str,
    date_prevue: str | None = "",
    statut: str = "idee",
    format: str = "longue",
    notes: str = "",
) -> None:
    """Met à jour une fiche existante. Un identifiant inconnu est signalé."""
    titre_propre, notes_propres = _verifier(titre, notes)
    try:
        with _base(racine) as conn:
            curseur = conn.execute(
                """
                UPDATE editorial_plan
                   SET titre = ?, notes = ?, date_prevue = ?, statut = ?,
                       format = ?, maj_le = ?
                 WHERE id = ?
                """,
                (
                    titre_propre,
                    notes_propres,
                    normaliser_date(date_prevue),
                    _normaliser_statut(statut),
                    _normaliser_format(format),
                    iso_utc(),
                    int(identifiant),
                ),
            )
            conn.commit()
            if curseur.rowcount == 0:
                raise ErreurPlan("Cette fiche n'existe plus : actualisez la liste.")
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Écriture du plan impossible : {probleme}") from probleme


def supprimer(racine: Path, identifiant: int) -> bool:
    """Retire une fiche. Renvoie True si une ligne a réellement disparu."""
    try:
        with _base(racine) as conn:
            # Les estimations de production liées à la fiche n'ont plus de sens
            # une fois la fiche supprimée : on évite de laisser des orphelins.
            conn.execute(
                "DELETE FROM production_estimates WHERE plan_id = ?", (int(identifiant),)
            )
            conn.execute("DELETE FROM editorial_tasks WHERE plan_id = ?", (int(identifiant),))
            curseur = conn.execute("DELETE FROM editorial_plan WHERE id = ?", (int(identifiant),))
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Suppression impossible : {probleme}") from probleme


def contexte_pour_cerveau(
    conn: sqlite3.Connection, limite: int = 15
) -> tuple[str, list[dict]]:
    """Plan éditorial actif, pour que le cerveau tienne compte des engagements.

    Les fiches publiées restent visibles (elles montrent ce qui a déjà été
    traité), mais les échéances proches sont classées en premier. Le bloc est
    borné : le cerveau doit pouvoir relier une idée à une vidéo déjà planifiée
    sans recevoir tout le carnet.
    """
    lignes = conn.execute(
        "SELECT * FROM editorial_plan "
        "ORDER BY statut = 'publie', "
        "CASE WHEN date_prevue IS NULL OR date_prevue = '' THEN 1 ELSE 0 END, "
        "date_prevue ASC, maj_le DESC LIMIT ?",
        (max(1, int(limite)),),
    ).fetchall()
    if not lignes:
        return "(aucune fiche dans le plan éditorial)", []
    morceaux: list[str] = []
    instantane: list[dict] = []
    for ligne in lignes:
        statut = STATUTS.get(str(ligne["statut"]), str(ligne["statut"]))
        format_ = FORMATS.get(str(ligne["format"]), str(ligne["format"]))
        date_prevue = str(ligne["date_prevue"] or "sans date")
        bloc = f"- « {ligne['titre']} » — {statut} · {format_} · {date_prevue}"
        notes = " ".join(str(ligne["notes"] or "").split())
        if notes:
            bloc += "\n  Notes : " + notes[:500]
        morceaux.append(bloc)
        instantane.append(
            {
                "id": int(ligne["id"]),
                "titre": str(ligne["titre"]),
                "statut": str(ligne["statut"]),
                "date_prevue": str(ligne["date_prevue"] or ""),
                "maj_le": str(ligne["maj_le"] or ""),
            }
        )
    return "\n".join(morceaux), instantane


def lister(racine: Path) -> list[Fiche]:
    """Fiches triées par date prévue, les idées sans date à la fin."""
    if not _chemin(racine).exists():
        return []
    try:
        with _base(racine) as conn:
            lignes = conn.execute(
                """
                SELECT * FROM editorial_plan
                 ORDER BY CASE WHEN date_prevue IS NULL OR date_prevue = '' THEN 1 ELSE 0 END,
                          date_prevue ASC, id ASC
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        Fiche(
            identifiant=int(ligne["id"]),
            titre=str(ligne["titre"]),
            notes=str(ligne["notes"] or ""),
            date_prevue=ligne["date_prevue"] or None,
            statut=str(ligne["statut"]),
            format=str(ligne["format"]),
            cree_le=str(ligne["cree_le"] or ""),
            maj_le=str(ligne["maj_le"] or ""),
        )
        for ligne in lignes
    ]


# ---------------------------------------------------------------------------
# Tâches de production
# ---------------------------------------------------------------------------


TACHE_MAX = 240


@dataclass
class Tache:
    """Une case à cocher rattachée à une fiche de production."""

    identifiant: int
    plan_id: int
    libelle: str
    fait: bool
    cree_le: str = ""
    fait_le: str = ""


def _verifier_tache(libelle: str) -> str:
    texte = " ".join(str(libelle or "").split())
    if not texte:
        raise ErreurPlan("Écrivez la tâche à ajouter.")
    if len(texte) > TACHE_MAX:
        raise ErreurPlan(f"La tâche dépasse {TACHE_MAX} caractères.")
    return texte


def lister_taches(racine: Path, plan_id: int) -> list[Tache]:
    """Tâches d'une fiche, non terminées d'abord, puis dans l'ordre de création."""
    if not _chemin(racine).exists() or not plan_id:
        return []
    try:
        with _base(racine) as conn:
            lignes = conn.execute(
                """
                SELECT * FROM editorial_tasks
                 WHERE plan_id = ?
                 ORDER BY fait ASC, id ASC
                """,
                (int(plan_id),),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        Tache(
            identifiant=int(ligne["id"]),
            plan_id=int(ligne["plan_id"]),
            libelle=str(ligne["libelle"]),
            fait=bool(ligne["fait"]),
            cree_le=str(ligne["cree_le"] or ""),
            fait_le=str(ligne["fait_le"] or ""),
        )
        for ligne in lignes
    ]


def ajouter_tache(racine: Path, plan_id: int, libelle: str) -> int:
    """Ajoute une tâche à une fiche existante."""
    texte = _verifier_tache(libelle)
    try:
        with _base(racine) as conn:
            if conn.execute("SELECT 1 FROM editorial_plan WHERE id = ?", (int(plan_id),)).fetchone() is None:
                raise ErreurPlan("Cette fiche n'existe plus : actualisez la liste.")
            curseur = conn.execute(
                "INSERT INTO editorial_tasks (plan_id, libelle, cree_le) VALUES (?, ?, ?)",
                (int(plan_id), texte, iso_utc()),
            )
            conn.commit()
            return int(curseur.lastrowid or 0)
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Ajout de la tâche impossible : {probleme}") from probleme


def basculer_tache(racine: Path, identifiant: int, fait: bool | None = None) -> bool:
    """Coche ou décoche une tâche. Sans valeur, inverse son état."""
    try:
        with _base(racine) as conn:
            if fait is None:
                curseur = conn.execute(
                    "UPDATE editorial_tasks SET fait = CASE fait WHEN 0 THEN 1 ELSE 0 END, "
                    "fait_le = CASE fait WHEN 0 THEN ? ELSE NULL END WHERE id = ?",
                    (iso_utc(), int(identifiant)),
                )
            else:
                curseur = conn.execute(
                    "UPDATE editorial_tasks SET fait = ?, fait_le = ? WHERE id = ?",
                    (1 if fait else 0, iso_utc() if fait else None, int(identifiant)),
                )
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Mise à jour de la tâche impossible : {probleme}") from probleme


def supprimer_tache(racine: Path, identifiant: int) -> bool:
    try:
        with _base(racine) as conn:
            curseur = conn.execute("DELETE FROM editorial_tasks WHERE id = ?", (int(identifiant),))
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurPlan(f"Suppression de la tâche impossible : {probleme}") from probleme
