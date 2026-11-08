"""Génération des sorties : `channels.csv`, `outliers.csv` et `report.md`.

Le classement ne dépend jamais du LLM : une chaîne non scorée reste classable
par l'heuristique de recouvrement de mots-clés, et le rapport l'indique
explicitement. C'est ce qui garantit qu'une panne de fournisseur gratuit ne
détruit pas la valeur d'une exécution.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import proximite
from .config import Config
from .models import Metriques, date_iso
from .quota import COMPARTIMENT_PRINCIPAL, COMPARTIMENT_RECHERCHE, jour_pacifique

# Au-delà, la chaîne est considérée comme inactive dans le rapport.
INACTIVITE_JOURS = 365


@contextmanager
def _ecriture_atomique(chemin: Path, encoding: str = "utf-8", newline: str | None = None):
    """Écrit à côté puis remplace : un rapport existant n'est jamais tronqué."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(f".{chemin.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporaire.open("w", encoding=encoding, newline=newline) as fichier:
            yield fichier
        os.replace(temporaire, chemin)
    finally:
        try:
            temporaire.unlink(missing_ok=True)
        except OSError:
            pass


def _ecrire_texte_atomique(chemin: Path, texte: str) -> None:
    with _ecriture_atomique(chemin, encoding="utf-8") as fichier:
        fichier.write(texte)


@dataclass
class LigneRapport:
    channel_id: str
    titre: str
    handle: str | None
    abonnes: int | None
    nb_videos: int | None
    cree_le: str | None
    est_seed: bool
    statut_base: str
    raison_ecart: str | None
    score_heuristique: float | None
    score: int | None
    recouvrement: str | None
    format_detecte: str | None
    langue: str | None
    justification: str | None
    risques: list[str] = field(default_factory=list)
    metriques: Metriques | None = None
    statut: str = "ecartee"
    raison: str | None = None
    score_local: int | None = None
    termes_communs: list[str] = field(default_factory=list)
    complement: bool = False
    # Fiche détaillée, affichée dans l'interface sans aucun appel réseau.
    description: str | None = None
    pays: str | None = None
    vues_totales: int | None = None
    mots_cles: str | None = None
    playlist_uploads: str | None = None

    @property
    def score_affiche(self) -> int | None:
        """Score montré à l'utilisateur : LLM s'il existe, sinon local."""
        return self.score if self.score is not None else self.score_local

    @property
    def source_score(self) -> str:
        if self.score is not None:
            return "llm"
        if self.score_local is not None:
            return "local"
        return "heuristique" if self.statut == "retenue" else "aucun"

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/channel/{self.channel_id}"


def _nombre(valeur: int | float | None) -> str:
    if valeur is None:
        return "—"
    if isinstance(valeur, float) and not valeur.is_integer():
        return f"{valeur:,.1f}".replace(",", " ").replace(".", ",")
    return f"{int(valeur):,}".replace(",", " ")


def _date_courte(valeur: str | None) -> str:
    date = date_iso(valeur)
    return date.strftime("%Y-%m-%d") if date else "—"


