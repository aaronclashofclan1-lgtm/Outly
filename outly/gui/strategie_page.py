"""Page « Stratégie » : diagnostic des vidéos et ajustements éditoriaux.

La page ne calcule rien : elle lit le dernier audit, le dernier diagnostic
vidéo et le cache Studio. L'analyse locale est visible même sans LLM ; les
causes et ajustements apparaissent après « Analyser mes dernières vidéos ».
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from . import app as ui_app
from . import data, icones


def _texte(ui, parent, valeur, taille=13, couleur=None, gras=False, pady=(0, 4)):
    libelle = ctk.CTkLabel(
        parent,
        text=str(valeur or ""),
        font=(ui._police, taille, "bold" if gras else "normal"),
        text_color=couleur or ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=780,
    )
    libelle.pack(anchor="w", fill="x", pady=pady)
    ui._auto_wrap(libelle)
    return libelle


def _etiquette(ui, parent, texte, couleur=None, fond=None):
    return ctk.CTkLabel(
        parent,
        text=str(texte or ""),
        font=(ui._police, 10, "bold"),
        text_color=couleur or ui_app.ACCENT_TEXTE,
        fg_color=fond or ui_app.ACCENT_DOUX,
        corner_radius=8,
        padx=8,
        pady=2,
    )


def construire(ui) -> None:
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["strategie"] = page
    zone = ui._zone_defilante(page)
    contenu = ctk.CTkFrame(zone, fg_color="transparent")
    contenu.pack(fill="x")

    cadre, corps = ui._carte(
        contenu,
        "Ce que disent vos dernières vidéos",
        "Un diagnostic local immédiat, puis une analyse IA des causes probables et "
        "des ajustements à tester. Les causes « voix » ou « montage » restent des "
        "hypothèses : Analytics ne les mesure pas directement.",
        cle="strategie-entete",
    )
    cadre.pack(fill="x")
    actions = ctk.CTkFrame(corps, fg_color="transparent")
    actions.pack(fill="x", pady=(0, 8))
    ui._bouton_accent(
        actions,
        "Analyser mes dernières vidéos",
        ui._lancer_strategie,
        image=icones.image("ia", 17, ui_app.TEXTE_SUR_ACCENT),
        compound="left",
    ).pack(side="left")
    ctk.CTkCheckBox(
        actions,
        text="Forcer",
        variable=ui._strategie_forcer,
        font=(ui._police, 11),
        text_color=ui_app.DOUX,
        fg_color=ui_app.ACCENT,
        hover_color=ui_app.ACCENT_SURVOL,
        border_color=ui_app.BORDURE,
        checkmark_color=ui_app.TEXTE_SUR_ACCENT,
        corner_radius=4,
    ).pack(side="left", padx=(12, 0))
    ctk.CTkLabel(
        actions, text="Vidéos :", font=(ui._police, 11), text_color=ui_app.DOUX
    ).pack(side="left", padx=(14, 4))
    ctk.CTkOptionMenu(
        actions,
        values=["5", "10", "15", "20"],
        variable=ui._strategie_jours,
        width=66,
        height=28,
        fg_color=ui_app.CHAMP,
        button_color=ui_app.SURFACE_HAUTE,
        button_hover_color=ui_app.BORDURE,
        dropdown_fg_color=ui_app.SURFACE,
        dropdown_hover_color=ui_app.ACCENT_DOUX,
        dropdown_text_color=ui_app.TEXTE,
        text_color=ui_app.TEXTE,
        font=(ui._police, 11),
    ).pack(side="left")
    ctk.CTkLabel(
        corps,
        textvariable=ui._strategie_etat,
        font=(ui._police, 11),
        text_color=ui_app.DOUX_FONCE,
        anchor="w",
        justify="left",
        wraplength=760,
    ).pack(fill="x", pady=(6, 10))

    ui._strategie_segment = ctk.CTkSegmentedButton(
        corps,
        values=["Vue d'ensemble", "Vidéos", "Ajustements"],
        command=lambda valeur: _afficher_vue(ui, valeur),
        height=32,
        corner_radius=8,
        fg_color=ui_app.SURFACE_HAUTE,
        selected_color=ui_app.ACCENT,
        selected_hover_color=ui_app.ACCENT_SURVOL,
        unselected_color=ui_app.SURFACE_HAUTE,
        unselected_hover_color=ui_app.BORDURE,
        text_color=ui_app.TEXTE,
        font=(ui._police, 12),
    )
    ui._strategie_segment.set("Vue d'ensemble")
    ui._strategie_segment.pack(fill="x")

    ui._strategie_vues = {}
    for nom in ("Vue d'ensemble", "Vidéos", "Ajustements"):
        vue = ctk.CTkFrame(contenu, fg_color="transparent")
        ui._strategie_vues[nom] = vue
    ui._strategie_vue.set("Vue d'ensemble")
    _afficher_vue(ui, "Vue d'ensemble")
    remplir(ui)


def _afficher_vue(ui, nom: str) -> None:
    ui._strategie_vue.set(nom)
    for cle, vue in getattr(ui, "_strategie_vues", {}).items():
        if cle == nom:
            vue.pack(fill="x", pady=(12, 0))
        else:
            vue.pack_forget()


def remplir(ui) -> None:
    if not hasattr(ui, "_strategie_vues"):
        return
    if ui._tache is not None and getattr(ui, "_libelle_tache", "") == "Stratégie":
        return
    for vue in ui._strategie_vues.values():
        for enfant in vue.winfo_children():
            enfant.destroy()

    audit = data.derniere_audit(ui.racine)
    review = data.derniere_strategie(ui.racine)
    strategie = (audit or {}).get("reponse", {}).get("strategie") or {}
    local = None if review else data.contexte_strategie(ui.racine, 10)
    contexte = (review or {}).get("contexte") or (local or {})
    donnees = (review or {}).get("donnees") or {}

    if review:
        ui._strategie_etat.set(
            "Dernier diagnostic : "
            + ui_app._date_lisible(review.get("cree_le"))
            + " · "
            + str(review.get("fournisseur") or "LLM")
        )
    elif contexte.get("videos"):
        ui._strategie_etat.set(
            f"Analyse locale · source {contexte.get('source') or 'inconnue'} · "
            "lancez l'analyse IA pour les causes et les ajustements."
        )
    elif audit:
        ui._strategie_etat.set(
            "Audit disponible. Lancez l'analyse des dernières vidéos pour relier "
            "les résultats aux causes probables."
        )
    else:
        ui._strategie_etat.set(
            "Aucun audit ni diagnostic. Commencez par l'audit de votre chaîne."
        )

    _vue_ensemble(ui, ui._strategie_vues["Vue d'ensemble"], strategie, audit)
    _vue_videos(ui, ui._strategie_vues["Vidéos"], contexte, donnees, review)
    _vue_ajustements(ui, ui._strategie_vues["Ajustements"], donnees, review)
    _afficher_vue(ui, ui._strategie_vue.get() or "Vue d'ensemble")


def _vue_ensemble(ui, parent, strategie: dict, audit: dict | None) -> None:
    if not strategie:
        cadre, corps = ui._carte(parent, "Stratégie à construire")
        cadre.pack(fill="x")
        _texte(
            ui,
            corps,
            "L'audit produit le positionnement, les piliers, la cadence et les "
            "indicateurs. Sans lui, la page ne peut afficher que les performances.",
            couleur=ui_app.DOUX,
        )
        ui._bouton(
            corps,
            "Ouvrir l'audit",
            lambda: ui._afficher_page("audit"),
            image=icones.image("audit", 16, ui_app.DOUX),
            compound="left",
        ).pack(anchor="w", pady=(8, 0))
        return
    cadre, corps = ui._carte(
        parent,
        "Où vous allez",
        "Le cadre stable de la chaîne : ce que vous promettez, à qui, à quel rythme.",
        cle="strategie-positionnement",
    )
    cadre.pack(fill="x")
    if strategie.get("positionnement"):
        _texte(ui, corps, "POSITIONNEMENT", taille=10, couleur=ui_app.DOUX)
        _texte(ui, corps, strategie["positionnement"], taille=15, pady=(0, 8))
    if strategie.get("promesse"):
        _texte(ui, corps, "PROMESSE", taille=10, couleur=ui_app.DOUX)
        _texte(ui, corps, strategie["promesse"], taille=13, pady=(0, 6))
    if strategie.get("cadence"):
        _etiquette(ui, corps, "CADENCE · " + str(strategie["cadence"])).pack(
            anchor="w", pady=(4, 0)
        )

    piliers = strategie.get("piliers") or []
    if piliers:
        grille = ctk.CTkFrame(parent, fg_color="transparent")
        grille.pack(fill="x", pady=(10, 0))
        for index, pilier in enumerate(piliers[:4]):
            if not isinstance(pilier, dict):
                continue
            ligne, colonne = divmod(index, 2)
            grille.grid_columnconfigure(colonne, weight=1, uniform="pilier")
            bloc = ctk.CTkFrame(grille, fg_color=ui_app.SURFACE, corner_radius=7)
            bloc.grid(
                row=ligne,
                column=colonne,
                sticky="nsew",
                padx=(0, 8) if colonne == 0 else 0,
                pady=(0, 8),
            )
            _texte(
                ui,
                bloc,
                "PILIER · " + str(pilier.get("nom") or ""),
                taille=10,
                couleur=ui_app.ACCENT_TEXTE,
                pady=(10, 2),
            )
            _texte(ui, bloc, pilier.get("promesse") or "", taille=12, pady=(0, 2))
            if pilier.get("formats"):
                _texte(ui, bloc, pilier["formats"], taille=11, couleur=ui_app.DOUX)
            for titre in (pilier.get("exemples_de_titres") or [])[:2]:
                _texte(ui, bloc, "•  " + str(titre), taille=11, couleur=ui_app.DOUX_FONCE)
            ctk.CTkFrame(bloc, fg_color="transparent", height=4).pack()

    kpis = strategie.get("kpis") or []
    if kpis:
        grille = ctk.CTkFrame(parent, fg_color="transparent")
        grille.pack(fill="x", pady=(4, 0))
        for index, kpi in enumerate(kpis[:4]):
            if not isinstance(kpi, dict):
                continue
            grille.grid_columnconfigure(index, weight=1, uniform="kpi")
            bloc = ctk.CTkFrame(grille, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
            bloc.grid(row=0, column=index, sticky="nsew", padx=(0, 6) if index < 3 else 0)
            _texte(ui, bloc, kpi.get("indicateur") or "", taille=10, couleur=ui_app.DOUX, pady=(10, 0))
            _texte(ui, bloc, kpi.get("cible") or "", taille=17, gras=True, pady=(0, 2))
            _texte(ui, bloc, kpi.get("pourquoi") or "", taille=10, couleur=ui_app.DOUX_FONCE, pady=(0, 10))


def _vue_videos(ui, parent, contexte: dict, donnees: dict, review: dict | None) -> None:
    videos = contexte.get("videos") or []
    ia_par_id = {
        str(video.get("video_id")): video
        for video in donnees.get("videos") or []
        if isinstance(video, dict)
    }
    if videos and not review:
        cadre, corps = ui._carte(
            parent,
            "Analyse locale, sans IA",
            "Les constats ci-dessous sont calculés sur les mesures disponibles. "
            "Les causes probables et les ajustements demandent une clé LLM.",
        )
        cadre.pack(fill="x", pady=(0, 10))
        _etiquette(
            ui,
            corps,
            "SOURCE · " + str(contexte.get("source") or "base").upper(),
        ).pack(anchor="w")
        for limite in contexte.get("limites") or []:
            _texte(ui, corps, "•  " + str(limite), taille=11, couleur=ui_app.DOUX, pady=(4, 0))

    if not videos:
        cadre, corps = ui._carte(parent, "Aucune vidéo analysée")
        cadre.pack(fill="x")
        _texte(
            ui,
            corps,
            "Connectez le Studio YouTube pour la rétention et le CTR, ou lancez une "
            "veille pour les vues publiques. Ensuite, lancez l'analyse.",
            couleur=ui_app.DOUX,
        )
        rangee = ctk.CTkFrame(corps, fg_color="transparent")
        rangee.pack(fill="x", pady=(8, 0))
        ui._bouton(
            rangee,
            "Ouvrir le Studio",
            lambda: ui._afficher_page("studio"),
            image=icones.image("studio", 16, ui_app.DOUX),
            compound="left",
        ).pack(side="left")
        ui._bouton(
            rangee,
            "Lancer une veille",
            lambda: ui._afficher_page("lancer"),
            image=icones.image("lancer", 16, ui_app.DOUX),
            compound="left",
        ).pack(side="left", padx=(8, 0))
        return

    synthese = (review or {}).get("donnees", {}).get("synthese")
    if synthese:
        cadre, corps = ui._carte(parent, "Synthèse du diagnostic")
        cadre.pack(fill="x", pady=(0, 10))
        _texte(ui, corps, synthese, taille=13)
    for video in videos:
        vid = str(video.get("video_id") or video.get("identifiant") or "")
        diagnostic = ia_par_id.get(vid) or {}
        verdict = diagnostic.get("verdict") or video.get("verdict_local") or "—"
        couleur = (
            ui_app.ERREUR
            if verdict == "sous-performe"
            else (ui_app.ALERTE if verdict in ("fragile", "trop récent") else ui_app.OK)
        )
        cadre, corps = ui._carte(
            parent,
            video.get("titre") or "Vidéo",
            cle="video-" + vid,
        )
        cadre.pack(fill="x", pady=(0, 10))
        entete = ctk.CTkFrame(corps, fg_color="transparent")
        entete.pack(fill="x", pady=(0, 6))
        _etiquette(
            ui, entete, verdict.upper(), couleur=couleur, fond=ui_app.SURFACE_HAUTE
        ).pack(side="left")
        for libelle in (
            f"{ui_app._nombre(video.get('vues'))} vues",
            f"ratio {video.get('ratio_median')}×" if video.get("ratio_median") else "médiane inconnue",
            f"rétention {video.get('pourcentage_vu'):.1f} %" if video.get("pourcentage_vu") else "rétention inconnue",
            f"CTR {video.get('ctr'):.2f} %" if video.get("ctr") is not None else "CTR inconnu",
        ):
            ctk.CTkLabel(
                entete, text=libelle, font=(ui._police, 10), text_color=ui_app.DOUX
            ).pack(side="left", padx=(8, 0))
        for cause in diagnostic.get("causes") or []:
            bloc = ctk.CTkFrame(corps, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
            bloc.pack(fill="x", pady=(0, 5))
            _texte(
                ui,
                bloc,
                str(cause.get("cause") or "").upper()
                + (" · HYPOTHÈSE" if cause.get("hypothese") else ""),
                taille=10,
                couleur=ui_app.ALERTE if cause.get("hypothese") else ui_app.ACCENT_TEXTE,
                pady=(8, 1),
            )
            _texte(ui, bloc, "Preuve : " + str(cause.get("preuve") or ""), taille=11, pady=(0, 1))
            _texte(ui, bloc, "Test : " + str(cause.get("test") or ""), taille=11, couleur=ui_app.DOUX, pady=(0, 8))
        if diagnostic.get("action_prioritaire"):
            _texte(
                ui,
                corps,
                "ACTION PRIORITAIRE · " + str(diagnostic["action_prioritaire"]),
                taille=11,
                couleur=ui_app.OK,
                pady=(4, 2),
            )
        if not diagnostic:
            for signal in video.get("signaux") or []:
                _texte(
                    ui,
                    corps,
                    "•  " + str(signal.get("libelle") or "")
                    + " (" + str(signal.get("preuve") or "") + ")",
                    taille=11,
                    couleur=ui_app.DOUX,
                    pady=(0, 1),
                )


def _vue_ajustements(ui, parent, donnees: dict, review: dict | None) -> None:
    ajustements = donnees.get("ajustements_strategie") or []
    tests = donnees.get("tests_prochains") or []
    limites = donnees.get("limites") or []
    if not ajustements:
        cadre, corps = ui._carte(parent, "Aucun ajustement pour l'instant")
        cadre.pack(fill="x")
        _texte(
            ui,
            corps,
            "Lancez l'analyse des dernières vidéos : les ajustements apparaîtront ici, "
            "reliés aux constats mesurés.",
            couleur=ui_app.DOUX,
        )
        return
    cadre, corps = ui._carte(
        parent,
        "Ce qu'il faut changer",
        "Chaque ajustement part d'un constat. L'effort indique le coût de mise en œuvre.",
        cle="strategie-ajustements",
    )
    cadre.pack(fill="x", pady=(0, 10))
    for ajustement in ajustements:
        bloc = ctk.CTkFrame(corps, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
        bloc.pack(fill="x", pady=(0, 7))
        entete = ctk.CTkFrame(bloc, fg_color="transparent")
        entete.pack(fill="x", padx=12, pady=(9, 2))
        _texte(ui, entete, ajustement.get("axe") or "", taille=12, gras=True, pady=0)
        _etiquette(
            ui, entete, "EFFORT " + str(ajustement.get("effort") or "").upper()
        ).pack(side="right")
        _texte(
            ui,
            bloc,
            f"Avant : {ajustement.get('avant')}  →  Après : {ajustement.get('apres')}",
            taille=12,
            pady=(0, 2),
        )
        _texte(
            ui,
            bloc,
            str(ajustement.get("pourquoi") or ""),
            taille=11,
            couleur=ui_app.DOUX,
            pady=(0, 9),
        )
    if tests:
        cadre, corps = ui._carte(parent, "Prochains tests", "Un test, un critère, un délai.")
        cadre.pack(fill="x", pady=(0, 10))
        for test in tests:
            _texte(ui, corps, "•  " + str(test.get("test") or ""), taille=12, pady=(0, 1))
            _texte(
                ui,
                corps,
                "Réussite : " + str(test.get("critere_reussite") or "")
                + " · " + str(test.get("delai") or ""),
                taille=11,
                couleur=ui_app.DOUX,
                pady=(0, 6),
            )
    if limites:
        cadre, corps = ui._carte(parent, "Ce que les données ne disent pas")
        cadre.pack(fill="x")
        for limite in limites:
            _texte(ui, corps, "•  " + str(limite), taille=11, couleur=ui_app.DOUX)


__all__ = ["construire", "remplir"]
