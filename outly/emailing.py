"""Rapports par e-mail : composition, envoi SMTP et calcul des échéances.

Aucune dépendance externe : `smtplib` et `email` de la bibliothèque standard
suffisent. L'application envoie toujours depuis `outly.analyze@gmail.com`
(Gmail, STARTTLS) : l'utilisateur ne choisit que le destinataire, et seul le
mot de passe d'application vit dans `.env`, jamais dans `config.yaml` ni dans
un journal.

Le message est construit à partir des mêmes lignes que `report.md`. Il contient
une version texte et une version HTML, et joint éventuellement `channels.csv`
et `outliers.csv`.

Les échéances sont calculées en heure locale, sans fuseau : la tâche Windows
se déclenche à l'heure de la machine, comme l'affichage de l'interface.
"""

from __future__ import annotations

import html
import re
import smtplib
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import quote

from . import db, report
from .config import Config

FREQUENCES = ("jamais", "quotidien", "hebdomadaire", "mensuel")
JOURS_SEMAINE = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)

MOTIF_HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MOTIF_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CLE_DERNIER_ENVOI = "email_dernier_envoi"
CLE_DERNIER_STATUT = "email_dernier_statut"

# Compte d'envoi unique de l'application. L'utilisateur ne choisit que le
# destinataire : l'expéditeur et le serveur sont fixés ici, et seul le mot de
# passe d'application Gmail est demandé (il reste dans `.env`).
EXPEDITEUR = "outly.analyze@gmail.com"
SMTP_HOTE = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_SSL = False

# Deux CSV restent sous la limite de la plupart des serveurs SMTP (25 Mo),
# mais on s'arrête bien avant : un rapport de veille n'a aucune raison de peser
# lourd, et les boîtes gratuites limitent souvent à 10 Mo.
LIMITE_PIECE = 5 * 1024 * 1024
LIMITE_TOTAL = 8 * 1024 * 1024
MAX_CHAINES_EMAIL = 8


class ErreurEmail(Exception):
    """Configuration incomplète, serveur injoignable ou envoi refusé."""


@dataclass(frozen=True)
class Reglage:
    """Paramètres d'envoi effectifs, lus depuis `config.yaml` et `.env`."""

    destinataire: str
    expediteur: str
    hote: str
    port: int
    ssl: bool
    utilisateur: str
    mot_de_passe: str
    frequence: str = "jamais"
    heure: str = "08:00"
    jour_semaine: int = 1
    jour_mois: int = 1
    joindre_csv: bool = True
    dossier_sortie: Path | None = None


def reglage(config: Config) -> Reglage:
    """Assemble les réglages d'envoi.

    L'expéditeur et le serveur Gmail sont imposés par l'application : quel que
    soit le contenu de `config.yaml`, le message part de `EXPEDITEUR`. Seul le
    destinataire et la fréquence sont réglables par l'utilisateur.
    """
    email = config.email
    return Reglage(
        destinataire=email.destinataire.strip(),
        expediteur=EXPEDITEUR,
        hote=SMTP_HOTE,
        port=SMTP_PORT,
        ssl=SMTP_SSL,
        utilisateur=EXPEDITEUR,
        # Gmail affiche les mots de passe d'application par groupes de quatre :
        # les espaces recopiés ne font pas partie du secret.
        mot_de_passe="".join((config.smtp_mot_de_passe or "").split()),
        frequence=email.frequence,
        heure=email.heure,
        jour_semaine=email.jour_semaine,
        jour_mois=email.jour_mois,
        joindre_csv=email.joindre_csv,
        dossier_sortie=config.dossier_sortie,
    )


def valider(reglage: Reglage) -> list[str]:
    """Liste tout ce qui empêche un envoi, en français et sans secret."""
    problemes: list[str] = []
    if not reglage.destinataire:
        problemes.append("adresse du destinataire manquante")
    elif not MOTIF_EMAIL.match(reglage.destinataire):
        problemes.append(f"adresse du destinataire invalide ({reglage.destinataire})")
    if not reglage.expediteur:
        problemes.append("adresse d'envoi manquante")
    elif not MOTIF_EMAIL.match(reglage.expediteur):
        problemes.append(f"adresse d'envoi invalide ({reglage.expediteur})")
    if not reglage.hote:
        problemes.append("serveur SMTP manquant")
    if not 1 <= reglage.port <= 65535:
        problemes.append("port SMTP hors limites")
    if not reglage.mot_de_passe:
        problemes.append("mot de passe d'application Gmail manquant (SMTP_PASSWORD)")
    if reglage.frequence not in FREQUENCES:
        problemes.append(f"fréquence inconnue ({reglage.frequence})")
    if not MOTIF_HEURE.match(reglage.heure):
        problemes.append(f"heure invalide ({reglage.heure})")
    if not 1 <= reglage.jour_semaine <= 7:
        problemes.append("jour de la semaine hors limites")
    if not 1 <= reglage.jour_mois <= 28:
        problemes.append("jour du mois hors limites (1 à 28)")
    return problemes


