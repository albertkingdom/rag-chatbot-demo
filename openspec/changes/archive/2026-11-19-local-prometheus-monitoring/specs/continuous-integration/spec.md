## MODIFIED Requirements

### Requirement: CI runs on pull requests and master

The repository SHALL have a GitHub Actions workflow that runs on every pull request and every push to `master`, with read-only repository permissions and no secrets, and that cancels an in-progress run when a newer commit is pushed to the same ref.

#### Scenario: Pull request triggers CI

- **WHEN** a pull request is opened or updated
- **THEN** the backend, frontend, Terraform and Prometheus rules jobs run and report a status on the pull request

## ADDED Requirements

### Requirement: Prometheus rules validation

The Prometheus rules job SHALL run `promtool check config` on the Prometheus configuration and `promtool test rules` on the SLO rule tests, using the same pinned Prometheus image version as the local compose stack, and SHALL fail when either command fails.

#### Scenario: Image versions stay aligned

- **WHEN** the Prometheus image tag in the CI workflow differs from the tag in the local compose file
- **THEN** the automated check fails

#### Scenario: Failing rule test fails the job

- **WHEN** a promtool rule test assertion does not hold
- **THEN** the Prometheus rules job fails
