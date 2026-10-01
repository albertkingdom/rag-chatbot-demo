# Branch validation — 2026-10-01

## Verified

- Frontend unit tests: `pnpm test` — 1 file, 4 tests passed.
- TypeScript and production assets: `pnpm build` — typecheck and Vite build passed.
- Backend: copied this branch's `src/` and `tests/` into `/tmp/branch-review-20261001` in the existing `rag-react-local-web-1` container; `python -m pytest -q` — 216 passed, 17 warnings. This uses the container's installed dependencies rather than a fresh lockfile installation.
- `git diff --check` passed.
- Chat shell uses viewport height with a scrolling message list and a non-shrinking composer/navigation area.

## Remaining acceptance work

- The tasks checklist remains open: passing the current tests does not establish every planned scenario.
- Frontend currently has API/parser unit tests but no component suite, lint command, or Playwright suite.
- Fresh Docker build and smoke test with fake providers/job runners remain unverified.
- Responsive browser checks, an actual phone/virtual keyboard check, XSS/focus checks, and concurrent RAG smoke tests remain required.
- Production revision cutover and rollback verification remain pending. Gradio and its dependency intentionally remain available for rollback until acceptance.
- Code review is required by `CLAUDE.md` before merging.

## Spectra tooling

`spectra status --change replace-gradio-frontend --json` and `spectra archive replace-gradio-frontend --preview --json` reported that the change does not exist, although its artifacts are present in this worktree. Resolve CLI worktree discovery before syncing/archiving. No tasks have been marked complete without evidence, and no archive has been forced.