# ---------------------------------------------------------------------------
# Échéances
# ---------------------------------------------------------------------------


def _heure(texte: str) -> time:
    heures, minutes = texte.split(":", 1)
    return time(int(heures), int(minutes))


def _jour_du_mois(annee: int, mois: int, jour: int) -> date:
    return date(annee, mois, min(max(1, jour), 28))


def _mois_precedent(annee: int, mois: int) -> tuple[int, int]:
    return (annee - 1, 12) if mois == 1 else (annee, mois - 1)


def _mois_suivant(annee: int, mois: int) -> tuple[int, int]:
    return (annee + 1, 1) if mois == 12 else (annee, mois + 1)


def creneau(
    frequence: str,
    maintenant: datetime,
    heure: str,
    jour_semaine: int = 1,
    jour_mois: int = 1,
) -> datetime | None:
    """Dernier créneau d'envoi déjà atteint (celui de la période courante).

    Un créneau manqué (PC éteint) reste éligible jusqu'au suivant : au premier
    contrôle, un rapport en retard part une seule fois, pas une fois par jour
    d'absence.
    """
    if frequence == "quotidien":
        moment = datetime.combine(maintenant.date(), _heure(heure))
        return moment if moment <= maintenant else moment - timedelta(days=1)
    if frequence == "hebdomadaire":
        moment = datetime.combine(maintenant.date(), _heure(heure))
        delta = (maintenant.weekday() - (jour_semaine - 1)) % 7
        moment -= timedelta(days=delta)
        if moment > maintenant:
            moment -= timedelta(days=7)
        return moment
    if frequence == "mensuel":
        annee, mois = maintenant.year, maintenant.month
        moment = datetime.combine(_jour_du_mois(annee, mois, jour_mois), _heure(heure))
        if moment > maintenant:
            annee, mois = _mois_precedent(annee, mois)
            moment = datetime.combine(_jour_du_mois(annee, mois, jour_mois), _heure(heure))
        return moment
    return None


def prochaine_echeance(
    frequence: str,
    maintenant: datetime,
    heure: str,
    jour_semaine: int = 1,
    jour_mois: int = 1,
) -> datetime | None:
    """Prochain créneau strictement postérieur à `maintenant`."""
    if frequence not in ("quotidien", "hebdomadaire", "mensuel"):
        return None
    moment = datetime.combine(maintenant.date(), _heure(heure))
    if frequence == "quotidien":
        return moment if moment > maintenant else moment + timedelta(days=1)
    if frequence == "hebdomadaire":
        delta = ((jour_semaine - 1) - maintenant.weekday()) % 7
        moment += timedelta(days=delta)
        if moment <= maintenant:
            moment += timedelta(days=7)
        return moment

    # Mensuel : le jour cible n'est pas forcément celui d'aujourd'hui.
    moment = datetime.combine(
        _jour_du_mois(maintenant.year, maintenant.month, jour_mois), _heure(heure)
    )
    if moment <= maintenant:
        annee, mois = _mois_suivant(maintenant.year, maintenant.month)
        moment = datetime.combine(_jour_du_mois(annee, mois, jour_mois), _heure(heure))
    return moment


def est_du(reglage: Reglage, dernier: datetime | None, maintenant: datetime | None = None) -> bool:
    """Vrai si le rapport doit partir maintenant (jamais envoyé = oui)."""
    if reglage.frequence == "jamais":
        return False
    maintenant = maintenant or datetime.now()
    moment = creneau(
        reglage.frequence, maintenant, reglage.heure, reglage.jour_semaine, reglage.jour_mois
    )
    if moment is None:
        return False
    return dernier is None or dernier < moment


# ---------------------------------------------------------------------------
# État conservé en base
# ---------------------------------------------------------------------------


