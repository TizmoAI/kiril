-- Saved brand templates: a caption style plus logo and intro/outro references
-- that a task can start from.
CREATE TABLE IF NOT EXISTS brand_templates (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    caption_template VARCHAR(100),
    font_family VARCHAR(255),
    font_size INTEGER,
    font_color VARCHAR(7),
    logo_ref TEXT,
    intro_ref TEXT,
    outro_ref TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_brand_templates_user_id ON brand_templates(user_id);
