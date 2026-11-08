"""Prompts, extraction et validation des réponses du LLM.

Tout est en français, y compris les consignes envoyées au modèle. Le schéma de
sortie est strict (`additionalProperties: false`) et vérifié localement avec
`jsonschema` : le format n'est jamais supposé correct.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

from ..models import Chaine, Metriques, ScoreChaine, Video, date_iso

PROMPT_VERSION = "score-v1"
PROMPT_OUTLIER_VERSION = "outlier-v1"

DOSSIER_SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"

PROMPT_SYSTEME = """Tu es un analyste concurrentiel qui évalue des chaînes YouTube francophones pour une marque.
Tu reçois le positionnement de la marque, puis les données publiques d'une chaîne concurrente.
Tu mesures la proximité de niche entre les deux.

Règles absolues :
1. Réponds UNIQUEMENT par un objet JSON valide. Aucun texte avant, aucun texte après,
   aucune balise Markdown, aucun commentaire.
2. N'invente aucun chiffre : n'utilise que les données fournies.
3. Le score mesure le recouvrement réel (sujet + promesse + audience), pas la qualité
   ni la taille de la chaîne.
4. Si les données sont insuffisantes pour juger, donne un score ≤ 30 et explique-le
   dans "risques".
5. "justification" fait exactement 2 phrases, en français, chacune appuyée sur un élément
   concret fourni (sujet, format, audience, outlier).
6. "risques" contient 0 à 4 constats factuels : sujet exclu abordé, audience trop large,
   format incompatible, langue, chaîne inactive. Aucun conseil marketing.
7. Respecte strictement les valeurs autorisées des champs énumérés."""

PROMPT_SYSTEME_OUTLIER = """Tu es un analyste de contenu YouTube. Tu expliques pourquoi une vidéo précise
a nettement dépassé la performance habituelle de sa chaîne.

Règles absolues :
1. Réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni balise Markdown.
2. Appuie chaque affirmation sur les données fournies (titre, durée, vues, ratio,
   transcription). Si la transcription est absente, dis-le dans "explication" et
   reste prudent : tu ne peux pas analyser l'accroche réelle.
3. Ne recopie pas la transcription ; synthétise.
4. Tout est rédigé en français."""

GABARIT_UTILISATEUR = """## 1. POSITIONNEMENT DE LA MARQUE
{positioning}

## 2. CHAÎNE À ÉVALUER
Titre : {titre}
Handle : {handle}
Abonnés : {abonnes}
Vidéos publiées : {nb_videos}
Créée le : {cree_le}
Pays déclaré : {pays}
Mots-clés déclarés par la chaîne : {mots_cles}
Description :
<<<
{description}
>>>

## 3. DERNIÈRES VIDÉOS (max 10, les plus récentes)
{videos}

## 4. MEILLEURS OUTLIERS (vues ÷ médiane du même format)
{outliers}

## 5. REPÈRES CHIFFRÉS
Médiane des vues, vidéos longues : {mediane_longues}
Médiane des vues, Shorts : {mediane_shorts}
Cadence : {cadence} vidéos/semaine
Répartition : {n_shorts} Shorts / {n_longues} vidéos longues
Dernière publication : il y a {jours} jours

## 6. GRILLE DE NOTATION
score_proximite (entier 0-100) :
  0-20   audience ou sujet différents
  21-40  même thème général, promesse ou audience différentes
  41-60  concurrence partielle (sujets qui se recoupent, format ou audience différent)
  61-80  concurrent direct (même audience, même promesse, format proche)
  81-100 quasi-substitut (même promesse, mêmes sujets, même format, même audience)
recouvrement_audience : "faible" | "partiel" | "fort"
format_detecte : "voix_off" | "face_camera" | "mixte" | "autre" | "indetermine"
  (déduis-le des titres, durées, descriptions ; ne devine pas au hasard)
langue : "fr" | "en" | "autre"

## 7. SORTIE
Un unique objet JSON, rien d'autre :
{{"score_proximite": <entier 0-100>, "recouvrement_audience": "<faible|partiel|fort>",
 "format_detecte": "<voix_off|face_camera|mixte|autre|indetermine>", "langue": "<fr|en|autre>",
 "justification": "<exactement 2 phrases>", "risques": ["<constat>", "..."]}}"""

GABARIT_OUTLIER = """## 1. VIDÉO ANALYSÉE
Titre : {titre}
Chaîne : {chaine}
Durée : {duree}
Publiée le : {date}
Vues : {vues}
Performance : {ratio}× la médiane des vidéos du même format ({mediane} vues)

