CREATE TABLE schema_migrations (
    migration_id VARCHAR PRIMARY KEY,
    schema_version VARCHAR NOT NULL,
    database_purpose VARCHAR NOT NULL CHECK (database_purpose = 'C4_PIT_PRE_RIGHTS_VALIDATION_DB'),
    schema_contract VARCHAR NOT NULL,
    migration_checksum VARCHAR NOT NULL CHECK (regexp_full_match(migration_checksum, '[0-9a-f]{64}')),
    schema_fingerprint VARCHAR NOT NULL CHECK (regexp_full_match(schema_fingerprint, '[0-9a-f]{64}')),
    created_at VARCHAR NOT NULL,
    applied_at VARCHAR NOT NULL,
    code_commit_sha VARCHAR NOT NULL
);

CREATE TABLE system_policy (
    policy_id VARCHAR PRIMARY KEY,
    publication_allowed BOOLEAN NOT NULL CHECK (publication_allowed = FALSE),
    public_api_allowed BOOLEAN NOT NULL CHECK (public_api_allowed = FALSE),
    export_allowed BOOLEAN NOT NULL CHECK (export_allowed = FALSE),
    redistribution_allowed BOOLEAN NOT NULL CHECK (redistribution_allowed = FALSE),
    policy_json VARCHAR NOT NULL
);

CREATE TABLE rights_profile (
    rights_profile_id VARCHAR PRIMARY KEY,
    profile_version VARCHAR NOT NULL,
    source_channel VARCHAR NOT NULL,
    rights_status VARCHAR NOT NULL CHECK (rights_status IN ('ALLOWED_WITH_CONDITIONS', 'REVIEW_REQUIRED', 'SYNTHETIC_TEST_ONLY', 'DISABLED')),
    automated_retrieval_allowed BOOLEAN NOT NULL,
    materialization_allowed BOOLEAN NOT NULL,
    internal_analysis_allowed BOOLEAN NOT NULL,
    public_use_allowed BOOLEAN NOT NULL CHECK (public_use_allowed = FALSE),
    export_allowed BOOLEAN NOT NULL CHECK (export_allowed = FALSE),
    profile_json VARCHAR NOT NULL,
    UNIQUE (source_channel, profile_version),
    UNIQUE (rights_profile_id, source_channel, profile_version)
);

CREATE TABLE source_allowlist (
    source_channel VARCHAR PRIMARY KEY,
    activity_status VARCHAR NOT NULL CHECK (activity_status IN ('ENABLED_SYNTHETIC_ONLY', 'ENABLED_DATASET_READ_ONLY', 'DISABLED')),
    rights_profile_id VARCHAR NOT NULL,
    profile_version VARCHAR NOT NULL,
    allowlist_json VARCHAR NOT NULL,
    FOREIGN KEY (rights_profile_id, source_channel, profile_version)
        REFERENCES rights_profile(rights_profile_id, source_channel, profile_version),
    UNIQUE (source_channel, rights_profile_id, profile_version)
);

CREATE TABLE rights_assessment (
    rights_assessment_id VARCHAR PRIMARY KEY,
    rights_profile_id VARCHAR NOT NULL,
    profile_version VARCHAR NOT NULL,
    source_channel VARCHAR NOT NULL,
    assessed_activity VARCHAR NOT NULL CHECK (assessed_activity IN ('SYNTHETIC_MATERIALIZATION', 'DATASET_READ_ONLY_IMPORT', 'REAL_PDF_RETRIEVAL', 'PUBLICATION', 'EXPORT')),
    decision VARCHAR NOT NULL CHECK (decision IN ('PASS', 'BLOCKED_REVIEW_REQUIRED', 'BLOCKED_DISABLED', 'BLOCKED_PUBLIC_POLICY', 'SUPERSEDED', 'INVALIDATED')),
    assessed_at VARCHAR NOT NULL,
    assessment_json VARCHAR NOT NULL,
    FOREIGN KEY (rights_profile_id, source_channel, profile_version)
        REFERENCES rights_profile(rights_profile_id, source_channel, profile_version),
    FOREIGN KEY (source_channel, rights_profile_id, profile_version)
        REFERENCES source_allowlist(source_channel, rights_profile_id, profile_version),
    UNIQUE (rights_profile_id, source_channel, assessed_activity, assessed_at),
    UNIQUE (rights_assessment_id, rights_profile_id, profile_version, source_channel, assessed_activity)
);

