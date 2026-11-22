# Clip Studio pre-release

This is an early source release for technical Windows users. It includes the FastAPI clip service, the bundled browser editor, and the modified CutWright source. There is no Clip Studio Windows installer in this release.

Start with the [manual clip guide](https://github.com/vmukosey/clip-studio#make-your-first-clip-manually). It needs Python 3.12, FFmpeg/FFprobe, and a current desktop Chrome or Edge browser. The editor can import local media, arrange and trim it on a timeline, and export a vertical MP4 locally.

Automatic storyboard selection is a separate setup. It requires your own compatible external vision API and key, plus Redis and a Celery worker. See the [automatic selection instructions](https://github.com/vmukosey/clip-studio#try-automatic-selection). The Clip Studio side panel is currently Russian; the underlying editor can be switched to English using its language menu.

The attached demo uses synthetic media and shows the editor followed by an exported clip. No installer or Parabolic archive is attached.

Please report clean-install failures, export failures, or unclear steps through the [bug report template](https://github.com/vmukosey/clip-studio/issues/new?template=bug_report.md), without keys or private media.
