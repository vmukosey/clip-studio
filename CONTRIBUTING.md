# Contributing

Clip Studio is an early source release. Small fixes, reproducible bug reports, and documentation improvements are welcome.

1. Open an issue before a large change so its scope can be discussed.
2. Keep changes focused. Do not commit media, local databases, `.env` files, API keys, model files, or generated exports.
3. For Python changes, set `DATABASE_URL` and `CLIP_STORAGE_ROOT` to separate test paths, then run `.\.venv\Scripts\python.exe -m pytest -q` from `clip/`. For editor changes, run `npm test -- --exclude tests/clipHandoff.test.ts`, `npm run typecheck`, and `npm run build:web` from `studio-editor/`. The excluded integration test needs `CLIP_PROJECT_PATH` pointing to a generated editable project.
4. Describe the behavior you changed and the commands you ran in your pull request. Include a screenshot for visible UI changes, using only synthetic or shareable media.

Original Clip Studio contributions are accepted under AGPL-3.0-only. `studio-editor/` is modified CutWright and retains its own license and attribution. Keep copyright and third-party notices intact.
