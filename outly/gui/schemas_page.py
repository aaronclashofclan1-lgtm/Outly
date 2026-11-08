"""Schémas vidéo : préparer les plans d'un Short ou d'une vidéo longue."""
from __future__ import annotations
import tkinter as tk
import customtkinter as ctk
from . import app as ui_app
from . import data

PLANS_COURT = [
    ("Accroche face cam", "face", "0-3 s", "Visage centré, épaules visibles", "Haut de l'écran", True, "Titre très court, lisible en 1 seconde."),
    ("Preuve ou B-roll", "broll", "3-8 s", "Sujet à droite, espace à gauche", "Gauche", False, "Sous-titre mot à mot, pas de paragraphe."),
    ("Démonstration", "demo", "8-20 s", "Écran plein ou mains filmées", "Bas", False, "Une seule action par plan."),
    ("Texte plein écran", "texte", "20-24 s", "Aucun sujet", "Centre", False, "Phrase de 6 mots maximum."),
    ("Retour caméra + CTA", "face", "24-30 s", "Visage centré, regard caméra", "Bas", True, "Une seule demande : commenter ou s'abonner."),
]
PLANS_LONG = [
    ("Introduction face cam", "face", "0:00-0:25", "Visage centré, cadrage poitrine", "Bas", True, "Annoncer la promesse et le plan."),
    ("Contexte / B-roll", "broll", "0:25-1:30", "Sujet à droite, espace titre à gauche", "Gauche", False, "Illustrer, ne pas répéter la voix."),
    ("Démonstration pas à pas", "demo", "1:30-3:00", "Capture d'écran ou mains", "Bas", False, "Numéroter les étapes à l'écran."),
    ("Preuve / données", "texte", "3:00-4:30", "Graphique ou texte plein écran", "Centre", False, "Citer la source à l'écran."),
    ("Nuance face cam", "face", "4:30-5:30", "Visage centré, plan un peu plus large", "Bas", True, "Donner le contre-argument."),
    ("Conclusion + CTA", "face", "5:30-6:00", "Visage centré", "Bas", True, "Résumer en trois points, puis une action."),
]
CHECKLIST = [
    "La zone de sécurité de 10 % est respectée (textes loin des bords).",
    "Le sujet ne change pas de côté sans raison entre deux plans.",
    "Chaque plan a une durée prévue et un objectif unique.",
    "Les sous-titres ne dépassent pas deux lignes.",
    "Le CTA final est unique et visible sans son.",
    "Les plans B-roll ont été filmés ou sourcés avant le montage.",
]

def _liste_plans(mode, duree, face):
    base = PLANS_COURT if mode == "Short" else PLANS_LONG
    plans = []
    for titre, typ, moment, placement, texte, camera, conseil in base:
        plans.append({"titre": titre, "type": typ, "moment": moment, "placement": placement,
                      "texte": texte, "camera": bool(camera and face), "conseil": conseil})
    return plans

def _diagramme(parent, plan, ui):
    largeur, hauteur = 168, 96
    canvas = tk.Canvas(parent, width=largeur, height=hauteur, bg=ui_app._couleur(ui_app.CHAMP),
                       highlightthickness=1, highlightbackground=ui_app._couleur(ui_app.BORDURE))
    canvas.pack(side="left", padx=(0, 12))
    canvas.create_rectangle(8, 8, largeur-8, hauteur-8, outline=ui_app._couleur(ui_app.DOUX), dash=(2,3))
    if plan["type"] == "face":
        canvas.create_oval(64, 18, 104, 60, outline=ui_app._couleur(ui_app.ACCENT), width=2)
        canvas.create_line(84, 60, 84, 82, fill=ui_app._couleur(ui_app.ACCENT), width=2)
    elif plan["type"] == "texte":
        canvas.create_text(84, 48, text="TEXTE", fill=ui_app._couleur(ui_app.ACCENT), font=(ui._police, 12, "bold"))
    elif plan["type"] == "broll":
        canvas.create_rectangle(22, 26, 146, 70, outline=ui_app._couleur(ui_app.OK), width=2)
        canvas.create_line(22, 70, 146, 26, fill=ui_app._couleur(ui_app.OK))
    else:
        canvas.create_rectangle(26, 22, 142, 74, outline=ui_app._couleur(ui_app.ACCENT), width=2)
        canvas.create_line(32, 66, 136, 30, fill=ui_app._couleur(ui_app.ACCENT), arrow="last")
    canvas.create_text(84, 86, text=plan["placement"], fill=ui_app._couleur(ui_app.DOUX), font=(ui._police, 9))
    return canvas

