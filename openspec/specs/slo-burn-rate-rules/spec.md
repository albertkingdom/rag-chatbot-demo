# slo-burn-rate-rules Specification

## Purpose

Keeps the Prometheus SLO recording and alerting rules verifiable: the rules are unit-tested with synthetic series and their latency thresholds line up with the histogram buckets the backend records.

## Requirements

### Requirement: Burn-rate alert rules pass promtool unit tests

The repository SHALL contain availability burn-rate alert rules with a fast-burn alert and a slow-burn alert, each evaluated over a long and a short window, and a promtool rule test file. `promtool test rules` SHALL pass, and the tests SHALL cover both firing and non-firing cases for each alert.

#### Scenario: Fast burn fires

- **WHEN** the synthetic error ratio stays above the fast-burn threshold across both fast-burn windows
- **THEN** the promtool test expects the fast-burn alert to be firing and passes

#### Scenario: Healthy traffic does not fire

- **WHEN** the synthetic series contain only successful requests
- **THEN** the promtool test expects no burn-rate alert and passes

---
### Requirement: Availability SLI covers every response source

The availability recording and alert rules SHALL compute availability from `chat_requests_total` across all `response_source` values, including `unknown`, as successes divided by successes plus errors, and SHALL exclude `cancelled` from both numerator and denominator.

#### Scenario: Errors without a response source count

- **WHEN** the synthetic series contain errors recorded only with `response_source="unknown"` and successes recorded with `response_source="rag"`
- **THEN** the promtool test expects the availability ratio to include those errors

#### Scenario: Cancelled requests excluded

- **WHEN** the synthetic series contain 90 successes, 10 cancelled and 0 errors
- **THEN** the promtool test expects an availability ratio of 1

---
### Requirement: SLO latency thresholds are histogram bucket boundaries

Every latency threshold referenced in the SLO rules SHALL equal one of the bucket boundaries of the corresponding latency histogram recorded by the backend, so that SLI ratios are exact counts rather than interpolated estimates.

#### Scenario: Threshold not on a bucket boundary

- **WHEN** a rule references `le="11"` while the latency histogram buckets do not include 11
- **THEN** the automated check fails
