"""Notes éditoriales : hooks, concepts, sujets, idées et références.

La bibliothèque est volontairement locale et sans appel réseau. Elle sert deux
usages complémentaires :

* garder une trace des idées de l'utilisateur, avec un historique restaurable ;
* fournir un contexte durable au LLM, en plus des vidéos outliers.

Toutes les fonctions publiques prennent la racine du projet et ouvrent une
connexion courte : l'interface peut ainsi enregistrer une note sans garder un
verrou SQLite pendant qu'une veille tourne en arrière-plan.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .models import iso_utc

TYPES = {
    "hook": "Hook",
    "concept": "Concept",
    "sujet": "Sujet",
    "idee": "Idée",
    "reference": "Référence",
    "note": "Note",
}
STATUTS = {
    "brouillon": "Brouillon",
    "a_tester": "À tester",
    "valide": "Validé",
    "archive": "Archivé",
}
TYPES_LABELLES = {valeur: cle for cle, valeur in TYPES.items()}
STATUTS_LABELLES = {valeur: cle for cle, valeur in STATUTS.items()}


def _cle(texte: str) -> str:
    """Clé insensible à la casse et aux accents (« Reference » → reference)."""
    decompose = unicodedata.normalize("NFKD", str(texte))
    return "".join(c for c in decompose if not unicodedata.combining(c)).casefold()


TYPES_CLES = {_cle(libelle): code for libelle, code in TYPES_LABELLES.items()}
STATUTS_CLES = {_cle(libelle): code for libelle, code in STATUTS_LABELLES.items()}
TAGS_MAX = 12
NOTE_MIN = 0
NOTE_MAX = 10
# Choix proposés dans l'interface : une seule liste, partagée par les notes et
# la playlist, pour que les deux classements parlent le même langage.
NOTES_CHOIX = ["Non notée", *[f"{note}/10" for note in range(NOTE_MIN, NOTE_MAX + 1)]]
TAILLE_TAG_MAX = 40
CONTENU_MAX = 200_000


class ErreurNotes(Exception):
    """Note invalide ou écriture impossible, avec un message affichable."""


def normaliser_note(valeur: int | float | str | None) -> int | None:
    """Convertit un classement en entier de 0 à 10, ou None si absent.

    Accepte ``8``, ``"8"``, ``"8/10"`` et les libellés « Non notée ». Toute
    autre valeur est refusée plutôt que silencieusement ramenée à zéro : un
    classement faux fausserait l'ordre de priorité du cerveau IA.
    """
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        raise ErreurNotes("La note doit être un entier de 0 à 10.")
    if isinstance(valeur, int):
        note = valeur
    elif isinstance(valeur, float):
        if not float(valeur).is_integer():
            raise ErreurNotes("La note doit être un entier de 0 à 10.")
        note = int(valeur)
    else:
        texte = str(valeur).strip().casefold().replace("/10", "").strip()
        if texte in {"", "non notée", "non notee", "aucune", "-", "—"}:
            return None
        try:
            note = int(texte)
        except ValueError as probleme:
            raise ErreurNotes("La note doit être un entier de 0 à 10.") from probleme
    if not NOTE_MIN <= note <= NOTE_MAX:
        raise ErreurNotes("La note doit être comprise entre 0 et 10.")
    return int(note)


def etiquette_note(note: int | None) -> str:
    """Libellé affichable d'un classement (« 8/10 » ou « Non notée »)."""
    return f"{note}/10" if note is not None else "Non notée"


@dataclass
class Note:
    id: int
    titre: str
    contenu: str
    type: str
    statut: str
    tags: list[str] = field(default_factory=list)
    epingle: bool = False
    note_sur_10: int | None = None
    source: str = ""
    channel_id: str | None = None
    video_id: str | None = None
    cree_le: str = ""
    maj_le: str = ""

    @property
    def etiquette_type(self) -> str:
        return TYPES.get(self.type, self.type.title())

    @property
    def etiquette_statut(self) -> str:
        return STATUTS.get(self.statut, self.statut.title())

    @property
    def note_lisible(self) -> str:
        return etiquette_note(self.note_sur_10)

    @property
    def apercu(self) -> str:
        """Première ligne utile, pour la liste compacte de l'interface."""
        texte = " ".join(self.contenu.split())
        if not texte:
            return "Note vide"
        return texte[:140] + ("…" if len(texte) > 140 else "")


@dataclass
class Revision:
    id: int
    note_id: int
    titre: str
    contenu: str
    type: str
    statut: str
    tags: list[str]
    cree_le: str


