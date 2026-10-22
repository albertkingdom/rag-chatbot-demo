# continuous-integration Specification

## Purpose

Runs the project's automated checks on every pull request and every push to master, so results are visible on GitHub before merging.

## Requirements

### Requirement: CI runs on pull requests and master

The repository SHALL have a GitHub Actions workflow that runs on every pull request and every push to `master`, with read-only repository permissions and no secrets, and that cancels an in-progress run when a newer commit is pushed to the same ref.

#### Scenario: Pull request triggers CI

- **WHEN** a pull request is opened or updated
- **THEN** the backend, frontend and Terraform jobs run and report a status on the pull request

---
### Requirement: Backend tests

The backend job SHALL install Python dependencies from the hash-locked `requirements.txt` with `--require-hashes` on Python 3.13 and SHALL fail when `python -m pytest tests` fails.

#### Scenario: Failing test fails the job

- **WHEN** any test in `tests/` fails
- **THEN** the backend job fails

---
### Requirement: Frontend checks

The frontend job SHALL install dependencies with `pnpm install --frozen-lockfile` and SHALL run lint, typecheck, the vitest suite and the production build, failing on any error.

#### Scenario: Lint error fails the job

- **WHEN** `pnpm lint` reports an error or warning
- **THEN** the frontend job fails

---
### Requirement: Terraform validation

The Terraform job SHALL fail when `terraform fmt -check -recursive` reports unformatted files or when `terraform validate` fails after `terraform init -backend=false`.

#### Scenario: Unformatted Terraform fails the job

- **WHEN** a `.tf` file is not formatted
- **THEN** the Terraform job fails
