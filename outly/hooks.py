"""Analyse des intros d'outliers et génération de variantes d'accroches.

Le principe, en deux temps :

1. **Où commence et finit l'intro ?** Pour chaque vidéo outlier dont la
   transcription horodatée est disponible, le LLM reçoit les morceaux avec
   leur minute et renvoie les bornes de l'intro, la citation de l'accroche et
   les procédés employés. Cette analyse ne dépend pas du sujet de l'utilisateur
   et est donc réutilisée d'un run à l'autre.
2. **Quelles accroches écrire pour MON sujet ?** Une seconde requête reçoit les
   analyses d'intro et le titre/sujet fourni par l'utilisateur, puis produit
   plusieurs variantes explicitement inspirées des outliers.

Rien n'est inventé quand les transcriptions manquent : la génération s'arrête
avec un message clair. Toutes les erreurs sont consignées, jamais levées.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from . import notes as notes_module
from . import transcripts
from .config import Config
from .llm import prompts as prompts_llm
from .llm.client import ErreurLlm, ServiceLlm
from .llm.scoring import demander_json
from .models import iso_utc

PROMPT_INTRO_VERSION = "intro-v1"
PROMPT_HOOKS_VERSION = "hooks-v3"

PROMPT_SYSTEME_INTRO = """Tu es un analyste de contenu YouTube. Tu repères l'intro d'une vidéo
et tu expliques ce qui retient l'attention dans les premières secondes.

Règles absolues :
1. Réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni balise Markdown.
2. Appuie-toi exclusivement sur la transcription horodatée fournie. N'invente aucune
   phrase et ne recopie pas la transcription entière.
3. Les bornes sont exprimées en secondes depuis le début de la vidéo. L'intro commence
   en général à 0 s et se termine quand la promesse est posée ou quand le contenu
   principal démarre. Si l'intro est très courte, donne des bornes réalistes.
4. "hook_texte" est une citation exacte, courte, des premières secondes.
5. Tout est rédigé en français, même si la transcription est dans une autre langue ;
   la citation "hook_texte", elle, reste dans sa langue d'origine."""

GABARIT_INTRO = """## 1. VIDÉO
Titre : {titre}
Chaîne : {chaine}
Durée : {duree}
Performance : {ratio}× la médiane du même format
Publiée le : {date}

## 2. TRANSCRIPTION HORODATÉE
<<<
{transcription}
>>>

## 3. CE QUE J'ATTENDS
- "intro_debut_s" et "intro_fin_s" : bornes de l'intro, en secondes (nombres).
- "hook_texte" : citation exacte de l'accroche (10 à 500 caractères).
- "resume" : ce que fait l'intro, concrètement (rythme, question, promesse, preuve…).
- "promesse" : la promesse ou le bénéfice annoncé au spectateur.
- "techniques" : 1 à 6 procédés utilisés (ex. « question directe », « chiffre choc »,
  « démonstration immédiate », « boucle ouverte »).

## 4. SORTIE
Un unique objet JSON :
{{"intro_debut_s": <nombre>, "intro_fin_s": <nombre>,
 "hook_texte": "<citation>", "resume": "<texte>", "promesse": "<texte>",
 "techniques": ["<procédé>", "..."]}}"""

PROMPT_SYSTEME_HOOKS = """Tu es scénariste YouTube. À partir d'intros d'outliers concurrents et
d'un sujet de vidéo fourni, tu écris des variantes d'accroches originales pour ce sujet.

Règles absolues :
1. Réponds UNIQUEMENT par un objet JSON valide, sans texte autour ni balise Markdown.
2. Chaque variante s'inspire réellement des procédés observés dans les intros fournies,
   mais ne recopie aucune phrase : le texte doit être nouveau et adapté au sujet.
3. Le sujet fourni est la contrainte principale : chaque accroche doit parler de CE
   sujet, pas d'un sujet voisin. Si le sujet est un titre de vidéo, garde son angle.
4. Une accroche se lit à voix haute en 3 à 20 secondes. "duree_secondes" est une
   estimation entière de cette lecture.
5. "inspire_de" contient un ou deux identifiants exacts de vidéos fournies.
6. "pourquoi" explique en une phrase le lien entre le procédé copié et le sujet.
7. Inspire-toi aussi des notes personnelles fournies (concepts, hooks, sujets, idées) :
   elles précisent la voix et les obsessions de l'auteur. Chaque note peut porter un
   classement sur 10 : commence par les mieux notées, ce sont les hooks et les angles
   que l'auteur juge les plus forts. Ne les recopie pas et ne cite pas leurs identifiants
   dans la sortie ; utilise-les pour nourrir le fond et le ton.
8. Écris dans la langue du sujet (français si le sujet est en français).
9. Ne donne aucun conseil général : uniquement des accroches prêtes à enregistrer."""