def _chemin(racine: Path) -> Path:
    return Path(racine) / "data" / "radar.sqlite3"


def _connexion(racine: Path) -> sqlite3.Connection:
    conn = db.connexion(_chemin(racine))
    db.migrer(conn)
    return conn


@contextmanager
def _base(racine: Path):
    conn = _connexion(racine)
    try:
        yield conn
    finally:
        conn.close()


def _normaliser_type(valeur: str | None) -> str:
    texte = str(valeur or "note").strip()
    if texte in TYPES:
        return texte
    if texte in TYPES_LABELLES:
        return TYPES_LABELLES[texte]
    if _cle(texte) in TYPES_CLES:
        return TYPES_CLES[_cle(texte)]
    raise ErreurNotes(f"Type de note inconnu : {valeur!r}.")


def _normaliser_statut(valeur: str | None) -> str:
    texte = str(valeur or "brouillon").strip()
    if texte in STATUTS:
        return texte
    if texte in STATUTS_LABELLES:
        return STATUTS_LABELLES[texte]
    if _cle(texte) in STATUTS_CLES:
        return STATUTS_CLES[_cle(texte)]
    raise ErreurNotes(f"Statut de note inconnu : {valeur!r}.")


def normaliser_tags(valeur: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Nettoie une liste de tags, dédoublonne sans tenir compte de la casse."""
    if valeur is None:
        return []
    if isinstance(valeur, str):
        morceaux = re.split(r"[,;\n]", valeur)
    else:
        morceaux = list(valeur)
    resultat: list[str] = []
    vus: set[str] = set()
    for morceau in morceaux:
        tag = str(morceau).strip().lstrip("#").strip()
        tag = re.sub(r"\s+", "-", tag)
        if not tag:
            continue
        tag = tag[:TAILLE_TAG_MAX]
        cle = tag.casefold()
        if cle in vus:
            continue
        vus.add(cle)
        resultat.append(tag)
        if len(resultat) >= TAGS_MAX:
            break
    return resultat


def _tags_json(tags: list[str]) -> str | None:
    return json.dumps(tags, ensure_ascii=False) if tags else None


def _lire_tags(valeur: str | None) -> list[str]:
    if not valeur:
        return []
    try:
        charge = json.loads(valeur)
    except (TypeError, ValueError):
        return []
    if not isinstance(charge, list):
        return []
    return [str(tag) for tag in charge if str(tag).strip()]


def _ligne_vers_note(ligne: sqlite3.Row) -> Note:
    return Note(
        id=int(ligne["id"]),
        titre=str(ligne["titre"]),
        contenu=str(ligne["contenu"] or ""),
        type=str(ligne["type"] or "note"),
        statut=str(ligne["statut"] or "brouillon"),
        tags=_lire_tags(ligne["tags_json"]),
        epingle=bool(ligne["epingle"]),
        note_sur_10=normaliser_note(ligne["note_sur_10"]),
        source=str(ligne["source"] or ""),
        channel_id=ligne["channel_id"],
        video_id=ligne["video_id"],
        cree_le=str(ligne["cree_le"] or ""),
        maj_le=str(ligne["maj_le"] or ""),
    )


def _valider(titre: str, contenu: str) -> tuple[str, str]:
    titre = str(titre or "").strip()
    if not titre:
        raise ErreurNotes("Une note doit avoir un titre.")
    if len(titre) > 200:
        raise ErreurNotes("Le titre d'une note ne peut pas dépasser 200 caractères.")
    contenu = str(contenu or "")
    if len(contenu) > CONTENU_MAX:
        raise ErreurNotes("Cette note est trop longue (200 000 caractères maximum).")
    return titre, contenu


def creer(
    racine: Path,
    *,
    titre: str,
    contenu: str = "",
    type: str = "note",
    statut: str = "brouillon",
    tags: str | list[str] | None = None,
    epingle: bool = False,
    note_sur_10: int | str | None = None,
    source: str = "",
    channel_id: str | None = None,
    video_id: str | None = None,
) -> int:
    """Crée une note et renvoie son identifiant."""
    titre, contenu = _valider(titre, contenu)
    type_note = _normaliser_type(type)
    statut_note = _normaliser_statut(statut)
    maintenant = iso_utc()
    try:
        with _base(racine) as conn:
            curseur = conn.execute(
                """
                INSERT INTO notes (
                    titre, contenu, type, statut, tags_json, epingle, note_sur_10,
                    source, channel_id, video_id, cree_le, maj_le
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    titre,
                    contenu,
                    type_note,
                    statut_note,
                    _tags_json(normaliser_tags(tags)),
                    int(epingle),
                    normaliser_note(note_sur_10),
                    str(source or "").strip()[:2000],
                    channel_id,
                    video_id,
                    maintenant,
                    maintenant,
                ),
            )
            conn.commit()
            return int(curseur.lastrowid)
    except sqlite3.Error as probleme:
        raise ErreurNotes(f"Création de la note impossible : {probleme}") from probleme


