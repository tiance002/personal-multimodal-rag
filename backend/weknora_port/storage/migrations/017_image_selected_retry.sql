-- Preserve every historical task; selected retries get a distinct immutable key.
ALTER TABLE port_image_tasks ADD COLUMN retry_key VARCHAR(64) NOT NULL DEFAULT '';
DO $$ DECLARE constraint_name text; BEGIN
  SELECT conname INTO STRICT constraint_name FROM pg_constraint
  WHERE conrelid='port_image_tasks'::regclass AND contype='u'
    AND pg_get_constraintdef(oid)='UNIQUE (tenant_id, knowledge_base_id, knowledge_id, source_version)';
  EXECUTE format('ALTER TABLE port_image_tasks DROP CONSTRAINT %I',constraint_name);
END $$;
ALTER TABLE port_image_tasks ADD CONSTRAINT port_image_tasks_source_retry_unique
 UNIQUE(tenant_id,knowledge_base_id,knowledge_id,source_version,retry_key);
