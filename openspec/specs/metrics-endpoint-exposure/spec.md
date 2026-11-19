# metrics-endpoint-exposure Specification

## Purpose

Controls whether the backend exposes Prometheus metrics over HTTP, so metrics are available to the local Prometheus without being published by the public deployment.

## Requirements

### Requirement: Metrics endpoint is disabled by default

The backend SHALL serve Prometheus text-format metrics at `GET /metrics` only when the `METRICS_ENABLED` environment variable is `true` or `1` (case-insensitive). For any other value, including when the variable is unset, `GET /metrics` SHALL NOT return Prometheus-format content.

#### Scenario: Enabled

- **WHEN** `METRICS_ENABLED=true` and a client requests `GET /metrics`
- **THEN** the response status is 200 and the body contains `# HELP chat_requests_total`

#### Scenario: Unset or other value

- **WHEN** `METRICS_ENABLED` is unset and a client requests `GET /metrics`
- **THEN** the response body does not contain `# HELP`

##### Example: flag parsing

| METRICS_ENABLED | Metrics served |
| --- | --- |
| (unset) | no |
| `true` | yes |
| `TRUE` | yes |
| `1` | yes |
| `false` | no |
| `yes` | no |

---
### Requirement: Only the local compose stack enables metrics

The local docker-compose configuration SHALL set `METRICS_ENABLED=true` for the web service and SHALL let Prometheus scrape it over the compose network. The Cloud Run deployment configuration SHALL NOT enable metrics.

#### Scenario: Deployment configuration does not enable metrics

- **WHEN** the Terraform configuration is inspected
- **THEN** no Cloud Run service or job sets `METRICS_ENABLED`

#### Scenario: Prometheus scrapes the web service

- **WHEN** the local compose stack is running
- **THEN** the Prometheus targets page lists the web service scrape target as UP