GABARIT_HOOKS = """## 1. SUJET DE LA VIDÉO À ÉCRIRE
{sujet}

## 2. INTROS D'OUTLIERS ANALYSÉES
{intros}

## 3. MÉMOIRE DE L'UTILISATEUR (notes personnelles)
{notes}

## 4. CE QUE J'ATTENDS
- "analyse" : 2 à 5 phrases sur les points communs des intros et ce qu'il faut
  transposer au sujet (pas un catalogue vidéo par vidéo).
- "variantes" : exactement {nombre} variantes, de styles différents (question,
  chiffre, promesse, démonstration, contre-intuition…).

## 5. SORTIE
Un unique objet JSON :
{{"analyse": "<texte>",
 "variantes": [
   {{"titre": "<nom court du style>", "hook": "<texte à dire>",
     "duree_secondes": <entier>, "technique": "<procédé>",
     "inspire_de": ["<video_id>"], "pourquoi": "<phrase>"}}
 ]}}"""


@dataclass
class AnalyseIntro:
    video_id: str
    titre: str
    chaine_titre: str
    duree_s: float | None
    score_outlier: float | None
    intro_debut_s: float
    intro_fin_s: float
    hook_texte: str
    resume: str
    promesse: str
    techniques: list[str] = field(default_factory=list)


@dataclass
class VarianteHook:
    titre: str
    hook: str
    duree_secondes: int
    technique: str
    inspire_de: list[str]
    pourquoi: str


@dataclass
class ResumeHooks:
    videos_examinees: int = 0
    transcriptions_lues: int = 0
    intros_analysees: int = 0
    intros_reutilisees: int = 0
    sans_transcription: int = 0
    variantes: int = 0
    jeu_reutilise: bool = False
    appels: int = 0
    messages: list[str] = field(default_factory=list)
    sujet: str = ""
    reussie: bool = False

    def resume(self) -> str:
        lignes = [
            f"Sujet              : {self.sujet or '(non renseigné)'}",
            f"Vidéos examinées   : {self.videos_examinees}",
            f"Transcriptions lues : {self.transcriptions_lues}",
            f"Intros analysées   : {self.intros_analysees} "
            f"(+ {self.intros_reutilisees} réutilisées)",
            f"Sans transcription : {self.sans_transcription}",
            f"Variantes générées : {self.variantes}"
            + (" (jeu déjà en cache)" if self.jeu_reutilise else ""),
            f"Appels LLM         : {self.appels}",
        ]
        lignes += [f"  ! {message}" for message in self.messages]
        return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Sélection des vidéos
# ---------------------------------------------------------------------------


def _candidats(conn: sqlite3.Connection, limite_essais: int) -> list[sqlite3.Row]:
    """Meilleurs outliers, diversifiés par chaîne (deux vidéos au maximum)."""
    lignes = conn.execute(
        """
        SELECT
            v.video_id, v.channel_id, v.titre, v.duree_s, v.publie_le, v.vues,
            v.est_short, v.score_outlier,
            c.titre AS chaine_titre,
            (SELECT s.score_proximite FROM channel_scores s
              WHERE s.channel_id = c.channel_id AND s.valide = 1
              ORDER BY s.id DESC LIMIT 1) AS score_proximite,
            (SELECT a.accroche FROM analyses_outliers a
              WHERE a.video_id = v.video_id AND a.valide = 1
              ORDER BY a.id DESC LIMIT 1) AS accroche
        FROM v_outliers v
        JOIN channels c ON c.channel_id = v.channel_id
        WHERE v.score_outlier > 1.0
          AND c.statut IN ('enrichie', 'scored')
        ORDER BY
            CASE WHEN score_proximite IS NULL THEN 1 ELSE 0 END,
            score_proximite DESC,
            v.score_outlier DESC
        LIMIT 200
        """
    ).fetchall()
    par_chaine: dict[str, list[sqlite3.Row]] = {}
    for ligne in lignes:
        par_chaine.setdefault(ligne["channel_id"], []).append(ligne)
    resultat: list[sqlite3.Row] = []
    for tour in range(2):
        for videos in par_chaine.values():
            if tour < len(videos) and len(resultat) < limite_essais:
                resultat.append(videos[tour])
    return resultat[:limite_essais]


