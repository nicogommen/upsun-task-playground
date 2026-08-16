-- Admin persistence schema (ITERATION-2.x §5).
--
-- Applied at startup by storage.connect(). Every statement is IF NOT EXISTS so
-- the call is idempotent and a fresh preview environment provisions itself with
-- no manual step. admin runs a single worker (D10 released but not exercised),
-- so there is no startup race here; raising the worker count would need an
-- advisory lock around this.

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    title       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runs (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    prompt             TEXT NOT NULL,
    status             TEXT NOT NULL,
    target_environment TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    activity_id        TEXT,
    branch_name        TEXT,
    pr_url             TEXT,
    error              TEXT,
    completed_at       TIMESTAMPTZ,

    -- Unused today. Q-iter2-8 removed the preview-env read; ITERATION-2 §7.4
    -- documents the restore path for when a project-scoped authorization ships.
    -- Nullable columns cost nothing now and save a migration then.
    preview_env_id     TEXT,
    preview_url        TEXT
);

CREATE INDEX IF NOT EXISTS runs_session_created_idx
    ON runs (session_id, created_at DESC);

-- Export jobs (EXPORT-TASK.md). Written by the `export-job` task, which
-- relates to this same service and updates its own row on completion (D16).
-- The admin only creates the row and reads it back for the download.
CREATE TABLE IF NOT EXISTS exports (
    id            TEXT PRIMARY KEY,
    status        TEXT NOT NULL,
    activity_id   TEXT,
    created_at    TIMESTAMPTZ NOT NULL,
    completed_at  TIMESTAMPTZ,
    session_count INTEGER,
    run_count     INTEGER,
    payload       JSONB,
    error         TEXT
);