def dernier_envoi(conn) -> datetime | None:
    valeur = db.lire_meta(conn, CLE_DERNIER_ENVOI)
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur)
    except ValueError:
        return None


def noter_envoi(
    conn,
    destinataire: str,
    retenues: int,
    maintenant: datetime | None = None,
) -> datetime:
    moment = maintenant or datetime.now()
    db.ecrire_meta(conn, CLE_DERNIER_ENVOI, moment.isoformat(timespec="seconds"))
    db.ecrire_meta(
        conn,
        CLE_DERNIER_STATUT,
        f"{retenues} chaîne(s) retenue(s) envoyée(s) à {destinataire}",
    )
    return moment


# ---------------------------------------------------------------------------
# Composition du message
# ---------------------------------------------------------------------------


def _nombre(valeur: float | int | None, decimales: int = 0) -> str:
    if valeur is None:
        return "—"
    if isinstance(valeur, float) and not valeur.is_integer():
        return f"{valeur:,.{decimales or 1}f}".replace(",", " ").replace(".", ",")
    return f"{int(valeur):,}".replace(",", " ")


def _extrait(texte: str | None, limite: int = 280) -> str:
    if not texte:
        return ""
    propre = " ".join(str(texte).split())
    if len(propre) <= limite:
        return propre
    coupe = propre[:limite].rsplit(" ", 1)[0]
    return coupe + "…"


