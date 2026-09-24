CREATE TABLE schema_migrations (
    schema_version VARCHAR NOT NULL,
    migration_id VARCHAR PRIMARY KEY,
    applied_at VARCHAR NOT NULL,
    code_commit_sha VARCHAR NOT NULL,
    migration_checksum VARCHAR NOT NULL
);

CREATE TABLE source_registry (
    source_id VARCHAR PRIMARY KEY,
    source_snapshot_json VARCHAR NOT NULL,
    registered_at VARCHAR NOT NULL
);

CREATE TABLE subject_registry (
    subject_id VARCHAR PRIMARY KEY,
    subject_snapshot_json VARCHAR NOT NULL,
    registered_at VARCHAR NOT NULL
);

CREATE TABLE instrument_registry (
    instrument_id VARCHAR PRIMARY KEY,
    instrument_snapshot_json VARCHAR NOT NULL,
    registered_at VARCHAR NOT NULL
);

CREATE TABLE source_usage_rights (
    source_usage_rights_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    rights_state VARCHAR NOT NULL CHECK (
        rights_state IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    evaluated_at VARCHAR NOT NULL,
    rights_snapshot_json VARCHAR NOT NULL
);

CREATE TABLE raw_payload (
    raw_payload_hash VARCHAR PRIMARY KEY,
    payload_bytes BLOB NOT NULL,
    byte_length BIGINT NOT NULL CHECK (byte_length >= 0)
);

CREATE TABLE raw_capture (
    raw_capture_id VARCHAR PRIMARY KEY,
    raw_payload_hash VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    captured_at VARCHAR NOT NULL,
    capture_snapshot_json VARCHAR NOT NULL
);

CREATE TABLE observation_identity (
    observation_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    source_record_identifier VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    source_period_type VARCHAR NOT NULL,
    source_market_date VARCHAR,
    source_period_start_date VARCHAR,
    source_period_end_date VARCHAR,
    identity_projection_json VARCHAR NOT NULL
);

CREATE TABLE observation_snapshot (
    observation_content_hash VARCHAR PRIMARY KEY,
    observation_id VARCHAR NOT NULL,
    data_origin VARCHAR NOT NULL,
    operational_status VARCHAR NOT NULL,
    scheduler_execution_at VARCHAR,
    local_business_date VARCHAR,
    source_business_date VARCHAR,
    scheduler_business_date VARCHAR,
    source_publication_at VARCHAR,
    collected_at VARCHAR,
    observed_at VARCHAR,
    source_available_at VARCHAR,
    channel_available_at VARCHAR,
    created_at VARCHAR,
    semantic_data_json VARCHAR NOT NULL,
    content_projection_json VARCHAR NOT NULL
);

CREATE TABLE observation_calendar_assignment (
    observation_content_hash VARCHAR NOT NULL,
    calendar_role VARCHAR NOT NULL CHECK (
        calendar_role IN ('MARKET', 'PUBLICATION', 'LOCAL_OPERATIONAL', 'SCHEDULER')
    ),
    subject_id VARCHAR NOT NULL,
    assignment_status VARCHAR NOT NULL CHECK (
        assignment_status IN ('RESOLVED', 'NOT_APPLICABLE', 'UNVERIFIED', 'CONFLICTING', 'OUT_OF_RANGE')
    ),
    calendar_reference_id VARCHAR,
    calendar_version VARCHAR,
    calendar_hash VARCHAR,
    assignment_projection_json VARCHAR NOT NULL,
    PRIMARY KEY (observation_content_hash, calendar_role)
);

CREATE TABLE observation_version_identity (
    observation_version_id VARCHAR PRIMARY KEY,
    observation_id VARCHAR NOT NULL,
    source_version_or_release_key VARCHAR NOT NULL,
    stable_version_key VARCHAR NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    transformation_version VARCHAR NOT NULL,
    identity_projection_json VARCHAR NOT NULL
);

CREATE TABLE observation_version_snapshot (
    observation_version_content_hash VARCHAR PRIMARY KEY,
    observation_version_id VARCHAR NOT NULL,
    parent_version_id VARCHAR,
    revision_available_at VARCHAR,
    collected_at VARCHAR,
    observed_at VARCHAR,
    created_at VARCHAR,
    semantic_data_json VARCHAR NOT NULL,
    content_projection_json VARCHAR NOT NULL
);

CREATE TABLE readiness_evaluation (
    readiness_evaluation_id VARCHAR PRIMARY KEY,
    observation_version_id VARCHAR NOT NULL,
    observation_version_content_hash VARCHAR NOT NULL,
    evaluated_at VARCHAR NOT NULL,
    evaluation_snapshot_json VARCHAR NOT NULL
);

CREATE TABLE pit_dataset_manifest (
    pit_dataset_manifest_id VARCHAR PRIMARY KEY,
    manifest_content_hash VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL,
    manifest_snapshot_json VARCHAR NOT NULL
);

CREATE TABLE pit_dataset_row (
    pit_dataset_row_id VARCHAR PRIMARY KEY,
    pit_dataset_manifest_id VARCHAR NOT NULL,
    row_content_hash VARCHAR NOT NULL,
    row_snapshot_json VARCHAR NOT NULL
);

CREATE TABLE pit_feature_snapshot (
    pit_feature_content_hash VARCHAR PRIMARY KEY,
    pit_dataset_row_id VARCHAR NOT NULL,
    feature_snapshot_json VARCHAR NOT NULL
);