# ---------------------------------------------------------------------------
# Cache / persistance
# ---------------------------------------------------------------------------


def _empreinte_intro(video_id: str, segments: list[dict]) -> str:
    return prompts_llm.empreinte_payload(
        {
            "prompt": PROMPT_INTRO_VERSION,
            "video_id": video_id,
            "segments": segments,
        }
    )


def _lire_intro(conn: sqlite3.Connection, video_id: str, empreinte: str) -> dict | None:
    ligne = conn.execute(
        "SELECT * FROM intro_analyses WHERE video_id = ? AND prompt_version = ? "
        "AND empreinte = ? AND valide = 1",
        (video_id, PROMPT_INTRO_VERSION, empreinte),
    ).fetchone()
    if ligne is None:
        return None
    try:
        techniques = json.loads(ligne["techniques_json"]) if ligne["techniques_json"] else []
    except (TypeError, ValueError):
        techniques = []
    if not isinstance(techniques, list):
        techniques = []
    return {
        "video_id": video_id,
        "intro_debut_s": ligne["intro_debut_s"],
        "intro_fin_s": ligne["intro_fin_s"],
        "hook_texte": ligne["hook_texte"] or "",
        "resume": ligne["resume"] or "",
        "promesse": ligne["promesse"] or "",
        "techniques": [str(element) for element in techniques],
    }


