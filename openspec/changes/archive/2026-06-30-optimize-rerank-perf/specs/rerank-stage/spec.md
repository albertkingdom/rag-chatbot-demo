## ADDED Requirements

### Requirement: Rerank stage is resourced for bounded inference latency

The rerank stage SHALL run its BGE Reranker cross-encoder inference with the BLAS/OpenMP thread count (`OMP_NUM_THREADS`) set equal to the number of vCPUs allocated to the runtime that executes the rerank stage, so that inference utilizes all allocated cores rather than defaulting to a single thread. The number of candidate `Document` objects passed into a single rerank call SHALL be bounded by the `FUSION_TOP_M` constant defined in `src/config.py`. Increasing the allocated vCPU count without setting `OMP_NUM_THREADS` to match SHALL be treated as a misconfiguration, because the added cores would not be used by inference.

#### Scenario: thread count matches allocated vCPUs

- **WHEN** the runtime allocates 4 vCPUs to the service that runs the rerank stage
- **THEN** `OMP_NUM_THREADS` is set to 4 so cross-encoder inference uses all 4 cores, and a single rerank call receives at most `FUSION_TOP_M` candidates

##### Example: resourcing matrix

| Allocated vCPUs | OMP_NUM_THREADS | FUSION_TOP_M | Result |
| --------------- | --------------- | ------------ | ------ |
| 1 | 1 (or unset) | 10 | baseline: ~25s for 10 candidates on one core |
| 4 | 4 | 5 | inference parallelized across 4 cores over 5 candidates |
| 4 | unset | 5 | misconfiguration: torch defaults to 1 thread, added cores idle |
