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
    confidence      TEXT,
    reasoning       TEXT,
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
"""
