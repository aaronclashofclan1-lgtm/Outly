"""Migrations SQLite : une base existante doit rester ouvrable."""
from __future__ import annotations

from outly import db


def test_migration_ajoute_le_cache_des_mentions_avant_index_et_trigger(tmp_path):
    conn = db.connexion(tmp_path / "data" / "radar.sqlite3")
    db.migrer(conn)
    # Simule une base créée avant la version 10.
    conn.execute("DROP TRIGGER IF EXISTS videos_invalidate_mentions")
    conn.execute("DROP INDEX IF EXISTS idx_videos_mentions")
    conn.execute("ALTER TABLE videos DROP COLUMN mentions_json")
    conn.execute("ALTER TABLE videos DROP COLUMN mentions_scannees_le")
    conn.commit()

    db.migrer(conn)

    colonnes = {ligne["name"] for ligne in conn.execute("PRAGMA table_info(videos)")}
    assert {"mentions_json", "mentions_scannees_le"} <= colonnes
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
        "AND name = 'videos_invalidate_mentions'"
    ).fetchone()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_videos_mentions'"
    ).fetchone()
    assert db.lire_meta(conn, "schema_version") == "10"
    conn.close()


def test_copier_si_absent_ne_remplace_jamais_et_ne_laisse_pas_de_temporaire(tmp_path):
    source = tmp_path / "modele.yaml"
    source.write_text("modele: oui\n", encoding="utf-8")
    destination = tmp_path / "config.yaml"

    assert db.copier_si_absent(source, destination) is True
    assert destination.read_text(encoding="utf-8") == "modele: oui\n"
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))

    destination.write_text("personnel: oui\n", encoding="utf-8")
    assert db.copier_si_absent(source, destination) is False
    assert destination.read_text(encoding="utf-8") == "personnel: oui\n"


def test_copier_si_absent_nettoie_le_temporaire_si_la_copie_echoue(tmp_path, monkeypatch):
    source = tmp_path / "modele.yaml"
    source.write_text("modele: oui\n", encoding="utf-8")
    destination = tmp_path / "config.yaml"

    def echec(*_args, **_kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(db.shutil, "copyfile", echec)

    try:
        db.copier_si_absent(source, destination)
    except OSError as probleme:
        assert "disque plein" in str(probleme)
    else:
        raise AssertionError("l'échec de copie doit être remonté")
    assert not destination.exists()
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_terminer_run_reessaie_une_erreur_es_transitoire(monkeypatch):
    class Connexion:
        def __init__(self):
            self.appels = 0
            self.rollbacks = 0

        def execute(self, *_args):
            self.appels += 1
            if self.appels == 1:
                raise OSError("support USB débranché")

        def commit(self):
            pass

        def rollback(self):
            self.rollbacks += 1

    connexion = Connexion()
    monkeypatch.setattr(db.time, "sleep", lambda _secondes: None)

    db.terminer_run(connexion, 42, "ok")  # type: ignore[arg-type]

    assert connexion.appels == 2
    assert connexion.rollbacks == 1
