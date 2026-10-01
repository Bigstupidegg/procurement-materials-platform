CREATE TABLE schema_migrations (
    schema_version VARCHAR NOT NULL,
    migration_id VARCHAR PRIMARY KEY,
    applied_at VARCHAR NOT NULL,
    code_commit_sha VARCHAR NOT NULL,
    migration_checksum VARCHAR NOT NULL
);

CREATE TABLE source_registry (
    source_id VARCHAR PRIMARY KEY,
    source_name VARCHAR NOT NULL,
    access_channel VARCHAR NOT NULL,
    default_timezone VARCHAR NOT NULL,
    data_classification VARCHAR NOT NULL,
    registry_version VARCHAR NOT NULL,
    active BOOLEAN NOT NULL,
    created_at VARCHAR NOT NULL
);

CREATE TABLE subject_registry (
    subject_id VARCHAR PRIMARY KEY,
    subject_snapshot_json VARCHAR NOT NULL,
    registered_at VARCHAR NOT NULL
);

CREATE TABLE instrument_registry (
    instrument_id VARCHAR PRIMARY KEY,
    subject_id VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    source_symbol VARCHAR NOT NULL,
    market_or_venue VARCHAR NOT NULL,
    metric_id VARCHAR NOT NULL,
    quote_type VARCHAR NOT NULL,
    term VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    unit VARCHAR NOT NULL,
    source_period_type VARCHAR NOT NULL,
    instrument_version VARCHAR NOT NULL,
    active_from VARCHAR NOT NULL,
    active_to VARCHAR
);

