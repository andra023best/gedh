"""Правила data governance на рівні БД (Data Governance by Design).

Навіть якщо в коді застосунку буде помилка, база даних не дозволить:
- змінити клас набору або його тип після створення (DG-02, Dataset.classification {readOnly});
- погодити заявку тому, хто її подав (D-01, DG-07, {decidedBy ≠ requester});
- змінити запис згоди (DG-10: записи згоди лише додаються).
Також створюється початковий («нульовий») запис ланцюжка журналу аудиту.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION gedh_dataset_class_readonly() RETURNS trigger AS $$
        BEGIN
            IF NEW.classification IS DISTINCT FROM OLD.classification OR NEW.kind IS DISTINCT FROM OLD.kind THEN
                RAISE EXCEPTION 'DG-02: клас і тип набору не змінюються після створення'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_datasets_class_readonly
            BEFORE UPDATE ON datasets
            FOR EACH ROW EXECUTE FUNCTION gedh_dataset_class_readonly();
        """
    )
    op.execute(
        """
        CREATE FUNCTION gedh_decision_not_by_requester() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM access_requests r
                       WHERE r.id = NEW.request_id AND r.requester_id = NEW.decided_by_id) THEN
                RAISE EXCEPTION 'D-01/DG-07: заявник не може погоджувати власну заявку'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_approval_decisions_sod
            BEFORE INSERT OR UPDATE ON approval_decisions
            FOR EACH ROW EXECUTE FUNCTION gedh_decision_not_by_requester();
        """
    )
    op.execute(
        """
        CREATE FUNCTION gedh_consent_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'DG-10: записи згоди не змінюються — додається новий запис'
                USING ERRCODE = 'check_violation';
        END $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_consent_records_append_only
            BEFORE UPDATE ON consent_records
            FOR EACH ROW EXECUTE FUNCTION gedh_consent_append_only();
        """
    )
    op.execute("INSERT INTO audit_trail (id, last_hash, last_seq) VALUES (1, repeat('0', 64), 0)")


def downgrade() -> None:
    op.execute("DELETE FROM audit_trail WHERE id = 1")
    op.execute("DROP TRIGGER trg_consent_records_append_only ON consent_records")
    op.execute("DROP FUNCTION gedh_consent_append_only()")
    op.execute("DROP TRIGGER trg_approval_decisions_sod ON approval_decisions")
    op.execute("DROP FUNCTION gedh_decision_not_by_requester()")
    op.execute("DROP TRIGGER trg_datasets_class_readonly ON datasets")
    op.execute("DROP FUNCTION gedh_dataset_class_readonly()")
