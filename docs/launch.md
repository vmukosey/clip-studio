# Launch materials

## Demo

- `docs/assets/studio-demo.png` shows Clip Studio with synthetic media.
- `audit/results/clip-studio-social-preview.png` is the 1280 x 640 GitHub social preview (kept out of the repository until uploaded in Settings).
- `audit/results/clip-studio-workflow-demo.mp4` is a 12-second demo: the Studio timeline followed by the locally exported synthetic vertical clip. It contains no personal footage.

## Post for video creators

I built Clip Studio to turn long videos into vertical clips you can actually review and edit. The first release lets you import footage, trim it on a browser timeline, and export a vertical MP4 locally. AI storyboard selection is optional and needs your own compatible vision API key plus a worker setup.

Here is a short demo using synthetic media: https://github.com/vmukosey/clip-studio/releases/download/v0.1.0-preview/clip-studio-workflow-demo.mp4. Source and Windows setup: https://github.com/vmukosey/clip-studio

This is an early source release, with no one-click installer yet. For creators who try it: where does the first manual clip workflow slow you down, and which step of installation is unclear?

## Post for technical communities

Clip Studio is a Windows-first FastAPI service plus a modified CutWright browser editor for making reviewable vertical clips. Manual editing and local export work without an AI provider. Automatic storyboard analysis uses an external OpenAI-compatible vision API, Redis, and Celery; the repo includes Docker Compose for that path.

Demo with synthetic input and an exported result: https://github.com/vmukosey/clip-studio/releases/download/v0.1.0-preview/clip-studio-workflow-demo.mp4. Code and setup: https://github.com/vmukosey/clip-studio (AGPL-3.0-only for original code; CutWright attribution is preserved).

I would value reports on clean-install failures, browser export reliability, and the API setup instructions. Please include your Windows, Python, browser, and FFmpeg versions, with keys and private paths removed.

Check each community's posting rules before sharing either post.

## First 30 days

Review GitHub Traffic weekly, since the rolling view is short-lived. Record views, unique visitors, and top referring sources; also count installation questions, first-run failures, browser export failures, and repeated feature requests from issues or comments. Keep a short dated note for each week and choose the next priority from the repeated problems. A simple Windows package is a candidate only if installation friction is a leading issue.
