"""Lecture OPTIONNELLE des sous-titres.

Pourquoi c'est fragile, et pourquoi l'outil ne doit jamais en dépendre :
  * `youtube-transcript-api` s'appuie sur une partie non documentée de YouTube ;
  * YouTube bloque la plupart des IP de datacenters et renvoie des PO tokens ;
  * les Developer Policies interdisent les « undocumented services » : cette
    fonction est donc désactivée par défaut et son usage relève de votre choix.

Chaque échec est traduit en statut, jamais en exception non gérée.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import iso_utc


@dataclass
class ResultatTranscription:
    video_id: str
    statut: str  # ok | desactive | absent | bloque | erreur
    langue: str | None = None
    texte: str | None = None
    erreur: str | None = None
    # Repères temporels : [{"debut": 12.4, "duree": 3.2, "texte": "…"}].
    # Ils permettent de situer précisément l'intro d'une vidéo.
    segments: list[dict] | None = None


def _segments(donnees) -> list[dict]:
    """Normalise les morceaux renvoyés par youtube-transcript-api."""
    resultat: list[dict] = []
    for partie in donnees:
        if isinstance(partie, dict):
            texte = str(partie.get("text") or "").strip()
            debut = partie.get("start", partie.get("debut"))
            duree = partie.get("duration", partie.get("duree"))
        else:
            texte = str(getattr(partie, "text", "") or "").strip()
            debut = getattr(partie, "start", None)
            duree = getattr(partie, "duration", None)
        if not texte:
            continue
        try:
            debut = float(debut) if debut is not None else None
        except (TypeError, ValueError):
            debut = None
        try:
            duree = float(duree) if duree is not None else None
        except (TypeError, ValueError):
            duree = None
        resultat.append({"debut": debut, "duree": duree, "texte": texte})
    return resultat


def formater_horodatage(secondes: float | int | None) -> str:
    """0:00, 3:42 ou 1:02:15 — format lisible par le LLM et l'interface."""
    if secondes is None:
        return "?"
    total = max(0, int(float(secondes)))
    heures, reste = divmod(total, 3600)
    minutes, sec = divmod(reste, 60)
    if heures:
        return f"{heures}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def texte_horodate(segments: list[dict] | None, max_chars: int = 12000) -> str:
    """Transcription avec un horodatage par ligne, tronquée proprement.

    Une ligne par morceau : le modèle peut ainsi répondre en secondes sans
    deviner la position d'une phrase dans un pavé de texte continu.
    """
    if not segments:
        return ""
    lignes: list[str] = []
    total = 0
    for segment in segments:
        texte = str(segment.get("texte") or "").strip()
        if not texte:
            continue
        debut = segment.get("debut")
        prefixe = f"[{formater_horodatage(debut)}] " if debut is not None else ""
        ligne = prefixe + texte
        if total + len(ligne) + 1 > max_chars:
            lignes.append("[…]")
            break
        lignes.append(ligne)
        total += len(ligne) + 1
    return "\n".join(lignes)


def _classes(module, noms: tuple[str, ...]) -> tuple:
    """Récupère des classes d'exception sans casser si la version en manque."""
    trouvees = []
    for nom in noms:
        classe = getattr(module, nom, None)
        if isinstance(classe, type):
            trouvees.append(classe)
    return tuple(trouvees)


def recuperer(video_id: str, langues: list[str], max_chars: int = 12000) -> ResultatTranscription:
    """Récupère la transcription d'une vidéo, sans jamais lever d'exception."""
    try:
        import youtube_transcript_api as yta
    except ImportError:
        return ResultatTranscription(
            video_id,
            "erreur",
            erreur=(
                "youtube-transcript-api n'est pas installé. "
                "Installez l'option : pip install \"outly[transcripts]\""
            ),
        )

    blocages = _classes(yta, ("IpBlocked", "RequestBlocked", "PoTokenRequired"))
    absences = _classes(yta, ("TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable"))
    age = _classes(yta, ("AgeRestricted", "VideoUnplayable"))

    try:
        api = yta.YouTubeTranscriptApi()
        morceaux = api.fetch(video_id, languages=langues)
        if hasattr(morceaux, "to_raw_data"):
            donnees = morceaux.to_raw_data()
        else:  # Compatibilité avec les versions 1.0/1.1
            donnees = list(morceaux)
        segments = _segments(donnees)
        texte = " ".join(segment["texte"] for segment in segments).strip()
        if not texte:
            return ResultatTranscription(video_id, "absent", erreur="transcription vide")
        return ResultatTranscription(
            video_id,
            "ok",
            texte=texte[:max_chars],
            segments=segments,
        )
    except blocages as erreur:
        return ResultatTranscription(
            video_id,
            "bloque",
            erreur=f"YouTube a bloqué la requête ({erreur.__class__.__name__}).",
        )
    except absences as erreur:
        return ResultatTranscription(
            video_id,
            "absent",
            erreur=f"Aucun sous-titre disponible ({erreur.__class__.__name__}).",
        )
    except age as erreur:
        return ResultatTranscription(
            video_id,
            "absent",
            erreur=f"Vidéo non lisible sans compte ({erreur.__class__.__name__}).",
        )
    except Exception as erreur:  # la bibliothèque peut changer sans préavis
        return ResultatTranscription(
            video_id,
            "erreur",
            erreur=f"Échec de récupération ({erreur.__class__.__name__} : {erreur})"[:300],
        )


def enregistrer(conn: sqlite3.Connection, resultat: ResultatTranscription) -> None:
    conn.execute(
        "INSERT INTO transcripts (video_id, langue, texte, caracteres, statut, erreur, "
        "segments_json, recupere_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(video_id) DO UPDATE SET langue = excluded.langue, texte = excluded.texte, "
        "caracteres = excluded.caracteres, statut = excluded.statut, "
        "erreur = excluded.erreur, segments_json = excluded.segments_json, "
        "recupere_le = excluded.recupere_le",
        (
            resultat.video_id,
            resultat.langue,
            resultat.texte,
            len(resultat.texte or ""),
            resultat.statut,
            resultat.erreur,
            json.dumps(resultat.segments, ensure_ascii=False) if resultat.segments else None,
            iso_utc(),
        ),
    )
    conn.commit()


def depuis_cache(
    conn: sqlite3.Connection,
    video_id: str,
    jours: int = 30,
    avec_segments: bool = False,
) -> ResultatTranscription | None:
    """Réutilise une transcription réussie récente (aucun appel réseau).

    `avec_segments=True` exige des repères temporels : une transcription
    enregistrée avant leur introduction est ignorée, pour ne pas demander au
    modèle de deviner où commence l'intro.
    """
    ligne = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? AND statut = 'ok'", (video_id,)
    ).fetchone()
    if ligne is None or not ligne["texte"]:
        return None
    try:
        recupere = datetime.fromisoformat(ligne["recupere_le"])
    except ValueError:
        return None
    if recupere.tzinfo is None:  # ligne héritée ou écrite par un autre outil
        recupere = recupere.replace(tzinfo=timezone.utc)
    if recupere < datetime.now(timezone.utc) - timedelta(days=jours):
        return None
    segments = None
    if ligne["segments_json"]:
        try:
            charge = json.loads(ligne["segments_json"])
            if isinstance(charge, list):
                segments = charge
        except (TypeError, ValueError):
            segments = None
    if avec_segments and not segments:
        return None
    return ResultatTranscription(
        video_id=video_id,
        statut="ok",
        langue=ligne["langue"],
        texte=ligne["texte"],
        segments=segments,
    )
