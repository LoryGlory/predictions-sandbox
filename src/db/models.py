"""SQLite table definitions as SQL strings.

Keeping schema as plain SQL (not an ORM) for simplicity and portability.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT    NOT NULL,
    external_id TEXT,
    question    TEXT    NOT NULL,
    category    TEXT,
    tags        TEXT,
    close_date  TEXT,
    current_price REAL  NOT NULL DEFAULT 0.5,
    last_updated  TEXT  NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    model           TEXT    NOT NULL,
    estimated_prob  REAL    NOT NULL,
    market_price    REAL,
    confidence      TEXT,
    reasoning       TEXT,
    prompt_version  TEXT,
    used_web_search INTEGER NOT NULL DEFAULT 0,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       INTEGER NOT NULL REFERENCES markets(id),
    prediction_id   INTEGER REFERENCES predictions(id),
    direction       TEXT    NOT NULL,
    size            REAL    NOT NULL,
    entry_price     REAL    NOT NULL,
    exit_price      REAL,
    outcome         TEXT,
    pnl             REAL,
    is_paper        INTEGER NOT NULL DEFAULT 1,
    timestamp       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS calibration (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   INTEGER NOT NULL REFERENCES predictions(id),
    predicted_prob  REAL    NOT NULL,
    actual_outcome  INTEGER,
    brier_score     REAL,
    resolved_at     TEXT
);

CREATE TABLE IF NOT EXISTS api_cost_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL DEFAULT (date('now')),
    calls       INTEGER NOT NULL DEFAULT 0,
    est_cost_usd REAL NOT NULL DEFAULT 0.0,
    UNIQUE(date)
);

CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_type  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    details     TEXT    NOT NULL,
    blog_post   TEXT,
    used        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date         TEXT    NOT NULL UNIQUE,
    resolved_count      INTEGER NOT NULL DEFAULT 0,
    mean_brier          REAL,
    market_brier        REAL,
    skill_score         REAL,
    best_prediction_id  INTEGER REFERENCES predictions(id),
    worst_prediction_id INTEGER REFERENCES predictions(id),
    api_spend_day       REAL,
    api_spend_month     REAL,
    full_report         TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""
