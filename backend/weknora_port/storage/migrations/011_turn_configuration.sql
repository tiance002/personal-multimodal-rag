-- Additive local durable request-option safety wrapper; existing v1 rows unchanged.
ALTER TABLE port_turns ADD COLUMN IF NOT EXISTS request_configuration JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(request_configuration)='object');