## 2. TRANSCRIPTION ({etat_transcription})
<<<
{transcription}
>>>

## 3. CE QUE J'ATTENDS
- "accroche" : ce qui retient l'attention dans les premières secondes (ou, sans
  transcription, l'angle annoncé par le titre).
- "structure" : 2 à 8 étapes du déroulé, dans l'ordre.
- "explication" : hypothèses sur ce qui explique la performance au-dessus de la
  médiane de la chaîne.
- "reutilisable" : 0 à 5 principes transposables à une autre chaîne de la même niche.

## 4. SORTIE
Un unique objet JSON :
{{"accroche": "<texte>", "structure": [{{"etape": "<titre court>", "description": "<texte>"}}],
 "explication": "<texte>", "reutilisable": ["<principe>"]}}"""


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------


def formater_nombre(valeur: int | float | None) -> str:
    if valeur is None:
        return "inconnu"
    if isinstance(valeur, float) and not valeur.is_integer():
        return f"{valeur:,.2f}".replace(",", " ").replace(".", ",")
    return f"{int(valeur):,}".replace(",", " ")


def formater_duree(secondes: int | None) -> str:
    if secondes is None:
        return "durée inconnue"
    heures, reste = divmod(int(secondes), 3600)
    minutes, sec = divmod(reste, 60)
    if heures:
        return f"{heures}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def formater_date(valeur: str | None) -> str:
    date = date_iso(valeur)
    return date.strftime("%Y-%m-%d") if date else "date inconnue"


def _ratio(video: Video, metriques: Metriques) -> float | None:
    if video.est_short is None:
        return None
    base = metriques.mediane_vues_short if video.est_short == 1 else metriques.mediane_vues_longues
    if not base or base <= 0 or video.vues is None:
        return None
    return round(video.vues / base, 2)


def _ligne_video(video: Video, metriques: Metriques) -> str:
    format_video = "short" if video.est_short == 1 else "longue" if video.est_short == 0 else "?"
    ratio = _ratio(video, metriques)
    ratio_texte = f"{ratio}× la médiane" if ratio is not None else "ratio indisponible"
    return (
        f"- {video.titre} | format={format_video} | {formater_duree(video.duree_s)} | "
        f"{formater_nombre(video.vues)} vues | {formater_date(video.publie_le)} | {ratio_texte}"
    )


def construire_prompt(
    positioning: str,
    chaine: Chaine,
    videos: list[Video],
    metriques: Metriques,
) -> str:
    """Assemble le message utilisateur complet (sections 1 à 7)."""
    recentes = sorted(videos, key=lambda v: v.publie_le or "", reverse=True)[:10]
    avec_ratio = [(v, _ratio(v, metriques)) for v in videos]
    outliers = [paire for paire in avec_ratio if paire[1] is not None]
    outliers.sort(key=lambda paire: paire[1] or 0, reverse=True)
    meilleurs = [video for video, _ in outliers[:5]]

    description = (chaine.description or "(aucune description)")[:1500]

    return GABARIT_UTILISATEUR.format(
        positioning=positioning[:8000],
        titre=chaine.titre,
        handle=chaine.handle or "(non renseigné)",
        abonnes="masqués" if chaine.abonnes_masques else formater_nombre(chaine.abonnes),
        nb_videos=formater_nombre(chaine.nb_videos),
        cree_le=formater_date(chaine.cree_le),
        pays=chaine.pays or "non renseigné",
        mots_cles=(chaine.mots_cles_chaine or "(aucun)")[:400],
        description=description,
        videos="\n".join(_ligne_video(v, metriques) for v in recentes) or "- (aucune vidéo)",
        outliers="\n".join(_ligne_video(v, metriques) for v in meilleurs) or "- (aucun outlier)",
        mediane_longues=formater_nombre(metriques.mediane_vues_longues),
        mediane_shorts=formater_nombre(metriques.mediane_vues_short),
        cadence=formater_nombre(metriques.cadence_semaine),
        n_shorts=metriques.n_shorts,
        n_longues=metriques.n_longues,
        jours=formater_nombre(metriques.jours_depuis_publication),
    )


def construire_prompt_outlier(
    chaine_titre: str,
    video: Video,
    mediane: float | None,
    ratio: float | None,
    transcription: str | None,
) -> str:
    etat = "disponible" if transcription else "indisponible"
    return GABARIT_OUTLIER.format(
        titre=video.titre,
        chaine=chaine_titre,
        duree=formater_duree(video.duree_s),
        date=formater_date(video.publie_le),
        vues=formater_nombre(video.vues),
        ratio=formater_nombre(ratio) if ratio is not None else "inconnu",
        mediane=formater_nombre(mediane),
        etat_transcription=etat,
        transcription=(transcription or "(aucune transcription disponible)")[:12000],
    )


# ---------------------------------------------------------------------------
# Empreintes (cache LLM : un re-run ne doit rien recoûter)
# ---------------------------------------------------------------------------


def empreinte_payload(payload: dict) -> str:
    canonique = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


def empreinte_chaine(
    prompt_version: str, positioning: str, chaine: Chaine, videos: list[Video], metriques: Metriques
) -> str:
    """Empreinte de tout ce qui influence la réponse du modèle."""
    payload = {
        "prompt_version": prompt_version,
        "positioning": hashlib.sha256(positioning.encode("utf-8")).hexdigest(),
        "chaine": [
            chaine.channel_id,
            chaine.titre,
            chaine.description or "",
            chaine.abonnes,
            chaine.nb_videos,
        ],
        "videos": sorted(
            [video.video_id, video.vues, video.duree_s, video.publie_le] for video in videos
        ),
        "metriques": [
            metriques.mediane_vues_short,
            metriques.mediane_vues_longues,
            metriques.cadence_semaine,
            metriques.n_shorts,
            metriques.n_longues,
        ],
    }
    return empreinte_payload(payload)


# ---------------------------------------------------------------------------
# Extraction et validation
# ---------------------------------------------------------------------------


def extraire_json(texte: str | None) -> dict | None:
    """Extrait le premier objet JSON d'une réponse de modèle.

    Tolère les balises Markdown et le bavardage autour, mais n'invente rien :
    renvoie None si aucun objet équilibré n'est trouvé.
    """
    if not texte:
        return None
    nettoye = texte.strip()
    if nettoye.startswith("```"):
        # Retire la première ligne de clôture (``` ou ```json) et la dernière.
        lignes = nettoye.splitlines()
        if lignes and lignes[0].startswith("```"):
            lignes = lignes[1:]
        if lignes and lignes[-1].strip().startswith("```"):
            lignes = lignes[:-1]
        nettoye = "\n".join(lignes).strip()

    try:
        charge = json.loads(nettoye)
        return charge if isinstance(charge, dict) else None
    except json.JSONDecodeError:
        pass

    # Recherche d'un objet équilibré, en respectant les chaînes et échappements.
    debut = nettoye.find("{")
    while debut != -1:
        profondeur = 0
        dans_chaine = False
        echappe = False
        for position in range(debut, len(nettoye)):
            caractere = nettoye[position]
            if dans_chaine:
                if echappe:
                    echappe = False
                elif caractere == "\\":
                    echappe = True
                elif caractere == '"':
                    dans_chaine = False
                continue
            if caractere == '"':
                dans_chaine = True
            elif caractere == "{":
                profondeur += 1
            elif caractere == "}":
                profondeur -= 1
                if profondeur == 0:
                    essai = nettoye[debut : position + 1]
                    try:
                        charge = json.loads(essai)
                        if isinstance(charge, dict):
                            return charge
                    except json.JSONDecodeError:
                        break
        debut = nettoye.find("{", debut + 1)
    return None


def charger_schema(nom: str) -> dict:
    return json.loads((DOSSIER_SCHEMAS / nom).read_text(encoding="utf-8"))


def valider(donnees: dict, schema: dict) -> list[str]:
    """Renvoie la liste des erreurs de schéma (vide si tout est bon)."""
    validateur = Draft202012Validator(schema)
    erreurs = sorted(validateur.iter_errors(donnees), key=lambda e: list(e.path))
    messages = []
    for erreur in erreurs:
        chemin = ".".join(str(partie) for partie in erreur.path) or "(racine)"
        messages.append(f"{chemin} : {erreur.message}")
    return messages


def transformer_score(donnees: dict) -> ScoreChaine:
    return ScoreChaine(
        score_proximite=int(donnees["score_proximite"]),
        recouvrement_audience=str(donnees["recouvrement_audience"]),
        format_detecte=str(donnees["format_detecte"]),
        langue=str(donnees["langue"]),
        justification=str(donnees["justification"]).strip(),
        risques=[str(risque).strip() for risque in donnees.get("risques") or []],
    )


def deux_phrases(texte: str) -> bool:
    """Contrôle souple : au moins deux phrases terminées par une ponctuation forte."""
    import re

    return len(re.findall(r"[.!?](\s|$)", texte)) >= 2


def lisible_maintenant(valeur: str | None) -> str:
    """Petit utilitaire d'affichage pour les horodatages ISO."""
    if not valeur:
        return "—"
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return valeur