CREATE TABLE retention_policy (
    retention_policy_id VARCHAR PRIMARY KEY,
    policy_version VARCHAR NOT NULL,
    storage_class VARCHAR NOT NULL CHECK (storage_class IN ('RESTRICTED_EVIDENCE_STORE', 'VALIDATED_EVIDENCE_LAYER', 'INTERNAL_RESEARCH_DB')),
    disposition VARCHAR NOT NULL CHECK (disposition IN ('DISPOSABLE_TEST_ONLY', 'APPEND_ONLY_AUDIT', 'READ_ONLY_SOURCE_COPY')),
    retention_json VARCHAR NOT NULL
);

CREATE TABLE source_release (
    source_release_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(source_release_id, '[0-9a-f]{64}')),
    source_release_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(source_release_content_hash, '[0-9a-f]{64}')),
    source_id VARCHAR NOT NULL,
    data_acquisition_channel VARCHAR NOT NULL,
    historical_evidence_channel VARCHAR NOT NULL,
    release_family VARCHAR NOT NULL,
    release_json VARCHAR NOT NULL,
    UNIQUE (source_release_id, source_release_content_hash)
);

CREATE TABLE acquisition_run (
    acquisition_run_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(acquisition_run_id, '[0-9a-f]{64}')),
    run_kind VARCHAR NOT NULL CHECK (run_kind IN ('SYNTHETIC_VALIDATION', 'DATASET_READ_ONLY_VALIDATION')),
    source_channel VARCHAR NOT NULL,
    rights_profile_id VARCHAR NOT NULL,
    rights_profile_version VARCHAR NOT NULL,
    rights_assessment_id VARCHAR NOT NULL,
    authorized_activity VARCHAR NOT NULL CHECK (authorized_activity IN ('SYNTHETIC_MATERIALIZATION', 'DATASET_READ_ONLY_IMPORT')),
    retention_policy_id VARCHAR NOT NULL REFERENCES retention_policy(retention_policy_id),
    planned_at VARCHAR NOT NULL,
    run_json VARCHAR NOT NULL,
    FOREIGN KEY (rights_assessment_id, rights_profile_id, rights_profile_version, source_channel, authorized_activity)
        REFERENCES rights_assessment(rights_assessment_id, rights_profile_id, profile_version, source_channel, assessed_activity),
    UNIQUE (acquisition_run_id, rights_profile_id, rights_assessment_id),
    UNIQUE (acquisition_run_id, rights_profile_id, rights_profile_version, rights_assessment_id),
    UNIQUE (acquisition_run_id, source_channel, rights_profile_id, rights_profile_version, rights_assessment_id, authorized_activity)
);

CREATE TABLE acquisition_run_event (
    acquisition_run_id VARCHAR NOT NULL REFERENCES acquisition_run(acquisition_run_id),
    event_ordinal BIGINT NOT NULL CHECK (event_ordinal >= 0),
    run_state VARCHAR NOT NULL CHECK (run_state IN ('PLANNED', 'BLOCKED_RIGHTS', 'RUNNING', 'PARTIAL', 'COMPLETE', 'FAILED', 'INVALIDATED')),
    occurred_at VARCHAR NOT NULL,
    event_json VARCHAR NOT NULL,
    PRIMARY KEY (acquisition_run_id, event_ordinal),
    UNIQUE (acquisition_run_id, run_state, occurred_at)
);

