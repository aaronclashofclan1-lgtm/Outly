-- Schéma SQLite d'Outly (version 10 : cache des mentions de descriptions,
-- notes et playlist notées, historique, cerveau IA, idées, recherche
-- documentaire, plan et audit complet de ma chaîne).
-- Toutes les dates sont stockées en ISO 8601 UTC (TEXT).
-- Aucune donnée dénormalisée : le score d'outlier est calculé par la vue v_outliers.

CREATE TABLE IF NOT EXISTS meta (
    cle     TEXT PRIMARY KEY,
    valeur  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at             TEXT NOT NULL,
    finished_at            TEXT,
    mode                   TEXT NOT NULL,
    args_json              TEXT NOT NULL,
    unites_utilisees       INTEGER NOT NULL DEFAULT 0,
    recherches_utilisees   INTEGER NOT NULL DEFAULT 0,
    appels_llm             INTEGER NOT NULL DEFAULT 0,
    statut                 TEXT NOT NULL DEFAULT 'running'
);

-- Journal de quota : source unique de vérité du compteur local.
-- `compartiment` vaut 'search' (100 appels/jour) ou 'principal' (10 000 unités/jour).
CREATE TABLE IF NOT EXISTS api_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER REFERENCES runs(id),
    jour          TEXT NOT NULL,
    ts            TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    compartiment  TEXT NOT NULL,
    unites        INTEGER NOT NULL,
    depuis_cache  INTEGER NOT NULL DEFAULT 0,
    empreinte     TEXT,
    http_status   INTEGER,
    erreur        TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_calls_jour ON api_calls(jour);
CREATE INDEX IF NOT EXISTS idx_api_calls_run  ON api_calls(run_id);