def _rendre(ui):
    zone = getattr(ui, "_schemas_timeline", None)
    if zone is None:
        return
    for enfant in zone.winfo_children():
        enfant.destroy()
    plans = getattr(ui, "_schemas_plans", [])
    for index, plan in enumerate(plans):
        cadre = ctk.CTkFrame(zone, fg_color=ui_app.SURFACE, corner_radius=6, border_width=1, border_color=ui_app.BORDURE)
        cadre.pack(fill="x", pady=4)
        corps = ctk.CTkFrame(cadre, fg_color="transparent"); corps.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(corps, text=str(index+1), width=24, height=24, corner_radius=12,
                     fg_color=ui_app.ACCENT, text_color=ui_app.TEXTE_SUR_ACCENT,
                     font=(ui._police, 11, "bold")).pack(side="left", padx=(0, 8))
        _diagramme(corps, plan, ui)
        texte = ctk.CTkFrame(corps, fg_color="transparent"); texte.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(texte, text=f"{plan['titre']} · {plan['moment']}", anchor="w",
                     font=(ui._police, 13, "bold"), text_color=ui_app.TEXTE).pack(anchor="w")
        ctk.CTkLabel(texte, text=f"Cadre : {plan['placement']} · Texte : {plan['texte']} · Caméra : {'oui' if plan['camera'] else 'non'}",
                     anchor="w", justify="left", wraplength=460, font=(ui._police, 11),
                     text_color=ui_app.DOUX).pack(anchor="w")
        ctk.CTkLabel(texte, text=plan["conseil"], anchor="w", justify="left", wraplength=460,
                     font=(ui._police, 11), text_color=ui_app.ACCENT_TEXTE).pack(anchor="w", pady=(2,0))
        actions = ctk.CTkFrame(corps, fg_color="transparent"); actions.pack(side="right")
        for libelle, delta in (("↑", -1), ("↓", 1)):
            ctk.CTkButton(actions, text=libelle, width=26, height=24,
                          command=lambda i=index, d=delta: _deplacer(ui, i, d),
                          fg_color="transparent", border_width=1, border_color=ui_app.BORDURE,
                          hover_color=ui_app.SURFACE_HAUTE, text_color=ui_app.DOUX,
                          font=(ui._police, 12, "bold")).pack(side="left", padx=1)

def _deplacer(ui, index, delta):
    plans = getattr(ui, "_schemas_plans", [])
    cible = index + delta
    if 0 <= index < len(plans) and 0 <= cible < len(plans):
        plans[index], plans[cible] = plans[cible], plans[index]
        _rendre(ui)

def _nouveau(ui):
    ui._schemas_plans = _liste_plans(ui._schemas_mode.get(), ui._schemas_duree.get(), ui._schemas_face.get())
    _rendre(ui)

def _depuis_concurrent(ui, choix):
    if choix and choix != "Aucune vidéo concurrente":
        ui._schemas_mode.set("Short" if "short" in choix.lower() or "30" in choix else "Vidéo longue")
        ui._schemas_duree.set("30 secondes" if ui._schemas_mode.get() == "Short" else "6 minutes")
        ui._schemas_origine.set(f"Format prérempli depuis : {choix}. Les plans restent à confirmer.")
        _nouveau(ui)