CREATE TABLE retrieval_evidence (
    retrieval_evidence_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(retrieval_evidence_id, '[0-9a-f]{64}')),
    acquisition_run_id VARCHAR NOT NULL REFERENCES acquisition_run(acquisition_run_id),
    source_release_id VARCHAR NOT NULL REFERENCES source_release(source_release_id),
    retrieval_mode VARCHAR NOT NULL CHECK (retrieval_mode IN ('SYNTHETIC_FIXTURE', 'EXISTING_DATASET_READ_ONLY')),
    source_locator_safe VARCHAR NOT NULL,
    retrieved_at VARCHAR NOT NULL,
    response_payload_hash VARCHAR NOT NULL CHECK (regexp_full_match(response_payload_hash, '[0-9a-f]{64}')),
    retrieval_json VARCHAR NOT NULL,
    UNIQUE (acquisition_run_id, source_release_id, source_locator_safe, response_payload_hash),
    UNIQUE (retrieval_evidence_id, acquisition_run_id, source_release_id)
);

CREATE TABLE restricted_artifact_blob (
    payload_hash VARCHAR PRIMARY KEY CHECK (regexp_full_match(payload_hash, '[0-9a-f]{64}')),
    payload_bytes BLOB NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    synthetic_only BOOLEAN NOT NULL CHECK (synthetic_only = TRUE),
    retention_policy_id VARCHAR NOT NULL REFERENCES retention_policy(retention_policy_id),
    storage_class VARCHAR NOT NULL CHECK (storage_class = 'RESTRICTED_EVIDENCE_STORE')
);

CREATE TABLE evidence_artifact (
    artifact_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(artifact_id, '[0-9a-f]{64}')),
    artifact_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(artifact_content_hash, '[0-9a-f]{64}')),
    source_release_id VARCHAR NOT NULL,
    acquisition_run_id VARCHAR NOT NULL,
    retrieval_evidence_id VARCHAR NOT NULL,
    rights_profile_id VARCHAR NOT NULL,
    rights_assessment_id VARCHAR NOT NULL,
    payload_hash VARCHAR NOT NULL REFERENCES restricted_artifact_blob(payload_hash),
    official_locator VARCHAR NOT NULL,
    media_type VARCHAR NOT NULL,
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    artifact_json VARCHAR NOT NULL,
    synthetic_only BOOLEAN NOT NULL CHECK (synthetic_only = TRUE),
    UNIQUE (artifact_id, artifact_content_hash),
    UNIQUE (artifact_id, source_release_id),
    UNIQUE (artifact_id, artifact_content_hash, payload_hash),
    UNIQUE (official_locator),
    FOREIGN KEY (retrieval_evidence_id, acquisition_run_id, source_release_id)
        REFERENCES retrieval_evidence(retrieval_evidence_id, acquisition_run_id, source_release_id),
    FOREIGN KEY (acquisition_run_id, rights_profile_id, rights_assessment_id)
        REFERENCES acquisition_run(acquisition_run_id, rights_profile_id, rights_assessment_id)
);

CREATE TABLE acquisition_manifest (
    manifest_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(manifest_id, '[0-9a-f]{64}')),
    manifest_hash VARCHAR NOT NULL UNIQUE CHECK (regexp_full_match(manifest_hash, '[0-9a-f]{64}')),
    acquisition_run_id VARCHAR NOT NULL,
    rights_profile_id VARCHAR NOT NULL,
    rights_profile_version VARCHAR NOT NULL,
    rights_assessment_id VARCHAR NOT NULL,
    manifest_json VARCHAR NOT NULL,
    FOREIGN KEY (acquisition_run_id, rights_profile_id, rights_profile_version, rights_assessment_id)
        REFERENCES acquisition_run(acquisition_run_id, rights_profile_id, rights_profile_version, rights_assessment_id),
    UNIQUE (manifest_id, acquisition_run_id, rights_assessment_id)
);