CREATE TABLE IF NOT EXISTS cache (
    empreinte    TEXT PRIMARY KEY,
    endpoint     TEXT NOT NULL,
    charge       TEXT NOT NULL,
    recupere_le  TEXT NOT NULL,
    expire_le    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id         TEXT PRIMARY KEY,
    handle             TEXT,
    titre              TEXT NOT NULL,
    description        TEXT,
    pays               TEXT,
    cree_le            TEXT,
    abonnes            INTEGER,
    abonnes_masques    INTEGER NOT NULL DEFAULT 0,
    nb_videos          INTEGER,
    vues_totales       INTEGER,
    playlist_uploads   TEXT,
    mots_cles_chaine   TEXT,
    sujets_json        TEXT,
    est_seed           INTEGER NOT NULL DEFAULT 0,
    source             TEXT,
    score_heuristique  REAL,
    langue_estimee     TEXT,
    -- candidate → enrichie → scored ; ecartee (inutilisable) ou filtree (hors pré-filtre)
    statut             TEXT NOT NULL DEFAULT 'candidate',
    raison_ecart       TEXT,
    recupere_le        TEXT,
    ajoute_le          TEXT NOT NULL,
    maj_le             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channels_statut ON channels(statut);

CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL REFERENCES channels(channel_id),
    titre           TEXT NOT NULL,
    description     TEXT,
    -- Cache local des @handles/IDs cités : une description n'est analysée
    -- qu'une fois, puis seulement si elle change réellement.
    mentions_json       TEXT,
    mentions_scannees_le TEXT,
    publie_le       TEXT,
    duree_s         INTEGER,
    vues            INTEGER,
    likes           INTEGER,
    commentaires    INTEGER,
    est_short       INTEGER,
    live            TEXT NOT NULL DEFAULT 'none',
    rang_uploads    INTEGER,
    recupere_le     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_chaine ON videos(channel_id, publie_le DESC);

-- Profil de référence local : ma chaîne et ses vidéos récentes, utilisés par
-- le score de proximité de secours quand le LLM est indisponible.
CREATE TABLE IF NOT EXISTS channel_reference (
    channel_id     TEXT PRIMARY KEY,
    titre          TEXT,
    mots_cles      TEXT,
    description    TEXT,
    titres_videos  TEXT,
    maj_le         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_metrics (
    channel_id               TEXT PRIMARY KEY REFERENCES channels(channel_id),
    calcule_le               TEXT NOT NULL,
    fenetre                  INTEGER NOT NULL,
    n_videos                 INTEGER NOT NULL,
    n_shorts                 INTEGER NOT NULL,
    n_longues                INTEGER NOT NULL,
    mediane_vues_short       REAL,
    mediane_vues_longues     REAL,
    cadence_semaine          REAL,
    jours_depuis_publication INTEGER,
    meilleur_outlier         REAL,
    mix_format               TEXT,
    detail_json              TEXT
);

CREATE TABLE IF NOT EXISTS channel_scores (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id             TEXT NOT NULL REFERENCES channels(channel_id),
    run_id                 INTEGER REFERENCES runs(id),
    empreinte_prompt       TEXT NOT NULL,
    prompt_version         TEXT NOT NULL,
    fournisseur            TEXT,
    modele                 TEXT,
    score_proximite        INTEGER,
    recouvrement_audience  TEXT,
    format_detecte         TEXT,
    langue                 TEXT,
    justification          TEXT,
    risques_json           TEXT,
    reponse_brute          TEXT,
    tentatives             INTEGER NOT NULL DEFAULT 1,
    valide                 INTEGER NOT NULL DEFAULT 0,
    erreur                 TEXT,
    cree_le                TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_unique
    ON channel_scores(channel_id, prompt_version, empreinte_prompt);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id      TEXT PRIMARY KEY,
    langue        TEXT,
    texte         TEXT,
    caracteres    INTEGER,
    statut        TEXT NOT NULL,
    erreur        TEXT,
    -- Repères temporels (JSON) : indispensables pour situer l'intro d'une vidéo.
    segments_json TEXT,
    recupere_le   TEXT NOT NULL
);

-- Analyse de l'intro d'une vidéo outlier : bornes temporelles, citation de
-- l'accroche et procédés utilisés. Une ligne par vidéo et par version de prompt.
CREATE TABLE IF NOT EXISTS intro_analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL REFERENCES videos(video_id),
    channel_id      TEXT,
    run_id          INTEGER REFERENCES runs(id),
    prompt_version  TEXT NOT NULL,
    empreinte       TEXT,
    fournisseur     TEXT,
    modele          TEXT,
    intro_debut_s   REAL,
    intro_fin_s     REAL,
    hook_texte      TEXT,
    resume          TEXT,
    promesse        TEXT,
    techniques_json TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_intro_analyses_unique
    ON intro_analyses(video_id, prompt_version, empreinte);

-- Jeu de variantes d'accroches généré pour un sujet donné. L'empreinte
-- couvre le sujet, les intros utilisées et la version du prompt : relancer
-- une génération identique ne recoûte aucun appel LLM.
CREATE TABLE IF NOT EXISTS hook_sets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    sujet           TEXT NOT NULL,
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    analyse         TEXT,
    variantes_json  TEXT,
    sources_json    TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_hook_sets_unique
    ON hook_sets(empreinte, prompt_version);

-- Notes libres de l'utilisateur (hooks, concepts, sujets, idées, références).
-- `tags_json` est une liste JSON de chaînes ; le contenu est du texte brut.
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    titre       TEXT NOT NULL,
    contenu     TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'note',
    statut      TEXT NOT NULL DEFAULT 'brouillon',
    tags_json   TEXT,
    epingle     INTEGER NOT NULL DEFAULT 0,
    -- Classement personnel de 0 à 10 (NULL = non notée) : il indique au
    -- cerveau IA quels hooks et quels points utiliser en premier.
    note_sur_10 INTEGER CHECK (note_sur_10 IS NULL OR (note_sur_10 BETWEEN 0 AND 10)),
    source      TEXT,
    channel_id  TEXT,
    video_id    TEXT,
    cree_le     TEXT NOT NULL,
    maj_le      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(maj_le DESC);

-- Une ligne par version précédente : l'historique ne grossit pas à chaque
-- lecture, seulement quand le contenu change réellement.
CREATE TABLE IF NOT EXISTS note_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER NOT NULL,
    titre       TEXT NOT NULL,
    contenu     TEXT NOT NULL,
    type        TEXT NOT NULL,
    statut      TEXT NOT NULL,
    tags_json   TEXT,
    epingle     INTEGER NOT NULL DEFAULT 0,
    source      TEXT,
    cree_le     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_revisions_note
    ON note_revisions(note_id, cree_le DESC);

-- (Les idées générées vivent dans `idea_sets`, plus bas, après les sessions du
-- cerveau IA : les deux tables partagent le même principe de cache.)

-- Session du « cerveau IA » : synthèse, angles, sujets et questions calculés
-- à partir des notes, du positionnement et des outliers. `notes_json` conserve
-- un instantané léger des notes utilisées pour l'affichage.
CREATE TABLE IF NOT EXISTS brain_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    question        TEXT,
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    reponse_json    TEXT,
    notes_json      TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_brain_sessions_unique
    ON brain_sessions(empreinte, prompt_version);

-- Jeu d'idées de vidéos : sujets et concepts proposés par le LLM, chacun avec
-- trois titres à angles différents. L'empreinte couvre les notes, les mots-clés,
-- les outliers, la playlist et la dernière analyse du cerveau : relancer une
-- recherche identique ne recoûte aucun appel LLM.
CREATE TABLE IF NOT EXISTS idea_sets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    question        TEXT,
    nombre          INTEGER NOT NULL DEFAULT 5,
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    introduction    TEXT,
    idees_json      TEXT,
    notes_json      TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_idea_sets_unique
    ON idea_sets(empreinte, prompt_version);

-- Dossier de recherche documentaire : pages Wikipédia et sources web,
-- intention éditoriale et synthèse. L'empreinte couvre le titre, les mots-clés
-- et le mode : relancer une recherche identique ne refait aucun appel réseau.
CREATE TABLE IF NOT EXISTS research_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    titre           TEXT NOT NULL,
    mots_cles_json  TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('sans_ia', 'avec_ia')),
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    intention_json  TEXT,
    sources_json    TEXT,
    pages_json      TEXT,
    dossier_json    TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_research_sessions_unique
    ON research_sessions(empreinte, mode, prompt_version);


-- Audit complet de « ma chaîne » : secteur, diagnostic, paysage concurrentiel,
-- stratégie de contenu et idées. L'empreinte couvre le profil, les concurrents,
-- les notes, les mots-clés et le positionnement : relancer un audit identique
-- ne recoûte aucun appel LLM.
CREATE TABLE IF NOT EXISTS audit_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    reponse_json    TEXT,
    profil_json     TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_sessions_unique
    ON audit_sessions(empreinte, prompt_version);

CREATE TABLE IF NOT EXISTS analyses_outliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL REFERENCES videos(video_id),
    run_id          INTEGER REFERENCES runs(id),
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    accroche        TEXT,
    structure_json  TEXT,
    explication     TEXT,
    reutilisable    TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_analyses_unique
    ON analyses_outliers(video_id, prompt_version);

-- Playlist « À regarder plus tard » : vidéos et chaînes mises de côté par
-- l'utilisateur. La clé est le couple (identifiant, type) : une vidéo et une
-- chaîne peuvent partager le même identifiant sans se gêner.
CREATE TABLE IF NOT EXISTS watch_later (
    element_id   TEXT NOT NULL,
    type         TEXT NOT NULL CHECK (type IN ('video', 'channel')),
    titre        TEXT NOT NULL,
    sous_titre   TEXT,
    url          TEXT NOT NULL,
    channel_id   TEXT,
    meta_json    TEXT,
    -- Même barème que les notes : 0 à 10, NULL quand l'élément n'est pas noté.
    note_sur_10  INTEGER CHECK (note_sur_10 IS NULL OR (note_sur_10 BETWEEN 0 AND 10)),
    ajoute_le    TEXT NOT NULL,
    PRIMARY KEY (element_id, type)
);

CREATE INDEX IF NOT EXISTS idx_watch_later_date ON watch_later(ajoute_le DESC);

-- Plan éditorial : les vidéos à venir et leur avancement. `date_prevue` reste
-- facultative (une idée sans créneau) et au format AAAA-MM-JJ. `statut` suit
-- les étapes déclarées dans outly/plan.py ; `format` distingue Short et longue.
CREATE TABLE IF NOT EXISTS editorial_plan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    titre        TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    date_prevue  TEXT,
    statut       TEXT NOT NULL DEFAULT 'idee',
    format       TEXT NOT NULL DEFAULT 'longue',
    cree_le      TEXT NOT NULL,
    maj_le       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_editorial_plan_date
    ON editorial_plan(date_prevue);

-- Tâches de production d'une fiche : chaque ligne est cochable dans le plan.
CREATE TABLE IF NOT EXISTS editorial_tasks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id   INTEGER NOT NULL REFERENCES editorial_plan(id) ON DELETE CASCADE,
    libelle   TEXT NOT NULL,
    fait      INTEGER NOT NULL DEFAULT 0 CHECK (fait IN (0, 1)),
    cree_le   TEXT NOT NULL,
    fait_le   TEXT
);

