# dependency-locking Specification

## Purpose

TBD - created by archiving change 'pin-dependencies'. Update Purpose after archive.

## Requirements

### Requirement: Locked dependency file with transitive pins and hashes

The repository SHALL maintain a machine-generated lock file (`requirements.txt`) that pins every installed package — direct and transitive — to an exact version (`==<version>`), with a `--hash` annotation per package. The lock file SHALL be reproducible from a hand-maintained source file (`requirements.in`) via a single `pip-compile --generate-hashes` invocation. The source file SHALL preserve the existing range constraints for the four packages that already had them (`sentence-transformers>=2.3,<6`, `huggingface_hub>=0.20,<1`, `ragas>=0.2,<0.3`, `datasets>=2.14`); other entries MAY be unpinned in the source file so `pip-compile` can pick the latest resolvable version.

#### Scenario: lock file pins all installed packages exactly

- **WHEN** `pip-compile --generate-hashes -r requirements.in` is run
- **THEN** the resulting `requirements.txt` contains one `==<version>` line per package (direct and transitive) and at least one `--hash` per line

#### Scenario: source file preserves existing range constraints

- **WHEN** the source file is inspected
- **THEN** the four previously-constrained packages retain their `>=...,<...` ranges, and `pip-compile` resolves within those ranges

#### Scenario: recompile is idempotent at the same resolution

- **WHEN** `pip-compile` is run twice consecutively without changing `requirements.in` or the resolver index state
- **THEN** the generated `requirements.txt` is byte-identical (modulo header timestamp comments, which pip-compile may vary)


<!-- @trace
source: pin-dependencies
updated: 2026-07-02
code:
  - project-architecture-slide.html
  - architecture-diagram.png
  - requirements.in
  - requirements.txt
  - architecture-diagram.drawio
  - Dockerfile
  - project-architecture-slide.standalone.html
  - project-intro-slide.html
  - README.md
  - architecture-diagram.svg
  - scripts/compile_requirements.sh
-->

---
### Requirement: Reproducible install with hash verification

The Dockerfile (and any local install path) SHALL install dependencies via `pip install --require-hashes -r requirements.txt` (or `pip install --no-cache-dir -r requirements.txt` when `--require-hashes` is implied by the file format). A clean build SHALL produce the same package versions regardless of when it runs. If a package hash does not match the recorded hash, the install SHALL fail fast rather than silently installing a tampered artifact.

#### Scenario: clean install reproduces locked versions

- **WHEN** `pip install --require-hashes -r requirements.txt` is run in a fresh environment
- **THEN** the installed versions match the `==<version>` pins exactly, and any hash mismatch aborts the install

#### Scenario: tampered package is rejected

- **WHEN** a downstream package is replaced by a file with a different hash than recorded
- **THEN** `pip install` exits non-zero and no partial install is used


<!-- @trace
source: pin-dependencies
updated: 2026-07-02
code:
  - project-architecture-slide.html
  - architecture-diagram.png
  - requirements.in
  - requirements.txt
  - architecture-diagram.drawio
  - Dockerfile
  - project-architecture-slide.standalone.html
  - project-intro-slide.html
  - README.md
  - architecture-diagram.svg
  - scripts/compile_requirements.sh
-->

---
### Requirement: Single recompile entry point

The repository SHALL provide a single script (`scripts/compile_requirements.sh`) that regenerates `requirements.txt` from `requirements.in` using `pip-compile --generate-hashes`. The script SHALL be the documented entry point for updating dependencies so contributors do not hand-edit the lock file.

#### Scenario: script regenerates the lock file

- **WHEN** `scripts/compile_requirements.sh` is executed
- **THEN** `requirements.txt` is overwritten with the current `pip-compile` output for `requirements.in`

#### Scenario: lock file is not hand-edited

- **WHEN** a contributor needs to add or upgrade a package
- **THEN** they edit `requirements.in` and run the script, never `requirements.txt` directly

<!-- @trace
source: pin-dependencies
updated: 2026-07-02
code:
  - project-architecture-slide.html
  - architecture-diagram.png
  - requirements.in
  - requirements.txt
  - architecture-diagram.drawio
  - Dockerfile
  - project-architecture-slide.standalone.html
  - project-intro-slide.html
  - README.md
  - architecture-diagram.svg
  - scripts/compile_requirements.sh
-->