def _date_longue() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _accord(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


def _csv_nombre(valeur, separateur: str):
    """Nombre lisible par Excel : virgule décimale quand le séparateur est « ; »."""
    if valeur is None or valeur == "":
        return ""
    if isinstance(valeur, float):
        texte = f"{valeur:.2f}".rstrip("0").rstrip(".")
        return texte.replace(".", ",") if separateur == ";" else texte
    return valeur


# Seuil minimal pour parler d'« outlier » : une vidéo à la médiane ou en dessous
# n'a rien d'exceptionnel, même si elle fait partie des « moins mauvaises ».
SEUIL_OUTLIER = 1.0


def charger_lignes(conn: sqlite3.Connection, config: Config) -> list[LigneRapport]:
    """Charge toutes les chaînes évaluables et applique la règle de rétention."""
    lignes = conn.execute(
        """
        SELECT c.channel_id, c.handle, c.titre, c.abonnes, c.nb_videos, c.cree_le,
               c.description, c.pays, c.vues_totales, c.mots_cles_chaine,
               c.playlist_uploads, c.est_seed, c.statut AS statut_base,
               c.raison_ecart, c.score_heuristique,
               m.channel_id AS m_channel_id, m.fenetre, m.n_videos, m.n_shorts, m.n_longues,
               m.mediane_vues_short, m.mediane_vues_longues, m.cadence_semaine,
               m.jours_depuis_publication, m.meilleur_outlier, m.mix_format,
               s.score_proximite, s.recouvrement_audience, s.format_detecte, s.langue,
               s.justification, s.risques_json
        FROM channels c
        LEFT JOIN channel_metrics m ON m.channel_id = c.channel_id
        LEFT JOIN channel_scores s ON s.id = (
            SELECT id FROM channel_scores
            WHERE channel_id = c.channel_id AND valide = 1
            ORDER BY id DESC LIMIT 1
        )
        WHERE c.statut IN ('enrichie', 'scored', 'ecartee')
        """
    ).fetchall()

    scores_locaux = proximite.scorer(conn, config)
    resultat: list[LigneRapport] = []
    for ligne in lignes:
        metriques = None
        if ligne["m_channel_id"]:
            metriques = Metriques(
                channel_id=ligne["channel_id"],
                fenetre=ligne["fenetre"],
                n_videos=ligne["n_videos"],
                n_shorts=ligne["n_shorts"],
                n_longues=ligne["n_longues"],
                mediane_vues_short=ligne["mediane_vues_short"],
                mediane_vues_longues=ligne["mediane_vues_longues"],
                cadence_semaine=ligne["cadence_semaine"],
                jours_depuis_publication=ligne["jours_depuis_publication"],
                meilleur_outlier=ligne["meilleur_outlier"],
                mix_format=ligne["mix_format"],
            )
        element = LigneRapport(
            channel_id=ligne["channel_id"],
            titre=ligne["titre"],
            handle=ligne["handle"],
            abonnes=ligne["abonnes"],
            nb_videos=ligne["nb_videos"],
            cree_le=ligne["cree_le"],
            est_seed=bool(ligne["est_seed"]),
            statut_base=ligne["statut_base"],
            raison_ecart=ligne["raison_ecart"],
            score_heuristique=ligne["score_heuristique"],
            score=ligne["score_proximite"],
            recouvrement=ligne["recouvrement_audience"],
            format_detecte=ligne["format_detecte"],
            langue=ligne["langue"],
            justification=ligne["justification"],
            risques=json.loads(ligne["risques_json"]) if ligne["risques_json"] else [],
            metriques=metriques,
            score_local=(
                scores_locaux[ligne["channel_id"]].score
                if ligne["channel_id"] in scores_locaux
                else None
            ),
            termes_communs=(
                list(scores_locaux[ligne["channel_id"]].termes_communs)
                if ligne["channel_id"] in scores_locaux
                else []
            ),
            description=ligne["description"],
            pays=ligne["pays"],
            vues_totales=ligne["vues_totales"],
            mots_cles=ligne["mots_cles_chaine"],
            playlist_uploads=ligne["playlist_uploads"],
        )
        _decider(element, config)
        resultat.append(element)

    _garantir_minimum(resultat, config)
    resultat.sort(
        key=lambda element: (
            0 if element.statut == "retenue" else 1,
            -(element.score_affiche if element.score_affiche is not None else -1),
            -(element.score_heuristique or 0),
        )
    )
    return resultat


@dataclass
class Nouveaute:
    """Vidéo récente d'une chaîne suivie, mise en avant dans le rapport e-mail."""

    video_id: str
    titre: str
    chaine: str
    channel_id: str
    publie_le: str | None
    vues: int | None
    est_short: bool | None
    score_outlier: float | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def jours(self) -> int | None:
        """Ancienneté en jours, arrondie ; None si la date est illisible."""
        publiee = _date_video(self.publie_le)
        if publiee is None:
            return None
        return max(0, (datetime.now().date() - publiee.date()).days)


def _date_video(valeur: str | None) -> datetime | None:
    """Date de publication (UTC en base) ramenée en heure locale."""
    if not valeur:
        return None
    try:
        return (
            datetime.fromisoformat(valeur.replace("Z", "+00:00"))
            .astimezone()
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


def nouveautes(
    conn: sqlite3.Connection,
    config: Config,
    depuis: datetime | None = None,
    limite: int = 6,
) -> list[Nouveaute]:
    """Dernières vidéos des chaînes suivies, les plus récentes d'abord.

    `depuis` est la date du rapport précédent : chaque envoi ne parle donc que
    de ce qui est sorti depuis. Sans borne (premier rapport, message de test),
    on regarde les sept derniers jours.
    """
    borne = depuis or (datetime.now() - timedelta(days=7))
    lignes = conn.execute(
        """
        SELECT v.video_id, v.titre, v.publie_le, v.vues, v.est_short,
               v.channel_id, c.titre AS chaine,
               CASE
                   WHEN v.est_short = 1 AND m.mediane_vues_short   > 0
                       THEN ROUND(1.0 * v.vues / m.mediane_vues_short, 3)
                   WHEN v.est_short = 0 AND m.mediane_vues_longues > 0
                       THEN ROUND(1.0 * v.vues / m.mediane_vues_longues, 3)
               END AS score_outlier
        FROM videos v
        JOIN channels c ON c.channel_id = v.channel_id
        LEFT JOIN channel_metrics m ON m.channel_id = v.channel_id
        WHERE c.statut IN ('enrichie', 'scored', 'ecartee')
          AND v.live = 'none'
          AND v.publie_le IS NOT NULL
        ORDER BY v.publie_le DESC
        LIMIT 160
        """
    ).fetchall()

    resultat: list[Nouveaute] = []
    for ligne in lignes:
        publiee = _date_video(ligne["publie_le"])
        if publiee is None or publiee < borne:
            continue
        resultat.append(
            Nouveaute(
                video_id=ligne["video_id"],
                titre=ligne["titre"],
                chaine=ligne["chaine"],
                channel_id=ligne["channel_id"],
                publie_le=ligne["publie_le"],
                vues=ligne["vues"],
                est_short=bool(ligne["est_short"]) if ligne["est_short"] is not None else None,
                score_outlier=ligne["score_outlier"],
            )
        )
        if len(resultat) >= limite:
            break
    return resultat


def _decider(element: LigneRapport, config: Config) -> None:
    """Applique les règles de rétention, dans l'ordre, avec une raison lisible."""
    if element.statut_base == "ecartee":
        element.statut = "ecartee"
        element.raison = element.raison_ecart or "écartée à l'enrichissement"
        return
    if element.metriques is None:
        element.statut = "ecartee"
        element.raison = "aucune métrique calculée"
        return
    if element.metriques.n_videos < config.metriques.min_videos_for_metrics:
        element.statut = "ecartee"
        element.raison = (
            f"données insuffisantes ({element.metriques.n_videos} vidéos, "
            f"minimum {config.metriques.min_videos_for_metrics})"
        )
        return
    if (
        element.metriques.jours_depuis_publication is not None
        and element.metriques.jours_depuis_publication > INACTIVITE_JOURS
    ):
        element.statut = "ecartee"
        element.raison = "chaîne inactive depuis plus d'un an"
        return
    if element.score is not None:
        if element.score >= config.llm.min_score_keep:
            element.statut = "retenue"
            element.raison = None
        else:
            element.statut = "ecartee"
            element.raison = (
                f"score de proximité {element.score} < seuil {config.llm.min_score_keep}"
            )
        return
    if element.score_local is not None:
        if element.score_local >= config.classement.seuil_local:
            element.statut = "retenue"
            element.raison = None
        else:
            element.statut = "ecartee"
            element.raison = (
                f"proximité locale {element.score_local} < seuil "
                f"{config.classement.seuil_local}"
            )
        return
    if (element.score_heuristique or 0) >= 0.5:
        element.statut = "retenue"
        element.raison = None
    else:
        element.statut = "ecartee"
        element.raison = (
            "non évaluée par le LLM et recouvrement de mots-clés faible "
            "(renseignez « ma chaîne » pour activer le score local)"
        )


def _eligible(element: LigneRapport, config: Config) -> bool:
    """Chaîne analysable : assez de vidéos, toujours active, non écartée."""
    if element.statut_base == "ecartee" or element.metriques is None:
        return False
    if element.metriques.n_videos < config.metriques.min_videos_for_metrics:
        return False
    if (
        element.metriques.jours_depuis_publication is not None
        and element.metriques.jours_depuis_publication > INACTIVITE_JOURS
    ):
        return False
    return True


def _garantir_minimum(lignes: list[LigneRapport], config: Config) -> None:
    """Complète la sélection pour atteindre `classement.min_retenues`.

    Les règles de seuil restent prioritaires. Si elles laissent moins de
    chaînes que le minimum demandé, les meilleures chaînes analysables sont
    promues, avec la mention « complément » dans le rapport : l'utilisateur
    voit ainsi le meilleur du corpus même quand le LLM est indisponible.
    Le minimum n'est appliqué que si le corpus contient assez de chaînes
    analysables — on ne présente jamais comme concurrent une chaîne dont les
    données sont insuffisantes ou qui est inactive.
    """
    minimum = config.classement.min_retenues
    if minimum <= 0:
        return
    eligibles = [ligne for ligne in lignes if _eligible(ligne, config)]
    if len(eligibles) < minimum:
        return
    retenues = [ligne for ligne in lignes if ligne.statut == "retenue"]
    if len(retenues) >= minimum:
        return

    reste = [ligne for ligne in eligibles if ligne.statut != "retenue"]
    reste.sort(
        key=lambda ligne: (
            -(ligne.score_affiche if ligne.score_affiche is not None else -1),
            -(ligne.score_heuristique or 0),
        )
    )
    for ligne in reste[: minimum - len(retenues)]:
        ligne.statut = "retenue"
        ligne.raison = None
        ligne.complement = True


def _outliers(conn: sqlite3.Connection, channel_id: str, limite: int = 3) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM v_outliers WHERE channel_id = ? AND score_outlier > ? "
        "ORDER BY score_outlier DESC LIMIT ?",
        (channel_id, SEUIL_OUTLIER, limite),
    ).fetchall()


def _quota_du_jour(conn: sqlite3.Connection) -> tuple[int, int]:
    lignes = conn.execute(
        "SELECT compartiment, SUM(unites) AS unites FROM api_calls "
        "WHERE jour = ? AND depuis_cache = 0 GROUP BY compartiment",
        (jour_pacifique(),),
    ).fetchall()
    principal = recherche = 0
    for ligne in lignes:
        if ligne["compartiment"] == COMPARTIMENT_RECHERCHE:
            recherche = int(ligne["unites"] or 0)
        elif ligne["compartiment"] == COMPARTIMENT_PRINCIPAL:
            principal = int(ligne["unites"] or 0)
    return principal, recherche


def ecrire_channels_csv(chemin: Path, lignes: list[LigneRapport], config: Config) -> None:
    entetes = [
        "rang",
        "statut",
        "raison",
        "channel_id",
        "titre",
        "handle",
        "url",
        "score_proximite",
        "score_local",
        "source_score",
        "termes_communs",
        "recouvrement_audience",
        "format_detecte",
        "langue",
        "abonnes",
        "nb_videos",
        "cree_le",
        "videos_semaine",
        "part_shorts",
        "mediane_vues_longues",
        "mediane_vues_short",
        "meilleur_outlier",
        "justification",
        "risques",
    ]
    with _ecriture_atomique(chemin, encoding=config.sortie.csv_encodage, newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=config.sortie.csv_separateur, lineterminator="\n")
        ecrivain.writerow(entetes)
        for rang, ligne in enumerate(lignes, start=1):
            part = ligne.metriques.part_shorts if ligne.metriques else None
            ecrivain.writerow(
                [
                    rang,
                    "retenue" if ligne.statut == "retenue" else "ecartee",
                    ligne.raison or "",
                    ligne.channel_id,
                    ligne.titre,
                    ligne.handle or "",
                    ligne.url,
                    ligne.score if ligne.score is not None else "",
                    ligne.score_local if ligne.score_local is not None else "",
                    ligne.source_score,
                    ", ".join(ligne.termes_communs),
                    ligne.recouvrement or "",
                    ligne.format_detecte or "",
                    ligne.langue or "",
                    ligne.abonnes if ligne.abonnes is not None else "",
                    ligne.nb_videos if ligne.nb_videos is not None else "",
                    ligne.cree_le or "",
                    _csv_nombre(
                        ligne.metriques.cadence_semaine if ligne.metriques else None,
                        config.sortie.csv_separateur,
                    ),
                    _csv_nombre(part, config.sortie.csv_separateur),
                    _csv_nombre(
                        ligne.metriques.mediane_vues_longues if ligne.metriques else None,
                        config.sortie.csv_separateur,
                    ),
                    _csv_nombre(
                        ligne.metriques.mediane_vues_short if ligne.metriques else None,
                        config.sortie.csv_separateur,
                    ),
                    _csv_nombre(
                        ligne.metriques.meilleur_outlier if ligne.metriques else None,
                        config.sortie.csv_separateur,
                    ),
                    (ligne.justification or "").replace("\n", " "),
                    " | ".join(ligne.risques),
                ]
            )


def ecrire_outliers_csv(
    chemin: Path, conn: sqlite3.Connection, lignes: list[LigneRapport], config: Config
) -> int:
    entetes = [
        "chaine",
        "channel_id",
        "score_chaine",
        "rang_outlier",
        "titre_video",
        "url_video",
        "format",
        "duree_s",
        "vues",
        "score_outlier",
        "publie_le",
    ]
    total = 0
    with _ecriture_atomique(chemin, encoding=config.sortie.csv_encodage, newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=config.sortie.csv_separateur, lineterminator="\n")
        ecrivain.writerow(entetes)
        for ligne in lignes:
            if ligne.statut != "retenue":
                continue
            for rang, video in enumerate(_outliers(conn, ligne.channel_id, 3), start=1):
                ecrivain.writerow(
                    [
                        ligne.titre,
                        ligne.channel_id,
                        ligne.score if ligne.score is not None else "",
                        rang,
                        video["titre"],
                        f"https://www.youtube.com/watch?v={video['video_id']}",
                        "short" if video["est_short"] == 1 else "longue",
                        video["duree_s"] or "",
                        video["vues"] or "",
                        _csv_nombre(video["score_outlier"], config.sortie.csv_separateur),
                        _date_courte(video["publie_le"]),
                    ]
                )
                total += 1
    return total


def ecrire_rapport(
    chemin: Path,
    lignes: list[LigneRapport],
    conn: sqlite3.Connection,
    config: Config,
    analyses: dict[str, dict] | None = None,
) -> None:
    analyses = analyses or {}
    retenues = [ligne for ligne in lignes if ligne.statut == "retenue"]
    ecartees = [ligne for ligne in lignes if ligne.statut == "ecartee"]
    principal, recherche = _quota_du_jour(conn)

    morceaux: list[str] = []
    morceaux.append("# Veille concurrentielle YouTube")
    morceaux.append("")
    morceaux.append(f"Rapport généré le {_date_longue()}.")
    morceaux.append("")
    morceaux.append(
        f"**{_accord(len(lignes), 'chaîne évaluée', 'chaînes évaluées')}** · "
        f"{len(retenues)} retenues · {len(ecartees)} écartées · "
        f"seuils : LLM {config.llm.min_score_keep}/100 · local "
        f"{config.classement.seuil_local}/100."
    )
    morceaux.append("")
    morceaux.append(
        f"Quota consommé aujourd'hui : {principal} unités du pool principal "
        f"(budget {config.youtube.max_units_per_day}) et {recherche} appels de recherche "
        f"(budget {config.youtube.max_search_calls_per_day})."
    )
    morceaux.append("")

    # --- Synthèse ---------------------------------------------------------
    morceaux.append("## Synthèse")
    morceaux.append("")
    if retenues:
        meilleure = retenues[0]
        valeur = meilleure.score_affiche
        source = (
            "score LLM"
            if meilleure.score is not None
            else "proximité locale"
            if meilleure.score_local is not None
            else "classement heuristique"
        )
        morceaux.append(
            f"- Meilleure correspondance : **{meilleure.titre}** "
            f"({valeur}/100, {source})."
            if valeur is not None
            else f"- Meilleure correspondance : **{meilleure.titre}** ({source})."
        )
    locales = [
        ligne for ligne in retenues if ligne.score is None and ligne.score_local is not None
    ]
    if locales:
        morceaux.append(
            f"- {_accord(len(locales), 'chaîne retenue par le score local', 'chaînes retenues par le score local')} "
            "(LLM indisponible), à confirmer après un scoring complet."
        )
    non_scorees = [
        ligne for ligne in retenues if ligne.score is None and ligne.score_local is None
    ]
    if non_scorees:
        morceaux.append(
            f"- {_accord(len(non_scorees), 'chaîne retenue sans score LLM', 'chaînes retenues sans score LLM')} : "
            "classement heuristique, à confirmer après un scoring complet."
        )
    complements = [ligne for ligne in retenues if ligne.complement]
    if complements:
        morceaux.append(
            f"- {_accord(len(complements), 'chaîne ajoutée en complément', 'chaînes ajoutées en complément')} "
            f"pour atteindre le minimum de {config.classement.min_retenues}."
        )
    sans_metriques = [
        ligne for ligne in ecartees if ligne.raison and "données insuffisantes" in ligne.raison
    ]
    if sans_metriques:
        morceaux.append(
            f"- {_accord(len(sans_metriques), 'chaîne sans', 'chaînes sans')} assez de vidéos publiques."
        )
    inactives = [ligne for ligne in ecartees if ligne.raison and "inactive" in ligne.raison]
    if inactives:
        morceaux.append(
            f"- {_accord(len(inactives), 'chaîne inactive', 'chaînes inactives')} depuis plus d'un an."
        )
    sous_seuil = [ligne for ligne in ecartees if ligne.raison and "score de proximité" in ligne.raison]
    if sous_seuil:
        morceaux.append(
            f"- {_accord(len(sous_seuil), 'chaîne sous', 'chaînes sous')} le seuil de proximité."
        )
    morceaux.append("")

    # --- Top chaînes ------------------------------------------------------
    morceaux.append(f"## Top {min(len(retenues), config.sortie.report_top)} chaînes")
    morceaux.append("")
    if not retenues:
        morceaux.append(
            "_Aucune chaîne ne dépasse le seuil. Élargissez `keywords.txt`, "
            "abaissez `llm.min_score_keep`, ou lancez le scoring._"
        )
        morceaux.append("")

    for position, ligne in enumerate(retenues[: config.sortie.report_top], start=1):
        metriques = ligne.metriques
        score_txt = (
            f"{ligne.score_affiche}/100" if ligne.score_affiche is not None else "score absent"
        )
        morceaux.append(f"### {position}. {ligne.titre} — {score_txt}")
        morceaux.append("")
        details = [
            f"{_nombre(ligne.abonnes)} abonnés" if ligne.abonnes is not None else "abonnés masqués",
            f"{_nombre(ligne.nb_videos)} vidéos",
            f"créée le {_date_courte(ligne.cree_le)}",
        ]
        if ligne.est_seed:
            details.append("seed")
        morceaux.append("- " + " · ".join(details))
        morceaux.append(f"- [Chaîne]({ligne.url})" + (f" · `{ligne.handle}`" if ligne.handle else ""))
        if metriques:
            morceaux.append(
                f"- Cadence : {_nombre(metriques.cadence_semaine)} vidéos/semaine · "
                f"{_accord(metriques.n_shorts, 'Short', 'Shorts')} / "
                f"{_accord(metriques.n_longues, 'vidéo longue', 'vidéos longues')} · "
                f"dernière publication il y a {_nombre(metriques.jours_depuis_publication)} jours"
            )
            morceaux.append(
                f"- Médiane des vues : {_nombre(metriques.mediane_vues_longues)} (longues) · "
                f"{_nombre(metriques.mediane_vues_short)} (Shorts)"
            )
        if ligne.score is not None:
            morceaux.append(
                f"- Recouvrement d'audience : **{ligne.recouvrement}** · "
                f"format : **{ligne.format_detecte}** · langue : **{ligne.langue}**"
            )
        elif ligne.score_local is not None:
            morceaux.append(
                f"- Proximité locale : **{ligne.score_local}/100** "
                "(LLM indisponible, calcul hors ligne)."
            )
            if ligne.termes_communs:
                morceaux.append(
                    "- Thèmes partagés avec ma chaîne : "
                    + ", ".join(f"**{terme}**" for terme in ligne.termes_communs[:6])
                    + "."
                )
        else:
            morceaux.append(
                "- Score absent : classement par recouvrement de mots-clés "
                f"({_nombre(ligne.score_heuristique)})."
            )
        if ligne.complement:
            morceaux.append(
                "- **Retenue de complément** : meilleure chaîne disponible pour "
                f"atteindre le minimum de {config.classement.min_retenues}."
            )
        if ligne.justification:
            morceaux.append("")
            morceaux.append(f"> {ligne.justification}")
        if ligne.risques:
            morceaux.append("")
            morceaux.append("**Risques et doutes**")
            for risque in ligne.risques:
                morceaux.append(f"- {risque}")
        morceaux.append("")

        videos = _outliers(conn, ligne.channel_id, 3)
        if videos:
            morceaux.append("**Meilleurs outliers**")
            morceaux.append("")
            morceaux.append("| # | Vidéo | Format | Vues | × médiane | Publiée |")
            morceaux.append("|---|---|---|---|---|---|")
            for rang, video in enumerate(videos, start=1):
                titre = str(video["titre"]).replace("|", "\\|")
                lien = f"[{titre}](https://www.youtube.com/watch?v={video['video_id']})"
                morceaux.append(
                    f"| {rang} | {lien} | "
                    f"{'Short' if video['est_short'] == 1 else 'longue'} | "
                    f"{_nombre(video['vues'])} | ×{_nombre(video['score_outlier'])} | "
                    f"{_date_courte(video['publie_le'])} |"
                )
            morceaux.append("")
            premiere = videos[0]["video_id"]
            analyse = analyses.get(premiere)
            if analyse:
                morceaux.append(f"**Pourquoi « {videos[0]['titre']} » a marché**")
                morceaux.append("")
                morceaux.append(f"- Accroche : {analyse.get('accroche', '—')}")
                morceaux.append(f"- Explication : {analyse.get('explication', '—')}")
                for etape in analyse.get("structure") or []:
                    morceaux.append(f"  - {etape.get('etape', '')} : {etape.get('description', '')}")
                morceaux.append("")

    # --- Écartées ---------------------------------------------------------
    morceaux.append("## Chaînes écartées")
    morceaux.append("")
    if not ecartees:
        morceaux.append("_Aucune chaîne écartée._")
    else:
        morceaux.append("| Chaîne | Score | Raison |")
        morceaux.append("|---|---|---|")
        for ligne in ecartees:
            score = (
                ligne.score
                if ligne.score is not None
                else f"local {ligne.score_local}"
                if ligne.score_local is not None
                else f"heuristique {_nombre(ligne.score_heuristique)}"
            )
            titre = str(ligne.titre).replace("|", "\\|")
            morceaux.append(f"| [{titre}]({ligne.url}) | {score} | {ligne.raison or '—'} |")
    morceaux.append("")

    # --- Méthode ----------------------------------------------------------
    morceaux.append("## Méthode")
    morceaux.append("")
    morceaux.append(
        f"- **Outlier** : vues ÷ médiane des vues du **même format** (Shorts ≤ "
        f"{config.metriques.short_max_seconds} s, vidéos longues au-delà). "
        "Une médiane nulle ou absente ne produit jamais de division."
    )
    morceaux.append(
        "- **Score de proximité LLM** : sortie JSON validée par schéma, "
        f"prompt `{config.llm_modele or 'non configuré'}`, température {config.llm.temperature}."
    )
    morceaux.append(
        "- **Proximité locale (repli)** : recouvrement hors ligne des mots-clés, du "
        "positionnement et des titres de ma chaîne avec les descriptions et les titres "
        "des candidats. Utilisée quand aucun score LLM n'est disponible. Au moins "
        f"{config.classement.min_retenues} chaînes analysables sont retenues : les "
        "meilleures complètent la sélection si les seuils n'en donnent pas assez."
    )
    morceaux.append(
        "- **Données** : API YouTube Data v3 uniquement (playlist « uploads » pour lister "
        "les vidéos, jamais `search.list`). Les transcriptions sont optionnelles et non "
        "officielles."
    )
    morceaux.append("")

    chemin.write_text("\n".join(morceaux), encoding="utf-8")


def generer(conn: sqlite3.Connection, config: Config) -> dict:
    """Point d'entrée : écrit les trois sorties et renvoie un résumé."""
    config.dossier_sortie.mkdir(parents=True, exist_ok=True)
    lignes = charger_lignes(conn, config)

    chemin_channels = config.dossier_sortie / "channels.csv"
    chemin_outliers = config.dossier_sortie / "outliers.csv"
    chemin_rapport = config.dossier_sortie / "report.md"

    ecrire_channels_csv(chemin_channels, lignes, config)
    nombre_outliers = ecrire_outliers_csv(chemin_outliers, conn, lignes, config)

    from .outliers import extraire_analyses

    analyses: dict[str, dict] = {}
    for ligne in lignes:
        if ligne.statut == "retenue":
            analyses.update(extraire_analyses(conn, ligne.channel_id))

    ecrire_rapport(chemin_rapport, lignes, conn, config, analyses)

    return {
        "channels": chemin_channels,
        "outliers_csv": chemin_outliers,
        "rapport": chemin_rapport,
        "evaluees": len(lignes),
        "retenues": sum(1 for ligne in lignes if ligne.statut == "retenue"),
        "nb_outliers": nombre_outliers,
    }
