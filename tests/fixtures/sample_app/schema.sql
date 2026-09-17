-- Core identity table.
-- One row per application user.
CREATE TABLE IF NOT EXISTS app_user (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'recruiter',
    -- Recruiters may not reach back-office screens.
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    org_id        BIGINT REFERENCES organisation(id),
    CONSTRAINT chk_app_user_role CHECK (role IN ('admin','recruiter','sysadmin')),
    CONSTRAINT uq_app_user_email UNIQUE (email)
);

COMMENT ON TABLE app_user IS 'Every person who can sign in. Deactivation is reversible; rows are never deleted.';
COMMENT ON COLUMN app_user.role IS 'Drives every server-side permission check.';

CREATE TABLE candidate (
    candidate_id  TEXT PRIMARY KEY,
    source        TEXT NOT NULL DEFAULT 'No Source Tag',  -- mandatory on manual upload
    status        TEXT NOT NULL CHECK (status IN ('Active','Hired','Rejected'))
);

CREATE SEQUENCE candidate_seq START 1001;
CREATE UNIQUE INDEX uq_candidate_source_msg ON candidate (source, candidate_id) WHERE source IS NOT NULL;
CREATE TRIGGER trg_sync_stage AFTER INSERT OR UPDATE ON candidate FOR EACH ROW EXECUTE FUNCTION sync_stage();
