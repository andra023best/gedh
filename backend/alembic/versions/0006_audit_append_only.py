"""Журнал аудиту лише на додавання (DG-19; табл. 5.4, рядок «СУБД»: роль застосунку без UPDATE/DELETE).

Два незалежні рівні захисту:
1. **Тригери** — `audit_events` не можна змінити, видалити чи очистити (TRUNCATE) жодній ролі,
   навіть власнику бази (наприклад, вручну з консолі Neon). Рядок-«голова» ланцюжка `audit_trail`
   не можна видалити, а його номер може лише зростати.
2. **Роль застосунку `gedh_app`** (NOLOGIN) — має SELECT/INSERT/UPDATE/DELETE на всі таблиці,
   крім `audit_events`, де лише SELECT і INSERT. Застосунок виконує `SET LOCAL ROLE gedh_app`
   на початку кожної транзакції (app/core/db.py), тож навіть помилковий чи зловмисний запит
   отримає «permission denied». Міграції виконуються від імені власника без цієї ролі.

Видалення записів, старших за 3 роки (DG-19), лишається за окремою службовою роллю — для демо
не потрібне (журнал ведеться з листопада 2026 р.); див. S-30.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "gedh_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION gedh_audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'DG-19: журнал аудиту лише доповнюється (% заборонено)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_audit_events_no_update_delete
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION gedh_audit_events_append_only();
        CREATE TRIGGER trg_audit_events_no_truncate
            BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION gedh_audit_events_append_only();

        CREATE FUNCTION gedh_audit_trail_forward_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION 'DG-19: голову ланцюжка журналу не можна видалити'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.last_seq <= OLD.last_seq THEN
                RAISE EXCEPTION 'DG-19: номер ланцюжка журналу може лише зростати (% → %)', OLD.last_seq, NEW.last_seq
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_audit_trail_forward_only
            BEFORE UPDATE OR DELETE ON audit_trail
            FOR EACH ROW EXECUTE FUNCTION gedh_audit_trail_forward_only();
        CREATE TRIGGER trg_audit_trail_no_truncate
            BEFORE TRUNCATE ON audit_trail
            FOR EACH STATEMENT EXECUTE FUNCTION gedh_audit_trail_forward_only();
        """
    )
    # Роль застосунку. CREATE ROLE — на рівні кластера, тому IF NOT EXISTS (тестова й робоча БД
    # локально в одному кластері). Власник отримує право SET ROLE на неї (PostgreSQL 16+).
    # Якщо прав створити роль немає (інший хостинг), міграція не падає: лишається захист тригерами,
    # а застосунок працює від імені власника й показує це в /api/health (db.app_role).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
            GRANT {APP_ROLE} TO CURRENT_USER;
            GRANT USAGE ON SCHEMA public TO {APP_ROLE};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM {APP_ROLE};
            REVOKE DELETE, TRUNCATE ON audit_trail FROM {APP_ROLE};
            REVOKE ALL ON alembic_version FROM {APP_ROLE};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
            ALTER DEFAULT PRIVILEGES IN SCHEMA public
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE};
            ALTER DEFAULT PRIVILEGES IN SCHEMA public
                GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE};
        EXCEPTION WHEN insufficient_privilege OR invalid_grant_operation THEN
            RAISE WARNING 'DG-19: роль {APP_ROLE} не налаштовано (%); захист журналу — лише тригерами', SQLERRM;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE};
                REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE};
                REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE};
                REVOKE USAGE ON SCHEMA public FROM {APP_ROLE};
                REVOKE {APP_ROLE} FROM CURRENT_USER;
            END IF;
        END $$;

        DROP TRIGGER trg_audit_trail_no_truncate ON audit_trail;
        DROP TRIGGER trg_audit_trail_forward_only ON audit_trail;
        DROP FUNCTION gedh_audit_trail_forward_only();
        DROP TRIGGER trg_audit_events_no_truncate ON audit_events;
        DROP TRIGGER trg_audit_events_no_update_delete ON audit_events;
        DROP FUNCTION gedh_audit_events_append_only();
        """
    )