CREATE TABLE acquisition_manifest_member (
    manifest_id VARCHAR NOT NULL REFERENCES acquisition_manifest(manifest_id),
    artifact_ordinal BIGINT NOT NULL CHECK (artifact_ordinal >= 0),
    artifact_id VARCHAR NOT NULL,
    artifact_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(artifact_content_hash, '[0-9a-f]{64}')),
    payload_hash VARCHAR NOT NULL CHECK (regexp_full_match(payload_hash, '[0-9a-f]{64}')),
    PRIMARY KEY (manifest_id, artifact_ordinal),
    UNIQUE (manifest_id, artifact_id),
    FOREIGN KEY (artifact_id, artifact_content_hash, payload_hash)
        REFERENCES evidence_artifact(artifact_id, artifact_content_hash, payload_hash)
);

CREATE TABLE temporal_bound (
    temporal_bound_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(temporal_bound_id, '[0-9a-f]{64}')),
    bound_type VARCHAR NOT NULL CHECK (bound_type IN ('EXACT', 'BY_DATE', 'NOT_BEFORE', 'NOT_LATER_THAN', 'INTERVAL', 'UNKNOWN')),
    precision VARCHAR NOT NULL CHECK (precision IN ('EXACT_TIMESTAMP', 'DATE_ONLY', 'MONTH_ONLY', 'UNKNOWN')),
    normalized_start VARCHAR,
    normalized_end VARCHAR,
    source_timezone VARCHAR,
    bound_json VARCHAR NOT NULL,
    CHECK ((bound_type = 'UNKNOWN' AND precision = 'UNKNOWN' AND normalized_start IS NULL AND normalized_end IS NULL) OR bound_type <> 'UNKNOWN')
);

CREATE TABLE evidence_claim (
    claim_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(claim_id, '[0-9a-f]{64}')),
    claim_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(claim_content_hash, '[0-9a-f]{64}')),
    source_release_id VARCHAR NOT NULL REFERENCES source_release(source_release_id),
    artifact_id VARCHAR REFERENCES evidence_artifact(artifact_id),
    temporal_bound_id VARCHAR NOT NULL REFERENCES temporal_bound(temporal_bound_id),
    claim_type VARCHAR NOT NULL CHECK (claim_type IN ('SOURCE_RELEASE_PUBLICATION', 'ARTIFACT_MEMBERSHIP', 'RECORD_VALUE', 'RECORD_VERSION')),
    subject_scope VARCHAR NOT NULL CHECK (subject_scope IN ('UNBOUND', 'SOURCE_RELEASE_ONLY', 'ATTACHMENT_BOUND', 'RECORD_BOUND', 'RECORD_VERSION_BOUND')),
    claim_json VARCHAR NOT NULL,
    UNIQUE (claim_id, claim_content_hash),
    UNIQUE (claim_id, source_release_id, artifact_id),
    FOREIGN KEY (artifact_id, source_release_id)
        REFERENCES evidence_artifact(artifact_id, source_release_id)
);

CREATE TABLE evidence_subject_binding (
    binding_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(binding_id, '[0-9a-f]{64}')),
    binding_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(binding_content_hash, '[0-9a-f]{64}')),
    claim_id VARCHAR NOT NULL REFERENCES evidence_claim(claim_id),
    source_release_id VARCHAR REFERENCES source_release(source_release_id),
    artifact_id VARCHAR REFERENCES evidence_artifact(artifact_id),
    subject_scope VARCHAR NOT NULL CHECK (subject_scope IN ('UNBOUND', 'SOURCE_RELEASE_ONLY', 'ATTACHMENT_BOUND', 'RECORD_BOUND', 'RECORD_VERSION_BOUND')),
    binding_json VARCHAR NOT NULL,
    UNIQUE (binding_id, binding_content_hash)
);

