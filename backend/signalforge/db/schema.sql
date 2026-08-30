CREATE TABLE IF NOT EXISTS dataset_versions (
  id VARCHAR PRIMARY KEY,
  source_name VARCHAR NOT NULL,
  source_url VARCHAR,
  file_hash VARCHAR NOT NULL,
  row_count INTEGER NOT NULL CHECK (row_count >= 0),
  imported_at TIMESTAMP NOT NULL
);

-- Version-scoped embodied episodes.  The payload is immutable JSON so the
-- simulator contract can evolve without losing the exact imported record.
CREATE TABLE IF NOT EXISTS embodied_episodes (
  dataset_version_id VARCHAR NOT NULL,
  episode_id VARCHAR NOT NULL,
  task_id VARCHAR NOT NULL,
  scene_id VARCHAR NOT NULL,
  payload JSON NOT NULL,
  payload_sha256 VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (dataset_version_id, episode_id),
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS embodied_events (
  dataset_version_id VARCHAR NOT NULL,
  episode_id VARCHAR NOT NULL,
  event_id VARCHAR NOT NULL,
  event_type VARCHAR NOT NULL,
  event_time_s DOUBLE NOT NULL CHECK (event_time_s >= 0),
  payload JSON NOT NULL,
  PRIMARY KEY (dataset_version_id, episode_id, event_id),
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS embodied_diagnoses (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  episode_id VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
  payload JSON,
  validation_code VARCHAR NOT NULL,
  trace JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id),
  UNIQUE (dataset_version_id, episode_id)
);

CREATE TABLE IF NOT EXISTS embodied_experiments (
  diagnosis_id VARCHAR PRIMARY KEY,
  experiment_id VARCHAR NOT NULL UNIQUE,
  dataset_version_id VARCHAR NOT NULL,
  episode_id VARCHAR NOT NULL,
  payload JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS reviews (
  id VARCHAR NOT NULL,
  dataset_version_id VARCHAR NOT NULL,
  content VARCHAR NOT NULL,
  rating INTEGER,
  aspect VARCHAR,
  sentiment VARCHAR,
  redacted BOOLEAN NOT NULL,
  PRIMARY KEY (id, dataset_version_id),
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS market_events (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  source_url VARCHAR NOT NULL,
  published_on DATE NOT NULL,
  excerpt VARCHAR NOT NULL,
  event_type VARCHAR NOT NULL,
  industry VARCHAR NOT NULL,
  evidence_quality INTEGER NOT NULL CHECK (evidence_quality BETWEEN 0 AND 100),
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS insights (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  payload JSON NOT NULL,
  status VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS decision_cards (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  payload JSON NOT NULL,
  status VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS decision_memos (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL UNIQUE,
  payload JSON NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('actionable', 'needs_evidence', 'refused')),
  model_name VARCHAR,
  prompt_version VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS decision_memo_revisions (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  payload JSON NOT NULL,
  status VARCHAR NOT NULL CHECK (status IN ('actionable', 'needs_evidence', 'refused')),
  model_name VARCHAR,
  prompt_version VARCHAR NOT NULL,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS memo_generation_jobs (
  id VARCHAR PRIMARY KEY,
  dataset_version_id VARCHAR NOT NULL,
  status VARCHAR NOT NULL CHECK (
    status IN (
      'queued',
      'analyzing_signals',
      'grouping_evidence',
      'validating_evidence',
      'generating_memo',
      'completed',
      'failed'
    )
  ),
  memo_id VARCHAR,
  error_code VARCHAR,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  CHECK (
    (status = 'completed' AND memo_id IS NOT NULL AND error_code IS NULL)
    OR (status = 'failed' AND memo_id IS NULL AND error_code IS NOT NULL)
    OR (
      status NOT IN ('completed', 'failed')
      AND memo_id IS NULL
      AND error_code IS NULL
    )
  ),
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id),
  FOREIGN KEY (memo_id) REFERENCES decision_memo_revisions(id)
);

CREATE TABLE IF NOT EXISTS active_memo_generation_jobs (
  dataset_version_id VARCHAR PRIMARY KEY,
  job_id VARCHAR NOT NULL UNIQUE,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

CREATE TABLE IF NOT EXISTS feedback (
  id VARCHAR PRIMARY KEY,
  entity_type VARCHAR NOT NULL,
  entity_id VARCHAR NOT NULL,
  decision VARCHAR NOT NULL CHECK (decision IN ('confirmed', 'rejected', 'edited')),
  reason VARCHAR,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
  id VARCHAR PRIMARY KEY,
  entity_type VARCHAR NOT NULL,
  entity_id VARCHAR NOT NULL,
  dataset_version_id VARCHAR NOT NULL,
  prompt_version VARCHAR NOT NULL,
  model_name VARCHAR,
  provider VARCHAR,
  stage VARCHAR NOT NULL DEFAULT 'generation',
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  evidence_ids JSON NOT NULL,
  validation_status VARCHAR NOT NULL,
  latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
  token_estimate INTEGER NOT NULL CHECK (token_estimate >= 0),
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
);

-- Idempotently upgrade databases created before stage-level model auditing.
-- Defaults preserve legacy insight traces without pretending a provider was used.
ALTER TABLE traces ADD COLUMN IF NOT EXISTS provider VARCHAR;
ALTER TABLE traces ADD COLUMN IF NOT EXISTS stage VARCHAR DEFAULT 'generation';
ALTER TABLE traces ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
UPDATE traces SET stage = 'generation' WHERE stage IS NULL;
UPDATE traces SET retry_count = 0 WHERE retry_count IS NULL;

-- Backfill the immutable audit store when opening a database created before
-- memo revisions were introduced. `decision_memos` remains the one-current-
-- memo projection while revisions preserve every generated artifact by ID.
INSERT INTO decision_memo_revisions (
  id, dataset_version_id, payload, status, model_name, prompt_version, created_at
)
SELECT id, dataset_version_id, payload, status, model_name, prompt_version, created_at
FROM decision_memos
ON CONFLICT (id) DO NOTHING;

-- Recover one active-job guard per version for databases created before the
-- guard table existed. New writes cannot create more than one active job.
INSERT INTO active_memo_generation_jobs (dataset_version_id, job_id)
SELECT dataset_version_id, id
FROM (
  SELECT
    dataset_version_id,
    id,
    ROW_NUMBER() OVER (
      PARTITION BY dataset_version_id ORDER BY created_at DESC, id DESC
    ) AS active_rank
  FROM memo_generation_jobs
  WHERE status NOT IN ('completed', 'failed')
)
WHERE active_rank = 1
ON CONFLICT (dataset_version_id) DO NOTHING;
