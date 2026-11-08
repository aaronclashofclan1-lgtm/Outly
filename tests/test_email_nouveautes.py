"""Tests des nouveautés dans le rapport e-mail.

Deux points sont vérifiés : chaque rapport parle des vidéos sorties depuis le
précédent envoi (les plus récentes d'abord), et le message ne divulgue aucun
identifiant d'envoi.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from outly import db, emailing, report
from outly.config import Config


def _config(tmp_path: Path) -> Config:
    config = Config(
        racine=tmp_path,
        fichier_db=tmp_path / "data" / "test.sqlite3",
        dossier_sortie=tmp_path / "out",
    )
    config.email.destinataire = "moi@exemple.fr"
    config.email.frequence = "quotidien"
    config.email.heure = "08:00"
    config.smtp_mot_de_passe = "secret-app"
    return config


def _base(config: Config):
    config.fichier_db.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connexion(config.fichier_db)
    db.migrer(conn)
    conn.execute(
        "INSERT INTO channels (channel_id, titre, statut, ajoute_le, maj_le) "
        "VALUES ('UC1', 'Chaîne A', 'scored', '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO channel_metrics (channel_id, calcule_le, fenetre, n_videos, "
        "n_shorts, n_longues, mediane_vues_longues) "
        "VALUES ('UC1', '2026-01-01T00:00:00Z', 10, 10, 0, 10, 1000)"
    )
    for identifiant, jours in (("v1", 1), ("v2", 5)):
        publiee = (datetime.now() - timedelta(days=jours)).astimezone().isoformat()
        conn.execute(
            "INSERT INTO videos (video_id, channel_id, titre, publie_le, vues, "
            "est_short, live, recupere_le) VALUES (?, 'UC1', ?, ?, 2500, 0, "
            "'none', '2026-01-01T00:00:00Z')",
            (identifiant, f"Vidéo {identifiant}", publiee),
        )
    conn.commit()
    return conn


def test_nouveautes_les_plus_recentes_d_abord(tmp_path):
    config = _config(tmp_path)
    conn = _base(config)
    resultat = report.nouveautes(conn, config, depuis=datetime.now() - timedelta(days=7))
    conn.close()
    assert [video.video_id for video in resultat] == ["v1", "v2"]
    assert resultat[0].score_outlier == 2.5
    assert resultat[0].url.endswith("v1")


def test_nouveautes_ignore_ce_qui_precede_le_rapport(tmp_path):
    config = _config(tmp_path)
    conn = _base(config)
    resultat = report.nouveautes(conn, config, depuis=datetime.now() - timedelta(days=2))
    conn.close()
    assert [video.video_id for video in resultat] == ["v1"]


def test_message_annonce_les_nouveautes(tmp_path):
    config = _config(tmp_path)
    nouveaute = report.Nouveaute(
        video_id="v1",
        titre="Nouvelle vidéo",
        chaine="Chaîne A",
        channel_id="UC1",
        publie_le=datetime.now().astimezone().isoformat(),
        vues=2500,
        est_short=False,
        score_outlier=2.5,
    )
    message = emailing.construire_message(
        [],
        config,
        emailing.reglage(config),
        datetime(2026, 10, 30, 9, 0),
        nouveautes=[nouveaute],
        depuis=datetime(2026, 10, 29, 9, 0),
    )
    assert "1 nouveauté" in message["Subject"]
    texte = message.get_body(preferencelist=("plain",)).get_content()
    assert "Nouveautés depuis le dernier rapport" in texte
    assert "Nouvelle vidéo" in texte
    assert "https://www.youtube.com/watch?v=v1" in texte


def test_le_message_ne_divulgue_aucun_acces(tmp_path):
    config = _config(tmp_path)
    message = emailing.construire_message(
        [], config, emailing.reglage(config), datetime(2026, 10, 30, 9, 0)
    )
    for preference in ("plain", "html"):
        corps = message.get_body(preferencelist=(preference,)).get_content()
        assert "secret-app" not in corps
        assert "SMTP_PASSWORD" not in corps
        assert "mot de passe" not in corps.lower()
        assert "outly.analyze" not in corps
