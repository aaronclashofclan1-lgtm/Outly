"""Tests des rapports e-mail : échéances, message, envoi simulé, planification.

Aucun test ne contacte un serveur SMTP ni ne crée de tâche Windows réelle :
le transport et le planificateur sont remplacés.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from outly import emailing, planification
from outly.config import Config, ErreurConfig, charger
from outly.models import Metriques
from outly.report import LigneRapport


def _config(tmp_path: Path) -> Config:
    config = Config(
        racine=tmp_path,
        fichier_db=tmp_path / "data" / "test.sqlite3",
        dossier_sortie=tmp_path / "out",
    )
    config.email.destinataire = "moi@exemple.fr"
    config.email.expediteur = "rapports@exemple.fr"
    config.email.smtp_hote = "smtp.exemple.fr"
    config.email.smtp_port = 587
    config.email.frequence = "hebdomadaire"
    config.email.heure = "08:00"
    config.email.jour_semaine = 1
    config.email.jour_mois = 1
    config.smtp_mot_de_passe = "secret-app"
    return config


def _ligne(
    titre: str = "Créateur test",
    score: int | None = 78,
    statut: str = "retenue",
    justification: str = "Même audience, formats proches.",
) -> LigneRapport:
    return LigneRapport(
        channel_id="UCabcdefghijklmnopqrstuv",
        titre=titre,
        handle="@createur",
        abonnes=12_300,
        nb_videos=84,
        cree_le="2021-04-02T00:00:00Z",
        est_seed=False,
        statut_base="scored",
        raison_ecart=None,
        score_heuristique=0.62,
        score=score,
        recouvrement="fort",
        format_detecte="tutoriels",
        langue="fr",
        justification=justification,
        risques=["rythme élevé"],
        metriques=Metriques(
            channel_id="UCabcdefghijklmnopqrstuv",
            fenetre=30,
            n_videos=20,
            n_shorts=6,
            n_longues=14,
            mediane_vues_short=4000.0,
            mediane_vues_longues=9000.0,
            cadence_semaine=2.4,
            jours_depuis_publication=2,
            meilleur_outlier=3.1,
            mix_format="Shorts + longues",
        ),
        statut=statut,
    )


# ---------------------------------------------------------------------------
# Échéances
# ---------------------------------------------------------------------------


def test_creneau_quotidien_passe_et_futur():
    matin = datetime(2026, 10, 30, 7, 0)
    assert emailing.creneau("quotidien", matin, "08:00") == datetime(2026, 10, 29, 8, 0)
    soir = datetime(2026, 10, 30, 9, 0)
    assert emailing.creneau("quotidien", soir, "08:00") == datetime(2026, 10, 30, 8, 0)


def test_creneau_hebdomadaire_rattrape_le_lundi():
    mercredi = datetime(2026, 10, 28, 12, 0)  # mercredi
    assert emailing.creneau("hebdomadaire", mercredi, "08:00", 1) == datetime(2026, 10, 26, 8, 0)


def test_creneau_mensuel_reste_dans_le_mois_precedent():
    trois = datetime(2026, 10, 3, 12, 0)
    assert emailing.creneau("mensuel", trois, "08:00", 1, 5) == datetime(2026, 9, 5, 8, 0)
    vingt = datetime(2026, 10, 20, 12, 0)
    assert emailing.creneau("mensuel", vingt, "08:00", 1, 5) == datetime(2026, 10, 5, 8, 0)


def test_prochaine_echeance():
    assert emailing.prochaine_echeance("quotidien", datetime(2026, 10, 30, 9), "08:00") == datetime(
        2026, 10, 31, 8
    )
    assert emailing.prochaine_echeance(
        "hebdomadaire", datetime(2026, 10, 28, 12), "08:00", 1
    ) == datetime(2026, 11, 2, 8)
    assert emailing.prochaine_echeance(
        "hebdomadaire", datetime(2026, 11, 2, 7), "08:00", 1
    ) == datetime(2026, 11, 2, 8)
    assert emailing.prochaine_echeance(
        "mensuel", datetime(2026, 10, 3, 12), "08:00", 1, 5
    ) == datetime(2026, 10, 5, 8)
    assert emailing.prochaine_echeance("jamais", datetime(2026, 10, 3, 12), "08:00") is None


def test_est_du(tmp_path):
    config = _config(tmp_path)
    reglage = emailing.reglage(config)
    maintenant = datetime(2026, 10, 28, 12, 0)  # mercredi
    assert emailing.est_du(reglage, None, maintenant)
    assert emailing.est_du(reglage, datetime(2026, 10, 20, 9), maintenant)
    assert not emailing.est_du(reglage, datetime(2026, 10, 26, 9), maintenant)
    reglage_inactif = emailing.Reglage(**{**reglage.__dict__, "frequence": "jamais"})
    assert not emailing.est_du(reglage_inactif, None, maintenant)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_message_html_et_texte(tmp_path):
    config = _config(tmp_path)
    message = emailing.construire_message(
        [_ligne()], config, emailing.reglage(config), datetime(2026, 10, 30, 9, 0)
    )
    assert message["To"] == "moi@exemple.fr"
    assert message["From"] == emailing.EXPEDITEUR == "outly.analyze@gmail.com"
    assert "1 chaîne à surveiller" in message["Subject"]
    assert message["Subject"].startswith("Outly · ")
    assert "30/10/2026" in message["Subject"]

    texte = message.get_body(preferencelist=("plain",)).get_content()
    assert "Créateur test" in texte
    assert "78/100" in texte
    assert "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv" in texte

    riche = message.get_body(preferencelist=("html",)).get_content()
    assert "Créateur test" in riche
    assert "0D3A4A" in riche
    assert message.get_content_type() == "multipart/alternative"


def test_message_echappe_le_html(tmp_path):
    config = _config(tmp_path)
    message = emailing.construire_message(
        [_ligne(titre='<script>alert("x")</script>')],
        config,
        emailing.reglage(config),
    )
    riche = message.get_body(preferencelist=("html",)).get_content()
    assert "<script>" not in riche
    assert "&lt;script&gt;" in riche


def test_message_sans_retenue(tmp_path):
    config = _config(tmp_path)
    message = emailing.construire_message(
        [_ligne(statut="ecartee", score=12)], config, emailing.reglage(config)
    )
    assert "rapport du" in message["Subject"]
    texte = message.get_body(preferencelist=("plain",)).get_content()
    assert "Aucune chaîne ne dépasse le seuil" in texte


def test_pieces_jointes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.dossier_sortie.mkdir(parents=True)
    (config.dossier_sortie / "channels.csv").write_bytes(b"a;b\n1;2\n")
    (config.dossier_sortie / "outliers.csv").write_bytes(b"c;d\n3;4\n")
    message = emailing.construire_message([_ligne()], config, emailing.reglage(config))
    noms = {part.get_filename() for part in message.iter_attachments()}
    assert noms == {"channels.csv", "outliers.csv"}

    monkeypatch.setattr(emailing, "LIMITE_PIECE", 5)
    gros = emailing.construire_message([_ligne()], config, emailing.reglage(config))
    assert not list(gros.iter_attachments())
    texte = gros.get_body(preferencelist=("plain",)).get_content()
    assert "trop volumineux" in texte


def test_pieces_desactivees(tmp_path):
    config = _config(tmp_path)
    config.email.joindre_csv = False
    config.dossier_sortie.mkdir(parents=True)
    (config.dossier_sortie / "channels.csv").write_bytes(b"a;b\n")
    message = emailing.construire_message([_ligne()], config, emailing.reglage(config))
    assert not list(message.iter_attachments())


def test_expediteur_et_serveur_imposes(tmp_path):
    """L'expéditeur reste celui de l'application, même si config.yaml dit autre chose."""
    config = _config(tmp_path)
    config.email.expediteur = "autre@exemple.fr"
    config.email.smtp_hote = "smtp.exemple.fr"
    config.email.smtp_port = 465
    config.email.smtp_ssl = True
    reglage = emailing.reglage(config)
    assert reglage.expediteur == "outly.analyze@gmail.com"
    assert reglage.utilisateur == "outly.analyze@gmail.com"
    assert reglage.hote == "smtp.gmail.com"
    assert reglage.port == 587
    assert reglage.ssl is False
    assert reglage.destinataire == "moi@exemple.fr"

    # Gmail affiche le mot de passe par groupes : les espaces sont ignorés.
    config.smtp_mot_de_passe = "abcd efgh ijkl mnop"
    assert emailing.reglage(config).mot_de_passe == "abcdefghijklmnop"


def test_valider_signale_les_manques(tmp_path):
    config = _config(tmp_path)
    config.email.destinataire = ""
    config.smtp_mot_de_passe = ""
    problemes = emailing.valider(emailing.reglage(config))
    assert any("destinataire" in probleme for probleme in problemes)
    assert any("SMTP_PASSWORD" in probleme for probleme in problemes)


def test_envoyer_transport_simule(tmp_path):
    config = _config(tmp_path)
    reglage = emailing.reglage(config)
    message = emailing.construire_message([_ligne()], config, reglage)
    recus = []

    def transport(reglage_recu, message_recu):
        recus.append((reglage_recu, message_recu))

    emailing.envoyer(reglage, message, transport=transport)
    assert recus and recus[0][1]["To"] == "moi@exemple.fr"
    assert "secret-app" not in message.as_string()


def test_envoyer_refuse_une_configuration_incomplete(tmp_path):
    config = _config(tmp_path)
    config.smtp_mot_de_passe = ""
    message = emailing.construire_message([_ligne()], config, emailing.reglage(config))
    with pytest.raises(emailing.ErreurEmail, match="SMTP_PASSWORD"):
        emailing.envoyer(emailing.reglage(config), message, transport=lambda *_: None)


def test_etat_dernier_envoi(connexion, tmp_path):
    config = _config(tmp_path)
    maintenant = datetime(2026, 10, 30, 9, 0)
    assert emailing.dernier_envoi(connexion) is None
    emailing.noter_envoi(connexion, config.email.destinataire, 4, maintenant)
    assert emailing.dernier_envoi(connexion) == maintenant


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _ecrire_config(tmp_path: Path, email: str) -> None:
    (tmp_path / "config.yaml").write_text(
        "youtube:\n  region_code: FR\nemail:\n" + email, encoding="utf-8"
    )


def test_config_email_lue(tmp_path):
    _ecrire_config(
        tmp_path,
        "  destinataire: moi@exemple.fr\n"
        "  smtp_port: 465\n"
        "  smtp_ssl: true\n"
        "  frequence: mensuel\n"
        "  heure: '07:30'\n"
        "  jour_mois: 12\n",
    )
    config = charger(tmp_path)
    assert config.email.destinataire == "moi@exemple.fr"
    assert config.email.smtp_port == 465
    assert config.email.smtp_ssl is True
    assert config.email.frequence == "mensuel"
    assert config.email.heure == "07:30"
    assert config.email.jour_mois == 12


def test_config_email_refuse_une_frequence_inconnue(tmp_path):
    _ecrire_config(tmp_path, "  frequence: toutes_les_heures\n")
    with pytest.raises(ErreurConfig, match="email.frequence"):
        charger(tmp_path)


def test_config_email_refuse_une_heure_invalide(tmp_path):
    _ecrire_config(tmp_path, "  heure: 25:00\n")
    with pytest.raises(ErreurConfig, match="email.heure"):
        charger(tmp_path)


# ---------------------------------------------------------------------------
# Planification
# ---------------------------------------------------------------------------


class _Resultat:
    def __init__(self, code: int = 0, sortie: str = "") -> None:
        self.returncode = code
        self.stdout = sortie
        self.stderr = ""


def test_script_bat(tmp_path):
    script = planification.contenu_script(r"C:\Python\pythonw.exe", tmp_path)
    assert "pythonw.exe" in script
    assert "-m outly mail --si-du" in script
    assert "out\\envoi-rapports.log" in script
    assert 'cd /d "%~dp0"' in script


def test_script_powershell(tmp_path):
    contenu = planification.contenu_ps1(tmp_path / "envoyer-rapports.bat", "07:30")
    assert "New-ScheduledTaskTrigger -Daily -At '07:30'" in contenu
    assert "-StartWhenAvailable" in contenu
    assert planification.NOM_TACHE in contenu
    assert "envoyer-rapports.bat" in contenu


def test_commande_etat():
    commande = planification.commande_etat()
    assert commande[0] == "powershell.exe"
    assert "Get-ScheduledTask" in commande[-1]
    assert planification.NOM_TACHE in commande[-1]


@pytest.mark.skipif(os.name != "nt", reason="planificateur Windows uniquement")
def test_creer_tache_sans_executer_powershell(tmp_path):
    appels = []

    def faux(commande, **options):
        appels.append(commande)
        return _Resultat()

    ok, detail = planification.creer_tache(tmp_path, "08:15", r"C:\Python\python.exe", faux)
    assert ok, detail
    assert planification.chemin_script(tmp_path).exists()
    assert appels and appels[0][0] == "powershell.exe"
    assert "-File" in appels[0]


def test_tache_programmee_avec_reponse_simulee():
    def oui(commande, **options):
        return _Resultat(0, "oui\n")

    def non(commande, **options):
        return _Resultat(1, "non\n")

    if os.name != "nt":
        assert planification.tache_programmee(oui) is False
    else:
        assert planification.tache_programmee(oui) is True
        assert planification.tache_programmee(non) is False
