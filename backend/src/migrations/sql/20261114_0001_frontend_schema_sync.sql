-- Columns the frontend's Prisma schema expects that init.sql and the earlier
-- backend migrations do not create. Idempotent, so it is safe on existing databases.
ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_account_id VARCHAR(255);
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS claim_token VARCHAR(255);
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE stripe_webhook_events ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'processed';
CREATE UNIQUE INDEX IF NOT EXISTS tasks_share_token_key ON tasks(share_token);