CREATE TABLE historical_vintage_claim (
    vintage_claim_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(vintage_claim_id, '[0-9a-f]{64}')),
    vintage_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(vintage_content_hash, '[0-9a-f]{64}')),
    observation_id VARCHAR NOT NULL CHECK (regexp_full_match(observation_id, '[0-9a-f]{64}')),
    source_release_id VARCHAR NOT NULL REFERENCES source_release(source_release_id),
    temporal_bound_id VARCHAR NOT NULL REFERENCES temporal_bound(temporal_bound_id),
    parent_vintage_claim_id VARCHAR REFERENCES historical_vintage_claim(vintage_claim_id),
    semantic_value DECIMAL(38, 12) NOT NULL,
    unit VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    vintage_json VARCHAR NOT NULL,
    synthetic_only BOOLEAN NOT NULL CHECK (synthetic_only = TRUE),
    UNIQUE (vintage_claim_id, vintage_content_hash),
    CHECK (parent_vintage_claim_id IS NULL OR parent_vintage_claim_id <> vintage_claim_id)
);

CREATE TABLE historical_vintage_claim_evidence (
    vintage_claim_id VARCHAR NOT NULL REFERENCES historical_vintage_claim(vintage_claim_id),
    evidence_ordinal BIGINT NOT NULL CHECK (evidence_ordinal >= 0),
    claim_id VARCHAR NOT NULL REFERENCES evidence_claim(claim_id),
    claim_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(claim_content_hash, '[0-9a-f]{64}')),
    PRIMARY KEY (vintage_claim_id, evidence_ordinal),
    UNIQUE (vintage_claim_id, claim_id)
);

CREATE TABLE historical_vintage_claim_binding (
    vintage_claim_id VARCHAR NOT NULL REFERENCES historical_vintage_claim(vintage_claim_id),
    binding_ordinal BIGINT NOT NULL CHECK (binding_ordinal >= 0),
    binding_id VARCHAR NOT NULL REFERENCES evidence_subject_binding(binding_id),
    binding_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(binding_content_hash, '[0-9a-f]{64}')),
    PRIMARY KEY (vintage_claim_id, binding_ordinal),
    UNIQUE (vintage_claim_id, binding_id)
);

CREATE TABLE extraction_evidence (
    extraction_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(extraction_id, '[0-9a-f]{64}')),
    artifact_id VARCHAR NOT NULL REFERENCES evidence_artifact(artifact_id),
    artifact_content_hash VARCHAR NOT NULL CHECK (regexp_full_match(artifact_content_hash, '[0-9a-f]{64}')),
    record_locator_json VARCHAR NOT NULL,
    commodity VARCHAR NOT NULL,
    period VARCHAR NOT NULL,
    raw_value VARCHAR NOT NULL,
    normalized_value DECIMAL(38, 12) NOT NULL,
    unit VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    extracted_at VARCHAR NOT NULL,
    validation_status VARCHAR NOT NULL CHECK (validation_status IN ('PASS', 'FAIL')),
    extraction_json VARCHAR NOT NULL
);

CREATE TABLE attribution_metadata (
    attribution_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(attribution_id, '[0-9a-f]{64}')),
    artifact_id VARCHAR NOT NULL REFERENCES evidence_artifact(artifact_id),
    attribution_text VARCHAR NOT NULL,
    metadata_json VARCHAR NOT NULL
);

CREATE TABLE audit_reference (
    audit_reference_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(audit_reference_id, '[0-9a-f]{64}')),
    acquisition_run_id VARCHAR NOT NULL,
    manifest_id VARCHAR NOT NULL,
    rights_assessment_id VARCHAR NOT NULL REFERENCES rights_assessment(rights_assessment_id),
    audit_type VARCHAR NOT NULL CHECK (audit_type IN ('SYNTHETIC_E2E', 'DATASET_READ_ONLY_IMPORT', 'INVALIDATION')),
    audit_json VARCHAR NOT NULL,
    FOREIGN KEY (manifest_id, acquisition_run_id, rights_assessment_id)
        REFERENCES acquisition_manifest(manifest_id, acquisition_run_id, rights_assessment_id),
    UNIQUE (audit_reference_id, acquisition_run_id, manifest_id, rights_assessment_id)
);

