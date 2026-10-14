# semantic-cache-lookup Specification

## Purpose

Defines how the semantic prompt cache indexes, bounds and searches cached question-answer entries so that every live entry is eligible for a similarity match. It exists because a lookup that examines only an arbitrary subset of entries makes the hit rate collapse as the cache grows.

## Requirements

### Requirement: Lookup covers every indexed entry

The system SHALL compare the incoming question embedding against every entry in the cache index, up to the configured maximum number of entries, ordered from newest to oldest by write time. The set of entries examined SHALL NOT depend on the process, the process start time, or the order in which entries were inserted. A match SHALL still require a cosine similarity strictly greater than the threshold, and the entry with the highest similarity SHALL be returned. When the index is empty the system SHALL return no result without issuing a read for entry values.

#### Scenario: Oldest entry is still found

- **WHEN** the index holds 20 live entries and the incoming embedding matches only the oldest entry above the threshold
- **THEN** the lookup returns that entry

##### Example: position in the index does not matter

| Entries in index | Position of the matching entry (1 = newest) | Result |
| ---------------- | ------------------------------------------- | ------ |
| 3                | 3                                           | hit    |
| 20               | 20                                          | hit    |
| 50               | 1                                           | hit    |

#### Scenario: Highest similarity wins across all entries

- **GIVEN** three entries whose similarities to the incoming embedding are 0.96, 0.99 and 0.97
- **WHEN** the lookup runs
- **THEN** the entry with similarity 0.99 is returned

#### Scenario: Similarity equal to the threshold is a miss

- **WHEN** the best similarity among all entries equals the threshold exactly
- **THEN** the lookup returns no result

#### Scenario: Empty index

- **WHEN** the index holds no entries
- **THEN** the lookup returns no result and no read for entry values is issued

#### Scenario: Result shape is unchanged

- **WHEN** a lookup hits
- **THEN** the returned mapping contains the question, answer, sources, similarity and hit_count, and the stored hit_count is increased by one


<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->

---
### Requirement: Index size is bounded with oldest-first eviction

The system SHALL keep the cache index at no more than the configured maximum number of entries after every write. When a write would exceed the maximum, the system SHALL register the new entry, select the overflow entries (all but the newest maximum) and remove them from the index within a single transaction, and SHALL then delete exactly the data keys of the entries that transaction removed. Writing an entry whose key already exists SHALL refresh its write time and SHALL NOT increase the number of entries. When the configured maximum is lowered, the next write SHALL reduce the index to the new maximum, and lookups SHALL examine only the newest entries up to the new maximum.

#### Scenario: Writing beyond the maximum evicts the oldest entry

- **GIVEN** the maximum is 3 and the index holds entries A (oldest), B and C
- **WHEN** entry D is written
- **THEN** the index holds B, C and D, and the data key of A no longer exists

#### Scenario: Rewriting an existing key does not grow the index

- **WHEN** an entry is written whose embedding hashes to a key already in the index
- **THEN** the number of index entries is unchanged, the existing hit_count is preserved, and the entry becomes the newest

#### Scenario: Eviction deletes exactly the entries it removed

- **GIVEN** the maximum is 3 and the index holds 3 entries
- **WHEN** a new entry is written
- **THEN** the registration, the selection of overflow entries and their removal are issued in one transaction in that order, and the data keys deleted afterwards are exactly the entries removed by that transaction

#### Scenario: Lowered maximum takes effect on the next write

- **GIVEN** the index holds 5 entries and the maximum is lowered from 5 to 3
- **WHEN** a lookup runs
- **THEN** only the 3 newest entries are examined
- **AND WHEN** the next entry is written
- **THEN** the index holds exactly 3 entries


<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->

---
### Requirement: Maximum size setting is validated

The configured maximum number of entries, read from the CACHE_MAX_ENTRIES setting, SHALL be an integer of at least 1. An unset value SHALL yield the default of 50 without a warning. A value that is set but is not an integer or is smaller than 1 SHALL be replaced by the default of 50 and a warning SHALL be logged. An invalid value SHALL NOT prevent the application from starting.