def lire(racine: Path, note_id: int) -> Note | None:
    try:
        with _base(racine) as conn:
            ligne = conn.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
    except sqlite3.Error:
        return None
    return _ligne_vers_note(ligne) if ligne else None


def _filtrer(
    notes: list[Note],
    *,
    recherche: str = "",
    type_note: str | None = None,
    statut: str | None = None,
    tag: str | None = None,
    epinglees_seulement: bool = False,
    inclure_archivees: bool = True,
) -> list[Note]:
    texte = str(recherche or "").strip().casefold()
    type_filtre = _normaliser_type(type_note) if type_note else None
    statut_filtre = _normaliser_statut(statut) if statut else None
    tag_filtre = str(tag or "").strip().lstrip("#").casefold() or None
    resultat: list[Note] = []
    for note in notes:
        if not inclure_archivees and note.statut == "archive":
            continue
        if epinglees_seulement and not note.epingle:
            continue
        if type_filtre and note.type != type_filtre:
            continue
        if statut_filtre and note.statut != statut_filtre:
            continue
        if tag_filtre and tag_filtre not in {t.casefold() for t in note.tags}:
            continue
        if texte:
            champs = " ".join(
                [note.titre, note.contenu, note.source, " ".join(note.tags)]
            ).casefold()
            if texte not in champs:
                continue
        resultat.append(note)
    resultat.sort(
        key=lambda n: (
            n.epingle,
            n.note_sur_10 if n.note_sur_10 is not None else -1,
            n.maj_le,
        ),
        reverse=True,
    )
    return resultat


def lister(
    racine: Path,
    *,
    recherche: str = "",
    type_note: str | None = None,
    statut: str | None = None,
    tag: str | None = None,
    epinglees_seulement: bool = False,
    inclure_archivees: bool = True,
    limite: int = 500,
) -> list[Note]:
    """Liste les notes, épinglées d'abord puis par dernière modification."""
    if not _chemin(racine).exists():
        return []
    try:
        with _base(racine) as conn:
            lignes = conn.execute("SELECT * FROM notes ORDER BY maj_le DESC LIMIT ?", (int(limite),)).fetchall()
    except sqlite3.Error:
        return []
    notes = [_ligne_vers_note(ligne) for ligne in lignes]
    return _filtrer(
        notes,
        recherche=recherche,
        type_note=type_note,
        statut=statut,
        tag=tag,
        epinglees_seulement=epinglees_seulement,
        inclure_archivees=inclure_archivees,
    )


def compter(racine: Path, *, inclure_archivees: bool = True) -> int:
    if not _chemin(racine).exists():
        return 0
    try:
        with _base(racine) as conn:
            if inclure_archivees:
                ligne = conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
            else:
                ligne = conn.execute("SELECT COUNT(*) AS n FROM notes WHERE statut != 'archive'").fetchone()
    except sqlite3.Error:
        return 0
    return int(ligne["n"]) if ligne else 0