CREATE INDEX IF NOT EXISTS idx_editorial_tasks_plan
    ON editorial_tasks(plan_id, fait, id);

-- Diagnostic des dernières vidéos : causes probables, ajustements de stratégie
-- et prochains tests. L'empreinte couvre le contexte mesuré et la version du
-- prompt : relancer une analyse identique ne recoûte aucun appel LLM.
CREATE TABLE IF NOT EXISTS strategy_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    reponse_json    TEXT,
    contexte_json   TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_reviews_unique
    ON strategy_reviews(empreinte, prompt_version);

-- Estimation de production d'une fiche du plan éditorial : temps, matériel,
-- footage, budget, risques et checklist. Une fiche peut avoir plusieurs
-- estimations (fiche modifiée), d'où la clé (plan_id, empreinte, version).
CREATE TABLE IF NOT EXISTS production_estimates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    plan_id         INTEGER NOT NULL,
    empreinte       TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    fournisseur     TEXT,
    modele          TEXT,
    estimation_json TEXT,
    valide          INTEGER NOT NULL DEFAULT 0,
    erreur          TEXT,
    cree_le         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_production_estimates_unique
    ON production_estimates(plan_id, empreinte, prompt_version);
CREATE INDEX IF NOT EXISTS idx_production_estimates_plan
    ON production_estimates(plan_id, cree_le DESC);

-- Score d'outlier : vues ÷ médiane du MÊME format (Shorts et longues séparés).
-- NULL quand la médiane du format est absente ou nulle (jamais de division par zéro).
CREATE VIEW IF NOT EXISTS v_outliers AS
SELECT
    v.video_id,
    v.channel_id,
    v.titre,
    v.description,
    v.publie_le,
    v.duree_s,
    v.vues,
    v.likes,
    v.commentaires,
    v.est_short,
    v.rang_uploads,
    m.mediane_vues_short,
    m.mediane_vues_longues,
    CASE
        WHEN v.est_short = 1 AND m.mediane_vues_short   > 0
            THEN ROUND(1.0 * v.vues / m.mediane_vues_short, 3)
        WHEN v.est_short = 0 AND m.mediane_vues_longues > 0
            THEN ROUND(1.0 * v.vues / m.mediane_vues_longues, 3)
    END AS score_outlier
FROM videos v
JOIN channel_metrics m ON m.channel_id = v.channel_id
WHERE v.live = 'none';
