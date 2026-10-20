-- The pool pins one official runtime image per active challenge: the content
-- digest the registry serves for TIG's runtime version tag. Rows are
-- append-only history; the newest row per challenge at the configured runtime
-- version is the image members receive inside each assignment. The coordinator
-- appends and reads, the API reads, health reads may inspect. The deployment
-- grant file stays unchanged: this migration grants the table's own privileges
-- when the deployment roles exist (they do not in a disposable test database).
CREATE TABLE runtime_images (
    id uuid PRIMARY KEY,
    challenge_id text NOT NULL CHECK (challenge_id ~ '^c[0-9]+$'),
    challenge_name text NOT NULL CHECK (challenge_name ~ '^[a-z_]+$'),
    runtime_version text NOT NULL CHECK (runtime_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    image text NOT NULL CHECK (image ~ ('^ghcr\.io/tig-foundation/tig-monorepo/' || challenge_name || '/runtime@sha256:[0-9a-f]{64}$')),
    resolved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX runtime_images_current ON runtime_images (runtime_version, challenge_id, resolved_at DESC, id DESC);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON runtime_images
    FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER prevent_truncate BEFORE TRUNCATE ON runtime_images
    FOR EACH STATEMENT EXECUTE FUNCTION immutable_record();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'innopool_runtime') THEN
        GRANT SELECT, INSERT ON TABLE runtime_images TO innopool_runtime;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'innopool_observer') THEN
        GRANT SELECT ON TABLE runtime_images TO innopool_observer;
    END IF;
END $$;
