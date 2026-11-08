"""Historique local des vues d'une chaîne, reconstruit sans réseau.

Les vues d'une vidéo sont un total **actuel**. En les cumulant par date de
publication, on obtient une courbe d'évolution approchée : elle ne remplace pas
un historique quotidien, que YouTube ne fournit que pour sa propre chaîne.
L'interface affiche toujours cette limite et la couverture des vidéos connues.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime


def _jour(valeur) -> str:
    texte = str(valeur or "").strip()
    if not texte:
        return ""
    try:
        return datetime.fromisoformat(texte.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(texte[:10]).isoformat()
        except ValueError:
            return ""


def serie_cumulee(videos: list[dict]) -> list[dict]:
    """Cumule les vues par jour de publication.

    Les vidéos sans date ou sans vues sont ignorées. Plusieurs vidéos publiées
    le même jour sont agrégées en un seul point, pour ne pas dessiner de
    micro-marches artificielles.
    """
    par_jour: dict[str, dict] = {}
    for video in videos:
        if not isinstance(video, dict):
            continue
        jour = _jour(video.get("publie_le"))
        try:
            vues = max(0, int(float(video.get("vues") or 0)))
        except (TypeError, ValueError):
            vues = 0
        if not jour or vues <= 0:
            continue
        point = par_jour.setdefault(jour, {"jour": jour, "vues": 0, "videos": 0})
        point["vues"] += vues
        point["videos"] += 1
    points: list[dict] = []
    cumul = 0
    for jour in sorted(par_jour):
        point = par_jour[jour]
        cumul += int(point["vues"])
        points.append(
            {
                "jour": jour,
                "vues": int(point["vues"]),
                "cumul": cumul,
                "videos": int(point["videos"]),
            }
        )
    return points


def _chaine(ligne: sqlite3.Row) -> dict:
    return {
        "channel_id": str(ligne["channel_id"]),
        "titre": str(ligne["titre"] or ligne["channel_id"]),
        "abonnes": ligne["abonnes"],
        "vues_totales": ligne["vues_totales"],
        "nb_videos": ligne["nb_videos"],
        "videos_connues": int(ligne["videos_connues"] or 0),
    }


def chaines(conn: sqlite3.Connection) -> list[dict]:
    """Chaînes concurrentes trouvées par la veille, avec des vidéos.

    Les seeds et les fiches ajoutées hors découverte sont exclues : elles
    servent de point de départ, pas de résultat de veille concurrentielle.
    Les sources acceptées sont celles que `discovery` écrit réellement :
    `mot-cle:*`, `section`, `ma-chaine` et `description`.
    """
    lignes = conn.execute(
        """
        SELECT c.channel_id, c.titre, c.abonnes, c.vues_totales, c.nb_videos,
               COUNT(v.video_id) AS videos_connues
          FROM channels c
          JOIN videos v ON v.channel_id = c.channel_id
         WHERE c.statut != 'moi'
           AND c.est_seed = 0
           AND (
                 c.source IN ('ma-chaine', 'section', 'description')
                 OR c.source LIKE 'mot-cle:%'
           )
           AND v.publie_le IS NOT NULL
           AND v.vues IS NOT NULL
         GROUP BY c.channel_id
        HAVING COUNT(v.video_id) > 0
         ORDER BY c.titre COLLATE NOCASE
        """
    ).fetchall()
    return [_chaine(ligne) for ligne in lignes]


def serie_chaine(conn: sqlite3.Connection, channel_id: str) -> dict | None:
    """Toutes les vidéos connues d'une chaîne, cumulées par publication."""
    ligne = conn.execute(
        """
        SELECT channel_id, titre, abonnes, vues_totales, nb_videos
          FROM channels WHERE channel_id = ?
        """,
        (str(channel_id),),
    ).fetchone()
    if ligne is None:
        return None
    videos = conn.execute(
        """
        SELECT video_id, titre, publie_le, vues, likes, commentaires,
               duree_s, est_short, live
          FROM videos
         WHERE channel_id = ?
           AND publie_le IS NOT NULL
           AND vues IS NOT NULL
         ORDER BY publie_le ASC
        """,
        (str(channel_id),),
    ).fetchall()
    points = serie_cumulee([dict(video) for video in videos])
    total_connu = points[-1]["cumul"] if points else 0
    total = ligne["vues_totales"]
    couverture = (total_connu / float(total) * 100) if total and total > 0 else None
    toutes = [dict(video) for video in videos]
    return {
        "chaine": _chaine(
            {
                **dict(ligne),
                "videos_connues": len(videos),
            }
        ),
        "points": points,
        "videos": list(reversed(toutes)),
        "total_connu": total_connu,
        "couverture": couverture,
        "premiere": points[0]["jour"] if points else "",
        "derniere": points[-1]["jour"] if points else "",
    }


__all__ = ["chaines", "serie_chaine", "serie_cumulee"]
