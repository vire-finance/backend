"""Normalize legacy lowercase OCR enum labels without rewriting OCR rows.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""
from alembic import op

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = depends_on = None


def upgrade():
    # Resolve the type through the actual target column, rather than assuming
    # public: isolated migration tests and non-default schemas must stay isolated.
    # Validate every label before issuing any ALTER TYPE. Native enum renames
    # preserve enum identities, rows, timestamps, indexes and foreign keys.
    op.execute("""
DO $ocr_enum_fix$
DECLARE
    enum_oid oid;
    enum_schema text;
    enum_name text;
    enum_kind "char";
    table_schema text;
    labels text[];
    old_label text;
BEGIN
    SELECT t.oid, tn.nspname, t.typname, t.typtype, cn.nspname
      INTO enum_oid, enum_schema, enum_name, enum_kind, table_schema
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace cn ON cn.oid = c.relnamespace
      JOIN pg_type t ON t.oid = a.atttypid
      JOIN pg_namespace tn ON tn.oid = t.typnamespace
     WHERE a.attrelid = to_regclass('ocr_documents')
       AND a.attname = 'ocr_status'
       AND a.attnum > 0 AND NOT a.attisdropped;

    IF enum_oid IS NULL OR enum_kind <> 'e' OR enum_name <> 'ocrstatus'
       OR enum_schema <> table_schema THEN
        RAISE EXCEPTION
            'OCR enum normalization requires ocr_documents.ocr_status to use the native ocrstatus enum in the same schema';
    END IF;

    SELECT array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
      INTO labels FROM pg_enum e WHERE e.enumtypid = enum_oid;

    IF cardinality(labels) = 4
       AND labels @> ARRAY['PENDING', 'PROCESSING', 'COMPLETED', 'FAILED']::text[] THEN
        RETURN;
    ELSIF cardinality(labels) = 4
       AND labels @> ARRAY['pending', 'processing', 'completed', 'failed']::text[] THEN
        FOREACH old_label IN ARRAY ARRAY['pending', 'processing', 'completed', 'failed']::text[] LOOP
            EXECUTE format('ALTER TYPE %I.%I RENAME VALUE %L TO %L',
                           enum_schema, enum_name, old_label, upper(old_label));
        END LOOP;
    ELSE
        RAISE EXCEPTION
            'OCR enum normalization refused: expected exactly four all-lowercase or all-uppercase OCR labels; mixed, duplicate-case, missing or extra labels require manual review';
    END IF;
END
$ocr_enum_fix$;
""")


def downgrade():
    # The preceding historical migration already specifies uppercase labels.
    # Keep that canonical schema when downgrading the revision; reintroducing
    # lowercase drift would break the preceding application's ORM as well.
    pass
