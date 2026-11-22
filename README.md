# Clip Studio

Clip Service and the modified CutWright browser editor used by Studio. The editor
source is in `studio-editor/` under [AGPL-3.0-only](studio-editor/LICENSE). The
ready-to-use web build is in `clip/clip_service/studio_web/`.

## Local setup (Windows)

1. Install Python 3.12 and FFmpeg with both `ffmpeg` and `ffprobe` on `PATH`.
2. From `clip/`, create the environment and install dependencies:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. From the repository root, run `Open Studio.bat`. It creates a local SQLite
   database at `clip/gaming-run.db` when one does not exist and uses
   `clip/gaming-run-storage/` for media and projects. Neither is tracked by Git.

The launcher also accepts the existing local FFmpeg installation at
`clip/tools/ffmpeg/ffmpeg-9.0.1-essentials_build/bin/`.

For optional local voiceover, install [Ollama](https://ollama.com/download/windows),
pull `qwen3-vl:4b-instruct`, and put the Piper voice files
`ru_RU-denis-medium.onnx` and `ru_RU-denis-medium.onnx.json` in `clip/models/`.
See [Clip Service documentation](clip/README.md) for API and voiceover settings.

## Rebuild Studio

Install Node.js 24 and run:

```powershell
Set-Location studio-editor
npm ci
Set-Location ..
.\studio-editor\scripts\build-studio.ps1
```

The build script updates the bundled web files in `clip/clip_service/studio_web/`.
CutWright's license and dependency notices are included with that build.

## Portable Parabolic

The `initial-import-2026-11-23` GitHub Release contains a separate Windows
portable Parabolic archive. Extract it and run `Parabolic.bat`. Its source is
[NickvisionApps/Parabolic](https://github.com/NickvisionApps/Parabolic), licensed
under [MIT](https://github.com/NickvisionApps/Parabolic/blob/main/LICENSE).
The archive excludes the local `app.db`, logs, debug symbols, and backup binaries.
The portable app stores its own settings locally; treat any new `app.db` as private.