def _enregistrer_intro(
    conn: sqlite3.Connection,
    video_id: str,
    channel_id: str,
    run_id: int | None,
    empreinte: str,
    service: ServiceLlm | None,
    donnees: dict | None,
    erreur: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO intro_analyses (
            video_id, channel_id, run_id, prompt_version, empreinte, fournisseur, modele,
            intro_debut_s, intro_fin_s, hook_texte, resume, promesse, techniques_json,
            valide, erreur, cree_le
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id, prompt_version, empreinte) DO UPDATE SET
            run_id          = excluded.run_id,
            fournisseur     = excluded.fournisseur,
            modele          = excluded.modele,
            intro_debut_s   = excluded.intro_debut_s,
            intro_fin_s     = excluded.intro_fin_s,
            hook_texte      = excluded.hook_texte,
            resume          = excluded.resume,
            promesse        = excluded.promesse,
            techniques_json = excluded.techniques_json,
            valide          = excluded.valide,
            erreur          = excluded.erreur,
            cree_le         = excluded.cree_le
        """,
        (
            video_id,
            channel_id,
            run_id,
            PROMPT_INTRO_VERSION,
            empreinte,
            service.nom_fournisseur if service and donnees else None,
            service.nom_modele if service and donnees else None,
            (donnees or {}).get("intro_debut_s"),
            (donnees or {}).get("intro_fin_s"),
            (donnees or {}).get("hook_texte"),
            (donnees or {}).get("resume"),
            (donnees or {}).get("promesse"),
            json.dumps(donnees.get("techniques") or [], ensure_ascii=False) if donnees else None,
            1 if donnees else 0,
            erreur,
            iso_utc(),
        ),
    )
    conn.commit()


def _empreinte_jeu(
    sujet: str, intros: list[dict], nombre_variantes: int, bloc_notes: str = ""
) -> str:
    return prompts_llm.empreinte_payload(
        {
            "prompt": PROMPT_HOOKS_VERSION,
            "sujet": " ".join(sujet.split()),
            "variantes": int(nombre_variantes),
            "notes": bloc_notes,
            "intros": [
                [
                    intro["video_id"],
                    intro.get("intro_debut_s"),
                    intro.get("intro_fin_s"),
                    intro.get("hook_texte"),
                    intro.get("techniques"),
                ]
                for intro in intros
            ],
        }
    )


def _enregistrer_jeu(
    conn: sqlite3.Connection,
    run_id: int | None,
    sujet: str,
    empreinte: str,
    service: ServiceLlm | None,
    donnees: dict | None,
    sources: list[dict],
    erreur: str | None,
) -> None:
    variantes = (donnees or {}).get("variantes") or []
    conn.execute(
        """
        INSERT INTO hook_sets (
            run_id, sujet, empreinte, prompt_version, fournisseur, modele, analyse,
            variantes_json, sources_json, valide, erreur, cree_le
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(empreinte, prompt_version) DO UPDATE SET
            run_id         = excluded.run_id,
            fournisseur    = excluded.fournisseur,
            modele         = excluded.modele,
            analyse        = excluded.analyse,
            variantes_json = excluded.variantes_json,
            sources_json   = excluded.sources_json,
            valide         = excluded.valide,
            erreur         = excluded.erreur,
            cree_le        = excluded.cree_le
        """,
        (
            run_id,
            sujet,
            empreinte,
            PROMPT_HOOKS_VERSION,
            service.nom_fournisseur if service and donnees else None,
            service.nom_modele if service and donnees else None,
            (donnees or {}).get("analyse"),
            json.dumps(variantes, ensure_ascii=False) if donnees else None,
            json.dumps(sources, ensure_ascii=False),
            1 if donnees else 0,
            erreur,
            iso_utc(),
        ),
    )
    conn.commit()


def _lire_jeu(conn: sqlite3.Connection, empreinte: str) -> dict | None:
    ligne = conn.execute(
        "SELECT * FROM hook_sets WHERE empreinte = ? AND prompt_version = ? AND valide = 1",
        (empreinte, PROMPT_HOOKS_VERSION),
    ).fetchone()
    if ligne is None:
        return None
    return _ligne_vers_jeu(ligne)


def _ligne_vers_jeu(ligne: sqlite3.Row) -> dict:
    def charger(valeur, defaut):
        if not valeur:
            return defaut
        try:
            charge = json.loads(valeur)
            return charge if isinstance(charge, type(defaut)) else defaut
        except (TypeError, ValueError):
            return defaut

    return {
        "id": ligne["id"],
        "sujet": ligne["sujet"],
        "analyse": ligne["analyse"] or "",
        "variantes": charger(ligne["variantes_json"], []),
        "sources": charger(ligne["sources_json"], []),
        "fournisseur": ligne["fournisseur"],
        "modele": ligne["modele"],
        "cree_le": ligne["cree_le"],
    }


def dernier_jeu(conn: sqlite3.Connection) -> dict | None:
    """Dernier jeu valide, quel que soit le sujet (lecture pour l'interface)."""
    ligne = conn.execute(
        "SELECT * FROM hook_sets WHERE valide = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _ligne_vers_jeu(ligne) if ligne is not None else None


def intros_du_jeu(conn: sqlite3.Connection, jeu: dict) -> list[dict]:
    """Analyses d'intro correspondant aux vidéos citées par un jeu."""
    identifiants = [
        str(source.get("video_id"))
        for source in jeu.get("sources") or []
        if isinstance(source, dict) and source.get("video_id")
    ]
    if not identifiants:
        return []
    marques = ",".join("?" for _ in identifiants)
    lignes = conn.execute(
        f"""
        SELECT i.*, v.titre, v.duree_s, c.titre AS chaine_titre
        FROM intro_analyses i
        JOIN videos v ON v.video_id = i.video_id
        JOIN channels c ON c.channel_id = v.channel_id
        WHERE i.valide = 1 AND i.video_id IN ({marques})
        ORDER BY i.id DESC
        """,
        identifiants,
    ).fetchall()
    resultat: list[dict] = []
    vus: set[str] = set()
    for ligne in lignes:
        if ligne["video_id"] in vus:
            continue
        vus.add(ligne["video_id"])
        try:
            techniques = json.loads(ligne["techniques_json"]) if ligne["techniques_json"] else []
        except (TypeError, ValueError):
            techniques = []
        resultat.append(
            {
                "video_id": ligne["video_id"],
                "titre": ligne["titre"],
                "chaine_titre": ligne["chaine_titre"],
                "duree_s": ligne["duree_s"],
                "intro_debut_s": ligne["intro_debut_s"],
                "intro_fin_s": ligne["intro_fin_s"],
                "hook_texte": ligne["hook_texte"] or "",
                "resume": ligne["resume"] or "",
                "promesse": ligne["promesse"] or "",
                "techniques": techniques if isinstance(techniques, list) else [],
            }
        )
    return resultat


# ---------------------------------------------------------------------------
# Analyse d'une intro
# ---------------------------------------------------------------------------


def _duree_lisible(secondes) -> str:
    if secondes is None:
        return "durée inconnue"
    return transcripts.formater_horodatage(float(secondes))


def _prompt_intro(ligne: sqlite3.Row, transcription: str) -> str:
    ratio = ligne["score_outlier"]
    return GABARIT_INTRO.format(
        titre=ligne["titre"],
        chaine=ligne["chaine_titre"],
        duree=_duree_lisible(ligne["duree_s"]),
        ratio=f"{float(ratio):.1f}".replace(".", ",") if ratio is not None else "inconnu",
        date=(ligne["publie_le"] or "date inconnue")[:10],
        transcription=transcription or "(transcription vide)",
    )


def _transformer_intro(donnees: dict, duree_s: float | None) -> dict:
    """Normalise les bornes et refuse une intro incohérente."""
    debut = float(donnees["intro_debut_s"])
    fin = float(donnees["intro_fin_s"])
    if duree_s and duree_s > 0:
        debut = min(max(0.0, debut), duree_s)
        fin = min(max(0.0, fin), duree_s)
    if fin <= debut:
        raise ValueError("bornes d'intro incohérentes (fin ≤ début)")
    return {
        "intro_debut_s": round(debut, 2),
        "intro_fin_s": round(fin, 2),
        "hook_texte": str(donnees["hook_texte"]).strip(),
        "resume": str(donnees["resume"]).strip(),
        "promesse": str(donnees["promesse"]).strip(),
        "techniques": [str(element).strip() for element in donnees.get("techniques") or []],
    }


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------


def _contexte_intros(intros: list[dict], lignes: dict[str, sqlite3.Row]) -> str:
    blocs = []
    for intro in intros:
        ligne = lignes[intro["video_id"]]
        techniques = ", ".join(intro.get("techniques") or []) or "non précisé"
        blocs.append(
            "\n".join(
                [
                    f"### Vidéo {intro['video_id']} — {ligne['titre']}",
                    f"Chaîne : {ligne['chaine_titre']} · performance : "
                    f"×{float(ligne['score_outlier'] or 0):.1f} la médiane",
                    f"Intro : {transcripts.formater_horodatage(intro.get('intro_debut_s'))} → "
                    f"{transcripts.formater_horodatage(intro.get('intro_fin_s'))}",
                    f"Citation : « {intro.get('hook_texte') or '(vide)'} »",
                    f"Ce que fait l'intro : {intro.get('resume') or '(non précisé)'}",
                    f"Promesse : {intro.get('promesse') or '(non précisée)'}",
                    f"Procédés : {techniques}",
                ]
            )
        )
    return "\n\n".join(blocs)


def _nettoyer_variantes(donnees: dict, intros: list[dict], nombre: int) -> dict:
    """Garde des variantes exploitables et rattache-les à de vraies vidéos."""
    identifiants = {intro["video_id"] for intro in intros}
    variantes: list[dict] = []
    for brute in donnees.get("variantes") or []:
        if not isinstance(brute, dict):
            continue
        hook = str(brute.get("hook") or "").strip()
        if len(hook) < 10:
            continue
        inspire = [
            str(element).strip()
            for element in brute.get("inspire_de") or []
            if str(element).strip() in identifiants
        ]
        if not inspire:
            inspire = [intros[0]["video_id"]]
        try:
            duree = int(brute.get("duree_secondes") or 0)
        except (TypeError, ValueError):
            duree = 0
        variantes.append(
            {
                "titre": str(brute.get("titre") or "Variante").strip()[:120],
                "hook": hook[:800],
                "duree_secondes": max(3, min(60, duree or 10)),
                "technique": str(brute.get("technique") or "").strip()[:160],
                "inspire_de": inspire[:2],
                "pourquoi": str(brute.get("pourquoi") or "").strip()[:500],
            }
        )
        if len(variantes) >= nombre:
            break
    return {
        "analyse": str(donnees.get("analyse") or "").strip()[:2000],
        "variantes": variantes,
    }


def generer(
    conn: sqlite3.Connection,
    config: Config,
    service: ServiceLlm,
    sujet: str | None = None,
    run_id: int | None = None,
    forcer: bool = False,
) -> ResumeHooks:
    """Analyse les intros, puis écrit les variantes adaptées au sujet."""
    resume = ResumeHooks()
    resume.sujet = " ".join((sujet or config.hooks.sujet or "").split())[:400]
    if not resume.sujet:
        resume.messages.append(
            "Aucun sujet renseigné : ouvrez la page Hooks et saisissez un titre de vidéo "
            "ou une phrase précise, puis relancez."
        )
        return resume

    if not config.transcriptions.enabled:
        resume.messages.append(
            "La lecture des sous-titres est désactivée : seules les transcriptions déjà "
            "en cache seront utilisées. Activez « Lire les sous-titres » dans Réglages "
            "pour analyser de nouvelles vidéos."
        )

    limite = max(1, min(int(config.hooks.max_videos), 20))
    candidats = _candidats(conn, limite_essais=min(3 * limite, 60))
    if not candidats:
        resume.messages.append(
            "Aucune vidéo outlier disponible : lancez d'abord une découverte, un "
            "enrichissement et, si possible, l'analyse des outliers."
        )
        return resume

    schema_intro = prompts_llm.charger_schema("intro_analysis.schema.json")
    intros: list[dict] = []
    lignes_par_video: dict[str, sqlite3.Row] = {}
    blocages = 0
    echecs_intro = 0

    for ligne in candidats:
        if len(intros) >= limite:
            break
        resume.videos_examinees += 1
        video_id = ligne["video_id"]
        resultat = transcripts.depuis_cache(
            conn, video_id, jours=30, avec_segments=True
        )
        if resultat is None and config.transcriptions.enabled and blocages < 2:
            resultat = transcripts.recuperer(
                video_id,
                config.transcriptions.languages,
                config.hooks.max_chars_transcription,
            )
            resultat.langue = resultat.langue or ",".join(config.transcriptions.languages)
            transcripts.enregistrer(conn, resultat)
            if resultat.statut == "ok":
                resume.transcriptions_lues += 1
                blocages = 0
            elif resultat.statut == "bloque":
                blocages += 1
                if blocages == 2:
                    resume.messages.append(
                        "YouTube bloque la lecture des sous-titres : arrêt des "
                        "téléchargements pour cette génération."
                    )
        if resultat is None or resultat.statut != "ok" or not resultat.segments:
            resume.sans_transcription += 1
            continue
        if echecs_intro >= 2:
            resume.messages.append(
                "Deux analyses d'intro ont échoué : génération interrompue pour éviter "
                "d'enchaîner les appels inutiles."
            )
            break

        empreinte = _empreinte_intro(video_id, resultat.segments)
        intro = _lire_intro(conn, video_id, empreinte)
        if intro is not None:
            resume.intros_reutilisees += 1
        else:
            transcription = transcripts.texte_horodate(
                resultat.segments, config.hooks.max_chars_transcription
            )
            messages = [
                {"role": "system", "content": PROMPT_SYSTEME_INTRO},
                {"role": "user", "content": _prompt_intro(ligne, transcription)},
            ]
            try:
                donnees, _brut, _tentatives, erreur = demander_json(
                    service, messages, schema_intro
                )
            except ErreurLlm as probleme:
                echecs_intro += 1
                _enregistrer_intro(
                    conn, video_id, ligne["channel_id"], run_id, empreinte, None, None,
                    str(probleme),
                )
                resume.messages.append(f"{ligne['titre']} : {probleme}")
                continue
            if donnees is None:
                echecs_intro += 1
                _enregistrer_intro(
                    conn, video_id, ligne["channel_id"], run_id, empreinte, service, None,
                    erreur,
                )
                resume.messages.append(f"{ligne['titre']} : intro invalide ({erreur})")
                continue
            try:
                propre = _transformer_intro(donnees, ligne["duree_s"])
            except (KeyError, TypeError, ValueError) as probleme:
                echecs_intro += 1
                _enregistrer_intro(
                    conn, video_id, ligne["channel_id"], run_id, empreinte, service, None,
                    str(probleme),
                )
                resume.messages.append(f"{ligne['titre']} : {probleme}")
                continue
            echecs_intro = 0
            _enregistrer_intro(
                conn, video_id, ligne["channel_id"], run_id, empreinte, service, propre, None
            )
            resume.intros_analysees += 1
            intro = propre

        intro["video_id"] = video_id
        intros.append(intro)
        lignes_par_video[video_id] = ligne

    if not intros:
        resume.messages.append(
            "Aucune intro exploitable : il faut au moins une transcription horodatée. "
            "Vérifiez que « Lire les sous-titres » est activé et que le paquet "
            "youtube-transcript-api est installé (pip install \"outly[transcripts]\")."
        )
        resume.appels = service.appels
        return resume

    bloc_notes = notes_module.contexte_pour_prompt(conn, resume.sujet)
    empreinte_jeu = _empreinte_jeu(
        resume.sujet, intros, config.hooks.variants, bloc_notes
    )
    if not forcer:
        jeu = _lire_jeu(conn, empreinte_jeu)
        if jeu is not None:
            resume.jeu_reutilise = True
            resume.variantes = len(jeu.get("variantes") or [])
            resume.reussie = bool(jeu.get("variantes"))
            resume.appels = service.appels
            return resume

    schema_hooks = prompts_llm.charger_schema("hook_variants.schema.json")
    messages = [
        {"role": "system", "content": PROMPT_SYSTEME_HOOKS},
        {
            "role": "user",
            "content": GABARIT_HOOKS.format(
                sujet=resume.sujet,
                intros=_contexte_intros(intros, lignes_par_video),
                notes=bloc_notes,
                nombre=config.hooks.variants,
            ),
        },
    ]
    sources = [
        {
            "video_id": intro["video_id"],
            "titre": lignes_par_video[intro["video_id"]]["titre"],
            "chaine": lignes_par_video[intro["video_id"]]["chaine_titre"],
            "duree_s": lignes_par_video[intro["video_id"]]["duree_s"],
            "score_outlier": lignes_par_video[intro["video_id"]]["score_outlier"],
        }
        for intro in intros
    ]

    try:
        donnees, _brut, _tentatives, erreur = demander_json(service, messages, schema_hooks)
    except ErreurLlm as probleme:
        _enregistrer_jeu(conn, run_id, resume.sujet, empreinte_jeu, None, None, sources, str(probleme))
        resume.messages.append(f"Génération des hooks : {probleme}")
        resume.appels = service.appels
        return resume

    if donnees is None:
        _enregistrer_jeu(conn, run_id, resume.sujet, empreinte_jeu, service, None, sources, erreur)
        resume.messages.append(f"Génération des hooks : réponse invalide ({erreur})")
        resume.appels = service.appels
        return resume

    propre = _nettoyer_variantes(donnees, intros, config.hooks.variants)
    if not propre["variantes"]:
        _enregistrer_jeu(
            conn, run_id, resume.sujet, empreinte_jeu, service, None, sources,
            "aucune variante exploitable dans la réponse",
        )
        resume.messages.append("Le modèle n'a produit aucune variante exploitable.")
        resume.appels = service.appels
        return resume

    _enregistrer_jeu(conn, run_id, resume.sujet, empreinte_jeu, service, propre, sources, None)
    resume.variantes = len(propre["variantes"])
    resume.reussie = True
    resume.appels = service.appels
    return resume


__all__ = [
    "AnalyseIntro",
    "PROMPT_HOOKS_VERSION",
    "PROMPT_INTRO_VERSION",
    "ResumeHooks",
    "VarianteHook",
    "dernier_jeu",
    "generer",
    "intros_du_jeu",
]
