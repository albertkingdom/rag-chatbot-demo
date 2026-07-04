## ADDED Requirements

### Requirement: Cache key stability

The system SHALL generate cache keys using cryptographic hashing of the complete embedding vector, ensuring consistent key generation across processes, platforms, and execution instances.

#### Scenario: Same embedding produces same cache key

- **WHEN** the same embedding is hashed in different processes/workers
- **THEN** the generated cache key is identical

##### Example: Cross-process consistency

- **GIVEN** embedding = [0.1, 0.2, 0.3, ..., 1.5] (1536 dims)
- **WHEN** process A hashes the embedding
- **THEN** cache_key = "prompt_cache:a1b2c3d4e5f6g7h8"
- **AND** process B hashes the same embedding
- **THEN** cache_key = "prompt_cache:a1b2c3d4e5f6g7h8" (identical)

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

### Requirement: Cache entry deduplication

The system SHALL prevent duplicate cache entries from silently overwriting existing entries and losing hit statistics.

#### Scenario: Repeated caching of same question

- **WHEN** the same question is cached twice without intermediate eviction
- **THEN** the existing cache entry's hit_count is preserved (not reset to 0)

##### Example: Hit count persistence

- **GIVEN** question Q cached with hit_count = 5
- **WHEN** the same Q is cached again
- **THEN** the new cache entry has hit_count = 5 (not reset to 0)

#### Scenario: Cache entry detection

- **WHEN** checking if a question embedding is already cached
- **THEN** the system identifies the duplicate using the stable cache key

## REMOVED Requirements

(none)