#### Scenario: Valid override

- **WHEN** CACHE_MAX_ENTRIES is set to 10
- **THEN** after every write the index holds no more than 10 entries

##### Example: setting values

| CACHE_MAX_ENTRIES | Effective maximum | Warning logged |
| ----------------- | ----------------- | -------------- |
| (unset)           | 50                | no             |
| 10                | 10                | no             |
| 0                 | 50                | yes            |
| -3                | 50                | yes            |
| abc               | 50                | yes            |


<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->

---
### Requirement: Expired entries are purged during lookup

The system SHALL remove from the index every member whose data key no longer exists, within the single lookup that discovers it. Purging SHALL NOT depend on the expired member having been selected by an earlier lookup, and SHALL NOT issue a removal command when there is nothing to remove.

#### Scenario: One lookup clears all expired members

- **GIVEN** an index with 5 members whose data keys have expired and 1 live member
- **WHEN** a lookup runs
- **THEN** all 5 expired members are removed from the index and the live member is still eligible to match


<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->

---
### Requirement: Corrupted entries do not disable the cache

The system SHALL skip any single entry that cannot be used and SHALL continue to evaluate the remaining entries. An entry whose value is not valid JSON or is not a JSON object SHALL also be removed from the index. An entry whose answer is not a non-empty string, or whose sources field is missing or is not a list of strings, SHALL be skipped and removed from the index. An entry that lacks an embedding, has an embedding that is not a list of numbers, or whose similarity cannot be computed (for example because its dimension differs from the query) SHALL be skipped. A write SHALL succeed even when the existing value at the same key cannot be parsed, in which case the preserved hit_count SHALL be 0.

#### Scenario: Unparseable entry is skipped and removed

- **GIVEN** an index with one member whose value is not valid JSON and one valid matching member
- **WHEN** a lookup runs
- **THEN** the valid member is returned and the unparseable member is removed from the index

#### Scenario: Entry without embedding is skipped

- **WHEN** a member's stored value has no embedding field
- **THEN** that member is ignored and the lookup returns no result if no other member matches

#### Scenario: Dimension mismatch does not fail the lookup

- **GIVEN** one entry whose embedding has a different number of dimensions than the query and one valid matching entry
- **WHEN** a lookup runs
- **THEN** the valid entry is returned

#### Scenario: Entry without sources is unusable

- **WHEN** an entry that would otherwise match has no sources field
- **THEN** the entry is skipped and removed from the index, and the lookup returns no result if no other entry matches

#### Scenario: Entry with an invalid answer is unusable

- **WHEN** an entry that would otherwise match has an answer that is null or not a string
- **THEN** the entry is skipped and removed from the index

#### Scenario: Rewriting over a corrupted value

- **WHEN** an entry is written whose key currently holds a value that is not valid JSON
- **THEN** the write succeeds and the stored hit_count is 0


<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->

---
### Requirement: Legacy index coexists safely

The system SHALL use a new index key distinct from the legacy index key, and SHALL NOT read or write the legacy index during lookup or write. The presence of a legacy index of a different Redis type SHALL NOT cause lookup or write to fail. Clearing the cache SHALL remove the new index, the legacy index, and every data key registered in either, and SHALL return the number of distinct data keys removed.

#### Scenario: Legacy set exists at deployment

- **GIVEN** the legacy index key holds a Redis Set from a previous release
- **WHEN** an entry is written and then looked up
- **THEN** both operations succeed and the legacy Set is unchanged

#### Scenario: Clearing removes both indexes

- **GIVEN** entries registered in both the new index and the legacy Set, with one data key registered in both
- **WHEN** the cache is cleared
- **THEN** both index keys and all their registered data keys no longer exist, and the returned count counts the shared data key once

<!-- @trace
source: fix-cache-lookup-coverage
updated: 2026-10-14
code:
  - .env.example
  - architecture.md
  - src/cache_service.py
  - scripts/benchmark_cache_lookup.py
  - src/config.py
tests:
  - tests/test_cache_service.py
-->