def construire(ui):
    page = ctk.CTkFrame(ui._conteneur, fg_color=ui_app.FOND, corner_radius=0)
    ui._pages["schemas"] = page
    zone = ui._zone_defilante(page)
    ui._bandeau_parcours(zone, "Schémas vidéo", [
        ("Choisir Short ou longue", None),
        ("Confirmer les plans", None),
        ("Vérifier la checklist", None),
    ], lien=("Voir les concurrents", lambda: ui._afficher_page("resultats")))
    ui._schemas_mode = tk.StringVar(value="Short")
    ui._schemas_duree = tk.StringVar(value="30 secondes")
    ui._schemas_face = tk.BooleanVar(value=True)
    ui._schemas_origine = tk.StringVar(value="")
    ui._schemas_videos = ["Aucune vidéo concurrente"]
    try:
        for chaine in data.concurrents_historique(ui.racine)[:30]:
            nom = chaine.get("titre") or chaine.get("nom") or chaine.get("channel_id") or "Chaîne"
            for video in (chaine.get("videos") or [])[:3]:
                titre = video.get("titre") or "Vidéo"
                duree = video.get("duree") or video.get("duration") or "durée inconnue"
                ui._schemas_videos.append(f"{titre} · {duree}")
    except Exception:
        pass
    ui._schemas_choix_video = tk.StringVar(value=ui._schemas_videos[0])
    carte, corps = ui._carte(zone, "Préparer la structure", "Choisissez le format, puis vérifiez chaque plan. Les types de plans ne sont jamais devinés depuis YouTube : vous les confirmez ici.")
    carte.pack(fill="x", pady=(0, 12))
    ligne = ctk.CTkFrame(corps, fg_color="transparent"); ligne.pack(fill="x", pady=(0, 8))
    ctk.CTkSegmentedButton(ligne, values=["Short", "Vidéo longue"], variable=ui._schemas_mode,
        command=lambda _v: (ui._schemas_duree.set("30 secondes" if _v == "Short" else "6 minutes"), _nouveau(ui)),
        selected_color=ui_app.ACCENT, selected_hover_color=ui_app.ACCENT_SURVOL,
        unselected_color=ui_app.SURFACE_HAUTE, text_color=ui_app.TEXTE,
        font=(ui._police, 12)).pack(side="left", padx=(0, 12))
    ctk.CTkOptionMenu(ligne, values=["15 secondes", "30 secondes", "45 secondes", "60 secondes"] if ui._schemas_mode.get() == "Short" else ["4 minutes", "6 minutes", "8 minutes", "10 minutes"],
        variable=ui._schemas_duree, width=130, height=28, fg_color=ui_app.CHAMP,
        button_color=ui_app.SURFACE_HAUTE, button_hover_color=ui_app.BORDURE,
        text_color=ui_app.TEXTE, font=(ui._police, 11), command=lambda _v: _nouveau(ui)).pack(side="left", padx=(0, 12))
    ctk.CTkSwitch(ligne, text="Face cam disponible", variable=ui._schemas_face,
        command=lambda: _nouveau(ui), progress_color=ui_app.ACCENT,
        text_color=ui_app.TEXTE, font=(ui._police, 11)).pack(side="left", padx=(0, 12))
    ctk.CTkButton(ligne, text="Régénérer les plans", command=lambda: _nouveau(ui), width=152,
        fg_color=ui_app.ACCENT, hover_color=ui_app.ACCENT_SURVOL, text_color=ui_app.TEXTE_SUR_ACCENT,
        font=(ui._police, 12, "bold")).pack(side="right")
    ligne2 = ctk.CTkFrame(corps, fg_color="transparent"); ligne2.pack(fill="x", pady=(4, 0))
    ctk.CTkLabel(ligne2, text="Partir d'une vidéo de la base :", font=(ui._police, 11), text_color=ui_app.DOUX).pack(side="left", padx=(0, 8))
    ctk.CTkOptionMenu(ligne2, values=ui._schemas_videos[:60], variable=ui._schemas_choix_video,
        width=360, height=28, fg_color=ui_app.CHAMP, button_color=ui_app.SURFACE_HAUTE,
        button_hover_color=ui_app.BORDURE, text_color=ui_app.TEXTE,
        font=(ui._police, 11), command=lambda v: _depuis_concurrent(ui, v)).pack(side="left")
    ctk.CTkLabel(corps, textvariable=ui._schemas_origine, font=(ui._police, 11),
        text_color=ui_app.ALERTE, anchor="w", justify="left", wraplength=700).pack(anchor="w", pady=(6, 0))
    ctk.CTkLabel(corps, text="Rappel : l'API YouTube ne fournit pas le découpage des plans. Les propositions ci-dessous sont des modèles à confirmer, pas une analyse automatique de la vidéo.",
        font=(ui._police, 11), text_color=ui_app.DOUX, anchor="w", justify="left", wraplength=700).pack(anchor="w", pady=(6, 0))
    ui._schemas_timeline = ctk.CTkFrame(zone, fg_color="transparent")
    ui._schemas_timeline.pack(fill="x")
    carte2, corps2 = ui._carte(zone, "Checklist de tournage", "À cocher avant de filmer, puis avant de publier.")
    carte2.pack(fill="x", pady=(12, 0))
    for texte in CHECKLIST:
        var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(corps2, text=texte, variable=var, text_color=ui_app.TEXTE,
            font=(ui._police, 11), fg_color=ui_app.ACCENT, hover_color=ui_app.ACCENT_SURVOL,
            border_color=ui_app.BORDURE).pack(anchor="w", pady=3)
    ui._schemas_plans = _liste_plans("Short", "30 secondes", True)
    _rendre(ui)

def remplir(ui):
    try:
        liste = ["Aucune vidéo concurrente"]
        for chaine in data.concurrents_historique(ui.racine)[:30]:
            for video in (chaine.get("videos") or [])[:3]:
                titre = video.get("titre") or "Vidéo"
                duree = video.get("duree") or video.get("duration") or "durée inconnue"
                liste.append(f"{titre} · {duree}")
        if len(liste) != len(getattr(ui, "_schemas_videos", [])):
            ui._schemas_videos = liste
    except Exception:
        pass
