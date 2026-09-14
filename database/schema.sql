-- Local CRM schema for the micro-tutoring business.
-- SQLite dialect.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Students (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name         TEXT    NOT NULL,
    year_group        INTEGER,                 -- e.g. 3 for Year 3
    key_stage         INTEGER,                 -- 1 or 2
    age               INTEGER,
    date_of_birth     TEXT,
    school            TEXT,
    guardian_name     TEXT,
    guardian_contact  TEXT,                    -- phone / WhatsApp
    guardian_email    TEXT,
    guardian_relationship TEXT,                -- 'Mother', 'Father', 'Carer'
    address           TEXT,
    interests         TEXT,                    -- free text: "football, pizza, Minecraft"
    goals             TEXT,                    -- what the family wants (SATs, confidence, ...)
    status            TEXT DEFAULT 'enquiry',  -- 'enquiry' | 'trial' | 'active' | 'paused' | 'ended'
    notes             TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS Assessments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    subject           TEXT    NOT NULL DEFAULT 'maths',
    assessed_on       TEXT    DEFAULT (date('now')),
    kind              TEXT    DEFAULT 'diagnostic',   -- 'diagnostic' | 'weekly'
    raw_notes         TEXT,                    -- the tutor's original notes
    image_path        TEXT,                    -- photo of the pupil's worksheet / test
    overall_score     REAL,                    -- 0-100 if available
    difficulty_reco   TEXT,                    -- 'support' | 'core' | 'stretch'
    strengths_json    TEXT,                    -- JSON array of strings
    weak_points_json  TEXT,                    -- JSON array of {topic, detail, severity}
    analysis_json     TEXT,                    -- full Assessor agent output
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS WeeklyBooks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    week_of           TEXT    DEFAULT (date('now')),
    subject           TEXT    NOT NULL DEFAULT 'maths',
    topic             TEXT    NOT NULL,
    key_stage         INTEGER,
    year_group        INTEGER,
    difficulty        TEXT    DEFAULT 'core',   -- 'support' | 'core' | 'stretch'
    curriculum_json   TEXT,                    -- Ingestor output (objectives + misconceptions)
    strategy_json     TEXT,                    -- Curriculum Mapper output
    explanation_md    TEXT,                    -- Explainer output: revision notes (Markdown)
    problems_json     TEXT,                    -- Problem Setter output
    pdf_path          TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- A pupil's enrolment on the tutoring programme (their plan + pricing).
CREATE TABLE IF NOT EXISTS Enrolments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    status            TEXT DEFAULT 'active',      -- 'active' | 'paused' | 'ended'
    start_date        TEXT DEFAULT (date('now')),
    end_date          TEXT,
    hourly_rate       REAL NOT NULL DEFAULT 15,   -- GBP per hour
    classes_per_month INTEGER NOT NULL DEFAULT 4,
    class_length_min  INTEGER NOT NULL DEFAULT 60,
    subjects_json     TEXT,                        -- [{"subject":"maths","classes":3}, ...]
    payment_method    TEXT DEFAULT 'bank transfer',-- 'bank transfer' | 'card' | 'cash'
    payment_schedule  TEXT DEFAULT 'monthly',      -- 'monthly' | 'per class' | 'termly'
    notes             TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- A quote / invoice for a billing period, generated from an enrolment.
CREATE TABLE IF NOT EXISTS Invoices (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    enrolment_id      INTEGER REFERENCES Enrolments(id) ON DELETE SET NULL,
    number            TEXT,                        -- e.g. "INV-2026-014"
    period            TEXT,                        -- e.g. "October 2026"
    issued_on         TEXT DEFAULT (date('now')),
    due_on            TEXT,
    line_items_json   TEXT,                        -- [{"description","qty","unit_price","amount"}]
    subtotal          REAL DEFAULT 0,
    total             REAL DEFAULT 0,
    status            TEXT DEFAULT 'draft',         -- 'draft' | 'sent' | 'paid'
    pdf_path          TEXT,
    notes             TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- A progress report written for the parents.
CREATE TABLE IF NOT EXISTS Reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    period            TEXT,                        -- e.g. "Autumn term 2026"
    created_on        TEXT DEFAULT (date('now')),
    content_md        TEXT,                        -- the report body (AI-drafted, tutor-editable)
    summary           TEXT,                        -- one-line headline
    pdf_path          TEXT,
    status            TEXT DEFAULT 'draft',         -- 'draft' | 'sent'
    created_at        TEXT DEFAULT (datetime('now'))
);

-- Login accounts.  role 'admin' = the tutor; role 'parent' = a family, tied to one pupil.
CREATE TABLE IF NOT EXISTS Users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'parent',   -- 'admin' | 'parent'
    student_id        INTEGER REFERENCES Students(id) ON DELETE CASCADE,
    display_name      TEXT,
    must_change_pw    INTEGER DEFAULT 0,
    last_login        TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- The tutor's recurring weekly availability (bookable windows).
CREATE TABLE IF NOT EXISTS Availability (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday           INTEGER NOT NULL,          -- 0 = Monday ... 6 = Sunday
    start_time        TEXT NOT NULL,             -- 'HH:MM'
    end_time          TEXT NOT NULL,             -- 'HH:MM'
    slot_min          INTEGER NOT NULL DEFAULT 60,
    active            INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT (datetime('now'))
);

-- A booked class.
CREATE TABLE IF NOT EXISTS Sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id        INTEGER NOT NULL REFERENCES Students(id) ON DELETE CASCADE,
    starts_at         TEXT NOT NULL,             -- ISO 'YYYY-MM-DD HH:MM'
    duration_min      INTEGER NOT NULL DEFAULT 60,
    subject           TEXT,
    status            TEXT NOT NULL DEFAULT 'booked',  -- booked | arrived | departed | cancelled | missed
    booked_by         TEXT DEFAULT 'admin',      -- 'admin' | 'parent'
    billable          INTEGER DEFAULT 0,         -- 1 = an extra class, beyond the monthly plan
    charge_amount     REAL DEFAULT 0,            -- £ to add to the invoice for this extra class
    invoice_id        INTEGER REFERENCES Invoices(id) ON DELETE SET NULL,  -- set once billed
    arrived_at        TEXT,
    departed_at       TEXT,
    notes             TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessments_student ON Assessments(student_id);
CREATE INDEX IF NOT EXISTS idx_books_student       ON WeeklyBooks(student_id);
CREATE INDEX IF NOT EXISTS idx_enrolments_student  ON Enrolments(student_id);
CREATE INDEX IF NOT EXISTS idx_invoices_student    ON Invoices(student_id);
CREATE INDEX IF NOT EXISTS idx_reports_student     ON Reports(student_id);
CREATE INDEX IF NOT EXISTS idx_sessions_student    ON Sessions(student_id);
CREATE INDEX IF NOT EXISTS idx_sessions_start      ON Sessions(starts_at);
CREATE INDEX IF NOT EXISTS idx_users_student       ON Users(student_id);
