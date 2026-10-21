## MODIFIED Requirements

### Requirement: Cache key stability

The system SHALL generate cache keys using cryptographic hashing of the complete embedding vector, ensuring consistent key generation across processes, platforms, and execution instances. The hash SHALL be appended to the namespace prefix defined by the `cache-namespace` capability.

#### Scenario: Same embedding produces same cache key

- **WHEN** the same embedding is hashed in different processes/workers
- **THEN** the generated cache key is identical

##### Example: Cross-process consistency

- **GIVEN** embedding = [0.1, 0.2, 0.3, ..., 1.5] (1536 dims) and namespace `carbon-assistant-sample-index`
- **WHEN** process A hashes the embedding
- **THEN** cache_key = "prompt_cache:carbon-assistant-sample-index:a1b2c3d4e5f6g7h8"
- **AND** process B hashes the same embedding
- **THEN** cache_key = "prompt_cache:carbon-assistant-sample-index:a1b2c3d4e5f6g7h8" (identical)

#### Scenario: Different embeddings produce different cache keys

- **WHEN** two embeddings differ in any dimension
- **THEN** the generated cache keys are different

##### Example: Single dimension variance

- **GIVEN** embedding1 = [0.1, 0.2, 0.3, ..., 1.5]
- **AND** embedding2 = [0.1, 0.2, 0.3, ..., 1.501] (last dim differs by 0.001)
- **WHEN** both embeddings are hashed
- **THEN** cache_key_1 ≠ cache_key_2

#### Scenario: No collision on partial embedding data

- **WHEN** two embeddings have identical first 10 dimensions but differ in remaining dimensions
- **THEN** the generated cache keys are different

##### Example: First 10 dims identical, later dims different

- **GIVEN** embedding1 = [0.1, 0.2, ..., 0.9] + [0.01, 0.02, 0.03, ...]
- **AND** embedding2 = [0.1, 0.2, ..., 0.9] + [0.99, 0.98, 0.97, ...]
- **WHEN** both are hashed using full embedding
- **THEN** cache_key_1 ≠ cache_key_2 (not collided)
