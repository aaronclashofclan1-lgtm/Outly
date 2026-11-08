"""Page « Recherche » : un titre, des mots-clés, deux modes de recherche.

Le mode sans IA lit uniquement Wikipédia. Le mode avec IA ajoute Exa et une
synthèse du LLM ; les affirmations ne sont marquées « recoupées » que lorsque
trois domaines web indépendants et reconnus les confirment. La page ne calcule
rien : elle lance la commande `recherche` et affiche le dossier en cache.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser

import customtkinter as ctk

from .. import notes, recherche
from . import app as ui_app
from . import data
from .runner import arguments_recherche

MODE_SANS_IA = "Sans IA"
MODE_AVEC_IA = "Avec IA"


def _texte(
    ui,
    parent,
    valeur,
    taille: int = 13,
    couleur=None,
    gras: bool = False,
    pady=(0, 4),
) -> ctk.CTkLabel:
    libelle = ctk.CTkLabel(
        parent,
        text=str(valeur or ""),
        font=(ui._police, taille, "bold" if gras else "normal"),
        text_color=couleur or ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=820,
    )
    libelle.pack(anchor="w", fill="x", pady=pady)
    ui._auto_wrap(libelle)
    return libelle


def _etiquette(ui, parent, texte, couleur=None, fond=None) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent,
        text=str(texte or ""),
        font=(ui._police, 10, "bold"),
        text_color=couleur or ui_app.ACCENT_TEXTE,
        fg_color=fond or ui_app.ACCENT_DOUX,
        corner_radius=9,
        padx=8,
        pady=2,
    )


def _puce(ui, parent, valeur, couleur=None) -> None:
    cadre = ctk.CTkFrame(parent, fg_color="transparent")
    cadre.pack(fill="x", pady=(0, 2))
    ctk.CTkLabel(
        cadre,
        text="•",
        font=(ui._police, 13, "bold"),
        text_color=couleur or ui_app.ACCENT_TEXTE,
        width=14,
    ).pack(side="left", anchor="n")
    libelle = ctk.CTkLabel(
        cadre,
        text=str(valeur or ""),
        font=(ui._police, 12),
        text_color=ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=790,
    )
    libelle.pack(side="left", fill="x", expand=True)
    ui._auto_wrap(libelle)


def _source_par_id(dossier: dict, source_id: str) -> dict | None:
    for source in dossier.get("sources") or []:
        if source.get("id") == source_id:
            return source
    return None


def _ligne_sources(ui, parent, dossier: dict, identifiants: list[str]) -> None:
    if not identifiants:
        return
    rangee = ctk.CTkFrame(parent, fg_color="transparent")
    rangee.pack(fill="x", pady=(3, 0))
    ctk.CTkLabel(
        rangee,
        text="Sources :",
        font=(ui._police, 10, "bold"),
        text_color=ui_app.DOUX_FONCE,
    ).pack(side="left", padx=(0, 6))
    for identifiant in identifiants:
        source = _source_par_id(dossier, identifiant)
        if source is None:
            continue
        libelle = f"{identifiant} · {source.get('domaine') or 'source'}"
        bouton = ui._lien(
            rangee,
            libelle,
            lambda url=source.get("url", ""): webbrowser.open(url),
            width=max(120, min(240, 18 + len(libelle) * 6)),
        )
        bouton.pack(side="left", padx=(0, 5), pady=1)


def _bloc_fait(ui, parent, dossier: dict, fait: dict) -> None:
    statut = fait.get("statut")
    couleur = ui_app.OK if statut == "recoupe" else ui_app.ALERTE
    fond = ui_app.OK_DOUX if statut == "recoupe" else ui_app.ALERTE_DOUX
    entete = ctk.CTkFrame(parent, fg_color="transparent")
    entete.pack(fill="x", pady=(10, 2))
    _etiquette(ui, entete, fait.get("libelle_statut") or "À vérifier", couleur, fond).pack(
        side="left", padx=(0, 8)
    )
    _texte(ui, entete, fait.get("affirmation"), taille=13, gras=True, pady=(0, 0))
    if fait.get("precision"):
        _texte(ui, parent, fait["precision"], taille=12, couleur=ui_app.DOUX, pady=(1, 0))
    _ligne_sources(ui, parent, dossier, fait.get("sources") or [])


def _bloc_chiffre(ui, parent, dossier: dict, chiffre: dict) -> None:
    statut = chiffre.get("statut")
    couleur = ui_app.OK if statut == "recoupe" else ui_app.ALERTE
    fond = ui_app.OK_DOUX if statut == "recoupe" else ui_app.ALERTE_DOUX
    rangee = ctk.CTkFrame(parent, fg_color="transparent")
    rangee.pack(fill="x", pady=(10, 1))
    _etiquette(ui, rangee, chiffre.get("valeur"), ui_app.ACCENT_TEXTE, ui_app.ACCENT_DOUX).pack(
        side="left", padx=(0, 8)
    )
    _etiquette(ui, rangee, chiffre.get("libelle_statut") or "À vérifier", couleur, fond).pack(
        side="left"
    )
    _texte(ui, parent, chiffre.get("contexte"), taille=12, pady=(1, 0))
    _ligne_sources(ui, parent, dossier, chiffre.get("sources") or [])


def _carte_source(ui, parent, source: dict, avec_entete: bool = False) -> None:
    cadre = ctk.CTkFrame(parent, fg_color=ui_app.CHAMP, corner_radius=5)
    cadre.pack(fill="x", pady=(4, 0))
    entete = ctk.CTkFrame(cadre, fg_color="transparent")
    entete.pack(fill="x", padx=10, pady=(7, 2))
    if avec_entete and source.get("id"):
        _etiquette(ui, entete, source["id"], ui_app.DOUX, ui_app.SURFACE_HAUTE).pack(
            side="left", padx=(0, 7)
        )
    ctk.CTkLabel(
        entete,
        text=source.get("titre") or "Sans titre",
        font=(ui._police, 12, "bold"),
        text_color=ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=690,
    ).pack(side="left", fill="x", expand=True)
    domaine = source.get("domaine") or ""
    connu = source.get("connu") or (source.get("type") == "wikipedia")
    _etiquette(
        ui,
        entete,
        domaine,
        ui_app.OK if connu else ui_app.ALERTE,
        ui_app.OK_DOUX if connu else ui_app.ALERTE_DOUX,
    ).pack(side="right", padx=(8, 0))
    if source.get("extrait"):
        _texte(ui, cadre, source["extrait"], taille=11, couleur=ui_app.DOUX, pady=(0, 3))
    actions = ctk.CTkFrame(cadre, fg_color="transparent")
    actions.pack(fill="x", padx=10, pady=(0, 7))
    if source.get("date"):
        ctk.CTkLabel(
            actions,
            text=str(source["date"])[:10],
            font=(ui._police, 10),
            text_color=ui_app.DOUX_FONCE,
        ).pack(side="left")
    if source.get("url"):
        ui._lien(
            actions,
            "Ouvrir la source",
            lambda url=source["url"]: webbrowser.open(url),
            width=132,
        ).pack(side="right")


def _sources_par_mot(dossier: dict) -> dict[str, list[dict]]:
    groupes: dict[str, list[dict]] = {}
    for page in dossier.get("pages_wiki") or []:
        groupes.setdefault(page.get("mot_cle") or "Wikipédia", []).append(page)
    return groupes


def _afficher_dossier(ui, zone: ctk.CTkFrame, session: dict) -> None:
    dossier = session.get("dossier") or {}
    mode = session.get("mode") or dossier.get("mode") or "sans_ia"
    for enfant in zone.winfo_children():
        enfant.destroy()

    bandeau = ctk.CTkFrame(zone, fg_color="transparent")
    bandeau.pack(fill="x", pady=(0, 6))
    _etiquette(
        ui,
        bandeau,
        "WIKIPÉDIA SEULE" if mode == "sans_ia" else "WIKIPÉDIA + WEB · IA",
        ui_app.ACCENT_TEXTE,
        ui_app.ACCENT_DOUX,
    ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        bandeau,
        text=f"{session.get('titre') or ''} · {str(session.get('cree_le') or '')[:16].replace('T', ' ')}",
        font=(ui._police, 12),
        text_color=ui_app.DOUX,
        anchor="w",
    ).pack(side="left")
    actions = ctk.CTkFrame(bandeau, fg_color="transparent")
    actions.pack(side="right")
    titre_sujet = str(session.get("titre") or dossier.get("titre") or "Recherche").strip()
    ui._bouton(actions, "Créer une idée", lambda: _vers_idees(ui, titre_sujet), width=112).pack(side="left", padx=2)
    ui._bouton(actions, "Créer un hook", lambda: _vers_hooks(ui, titre_sujet), width=112).pack(side="left", padx=2)
    ui._bouton(actions, "Note pour le Cerveau", lambda: _vers_note(ui, session, dossier), width=150).pack(side="left", padx=2)
    ui._recherche_etat.set(
        f"{len(dossier.get('sources') or [])} source(s) · "
        f"{len(dossier.get('faits') or [])} affirmation(s) · "
        f"{len(dossier.get('angles') or [])} angle(s)."
    )

    if mode == "sans_ia":
        if dossier.get("resume"):
            cadre, corps = ui._carte(zone, "Résumé local (extraction, sans IA)", cle="recherche-resume-local")
            cadre.pack(fill="x", pady=(0, 10))
            _texte(ui, corps, dossier["resume"], taille=13)
        for manquant in dossier.get("manquants") or []:
            _texte(ui, zone, f"Aucune page trouvée pour « {manquant} ». Essayez un terme plus général.", couleur=ui_app.ALERTE)
        _texte(
            ui,
            zone,
            "Lecture directe des pages Wikipédia. Aucune synthèse n'est inventée : "
            "les extraits sont affichés tels quels, mot-clé par mot-clé.",
            taille=12,
            couleur=ui_app.DOUX,
            pady=(4, 8),
        )
        groupes = _sources_par_mot(dossier)
        for mot, pages in groupes.items():
            cadre, corps = ui._carte(zone, f"Wikipédia · {mot}", cle=f"wiki-{mot}")
            cadre.pack(fill="x", pady=(0, 10))
            for source in pages:
                _carte_source(ui, corps, source)
        if not groupes:
            _texte(ui, zone, "Aucune page Wikipédia n'a été conservée.", couleur=ui_app.ALERTE)
    else:
        intention = dossier.get("intention") or {}
        cadre, corps = ui._carte(
            zone,
            "Ce que le spectateur attend",
            "Hypothèses éditoriales déduites du titre et des mots-clés, pas des faits.",
            cle="recherche-intention",
        )
        cadre.pack(fill="x", pady=(0, 10))
        if intention:
            _texte(
                ui,
                corps,
                intention.get("sujet_reformule") or session.get("titre"),
                taille=13,
                gras=True,
            )
            for element in intention.get("public_attendu") or []:
                _puce(ui, corps, element)
            if intention.get("intention_createur"):
                _texte(
                    ui,
                    corps,
                    "Intention du créateur : " + str(intention["intention_createur"]),
                    taille=12,
                    couleur=ui_app.DOUX,
                    pady=(8, 2),
                )
            for question in intention.get("questions_utiles") or []:
                _puce(ui, corps, question, ui_app.DOUX)
        else:
            _texte(ui, corps, "Aucune analyse d'intention disponible.", couleur=ui_app.ALERTE)

        if dossier.get("resume"):
            cadre, corps = ui._carte(zone, "Synthèse prudente", cle="recherche-resume")
            cadre.pack(fill="x", pady=(0, 10))
            _texte(ui, corps, dossier["resume"], taille=13)

        if dossier.get("faits"):
            cadre, corps = ui._carte(
                zone,
                "Affirmations et recoupement",
                "Vert : au moins trois domaines web indépendants et reconnus. "
                "Ambre : information à vérifier, jamais présentée comme certaine.",
                cle="recherche-faits",
            )
            cadre.pack(fill="x", pady=(0, 10))
            for fait in dossier["faits"]:
                _bloc_fait(ui, corps, dossier, fait)

        if dossier.get("chiffres"):
            cadre, corps = ui._carte(zone, "Chiffres", cle="recherche-chiffres")
            cadre.pack(fill="x", pady=(0, 10))
            for chiffre in dossier["chiffres"]:
                _bloc_chiffre(ui, corps, dossier, chiffre)

        if dossier.get("contradictions"):
            cadre, corps = ui._carte(
                zone,
                "Points de désaccord",
                "Les sources ne disent pas la même chose : le dossier le signale.",
                cle="recherche-contradictions",
            )
            cadre.pack(fill="x", pady=(0, 10))
            for contradiction in dossier["contradictions"]:
                _texte(ui, corps, contradiction.get("point"), gras=True)
                _texte(ui, corps, contradiction.get("explication"), taille=12, couleur=ui_app.DOUX)
                _ligne_sources(ui, corps, dossier, contradiction.get("sources") or [])

        if dossier.get("angles"):
            cadre, corps = ui._carte(
                zone,
                "Angles possibles pour la vidéo",
                "Des traitements appuyés sur le corpus, sans fait supplémentaire.",
                cle="recherche-angles",
            )
            cadre.pack(fill="x", pady=(0, 10))
            for angle in dossier["angles"]:
                _texte(ui, corps, angle.get("titre"), taille=13, gras=True, pady=(8, 1))
                _texte(ui, corps, angle.get("angle"), taille=12)
                _texte(ui, corps, angle.get("pourquoi"), taille=11, couleur=ui_app.DOUX)
                _ligne_sources(ui, corps, dossier, angle.get("sources") or [])

        if dossier.get("a_verifier"):
            cadre, corps = ui._carte(
                zone,
                "À vérifier avant d'écrire",
                "Ces points ne sont pas établis par trois sources indépendantes.",
                cle="recherche-verifier",
            )
            cadre.pack(fill="x", pady=(0, 10))
            for element in dossier["a_verifier"]:
                _puce(ui, corps, element, ui_app.ALERTE)

        web = [source for source in dossier.get("sources") or [] if source.get("type") == "web"]
        wiki = [source for source in dossier.get("sources") or [] if source.get("type") == "wikipedia"]
        if wiki:
            cadre, corps = ui._carte(zone, "Pages Wikipédia consultées", cle="recherche-wiki")
            cadre.pack(fill="x", pady=(0, 10))
            for source in wiki:
                _carte_source(ui, corps, source, avec_entete=True)
        if web:
            cadre, corps = ui._carte(
                zone,
                "Sources web complémentaires",
                "Le badge vert signale un domaine éditorial connu ; il ne garantit pas "
                "à lui seul la véracité.",
                cle="recherche-web",
            )
            cadre.pack(fill="x")
            for source in web:
                _carte_source(ui, corps, source, avec_entete=True)


def _vers_idees(ui, titre: str) -> None:
    ui._idees_question.set(titre)
    ui._idees_etat.set("Sujet repris depuis la recherche. Lancez la génération d'idées.")
    ui._afficher_page("idees")
    ui._message.set("Sujet de recherche transmis à la page Idées.")


def _vers_hooks(ui, titre: str) -> None:
    ui._hooks_sujet.set(titre)
    ui._hooks_etat.set("Sujet repris depuis la recherche. Générez les accroches.")
    ui._afficher_page("hooks")
    ui._message.set("Sujet de recherche transmis à la page Hooks.")


def _vers_note(ui, session: dict, dossier: dict) -> None:
    titre = str(session.get("titre") or dossier.get("titre") or "Recherche")[:200]
    morceaux = []
    if dossier.get("resume"):
        morceaux.append(str(dossier["resume"]))
    for source in (dossier.get("sources") or [])[:20]:
        ligne = str(source.get("titre") or "Source")
        if source.get("url"):
            ligne += " — " + str(source["url"])
        morceaux.append(ligne)
    try:
        notes.creer(
            ui.racine, titre="Recherche — " + titre,
            contenu=(chr(10)*2).join(morceaux) or titre,
            type="note", tags=["recherche", "à vérifier"], source="Recherche Outly",
        )
    except notes.ErreurNotes as probleme:
        ui._message.set(f"Note impossible : {probleme}")
        return
    ui._message.set("Dossier enregistré dans Notes : le Cerveau IA le lira à sa prochaine analyse.")


def _lancer(ui) -> None:
    titre = ui._recherche_titre.get().strip()
    mots = ui._recherche_mots.get("1.0", "end-1c")
    try:
        titre_propre = recherche.normaliser_titre(titre)
        mots_propres = recherche.normaliser_mots_cles(mots)
    except recherche.ErreurRecherche as probleme:
        ui._recherche_etat.set(str(probleme))
        return

    avec_ia = ui._recherche_mode.get() == MODE_AVEC_IA
    variable_exa = getattr(ui, "_env_vars", {}).get("EXA_API_KEY")
    cle_exa = (
        variable_exa.get() if variable_exa is not None else ui._env_initial.get("EXA_API_KEY", "")
    )
    if avec_ia and not cle_exa.strip():
        message = (
            "Le mode avec IA a besoin d'EXA_API_KEY pour interroger d'autres sites.\n\n"
            "Ajoutez la clé dans « Clés & modèle », ou utilisez le mode sans IA."
        )
        ui._recherche_etat.set("Clé Exa absente — le mode avec IA ne peut pas démarrer.")
        from tkinter import messagebox

        messagebox.showwarning("Recherche avec IA", message, parent=ui)
        return

    arguments = arguments_recherche(
        titre_propre,
        mots_propres,
        avec_ia=avec_ia,
        forcer=ui._recherche_forcer.get(),
    )
    if recherche.compter_mots_cles(mots) > recherche.MAX_MOTS_CLES:
        message = (f"Vous avez saisi plus de {recherche.MAX_MOTS_CLES} mots-clés. "
                   f"Seuls les {recherche.MAX_MOTS_CLES} premiers seront utilisés.")
        ui._recherche_etat.set(message)
        from tkinter import messagebox
        messagebox.showwarning("Limite de mots-clés", message, parent=ui)
        return
    ui._recherche_etat.set(
        "Recherche en cours… Wikipédia"
        + (" et Exa, puis synthèse." if avec_ia else ".")
    )
    ui._lancer(arguments, "Recherche")


def _remplir(ui) -> None:
    if not hasattr(ui, "_recherche_zone"):
        return
    if ui._tache is not None and ui._libelle_tache == "Recherche":
        return
    session = data.derniere_recherche(ui.racine)
    zone = ui._recherche_zone
    for enfant in zone.winfo_children():
        enfant.destroy()
    if not session:
        _texte(
            ui,
            zone,
            "Aucun dossier pour l'instant. Saisissez un titre et des mots-clés, "
            "puis choisissez « Sans IA » (Wikipédia seule) ou « Avec IA » "
            "(Wikipédia + autres sites).",
            taille=12,
            couleur=ui_app.DOUX,
        )
        return
    _afficher_dossier(ui, zone, session)


def construire(ui) -> None:
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["recherche"] = page

    zone = ui._zone_defilante(page)
    contenu = ctk.CTkFrame(zone, fg_color="transparent")
    contenu.pack(fill="x")

    ui._recherche_titre = tk.StringVar(value="")
    ui._recherche_mode = tk.StringVar(value=MODE_SANS_IA)
    ui._recherche_forcer = tk.BooleanVar(value=False)
    ui._recherche_etat = tk.StringVar(value="Aucune recherche pour l'instant.")

    cadre, corps = ui._carte(
        contenu,
        "Chercher un sujet",
        "Le titre donne le cap ; les mots-clés sont les portes d'entrée documentaires. "
        "Aucune information ne sera affirmée sans source réelle.",
    )
    cadre.pack(fill="x", pady=(0, 12))

    ctk.CTkLabel(
        corps, text="TITRE DU SUJET", font=(ui._police, 10, "bold"),
        text_color=ui_app.DOUX, anchor="w",
    ).pack(anchor="w")
    ctk.CTkEntry(
        corps,
        textvariable=ui._recherche_titre,
        height=40,
        corner_radius=7,
        fg_color=ui_app.CHAMP,
        border_color=ui_app.BORDURE,
        border_width=1,
        text_color=ui_app.TEXTE,
        placeholder_text="Ex. : Pourquoi les villes sont-elles plus chaudes que les campagnes ?",
        font=(ui._police, 14, "bold"),
    ).pack(fill="x", pady=(4, 12))

    ctk.CTkLabel(
        corps, text="MOTS-CLÉS — UN PAR LIGNE OU SÉPARÉS PAR DES VIRGULES",
        font=(ui._police, 10, "bold"), text_color=ui_app.DOUX, anchor="w",
    ).pack(anchor="w")
    ui._recherche_mots = ctk.CTkTextbox(
        corps,
        height=92,
        corner_radius=7,
        fg_color=ui_app.CHAMP,
        border_color=ui_app.BORDURE,
        border_width=1,
        text_color=ui_app.TEXTE,
        font=(ui._police, 13),
        wrap="word",
    )
    ui._recherche_mots.pack(fill="x", pady=(4, 12))
    ui._recherche_mots.insert(
        "1.0",
        "Exemples : îlot de chaleur urbain, canicule, végétalisation, climat urbain",
    )
    compteur = ctk.CTkLabel(corps, text="", font=(ui._police, 11), text_color=ui_app.DOUX, anchor="w")
    compteur.pack(anchor="w", pady=(0, 8))
    def _maj_compteur(_event=None):
        nombre = recherche.compter_mots_cles(ui._recherche_mots.get("1.0", "end-1c"))
        compteur.configure(
            text=f"{nombre} mot(s)-clé(s) · {recherche.MAX_MOTS_CLES} maximum · "
                 f"3 pages Wikipédia par mot-clé · 18 sources web · 4 résultats Exa par requête",
            text_color=ui_app.ALERTE if nombre > recherche.MAX_MOTS_CLES else ui_app.DOUX,
        )
    ui._recherche_mots.bind("<KeyRelease>", _maj_compteur)
    _maj_compteur()

    mode = ctk.CTkSegmentedButton(
        corps,
        values=[MODE_SANS_IA, MODE_AVEC_IA],
        variable=ui._recherche_mode,
        height=36,
        corner_radius=7,
        selected_color=ui_app.ACCENT,
        selected_hover_color=ui_app.ACCENT_SURVOL,
        unselected_color=ui_app.SURFACE_HAUTE,
        unselected_hover_color=ui_app.BORDURE,
        text_color=ui_app.TEXTE,
        font=(ui._police, 13, "bold"),
    )
    mode.pack(fill="x", pady=(0, 6))

    aide = ctk.CTkLabel(
        corps,
        text="Sans IA : Wikipédia uniquement, aucun appel LLM, aucun crédit.\n"
        "Avec IA : Wikipédia + Exa, puis un dossier hiérarchisé. Une affirmation "
        "n'est « recoupée » que si trois domaines web indépendants et connus la confirment.",
        font=(ui._police, 11),
        text_color=ui_app.DOUX,
        justify="left",
        anchor="w",
        wraplength=800,
    )
    aide.pack(anchor="w", fill="x", pady=(2, 10))
    ui._auto_wrap(aide)

    actions = ctk.CTkFrame(corps, fg_color="transparent")
    actions.pack(fill="x")
    bouton = ui._bouton_accent(actions, "Lancer la recherche", lambda: _lancer(ui))
    bouton.pack(side="left")
    ui._boutons_execution.append(bouton)
    ctk.CTkCheckBox(
        actions,
        text="Forcer un nouveau dossier (ignorer le cache)",
        variable=ui._recherche_forcer,
        font=(ui._police, 12),
        text_color=ui_app.DOUX,
        fg_color=ui_app.ACCENT,
        hover_color=ui_app.ACCENT_SURVOL,
        border_color=ui_app.BORDURE,
    ).pack(side="left", padx=(16, 0))

    ctk.CTkLabel(
        corps,
        textvariable=ui._recherche_etat,
        font=(ui._police, 11),
        text_color=ui_app.DOUX_FONCE,
        anchor="w",
        justify="left",
        wraplength=820,
    ).pack(fill="x", pady=(10, 0))

    ui._recherche_zone = ctk.CTkFrame(contenu, fg_color="transparent")
    ui._recherche_zone.pack(fill="x")
    _remplir(ui)
