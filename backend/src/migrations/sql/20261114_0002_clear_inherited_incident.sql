-- The original seed shipped a "degraded" video-downloads incident from a hosted
-- service. A new install has no incident, so mark it operational.
UPDATE service_statuses
SET status = 'operational',
    headline = 'Operational',
    description = 'Video downloads are working normally.',
    updated_at = CURRENT_TIMESTAMP
WHERE id = 'video-downloads' AND status = 'degraded';