CREATE TABLE source_usage_rights (
    rights_profile_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    access_channel VARCHAR NOT NULL,
    instrument_scope VARCHAR NOT NULL,
    automated_access VARCHAR NOT NULL CHECK (
        automated_access IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    private_storage VARCHAR NOT NULL CHECK (
        private_storage IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    historical_archive VARCHAR NOT NULL CHECK (
        historical_archive IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    internal_analysis VARCHAR NOT NULL CHECK (
        internal_analysis IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    backtest VARCHAR NOT NULL CHECK (
        backtest IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    prediction VARCHAR NOT NULL CHECK (
        prediction IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    internal_display VARCHAR NOT NULL CHECK (
        internal_display IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    internet_display VARCHAR NOT NULL CHECK (
        internet_display IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    redistribution VARCHAR NOT NULL CHECK (
        redistribution IN ('ALLOWED', 'PROHIBITED', 'REVIEW_REQUIRED', 'UNKNOWN')
    ),
    evidence_reference VARCHAR NOT NULL,
    review_status VARCHAR NOT NULL,
    effective_from VARCHAR NOT NULL,
    effective_to VARCHAR,
    reviewed_at VARCHAR
);

CREATE TABLE raw_payload (
    raw_payload_hash VARCHAR PRIMARY KEY,
    relative_path VARCHAR NOT NULL,
    content_type VARCHAR NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    data_classification VARCHAR NOT NULL,
    first_seen_at VARCHAR NOT NULL
);

CREATE TABLE raw_capture (
    capture_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL,
    instrument_id VARCHAR,
    access_channel VARCHAR NOT NULL,
    source_locator_safe VARCHAR NOT NULL,
    collected_at VARCHAR NOT NULL,
    collection_status VARCHAR NOT NULL,
    raw_payload_hash VARCHAR NOT NULL,
    collector_version VARCHAR NOT NULL,
    rights_profile_id VARCHAR NOT NULL,
    error_class VARCHAR,
    created_at VARCHAR NOT NULL
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
    evaluation_hash VARCHAR PRIMARY KEY,
    observation_id VARCHAR NOT NULL,
    observation_version_id VARCHAR NOT NULL,
    evaluation_role VARCHAR NOT NULL,
    cutoff_at VARCHAR NOT NULL,
    evaluation_as_of_at VARCHAR NOT NULL,
    label_available_at VARCHAR,
    readiness_state VARCHAR NOT NULL,
    eligibility_state VARCHAR NOT NULL,
    reason_codes_json VARCHAR NOT NULL,
    blocker_ids_json VARCHAR NOT NULL,
    evidence_references_json VARCHAR NOT NULL,
    contract_version VARCHAR NOT NULL,
    evaluator_version VARCHAR NOT NULL,
    rule_bundle_version VARCHAR NOT NULL,
    rule_bundle_hash VARCHAR NOT NULL,
    source_profile_id VARCHAR NOT NULL,
    source_profile_version VARCHAR NOT NULL,
    observation_content_hash VARCHAR NOT NULL,
    observation_version_content_hash VARCHAR NOT NULL,
    persisted_at VARCHAR NOT NULL
);

CREATE TABLE pit_dataset_manifest (
    dataset_identity VARCHAR PRIMARY KEY,
    manifest_type VARCHAR NOT NULL,
    manifest_version VARCHAR NOT NULL,
    dataset_contract_version VARCHAR NOT NULL,
    feature_set_version VARCHAR NOT NULL,
    feature_computation_profile_version VARCHAR NOT NULL,
    cutoff_policy_version VARCHAR NOT NULL,
    rule_bundle_bindings_json VARCHAR NOT NULL,
    source_profile_bindings_json VARCHAR NOT NULL,
    authorization_snapshot_json VARCHAR NOT NULL,
    request_scope_json VARCHAR NOT NULL,
    include_count BIGINT NOT NULL CHECK (include_count >= 0),
    exclude_count BIGINT NOT NULL CHECK (exclude_count >= 0),
    quarantine_count BIGINT NOT NULL CHECK (quarantine_count >= 0),
    exclusion_reason_summary_json VARCHAR NOT NULL,
    quarantine_reason_summary_json VARCHAR NOT NULL,
    persisted_at VARCHAR NOT NULL
);

CREATE TABLE pit_dataset_row (
    row_content_hash VARCHAR NOT NULL,
    row_id VARCHAR NOT NULL,
    dataset_identity VARCHAR NOT NULL,
    manifest_row_ordinal BIGINT NOT NULL CHECK (manifest_row_ordinal >= 0),
    research_subject_id VARCHAR NOT NULL,
    observation_version_id VARCHAR NOT NULL,
    research_cutoff_at VARCHAR NOT NULL,
    feature_set_version VARCHAR NOT NULL,
    feature_computation_profile_version VARCHAR NOT NULL,
    cutoff_policy_version VARCHAR NOT NULL,
    feature_available_at_max VARCHAR,
    rd4_authority_bindings_json VARCHAR NOT NULL,
    rd5_authority_bindings_json VARCHAR NOT NULL,
    rule_bundle_bindings_json VARCHAR NOT NULL,
    source_profile_bindings_json VARCHAR NOT NULL,
    label_specification_json VARCHAR NOT NULL,
    authorization_snapshot_json VARCHAR NOT NULL,
    operational_status VARCHAR NOT NULL,
    persisted_at VARCHAR NOT NULL,
    PRIMARY KEY (dataset_identity, manifest_row_ordinal),
    UNIQUE (dataset_identity, row_content_hash)
);

CREATE TABLE pit_feature_snapshot (
    row_content_hash VARCHAR NOT NULL,
    feature_ordinal BIGINT NOT NULL CHECK (feature_ordinal >= 0),
    feature_content_hash VARCHAR NOT NULL,
    feature_definition_id VARCHAR NOT NULL,
    feature_definition_version VARCHAR NOT NULL,
    feature_computation_profile_version VARCHAR NOT NULL,
    research_cutoff_at VARCHAR NOT NULL,
    value_state VARCHAR NOT NULL,
    value_json VARCHAR NOT NULL,
    source_observation_id VARCHAR,
    source_observation_version_id VARCHAR,
    source_observed_at VARCHAR,
    source_available_at VARCHAR,
    source_profile_id VARCHAR,
    source_profile_version VARCHAR,
    evidence_refs_json VARCHAR NOT NULL,
    rd4_evaluation_hash VARCHAR,
    rd5_decision_hash VARCHAR,
    authority_binding_ref VARCHAR,
    PRIMARY KEY (row_content_hash, feature_ordinal)
);
