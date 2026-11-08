"""Tests de la bibliothèque de notes : versions, tags, recherche et contexte."""

from __future__ import annotations

from pathlib import Path

import pytest

from outly import db, notes


def test_creation_liste_et_normalisation_des_tags(tmp_path: Path):
    identifiant = notes.creer(
        tmp_path,
        titre="Le hook qui casse le rythme",
        contenu="Commencer au milieu d'une phrase.",
        type="hook",
        tags=["#Hook", "hook", "  Idée forte  ", "idée-forte"],
        epingle=True,
    )
    autre = notes.creer(tmp_path, titre="Une référence", type="Reference")

    liste = notes.lister(tmp_path)
    assert [note.id for note in liste][0] == identifiant
    note = notes.lire(tmp_path, identifiant)
    assert note is not None
    assert note.tags == ["Hook", "Idée-forte"]
    assert note.epingle
    assert note.etiquette_type == "Hook"
    assert notes.compter(tmp_path) == 2
    assert notes.lire(tmp_path, autre) is not None


def test_recherche_et_filtres(tmp_path: Path):
    notes.creer(tmp_path, titre="Hook storytelling", contenu="Une histoire personnelle", type="hook")
    notes.creer(tmp_path, titre="Sujet audience", contenu="Les débutants", type="sujet", statut="valide")
    notes.creer(tmp_path, titre="Vieille idée", contenu="Archivée", type="idee", statut="archive")

    assert [note.titre for note in notes.lister(tmp_path, recherche="débutants")] == [
        "Sujet audience"
    ]
    assert [note.titre for note in notes.lister(tmp_path, type_note="Hook")] == [
        "Hook storytelling"
    ]
    assert [note.titre for note in notes.lister(tmp_path, statut="Validé")] == [
        "Sujet audience"
    ]
    assert notes.lister(tmp_path, inclure_archivees=False)[0].titre != "Vieille idée"
    assert len(notes.lister(tmp_path, recherche="archivée")) == 1


def test_modification_cree_une_version_et_restauration(tmp_path: Path):
    identifiant = notes.creer(tmp_path, titre="Brouillon", contenu="Version un")
    assert notes.revisions(tmp_path, identifiant) == []

    assert notes.modifier(tmp_path, identifiant, contenu="Version deux")
    versions = notes.revisions(tmp_path, identifiant)
    assert len(versions) == 1
    assert versions[0].contenu == "Version un"

    # Une modification identique ne crée pas de bruit.
    assert not notes.modifier(tmp_path, identifiant, contenu="Version deux")
    assert len(notes.revisions(tmp_path, identifiant)) == 1

    notes.restaurer(tmp_path, versions[0].id)
    assert notes.lire(tmp_path, identifiant).contenu == "Version un"
    assert len(notes.revisions(tmp_path, identifiant)) == 2


def test_suppression_efface_aussi_l_historique(tmp_path: Path):
    identifiant = notes.creer(tmp_path, titre="À supprimer", contenu="Un")
    notes.modifier(tmp_path, identifiant, contenu="Deux")
    assert notes.supprimer(tmp_path, identifiant)
    assert notes.lire(tmp_path, identifiant) is None
    assert notes.revisions(tmp_path, identifiant) == []


def test_un_titre_vide_est_refuse(tmp_path: Path):
    with pytest.raises(notes.ErreurNotes):
        notes.creer(tmp_path, titre="   ")
    with pytest.raises(notes.ErreurNotes):
        notes.creer(tmp_path, titre="Type invalide", type="inventé")


def test_contexte_du_prompt_priorise_le_sujet_et_exclut_les_archives(tmp_path: Path):
    notes.creer(
        tmp_path,
        titre="Concept sur la rétention",
        contenu="Les trois premières secondes décident de tout.",
        type="concept",
        tags=["rétention"],
    )
    archive = notes.creer(
        tmp_path,
        titre="Vieille rétention",
        contenu="À ne pas utiliser.",
        type="concept",
        statut="archive",
    )
    # Le test lit la même base que les fonctions de notes : on la recrée en
    # mémoire à partir du fichier pour éviter deux connexions concurrentes.
    chemin = tmp_path / "data" / "radar.sqlite3"
    autre = db.connexion(chemin)
    db.migrer(autre)
    bloc = notes.contexte_pour_prompt(autre, "Comment améliorer la rétention ?")
    autre.close()

    assert "trois premières secondes" in bloc
    assert f"Note {archive}" not in bloc
    assert "### Note" in bloc


def test_le_classement_sur_dix_est_valide_et_prioritaire(tmp_path: Path):
    faible = notes.creer(tmp_path, titre="Hook faible", note_sur_10=3)
    forte = notes.creer(tmp_path, titre="Hook fort", note_sur_10="9/10")
    notes.creer(tmp_path, titre="Sans classement")

    assert notes.lire(tmp_path, forte).note_sur_10 == 9
    assert notes.lire(tmp_path, faible).note_lisible == "3/10"
    assert notes.lire(tmp_path, faible).note_sur_10 == 3
    # Les mieux notées passent devant dans la bibliothèque.
    assert [note.id for note in notes.lister(tmp_path)][:2] == [forte, faible]

    assert notes.modifier(tmp_path, faible, note_sur_10=8)
    assert notes.lire(tmp_path, faible).note_sur_10 == 8
    assert notes.modifier(tmp_path, faible, note_sur_10="Non notée")
    assert notes.lire(tmp_path, faible).note_sur_10 is None

    with pytest.raises(notes.ErreurNotes):
        notes.creer(tmp_path, titre="Trop haut", note_sur_10=11)
    with pytest.raises(notes.ErreurNotes):
        notes.creer(tmp_path, titre="Pas un nombre", note_sur_10="excellent")


def test_le_contexte_du_cerveau_affiche_le_classement(tmp_path: Path):
    notes.creer(tmp_path, titre="Hook fort", contenu="Une preuve concrète.", note_sur_10=9)
    notes.creer(tmp_path, titre="Hook faible", contenu="Une idée vague.", note_sur_10=2)
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    bloc, instantane = notes.pour_cerveau(conn)
    conn.close()

    assert "classement 9/10" in bloc
    assert bloc.index("Hook fort") < bloc.index("Hook faible")
    assert instantane[0]["titre"] == "Hook fort"
    assert instantane[0]["note_sur_10"] == 9


def test_pour_cerveau_renvoie_un_instantane_sans_contenu_complet(tmp_path: Path):
    identifiant = notes.creer(
        tmp_path,
        titre="Un concept",
        contenu="Un contenu très long " * 200,
        type="concept",
        epingle=True,
    )
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    bloc, instantane = notes.pour_cerveau(conn)
    conn.close()

    assert f"Note {identifiant}" in bloc
    assert instantane and instantane[0]["titre"] == "Un concept"
    assert "contenu" not in instantane[0]