CREATE TABLE trusted_collection (
    collection_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(collection_id, '[0-9a-f]{64}')),
    collection_hash VARCHAR NOT NULL UNIQUE CHECK (regexp_full_match(collection_hash, '[0-9a-f]{64}')),
    acquisition_run_id VARCHAR NOT NULL,
    manifest_id VARCHAR NOT NULL,
    rights_assessment_id VARCHAR NOT NULL,
    audit_reference_id VARCHAR NOT NULL,
    replay_fingerprint VARCHAR NOT NULL CHECK (regexp_full_match(replay_fingerprint, '[0-9a-f]{64}')),
    collection_status VARCHAR NOT NULL CHECK (collection_status IN ('SYNTHETIC_VALIDATED', 'INVALIDATED')),
    promoted_at VARCHAR NOT NULL,
    collection_json VARCHAR NOT NULL,
    FOREIGN KEY (audit_reference_id, acquisition_run_id, manifest_id, rights_assessment_id)
        REFERENCES audit_reference(audit_reference_id, acquisition_run_id, manifest_id, rights_assessment_id),
    UNIQUE (collection_id, audit_reference_id)
);

CREATE TABLE trusted_collection_membership (
    collection_id VARCHAR NOT NULL REFERENCES trusted_collection(collection_id),
    membership_ordinal BIGINT NOT NULL CHECK (membership_ordinal >= 0),
    artifact_id VARCHAR NOT NULL REFERENCES evidence_artifact(artifact_id),
    claim_id VARCHAR NOT NULL REFERENCES evidence_claim(claim_id),
    binding_id VARCHAR NOT NULL REFERENCES evidence_subject_binding(binding_id),
    vintage_claim_id VARCHAR NOT NULL REFERENCES historical_vintage_claim(vintage_claim_id),
    audit_reference_id VARCHAR NOT NULL,
    membership_hash VARCHAR NOT NULL CHECK (regexp_full_match(membership_hash, '[0-9a-f]{64}')),
    PRIMARY KEY (collection_id, membership_ordinal),
    UNIQUE (collection_id, vintage_claim_id),
    FOREIGN KEY (collection_id, audit_reference_id)
        REFERENCES trusted_collection(collection_id, audit_reference_id)
);

CREATE TABLE dataset_observation (
    dataset_record_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(dataset_record_id, '[0-9a-f]{64}')),
    rights_profile_id VARCHAR NOT NULL REFERENCES rights_profile(rights_profile_id),
    rights_assessment_id VARCHAR NOT NULL REFERENCES rights_assessment(rights_assessment_id),
    acquisition_run_id VARCHAR NOT NULL REFERENCES acquisition_run(acquisition_run_id),
    source_contract_id VARCHAR NOT NULL,
    source_relative_path VARCHAR NOT NULL CHECK (source_relative_path = 'data/world-bank.json'),
    source_sha256 VARCHAR NOT NULL CHECK (regexp_full_match(source_sha256, '[0-9a-f]{64}')),
    commodity VARCHAR NOT NULL CHECK (commodity = 'Copper'),
    period VARCHAR NOT NULL CHECK (regexp_full_match(period, '[0-9]{4}-(0[1-9]|1[0-2])')),
    value DECIMAL(38, 12) NOT NULL,
    unit VARCHAR NOT NULL CHECK (unit = '($/mt)'),
    source_lineage_json VARCHAR NOT NULL,
    historical_availability_status VARCHAR NOT NULL CHECK (historical_availability_status = 'ABSENT_NOT_PIT_VERIFIED'),
    data_classification VARCHAR NOT NULL CHECK (data_classification = 'REAL_DATASET_DERIVED'),
    UNIQUE (commodity, period, source_contract_id, source_sha256)
);
