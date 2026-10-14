"""Platform tables: policies, approvals, grants, actions (audit), usage.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

CLASSES = "('read','internal_write','internal_send','external_send','destructive','locked')"


def upgrade():
    op.execute(f"""
    CREATE TABLE policies (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        scope         text NOT NULL CHECK (scope IN ('user','org_ceiling')),
        user_id       text NOT NULL,
        skill         text NOT NULL DEFAULT '*',
        action_class  text NOT NULL CHECK (action_class IN {CLASSES}),
        mode          text NOT NULL CHECK (mode IN ('auto','approve','block')),
        approval_id   text,
        updated_by    text NOT NULL,
        updated_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (scope, user_id, skill, action_class),
        CHECK (action_class <> 'locked' OR scope = 'org_ceiling')
    );

    CREATE TABLE approvals (
        id            text PRIMARY KEY CHECK (id ~ '^apr_[0-9a-f]{{12,32}}$'),
        origin        text NOT NULL CHECK (origin IN ('broker','producer','seed','import')),
        user_id       text NOT NULL,
        kind          text NOT NULL CHECK (kind IN ('action','config-change')),
        skill         text NOT NULL,
        summary       text NOT NULL,
        action_class  text CHECK (action_class IS NULL OR action_class IN {CLASSES}),
        action_json   jsonb NOT NULL,
        reason        text,
        source_kind   text,
        source_id     text,
        run_id        text,
        session_id    text,
        task_id       text,
        requires_role text,
        status        text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','approved','rejected','expired')),
        created_at    timestamptz NOT NULL DEFAULT now(),
        expires_at    timestamptz NOT NULL,
        decided_at    timestamptz,
        decided_by    text,
        decision_json jsonb,
        CHECK ((status = 'pending') = (decided_at IS NULL))
    );
    CREATE INDEX approvals_status_created ON approvals (status, created_at DESC);

    CREATE TABLE grants (
        id            text PRIMARY KEY,
        approval_id   text NOT NULL REFERENCES approvals(id),
        user_id       text NOT NULL,
        connector     text NOT NULL,
        skill         text NOT NULL,
        action_class  text NOT NULL CHECK (action_class IN {CLASSES} AND action_class <> 'read'),
        destination   text NOT NULL,
        scope         text NOT NULL CHECK (scope IN ('once','session','task','until','always')),
        session_id    text NOT NULL DEFAULT '',
        task_id       text NOT NULL DEFAULT '',
        expires_at    timestamptz,
        used_at       timestamptz,
        revoked_at    timestamptz,
        created_at    timestamptz NOT NULL DEFAULT now(),
        created_by    text NOT NULL,
        CHECK (scope <> 'until' OR expires_at IS NOT NULL),
        CHECK (action_class <> 'locked' OR scope = 'once')
    );
    CREATE INDEX grants_lookup ON grants (user_id, connector, skill) WHERE revoked_at IS NULL;

    CREATE TABLE actions (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ts            timestamptz NOT NULL DEFAULT now(),
        event         text NOT NULL CHECK (event IN ('decision','taint','approval','grant','outbound')),
        outcome       text NOT NULL,
        connector     text,
        method        text,
        action_class  text,
        destinations  text[] NOT NULL DEFAULT '{{}}',
        skill         text NOT NULL DEFAULT '',
        user_id       text NOT NULL DEFAULT '',
        run_id        text NOT NULL DEFAULT '',
        session_id    text NOT NULL DEFAULT '',
        task_id       text NOT NULL DEFAULT '',
        tainted       boolean,
        verdict       text,
        policy_mode   text,
        sentinel_mode text,
        reason        text,
        grant_id      text,
        approval_id   text,
        source_kind   text,
        source_id     text,
        status_code   integer,
        detail        jsonb NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX actions_ts ON actions (ts);
    CREATE INDEX actions_executed ON actions (ts) WHERE outcome = 'executed';
    CREATE INDEX actions_taint ON actions (run_id) WHERE event = 'taint';

    CREATE FUNCTION actions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'actions is append-only: % refused', TG_OP;
    END $$;
    CREATE TRIGGER actions_no_update_delete BEFORE UPDATE OR DELETE ON actions
        FOR EACH ROW EXECUTE FUNCTION actions_append_only();
    CREATE TRIGGER actions_no_truncate BEFORE TRUNCATE ON actions
        FOR EACH STATEMENT EXECUTE FUNCTION actions_append_only();

    CREATE TABLE held_actions (
        action_id     bigint PRIMARY KEY REFERENCES actions(id),
        action_json   jsonb NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE usage (
        day                 date NOT NULL,
        model               text NOT NULL,
        purpose             text NOT NULL DEFAULT 'agent',
        requests            integer NOT NULL DEFAULT 0,
        input_tokens        bigint NOT NULL DEFAULT 0,
        output_tokens       bigint NOT NULL DEFAULT 0,
        cache_read_tokens   bigint NOT NULL DEFAULT 0,
        cache_write_tokens  bigint NOT NULL DEFAULT 0,
        cost_usd            numeric(14,6) NOT NULL DEFAULT 0,
        unpriced_requests   integer NOT NULL DEFAULT 0,
        PRIMARY KEY (day, model, purpose)
    );
    """)


def downgrade():
    raise RuntimeError("the platform schema is not downgraded; restore from backup")