def modifier(racine: Path, note_id: int, **champs) -> bool:
    """Met à jour une note. Renvoie False si rien n'a changé.

    Avant toute modification de contenu, l'ancienne version est copiée dans
    `note_revisions`. Les changements purement cosmétiques (statut, épingle)
    ne créent pas de bruit dans l'historique.
    """
    autorises = {
        "titre",
        "contenu",
        "type",
        "statut",
        "tags",
        "epingle",
        "note_sur_10",
        "source",
    }
    inconnus = set(champs) - autorises
    if inconnus:
        raise ErreurNotes(f"Champ de note inconnu : {sorted(inconnus)[0]}.")
    try:
        with _base(racine) as conn:
            ligne = conn.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
            if not ligne:
                raise ErreurNotes("Cette note n'existe plus.")
            actuelle = _ligne_vers_note(ligne)
            titre = _valider(
                champs.get("titre", actuelle.titre),
                champs.get("contenu", actuelle.contenu),
            )[0]
            contenu = str(champs.get("contenu", actuelle.contenu) or "")
            type_note = _normaliser_type(champs.get("type", actuelle.type))
            statut = _normaliser_statut(champs.get("statut", actuelle.statut))
            tags = normaliser_tags(champs.get("tags", actuelle.tags))
            epingle = bool(champs.get("epingle", actuelle.epingle))
            note_sur_10 = normaliser_note(
                champs.get("note_sur_10", actuelle.note_sur_10)
            )
            source = str(champs.get("source", actuelle.source) or "").strip()[:2000]
            titre, contenu = _valider(titre, contenu)

            change = (
                titre != actuelle.titre
                or contenu != actuelle.contenu
                or type_note != actuelle.type
                or statut != actuelle.statut
                or tags != actuelle.tags
                or epingle != actuelle.epingle
                or note_sur_10 != actuelle.note_sur_10
                or source != actuelle.source
            )
            if not change:
                return False

            versionne = (
                titre != actuelle.titre
                or contenu != actuelle.contenu
                or type_note != actuelle.type
                or tags != actuelle.tags
            )
            if versionne:
                conn.execute(
                    """
                    INSERT INTO note_revisions (
                        note_id, titre, contenu, type, statut, tags_json,
                        epingle, source, cree_le
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        actuelle.id,
                        actuelle.titre,
                        actuelle.contenu,
                        actuelle.type,
                        actuelle.statut,
                        _tags_json(actuelle.tags),
                        int(actuelle.epingle),
                        actuelle.source,
                        iso_utc(),
                    ),
                )
            conn.execute(
                """
                UPDATE notes SET titre = ?, contenu = ?, type = ?, statut = ?,
                    tags_json = ?, epingle = ?, note_sur_10 = ?, source = ?, maj_le = ?
                WHERE id = ?
                """,
                (
                    titre,
                    contenu,
                    type_note,
                    statut,
                    _tags_json(tags),
                    int(epingle),
                    note_sur_10,
                    source,
                    iso_utc(),
                    actuelle.id,
                ),
            )
            conn.commit()
            return True
    except sqlite3.Error as probleme:
        raise ErreurNotes(f"Mise à jour de la note impossible : {probleme}") from probleme


def basculer_epingle(racine: Path, note_id: int) -> bool:
    """Inverse l'épingle et renvoie son nouvel état."""
    try:
        with _base(racine) as conn:
            ligne = conn.execute("SELECT epingle FROM notes WHERE id = ?", (int(note_id),)).fetchone()
            if not ligne:
                raise ErreurNotes("Cette note n'existe plus.")
            nouvel_etat = 0 if ligne["epingle"] else 1
            conn.execute(
                "UPDATE notes SET epingle = ?, maj_le = ? WHERE id = ?",
                (nouvel_etat, iso_utc(), int(note_id)),
            )
            conn.commit()
            return bool(nouvel_etat)
    except sqlite3.Error as probleme:
        raise ErreurNotes(f"Épingle impossible : {probleme}") from probleme


def supprimer(racine: Path, note_id: int) -> bool:
    try:
        with _base(racine) as conn:
            conn.execute("DELETE FROM note_revisions WHERE note_id = ?", (int(note_id),))
            curseur = conn.execute("DELETE FROM notes WHERE id = ?", (int(note_id),))
            conn.commit()
            return curseur.rowcount > 0
    except sqlite3.Error as probleme:
        raise ErreurNotes(f"Suppression impossible : {probleme}") from probleme


def revisions(racine: Path, note_id: int, limite: int = 30) -> list[Revision]:
    try:
        with _base(racine) as conn:
            lignes = conn.execute(
                "SELECT * FROM note_revisions WHERE note_id = ? ORDER BY cree_le DESC, id DESC LIMIT ?",
                (int(note_id), int(limite)),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        Revision(
            id=int(ligne["id"]),
            note_id=int(ligne["note_id"]),
            titre=str(ligne["titre"]),
            contenu=str(ligne["contenu"] or ""),
            type=str(ligne["type"]),
            statut=str(ligne["statut"]),
            tags=_lire_tags(ligne["tags_json"]),
            cree_le=str(ligne["cree_le"] or ""),
        )
        for ligne in lignes
    ]


def restaurer(racine: Path, revision_id: int) -> int:
    """Restaure une version et renvoie l'identifiant de la note."""
    try:
        with _base(racine) as conn:
            ligne = conn.execute(
                "SELECT * FROM note_revisions WHERE id = ?", (int(revision_id),)
            ).fetchone()
    except sqlite3.Error as probleme:
        raise ErreurNotes(f"Lecture de l'historique impossible : {probleme}") from probleme
    if not ligne:
        raise ErreurNotes("Cette version n'existe plus.")
    note_id = int(ligne["note_id"])
    ancienne = Revision(
        id=int(ligne["id"]),
        note_id=note_id,
        titre=str(ligne["titre"]),
        contenu=str(ligne["contenu"] or ""),
        type=str(ligne["type"]),
        statut=str(ligne["statut"]),
        tags=_lire_tags(ligne["tags_json"]),
        cree_le=str(ligne["cree_le"] or ""),
    )
    modifier(
        racine,
        note_id,
        titre=ancienne.titre,
        contenu=ancienne.contenu,
        type=ancienne.type,
        statut=ancienne.statut,
        tags=ancienne.tags,
    )
    return note_id


def _mots(texte: str) -> set[str]:
    return {
        mot
        for mot in re.findall(r"[a-z0-9àâäéèêëîïôöùûüçœ]{3,}", str(texte).casefold())
    }


def _pertinence(note: Note, mots_sujet: set[str]) -> int:
    if not mots_sujet:
        return 0
    titre = _mots(note.titre)
    tags = _mots(" ".join(note.tags))
    contenu = _mots(note.contenu)
    return 4 * len(mots_sujet & titre) + 3 * len(mots_sujet & tags) + len(mots_sujet & contenu)


def _bloc_note(note: Note, contenu: str) -> str:
    tags = " ".join("#" + tag for tag in note.tags) or "aucun"
    return (
        f"### Note {note.id} · {note.etiquette_type} · {note.etiquette_statut}"
        f" · classement {note.note_lisible}\n"
        f"Titre : {note.titre}\n"
        f"Tags : {tags}\n"
        f"Contenu :\n{contenu}\n"
    )


def contexte_pour_prompt(
    conn: sqlite3.Connection,
    sujet: str = "",
    *,
    limite: int = 12,
    max_caracteres: int = 7000,
) -> str:
    """Contexte compact pour la génération de hooks.

    Les notes archivées sont exclues. Les notes épinglées passent devant, puis
    les notes dont le titre, les tags ou le contenu recoupent le sujet.
    """
    lignes = conn.execute("SELECT * FROM notes WHERE statut != 'archive'").fetchall()
    notes = [_ligne_vers_note(ligne) for ligne in lignes]
    if not notes:
        return "(aucune note enregistrée)"
    mots_sujet = _mots(sujet)
    notes.sort(
        key=lambda note: (
            note.note_sur_10 if note.note_sur_10 is not None else -1,
            note.epingle,
            _pertinence(note, mots_sujet),
            note.maj_le,
        ),
        reverse=True,
    )
    morceaux: list[str] = []
    total = 0
    for note in notes[: max(1, int(limite))]:
        contenu = " ".join(note.contenu.split())[:900]
        bloc = _bloc_note(note, contenu)
        if total + len(bloc) > max_caracteres:
            break
        morceaux.append(bloc)
        total += len(bloc)
    return "\n".join(morceaux) if morceaux else "(aucune note enregistrée)"


def pour_cerveau(
    conn: sqlite3.Connection,
    *,
    inclure_archivees: bool = False,
    limite: int = 40,
    max_caracteres: int = 16000,
) -> tuple[str, list[dict]]:
    """Notes et contexte pour le cerveau IA.

    Renvoie le bloc lisible par le LLM et un instantané léger (sans le contenu
    complet) destiné à l'affichage et à la mise en cache.
    """
    requete = "SELECT * FROM notes"
    if not inclure_archivees:
        requete += " WHERE statut != 'archive'"
    lignes = conn.execute(requete).fetchall()
    notes = [_ligne_vers_note(ligne) for ligne in lignes]
    notes.sort(
        key=lambda note: (
            note.note_sur_10 if note.note_sur_10 is not None else -1,
            note.epingle,
            note.statut == "valide",
            note.maj_le,
        ),
        reverse=True,
    )
    morceaux: list[str] = []
    instantane: list[dict] = []
    total = 0
    for note in notes[: max(1, int(limite))]:
        contenu = " ".join(note.contenu.split())[:1400]
        bloc = _bloc_note(note, contenu)
        if total + len(bloc) > max_caracteres and morceaux:
            break
        morceaux.append(bloc)
        total += len(bloc)
        instantane.append(
            {
                "id": note.id,
                "titre": note.titre,
                "type": note.type,
                "etiquette_type": note.etiquette_type,
                "statut": note.statut,
                "tags": note.tags,
                "note_sur_10": note.note_sur_10,
                "maj_le": note.maj_le,
            }
        )
    if not morceaux:
        return "(aucune note enregistrée)", []
    return "\n".join(morceaux), instantane
