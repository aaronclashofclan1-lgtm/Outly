"""Page « Audit » : diagnostic complet de la chaîne et stratégie de contenu.

La page ne calcule rien elle-même : elle lit le dernier audit en cache et
l'affiche en sections lisibles (secteur, diagnostic, concurrents, stratégie,
plan des 30 jours, indicateurs, idées). Le bouton principal lance la commande
`audit`, dont la sortie alimente le journal de la page Lancer.
"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from .. import notes, plan
from . import app as ui_app
from . import data
from .runner import arguments_audit


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
        wraplength=760,
    )
    libelle.pack(anchor="w", fill="x", pady=pady)
    ui._auto_wrap(libelle)
    return libelle


def _etiquette(ui, parent, texte, couleur=None, fond=None) -> ctk.CTkLabel:
    libelle = ctk.CTkLabel(
        parent,
        text=str(texte or ""),
        font=(ui._police, 11, "bold"),
        text_color=couleur or ui_app.ACCENT_TEXTE,
        fg_color=fond or ui_app.ACCENT_DOUX,
        corner_radius=9,
        padx=9,
        pady=3,
    )
    return libelle


def _puce(ui, parent, valeur, couleur=None, pady=(0, 2)) -> None:
    libelle = ctk.CTkLabel(
        parent,
        text="•  " + str(valeur or ""),
        font=(ui._police, 12),
        text_color=couleur or ui_app.DOUX,
        anchor="w",
        justify="left",
        wraplength=720,
    )
    libelle.pack(anchor="w", fill="x", pady=pady)
    ui._auto_wrap(libelle)


def _section(ui, parent, titre: str, sous_titre: str | None = None):
    cadre, corps = ui._carte(parent, titre, sous_titre)
    cadre.pack(fill="x", pady=(0, 12))
    return corps


def _constat(ui, parent, constat: dict) -> None:
    _texte(ui, parent, constat.get("constat") or "", taille=12, pady=(2, 0))
    if constat.get("preuve"):
        _texte(
            ui, parent, constat["preuve"], taille=11,
            couleur=ui_app.DOUX_FONCE, pady=(0, 6),
        )


def construire(ui) -> None:
    """Construit la page Audit et son état, puis affiche le dernier audit."""
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["audit"] = page
    zone = ui._zone_defilante(page)
    contenu = ctk.CTkFrame(zone, fg_color="transparent")
    contenu.pack(fill="x")

    ui._audit_etat = tk.StringVar(value="")
    if not hasattr(ui, "_audit_forcer"):
        ui._audit_forcer = tk.BooleanVar(value=False)
    if not hasattr(ui, "_audit_recuperation"):
        ui._audit_recuperation = tk.BooleanVar(value=True)

    cadre, corps = ui._carte(
        contenu,
        "Audit complet de votre chaîne",
        "Secteur, diagnostic chiffré, pratiques des concurrents, stratégie de création et "
        "dix idées prêtes à tourner. Tout part de vos données : la chaîne renseignée dans "
        "« Mon contenu », vos notes classées, vos mots-clés et les concurrents déjà mesurés.",
    )
    cadre.pack(fill="x")

    rangee = ctk.CTkFrame(corps, fg_color="transparent")
    rangee.pack(fill="x", pady=(0, 10))
    for texte, variable in (
        ("Compléter ma chaîne avec YouTube", ui._audit_recuperation),
        ("Forcer un nouvel audit", ui._audit_forcer),
    ):
        ctk.CTkCheckBox(
            rangee,
            text=texte,
            variable=variable,
            font=(ui._police, 12),
            text_color=ui_app.DOUX,
            fg_color=ui_app.ACCENT,
            hover_color=ui_app.ACCENT_SURVOL,
            border_color=ui_app.BORDURE,
            checkmark_color=ui_app.TEXTE_SUR_ACCENT,
            corner_radius=4,
        ).pack(side="left", padx=(0, 18))

    actions = ctk.CTkFrame(corps, fg_color="transparent")
    actions.pack(fill="x")
    bouton = ui._bouton_accent(actions, "Auditer ma chaîne", lambda: _lancer(ui))
    bouton.pack(side="left")
    ui._boutons_execution.append(bouton)
    ui._bouton(
        actions, "Voir mes concurrents", lambda: ui._afficher_page("resultats"), width=150
    ).pack(side="left", padx=(8, 0))
    ui._bouton(
        actions, "Mes notes", lambda: ui._afficher_page("notes"), width=100
    ).pack(side="left", padx=(8, 0))

    etat = ctk.CTkLabel(
        corps,
        textvariable=ui._audit_etat,
        font=(ui._police, 12),
        text_color=ui_app.DOUX_FONCE,
        anchor="w",
        justify="left",
        wraplength=760,
    )
    etat.pack(anchor="w", fill="x", pady=(10, 0))
    ui._auto_wrap(etat)

    ui._audit_corps = ctk.CTkFrame(contenu, fg_color="transparent")
    ui._audit_corps.pack(fill="x", pady=(14, 0))
    ui._zones_blocs[("audit", "principal")] = ui._audit_corps
    _remplir_audit(ui)


def _lancer(ui) -> None:
    if ui._tache is not None:
        return
    ui._audit_etat.set("Audit en cours : lecture de la chaîne, des concurrents et du LLM…")
    ui._message.set("Audit de ma chaîne en cours…")
    ui._lancer(
        arguments_audit(
            forcer=ui._audit_forcer.get(),
            recuperation=ui._audit_recuperation.get(),
        ),
        "Audit",
    )


def _remplir_audit(ui) -> None:
    """Affiche le dernier audit ; ne fait aucun appel réseau."""
    if not hasattr(ui, "_audit_corps"):
        return
    if ui._tache is not None and getattr(ui, "_libelle_tache", "") == "Audit":
        return
    for enfant in ui._audit_corps.winfo_children():
        enfant.destroy()

    session = data.derniere_audit(ui.racine)
    if session is None:
        ui._audit_etat.set(
            "Aucun audit pour l'instant. Renseignez votre chaîne et votre positionnement, "
            "puis lancez l'audit : le diagnostic s'appuie sur des données réelles, jamais "
            "sur des généralités."
        )
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Ce que l'audit va produire",
            "Le LLM reçoit uniquement des faits mesurés dans votre base : aucune promesse "
            "en l'air, chaque constat cite une preuve.",
            cle="vide",
        )
        cadre.pack(fill="x")
        for texte in (
            "Le secteur et la sous-niche réels de votre chaîne, déduits de vos titres, "
            "descriptions et mots-clés.",
            "Un diagnostic forces / faiblesses / opportunités, avec médianes, cadence, "
            "ratios et titres à l'appui.",
            "Ce que font les concurrents proches : formats, cadence, mots-clés, outliers "
            "et accroches déjà analysées.",
            "Une stratégie exécutable : positionnement, piliers, séries, plan des 30 jours "
            "et indicateurs.",
            "Dix idées de vidéos avec un titre prêt à publier, un angle et le format.",
        ):
            _puce(ui, corps, texte)
        ui._bouton(
            corps, "Compléter mes réglages", lambda: ui._afficher_page("contenu"), width=180
        ).pack(anchor="w", pady=(10, 0))
        ui._appliquer_disposition("audit")
        return

    reponse = session.get("reponse") or {}
    profil = session.get("profil") or {}
    secteur = reponse.get("secteur") or {}
    diagnostic = reponse.get("diagnostic") or {}
    concurrents = reponse.get("concurrents") or {}
    strategie = reponse.get("strategie") or {}
    idees = reponse.get("idees") or []
    limites = reponse.get("limites") or []

    identite = profil.get("titre") or "ma chaîne"
    ui._audit_etat.set(
        f"Dernier audit : {_date(ui, session.get('cree_le'))} · "
        f"{session.get('fournisseur') or 'LLM'} / {session.get('modele') or 'modèle inconnu'} · "
        f"{len(idees)} idée(s)"
    )

    # --- Bandeau d'identité et de secteur --------------------------------
    entete, corps = ui._carte(
        ui._audit_corps,
        f"Audit de {identite}",
        "Secteur, promesse et audience : le cadre dans lequel la stratégie s'inscrit.",
        cle="entete",
    )
    entete.pack(fill="x", pady=(0, 12))
    rangee = ctk.CTkFrame(corps, fg_color="transparent")
    rangee.pack(fill="x", pady=(0, 8))
    for libelle in (
        secteur.get("secteur"),
        secteur.get("sous_niche"),
        secteur.get("langue"),
        secteur.get("formats"),
    ):
        if libelle:
            _etiquette(ui, rangee, libelle).pack(side="left", padx=(0, 6), pady=2)
    if secteur.get("audience"):
        _texte(ui, corps, "Audience : " + str(secteur["audience"]), taille=13, pady=(4, 2))
    if secteur.get("promesse"):
        _texte(ui, corps, "Promesse : " + str(secteur["promesse"]), taille=13, pady=(0, 2))
    metriques = profil.get("metriques") or {}
    repere = []
    if profil.get("abonnes") is not None:
        repere.append(f"{ui_app._nombre(profil['abonnes'])} abonné(s)")
    if metriques.get("n_videos") is not None:
        repere.append(f"{metriques['n_videos']} vidéo(s) mesurée(s)")
    if metriques.get("cadence_semaine") is not None:
        repere.append(f"cadence {metriques['cadence_semaine']}/semaine")
    if metriques.get("mediane_vues_longues") is not None:
        repere.append(f"médiane longues {ui_app._nombre(metriques['mediane_vues_longues'])} vues")
    if repere:
        _texte(ui, corps, " · ".join(repere), taille=11, couleur=ui_app.DOUX_FONCE, pady=(6, 0))

    # --- Diagnostic ------------------------------------------------------
    colonnes = (
        ("forces", "Forces", ui_app.OK),
        ("faiblesses", "Faiblesses", ui_app.ALERTE),
        ("opportunites", "Opportunités", ui_app.ACCENT_TEXTE),
    )
    if any(diagnostic.get(cle) for cle, _titre, _couleur in colonnes):
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Diagnostic",
            "Chaque constat s'appuie sur une mesure de votre chaîne ou du paysage concurrentiel.",
            cle="diagnostic",
        )
        cadre.pack(fill="x", pady=(0, 12))
        grille = ctk.CTkFrame(corps, fg_color="transparent")
        grille.pack(fill="x")
        for index, (cle, titre, couleur) in enumerate(colonnes):
            grille.grid_columnconfigure(index, weight=1, uniform="diagnostic")
            colonne = ctk.CTkFrame(
                grille, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6
            )
            colonne.grid(
                row=0, column=index, sticky="nsew",
                padx=(0, 8) if index < 2 else 0,
            )
            ctk.CTkLabel(
                colonne,
                text=titre.upper(),
                font=(ui._police, 10, "bold"),
                text_color=couleur,
                anchor="w",
            ).pack(anchor="w", fill="x", padx=12, pady=(10, 4))
            constats = diagnostic.get(cle) or []
            if not constats:
                _texte(ui, colonne, "Aucun constat.", taille=11, couleur=ui_app.DOUX_FONCE)
            for constat in constats:
                if isinstance(constat, dict):
                    _constat(ui, colonne, constat)
            ctk.CTkFrame(colonne, fg_color="transparent", height=4).pack()

    # --- Paysage concurrentiel -------------------------------------------
    if concurrents.get("paysage") or concurrents.get("pratiques"):
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Ce que font les concurrents",
            "Pratiques réellement observées chez les chaînes les plus proches de la vôtre.",
            cle="concurrents",
        )
        cadre.pack(fill="x", pady=(0, 12))
        if concurrents.get("paysage"):
            _texte(ui, corps, concurrents["paysage"], taille=13, pady=(0, 8))
        for pratique in concurrents.get("pratiques") or []:
            if not isinstance(pratique, dict):
                continue
            bloc = ctk.CTkFrame(corps, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
            bloc.pack(fill="x", pady=(0, 8))
            _texte(
                ui, bloc, pratique.get("pratique") or "", taille=13, gras=True,
                pady=(10, 2),
            )
            if pratique.get("exemples"):
                _texte(ui, bloc, pratique["exemples"], taille=12, couleur=ui_app.DOUX)
            if pratique.get("pourquoi_ca_marche"):
                _texte(
                    ui, bloc, "Pourquoi ça marche : " + str(pratique["pourquoi_ca_marche"]),
                    taille=12, couleur=ui_app.DOUX_FONCE, pady=(0, 10),
                )
            else:
                ctk.CTkFrame(bloc, fg_color="transparent", height=4).pack()
        manques = concurrents.get("manques") or []
        if manques:
            _texte(ui, corps, "MANQUES À EXPLOITER", taille=10, couleur=ui_app.ACCENT_TEXTE, pady=(4, 2))
            for manque in manques:
                _puce(ui, corps, manque)

    # --- Stratégie -------------------------------------------------------
    if strategie:
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Votre stratégie de création",
            "Un positionnement, des piliers, des formats et un rythme tenables : chaque choix "
            "répond à un constat du diagnostic.",
            cle="strategie",
        )
        cadre.pack(fill="x", pady=(0, 12))
        if strategie.get("positionnement"):
            _texte(ui, corps, "POSITIONNEMENT", taille=10, couleur=ui_app.DOUX, pady=(0, 2))
            _texte(ui, corps, strategie["positionnement"], taille=14, pady=(0, 8))
        if strategie.get("promesse"):
            _texte(ui, corps, "PROMESSE", taille=10, couleur=ui_app.DOUX, pady=(0, 2))
            _texte(ui, corps, strategie["promesse"], taille=13, pady=(0, 6))
        if strategie.get("cadence"):
            _texte(ui, corps, "CADENCE  " + str(strategie["cadence"]), taille=12, couleur=ui_app.DOUX_FONCE)

        piliers = strategie.get("piliers") or []
        if piliers:
            _texte(ui, corps, "PILIERS ÉDITORIAUX", taille=10, couleur=ui_app.ACCENT_TEXTE, pady=(12, 4))
            grille = ctk.CTkFrame(corps, fg_color="transparent")
            grille.pack(fill="x")
            for index, pilier in enumerate(piliers):
                if not isinstance(pilier, dict):
                    continue
                ligne, colonne = divmod(index, 2)
                grille.grid_columnconfigure(colonne, weight=1, uniform="pilier")
                bloc = ctk.CTkFrame(grille, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
                bloc.grid(
                    row=ligne, column=colonne, sticky="nsew",
                    padx=(0, 8) if colonne == 0 else 0, pady=(0, 8),
                )
                _texte(ui, bloc, "PILIER · " + str(pilier.get("nom") or ""), taille=12, gras=True, pady=(10, 2))
                if pilier.get("promesse"):
                    _texte(ui, bloc, pilier["promesse"], taille=12, couleur=ui_app.DOUX)
                if pilier.get("formats"):
                    _texte(ui, bloc, pilier["formats"], taille=11, couleur=ui_app.DOUX_FONCE)
                for titre in (pilier.get("exemples_de_titres") or [])[:3]:
                    _puce(ui, bloc, titre, couleur=ui_app.TEXTE)
                ctk.CTkFrame(bloc, fg_color="transparent", height=4).pack()

        formats = strategie.get("formats") or []
        if formats:
            _texte(ui, corps, "FORMATS ET FRÉQUENCES", taille=10, couleur=ui_app.ACCENT_TEXTE, pady=(8, 4))
            for format_ in formats:
                if not isinstance(format_, dict):
                    continue
                _texte(
                    ui, corps,
                    f"{format_.get('format', '')} · {format_.get('frequence', '')} — "
                    f"{format_.get('objectif', '')}",
                    taille=12, pady=(0, 2),
                )

        series = strategie.get("series") or []
        if series:
            rangee = ctk.CTkFrame(corps, fg_color="transparent")
            rangee.pack(fill="x", pady=(10, 0))
            _texte(ui, rangee, "SÉRIES  ", taille=10, couleur=ui_app.DOUX)
            for serie in series:
                _etiquette(ui, rangee, serie).pack(side="left", padx=(0, 6), pady=2)

    # --- Plan des 30 jours ----------------------------------------------
    plan_30 = strategie.get("premiers_30_jours") or []
    if plan_30:
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Les 30 premiers jours",
            "Un enchaînement daté pour transformer la stratégie en publications.",
            cle="plan",
        )
        cadre.pack(fill="x", pady=(0, 12))
        for semaine in plan_30:
            if not isinstance(semaine, dict):
                continue
            bloc = ctk.CTkFrame(corps, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6)
            bloc.pack(fill="x", pady=(0, 8))
            _texte(
                ui, bloc,
                f"{semaine.get('semaine', '')} — {semaine.get('objectif', '')}",
                taille=13, gras=True, pady=(10, 2),
            )
            if semaine.get("actions"):
                _texte(ui, bloc, semaine["actions"], taille=12, couleur=ui_app.DOUX, pady=(0, 10))

    # --- Indicateurs -----------------------------------------------------
    kpis = strategie.get("kpis") or []
    if kpis:
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Indicateurs à suivre",
            "Quatre à six repères, choisis pour vérifier la stratégie sans se disperser.",
            cle="kpis",
        )
        cadre.pack(fill="x", pady=(0, 12))
        for kpi in kpis:
            if not isinstance(kpi, dict):
                continue
            _texte(
                ui, corps,
                f"{kpi.get('indicateur', '')} · cible {kpi.get('cible', '')}",
                taille=13, gras=True, pady=(0, 0),
            )
            if kpi.get("pourquoi"):
                _texte(ui, corps, kpi["pourquoi"], taille=12, couleur=ui_app.DOUX, pady=(0, 8))

    # --- Idées -----------------------------------------------------------
    if idees:
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Vos prochaines vidéos",
            "Dix titres prêts à publier, chacun avec un angle différent et la raison de le faire.",
            cle="idees",
        )
        cadre.pack(fill="x", pady=(0, 12))
        for position, idee in enumerate(idees, start=1):
            if isinstance(idee, dict):
                _carte_idee(ui, corps, idee, position)

    # --- Limites ---------------------------------------------------------
    if limites:
        cadre, corps = ui._carte(
            ui._audit_corps,
            "Ce que les données ne disent pas",
            "Pour garder un œil critique sur l'audit : ces limites viennent du modèle.",
            cle="limites",
        )
        cadre.pack(fill="x", pady=(0, 12))
        for limite in limites:
            _puce(ui, corps, limite)

    ui._appliquer_disposition("audit")


def _carte_idee(ui, parent, idee: dict, position: int) -> None:
    bloc = ctk.CTkFrame(
        parent, fg_color=ui_app.SURFACE_HAUTE, corner_radius=6,
        border_width=1, border_color=ui_app.BORDURE,
    )
    bloc.pack(fill="x", pady=(0, 10))
    entete = ctk.CTkFrame(bloc, fg_color="transparent")
    entete.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        entete,
        text=f"{position:02d}  {idee.get('titre') or ''}",
        font=(ui._police, 14, "bold"),
        text_color=ui_app.TEXTE,
        anchor="w",
        justify="left",
        wraplength=470,
    ).pack(side="left", fill="x", expand=True)
    ui._lien(entete, "Au plan", lambda i=idee: _au_plan(ui, i), width=80).pack(side="right")
    ui._lien(entete, "Note", lambda i=idee: _noter(ui, i), width=64).pack(side="right", padx=(0, 6))
    ui._lien(entete, "Copier", lambda i=idee: _copier(ui, i), width=72).pack(side="right", padx=(0, 6))

    if idee.get("angle"):
        _texte(ui, bloc, idee["angle"], taille=12, couleur=ui_app.DOUX, pady=(0, 2))
    if idee.get("pourquoi"):
        _texte(
            ui, bloc, "Pourquoi : " + str(idee["pourquoi"]),
            taille=12, couleur=ui_app.DOUX_FONCE, pady=(0, 8),
        )
    if idee.get("format"):
        _etiquette(ui, bloc, idee["format"]).pack(anchor="w", padx=14, pady=(0, 10))


def _texte_idee(idee: dict) -> str:
    morceaux = [str(idee.get("titre") or "")]
    for etiquette, cle in (
        ("Angle", "angle"),
        ("Pourquoi", "pourquoi"),
        ("Format", "format"),
    ):
        if idee.get(cle):
            morceaux.append(f"{etiquette} : {idee[cle]}")
    return "\n\n".join(morceaux)


def _copier(ui, idee: dict) -> None:
    ui._copier_texte(_texte_idee(idee))


def _noter(ui, idee: dict) -> None:
    titre = str(idee.get("titre") or "Idée d'audit").strip()
    try:
        notes.creer(ui.racine, titre=titre, contenu=_texte_idee(idee))
        ui._message.set(f"Note créée : {titre}")
    except Exception as probleme:  # l'erreur ne doit pas casser la page
        ui._message.set(f"Création de la note impossible : {probleme}")


def _au_plan(ui, idee: dict) -> None:
    titre = str(idee.get("titre") or "Idée d'audit").strip()
    try:
        plan.ajouter(ui.racine, titre=titre, notes=_texte_idee(idee))
        ui._message.set(f"Ajouté au plan éditorial : {titre}")
    except Exception as probleme:
        ui._message.set(f"Ajout au plan impossible : {probleme}")


def _date(ui, valeur) -> str:
    return ui_app._date_lisible(valeur)


__all__ = ["construire"]