def _accord(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


def _e(texte) -> str:
    return html.escape(str(texte), quote=True)


def _url_chaine(channel_id: str) -> str:
    return "https://www.youtube.com/channel/" + quote(channel_id, safe="")


def _libelle_frequence(frequence: str, heure: str, jour_semaine: int, jour_mois: int) -> str:
    if frequence == "quotidien":
        return f"tous les jours à {heure}"
    if frequence == "hebdomadaire":
        return f"chaque {JOURS_SEMAINE[jour_semaine - 1]} à {heure}"
    if frequence == "mensuel":
        return f"le {jour_mois} de chaque mois à {heure}"
    return "envoi manuel"


def _ligne_resume(ligne: report.LigneRapport) -> str:
    """Une ligne synthétique pour la version texte."""
    morceaux = []
    if ligne.score is not None:
        morceaux.append(f"{ligne.score}/100")
    else:
        morceaux.append("score LLM absent")
    if ligne.handle:
        morceaux.append(ligne.handle)
    if ligne.abonnes is not None:
        morceaux.append(f"{_nombre(ligne.abonnes)} abonnés")
    if ligne.metriques is not None:
        morceaux.append(f"{_nombre(ligne.metriques.cadence_semaine)} vidéos/semaine")
        if ligne.metriques.meilleur_outlier and ligne.metriques.meilleur_outlier > 1.0:
            morceaux.append(f"meilleur outlier ×{_nombre(ligne.metriques.meilleur_outlier, 1)}")
    return " · ".join(morceaux)


def _quand(publie_le: str | None) -> str:
    """« aujourd'hui », « hier », « il y a 4 jours » ou la date complète."""
    if not publie_le:
        return ""
    try:
        date = datetime.fromisoformat(publie_le.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    jours = max(0, (datetime.now().date() - date.date()).days)
    if jours == 0:
        return "aujourd'hui"
    if jours == 1:
        return "hier"
    if jours < 14:
        return f"il y a {jours} jours"
    return date.strftime("%d/%m/%Y")


def _bloc_nouveautes_html(
    nouveautes: list[report.Nouveaute], depuis: datetime | None
) -> str:
    """Les vidéos sorties depuis le rapport précédent, en tête de message."""
    titre = (
        "Nouveautés depuis le dernier rapport"
        if depuis
        else "Nouveautés des 7 derniers jours"
    )
    if not nouveautes:
        return (
            '<div style="background:#F5F8FA;border-radius:10px;padding:13px 15px;'
            'font-size:13px;line-height:1.6;color:#3C515B;margin:0 0 14px;">'
            f"<strong>{titre} :</strong> aucune nouvelle vidéo pour l'instant. "
            "La prochaine veille reprendra les uploads des chaînes suivies.</div>"
        )

    cartes = []
    for video in nouveautes:
        details = [video.chaine]
        quand = _quand(video.publie_le)
        if quand:
            details.append(quand)
        if video.vues is not None:
            details.append(f"{_nombre(video.vues)} vues")
        if video.score_outlier and video.score_outlier > 1.0:
            details.append(f"×{_nombre(video.score_outlier, 1)} la médiane")
        badge = (
            '<span style="display:inline-block;background:#EAF4F6;color:#0D3A4A;'
            'border-radius:999px;padding:2px 8px;font-size:11px;font-weight:700;'
            'margin-left:6px;">Short</span>'
            if video.est_short
            else ""
        )
        cartes.append(
            '<div style="border-left:3px solid #3FA3BC;padding:2px 0 2px 12px;'
            'margin:0 0 10px;">'
            f'<a href="{_e(video.url)}" style="font-size:14px;font-weight:700;'
            f'color:#10222A;text-decoration:none;">{_e(video.titre)}</a>{badge}'
            '<div style="font-size:12px;color:#61727C;margin-top:3px;">'
            f'{" · ".join(_e(detail) for detail in details)}</div></div>'
        )
    return (
        '<div style="margin:0 0 14px;">'
        '<div style="font-size:11px;font-weight:700;letter-spacing:.5px;color:#0D3A4A;'
        f'text-transform:uppercase;margin-bottom:8px;">{titre}</div>'
        + "".join(cartes)
        + "</div>"
    )


def _bloc_nouveautes_texte(
    nouveautes: list[report.Nouveaute], depuis: datetime | None
) -> list[str]:
    titre = (
        "Nouveautés depuis le dernier rapport"
        if depuis
        else "Nouveautés des 7 derniers jours"
    )
    if not nouveautes:
        return [f"{titre} : aucune nouvelle vidéo pour l'instant.", ""]
    morceaux = [f"{titre} :"]
    for position, video in enumerate(nouveautes, start=1):
        details = [video.chaine]
        quand = _quand(video.publie_le)
        if quand:
            details.append(quand)
        if video.vues is not None:
            details.append(f"{_nombre(video.vues)} vues")
        if video.score_outlier and video.score_outlier > 1.0:
            details.append(f"×{_nombre(video.score_outlier, 1)} la médiane")
        morceaux.append(
            f"{position}. {video.titre}" + (" [Short]" if video.est_short else "")
        )
        morceaux.append("   " + " · ".join(details))
        morceaux.append(f"   {video.url}")
        morceaux.append("")
    return morceaux


def _bloc_html(position: int, ligne: report.LigneRapport) -> str:
    metriques = ligne.metriques
    details = []
    if ligne.handle:
        details.append(_e(ligne.handle))
    details.append(
        f"{_nombre(ligne.abonnes)} abonnés" if ligne.abonnes is not None else "abonnés masqués"
    )
    if metriques is not None:
        details.append(f"{_nombre(metriques.cadence_semaine)} vidéos/semaine")
        if metriques.meilleur_outlier and metriques.meilleur_outlier > 1.0:
            details.append(f"meilleur outlier ×{_nombre(metriques.meilleur_outlier, 1)}")

    if ligne.score is not None:
        pastille = (
            f'<span style="display:inline-block;background:#0D3A4A;color:#FFFFFF;'
            f'border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;">'
            f"{ligne.score}/100</span>"
        )
    else:
        pastille = (
            '<span style="display:inline-block;background:#EAF4F6;color:#0D3A4A;'
            'border-radius:999px;padding:3px 10px;font-size:12px;font-weight:700;">'
            "heuristique</span>"
        )

    justification = _extrait(ligne.justification, 260)
    bloc_justification = (
        f'<div style="font-size:13px;line-height:1.55;color:#3C515B;margin-top:8px;">'
        f"{_e(justification)}</div>"
        if justification
        else ""
    )

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="border:1px solid #E3E8EC;border-radius:10px;margin:0 0 12px;">
      <tr><td style="padding:14px 16px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:15px;font-weight:700;color:#10222A;line-height:1.35;">
              {position}. {_e(ligne.titre)}
            </td>
            <td align="right" style="padding-left:12px;white-space:nowrap;">{pastille}</td>
          </tr>
        </table>
        <div style="font-size:12px;color:#61727C;margin-top:5px;">{" · ".join(details)}</div>
        {bloc_justification}
        <a href="{_url_chaine(ligne.channel_id)}"
           style="display:inline-block;margin-top:8px;font-size:13px;color:#2E7F95;text-decoration:none;">
          Voir la chaîne →
        </a>
      </td></tr>
    </table>"""


def _html(
    lignes: list[report.LigneRapport],
    retenues: list[report.LigneRapport],
    config: Config,
    reglage: Reglage,
    maintenant: datetime,
    test: bool,
    pieces: list[str],
    ignorees: list[str],
    nouveautes: list[report.Nouveaute] | None = None,
    depuis: datetime | None = None,
) -> str:
    affichees = retenues[: min(len(retenues), MAX_CHAINES_EMAIL, config.sortie.report_top)]
    seuil = config.llm.min_score_keep
    titre = (
        f"{len(retenues)} chaîne{'s' if len(retenues) > 1 else ''} à surveiller"
        if retenues
        else "Aucune chaîne ne dépasse le seuil"
    )

    if test:
        bandeau = (
            '<div style="background:#FFF6E5;border:1px solid #F0D9A8;border-radius:9px;'
            'padding:10px 12px;font-size:12px;color:#6B4E12;margin:14px 0 0;">'
            "Message de test : aucun envoi planifié n'a été modifié.</div>"
        )
    else:
        bandeau = ""

    blocs = "".join(_bloc_html(position, ligne) for position, ligne in enumerate(affichees, start=1))
    if not retenues:
        blocs = (
            '<div style="background:#F5F8FA;border-radius:10px;padding:16px;font-size:13px;'
            'line-height:1.6;color:#3C515B;">'
            "Élargissez <code>keywords.txt</code>, ajoutez des seeds ou abaissez le seuil "
            f"({seuil}/100), puis relancez une veille.</div>"
        )
    bloc_nouveautes = _bloc_nouveautes_html(nouveautes or [], depuis)

    if pieces:
        note_pieces = "Pièces jointes : " + ", ".join(_e(nom) for nom in pieces) + "."
    elif reglage.joindre_csv:
        note_pieces = "Aucun CSV à joindre pour ce rapport."
    else:
        note_pieces = "Pièces jointes désactivées."
    if ignorees:
        note_pieces += " Non joint : " + ", ".join(_e(nom) for nom in ignorees) + "."

    frequence = _libelle_frequence(
        reglage.frequence, reglage.heure, reglage.jour_semaine, reglage.jour_mois
    )

    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Outly — rapport du {maintenant:%d/%m/%Y}</title>
</head>
<body style="margin:0;padding:0;background:#F2F5F7;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#F2F5F7;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0"
             style="width:640px;max-width:100%;background:#FFFFFF;border-radius:14px;overflow:hidden;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr>
          <td style="background:#0D3A4A;padding:26px 28px;">
            <div style="font-size:23px;font-weight:700;color:#FFFFFF;letter-spacing:.3px;">Outly</div>
            <div style="font-size:13px;color:#A9D3DE;margin-top:4px;">
              Veille concurrentielle YouTube · rapport du {maintenant:%d/%m/%Y}
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:26px 28px 10px;">
            <div style="font-size:20px;font-weight:700;color:#10222A;">{titre}</div>
            <div style="font-size:14px;color:#61727C;margin-top:6px;">
              {_accord(len(lignes), "chaîne évaluée", "chaînes évaluées")} ·
              {_accord(len(retenues), "retenue", "retenues")} · seuil {seuil}/100
            </div>
            {bandeau}
          </td>
        </tr>
        <tr><td style="padding:8px 28px 0;">{bloc_nouveautes}</td></tr>
        <tr><td style="padding:8px 28px 0;">{blocs}</td></tr>
        <tr>
          <td style="padding:8px 28px 24px;">
            <div style="background:#EAF4F6;border-radius:10px;padding:13px 15px;font-size:12px;
                        line-height:1.6;color:#24414B;">
              Le rapport complet (<code>report.md</code>) reste dans <code>out/</code> sur ce PC.
              {note_pieces}
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:18px 28px 24px;border-top:1px solid #E3E8EC;font-size:12px;
                     line-height:1.6;color:#7A8790;">
            Envoyé automatiquement par Outly ({frequence}). Ce message ne contient
            aucun identifiant d'envoi : les accès restent sur le PC qui a produit
            le rapport.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _texte(
    lignes: list[report.LigneRapport],
    retenues: list[report.LigneRapport],
    config: Config,
    reglage: Reglage,
    maintenant: datetime,
    test: bool,
    pieces: list[str],
    ignorees: list[str],
    nouveautes: list[report.Nouveaute] | None = None,
    depuis: datetime | None = None,
) -> str:
    affichees = retenues[: min(len(retenues), MAX_CHAINES_EMAIL, config.sortie.report_top)]
    morceaux = [
        "Outly — veille concurrentielle YouTube",
        f"Rapport du {maintenant:%d/%m/%Y à %H:%M}",
        "",
    ]
    if test:
        morceaux += ["*** MESSAGE DE TEST : aucun envoi planifié n'a été modifié. ***", ""]
    morceaux.append(
        f"{_accord(len(lignes), 'chaîne évaluée', 'chaînes évaluées')} · "
        f"{_accord(len(retenues), 'retenue', 'retenues')} · "
        f"seuil {config.llm.min_score_keep}/100"
    )
    morceaux.append("")
    morceaux.extend(_bloc_nouveautes_texte(nouveautes or [], depuis))

    if not retenues:
        morceaux += [
            "Aucune chaîne ne dépasse le seuil de proximité.",
            "Élargissez keywords.txt, ajoutez des seeds ou abaissez le seuil.",
            "",
        ]
    for position, ligne in enumerate(affichees, start=1):
        morceaux.append(f"{position}. {ligne.titre}")
        morceaux.append(f"   {_ligne_resume(ligne)}")
        justification = _extrait(ligne.justification, 220)
        if justification:
            morceaux.append(f"   {justification}")
        morceaux.append(f"   {_url_chaine(ligne.channel_id)}")
        morceaux.append("")

    morceaux.append("Rapport complet : out/report.md (sur votre PC).")
    if pieces:
        morceaux.append("Pièces jointes : " + ", ".join(pieces) + ".")
    elif reglage.joindre_csv:
        morceaux.append("Aucun CSV à joindre pour ce rapport.")
    if ignorees:
        morceaux.append("Non joint : " + ", ".join(ignorees) + ".")
    morceaux.append("")
    morceaux.append(
        "Envoyé automatiquement par Outly ("
        + _libelle_frequence(reglage.frequence, reglage.heure, reglage.jour_semaine, reglage.jour_mois)
        + "). Aucun identifiant d'envoi ne figure dans ce message."
    )
    return "\n".join(morceaux)


def _pieces(reglage: Reglage) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Lit les CSV disponibles, en respectant les limites de taille.

    Les octets sont renvoyés plutôt qu'ajoutés au message : `set_content()`
    efface tout contenu existant, les pièces jointes doivent donc venir après
    les deux versions du corps.
    """
    pieces: list[tuple[str, bytes]] = []
    ignorees: list[str] = []
    dossier = reglage.dossier_sortie
    if not reglage.joindre_csv or dossier is None:
        return pieces, ignorees
    total = 0
    for nom in ("channels.csv", "outliers.csv"):
        chemin = dossier / nom
        try:
            if not chemin.is_file():
                continue
            taille = chemin.stat().st_size
            contenu = chemin.read_bytes()
        except OSError:
            ignorees.append(f"{nom} (illisible)")
            continue
        if taille > LIMITE_PIECE:
            ignorees.append(f"{nom} ({taille // 1024} Ko, trop volumineux)")
            continue
        if total + taille > LIMITE_TOTAL:
            ignorees.append(f"{nom} (limite d'envoi atteinte)")
            continue
        pieces.append((nom, contenu))
        total += taille
    return pieces, ignorees


def construire_message(
    lignes: list[report.LigneRapport],
    config: Config,
    reglage: Reglage,
    maintenant: datetime | None = None,
    test: bool = False,
    nouveautes: list[report.Nouveaute] | None = None,
    depuis: datetime | None = None,
) -> EmailMessage:
    """Construit le message complet, sans aucun accès réseau."""
    maintenant = maintenant or datetime.now()
    retenues = [ligne for ligne in lignes if ligne.statut == "retenue"]
    prefixe = "[test] " if test else ""
    if retenues:
        sujet = (
            f"{prefixe}Outly · {len(retenues)} chaîne"
            f"{'s' if len(retenues) > 1 else ''} à surveiller"
        )
        if nouveautes:
            sujet += f" · {len(nouveautes)} nouveauté{'s' if len(nouveautes) > 1 else ''}"
        sujet += f" — {maintenant:%d/%m/%Y}"
    else:
        sujet = f"{prefixe}Outly · rapport du {maintenant:%d/%m/%Y}"
        if nouveautes:
            sujet += f" · {len(nouveautes)} nouveauté{'s' if len(nouveautes) > 1 else ''}"

    message = EmailMessage()
    message["From"] = reglage.expediteur
    message["To"] = reglage.destinataire
    message["Subject"] = sujet
    message["Date"] = formatdate(localtime=True)
    message["Content-Language"] = "fr"
    domaine = reglage.expediteur.rsplit("@", 1)[-1] if "@" in reglage.expediteur else None
    message["Message-ID"] = make_msgid(domain=domaine or "outly.local")
    message["X-Mailer"] = "Outly"

    pieces, ignorees = _pieces(reglage)
    noms = [nom for nom, _ in pieces]
    texte = _texte(
        lignes,
        retenues,
        config,
        reglage,
        maintenant,
        test,
        noms,
        ignorees,
        nouveautes,
        depuis,
    )
    riche = _html(
        lignes,
        retenues,
        config,
        reglage,
        maintenant,
        test,
        noms,
        ignorees,
        nouveautes,
        depuis,
    )
    message.set_content(texte)
    message.add_alternative(riche, subtype="html")
    for nom, contenu in pieces:
        message.add_attachment(contenu, maintype="text", subtype="csv", filename=nom)
    return message


def preparer(
    conn,
    config: Config,
    test: bool = False,
    maintenant: datetime | None = None,
    depuis: datetime | None = None,
) -> tuple[EmailMessage, int]:
    """Charge les lignes du rapport puis construit le message.

    Renvoie (message, nombre de chaînes retenues) : ce nombre est mémorisé avec
    la date d'envoi pour l'afficher dans l'interface.
    """
    reglage_actif = reglage(config)
    problemes = valider(reglage_actif)
    if problemes:
        raise ErreurEmail("Configuration e-mail incomplète :\n- " + "\n- ".join(problemes))
    lignes = report.charger_lignes(conn, config)
    if not lignes:
        raise ErreurEmail(
            "Aucune chaîne évaluée dans la base : lancez d'abord une veille "
            "(ou attendez la prochaine exécution), le rapport serait vide."
        )
    retenues = sum(1 for ligne in lignes if ligne.statut == "retenue")
    try:
        dernieres = report.nouveautes(conn, config, depuis=depuis)
    except sqlite3.Error:
        dernieres = []
    message = construire_message(
        lignes,
        config,
        reglage_actif,
        maintenant=maintenant,
        test=test,
        nouveautes=dernieres,
        depuis=depuis,
    )
    return message, retenues


def envoyer(reglage: Reglage, message: EmailMessage, transport=None) -> None:
    """Envoie le message via SMTP, ou via `transport` (tests).

    `transport` reçoit (réglage, message) et ne doit pas effectuer de réseau.
    Les erreurs ne citent jamais le mot de passe.
    """
    problemes = valider(reglage)
    if problemes:
        raise ErreurEmail("Configuration e-mail incomplète :\n- " + "\n- ".join(problemes))
    if transport is not None:
        transport(reglage, message)
        return

    contexte = ssl.create_default_context()
    try:
        if reglage.ssl:
            with smtplib.SMTP_SSL(
                reglage.hote, reglage.port, timeout=45, context=contexte
            ) as serveur:
                serveur.login(reglage.utilisateur, reglage.mot_de_passe)
                serveur.send_message(
                    message, from_addr=reglage.expediteur, to_addrs=[reglage.destinataire]
                )
        else:
            with smtplib.SMTP(reglage.hote, reglage.port, timeout=45) as serveur:
                serveur.ehlo()
                serveur.starttls(context=contexte)
                serveur.ehlo()
                serveur.login(reglage.utilisateur, reglage.mot_de_passe)
                serveur.send_message(
                    message, from_addr=reglage.expediteur, to_addrs=[reglage.destinataire]
                )
    except smtplib.SMTPAuthenticationError as probleme:
        raise ErreurEmail(
            f"Le serveur {reglage.hote} a refusé l'identification. Vérifiez "
            "SMTP_PASSWORD : Gmail exige un mot de passe d'application, pas celui "
            "du compte."
        ) from probleme
    except (smtplib.SMTPConnectError, OSError) as probleme:
        raise ErreurEmail(
            f"Connexion impossible à {reglage.hote}:{reglage.port} ({probleme}). "
            "Vérifiez l'adresse du serveur, le port et la connexion Internet."
        ) from probleme
    except smtplib.SMTPException as probleme:
        raise ErreurEmail(f"Envoi refusé par {reglage.hote} : {probleme}") from probleme
