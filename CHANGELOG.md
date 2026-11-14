# Changelog

All notable changes to RingConvert. Versions follow [Semantic Versioning](https://semver.org).

## [1.0.0] - 2026-11-14

First public release.

### Added
- GNOME 51 support (GNOME Shell 51 / Fedora 45), next to GNOME 50.
- The Shell extension is ready for [extensions.gnome.org](https://extensions.gnome.org): homepage
  link, and `scripts/pack-extension.sh` builds the upload zip.
- Changelog, contributing guide, release guide and issue templates.

### Changed
- The ring is black smoked glass with a monochrome hover, in light and dark mode.
  `RINGCONVERT_THEME=warm` brings back the original cream-peach ring.
- The tool windows (Crop, Edit, Annotate, Add BG, Redact, Metadata) now use the same smoked-glass
  look as the ring.
- New IDs in the maintainer's own namespace: the app is `io.github.krkr8.RingConvert` and the
  extension `ringconvert@krkr8.github.io`. `install.sh` and `uninstall.sh` remove an install made
  under the old IDs (`io.github.ringconvert.RingConvert`, `ringconvert@ringconvert.github.io`).
- The extension no longer writes to the GNOME Shell log when it's switched on or off.

## [0.1.0]

Initial version: convert and tools rings, drag + Shift through the Shell extension, keyboard
shortcut, Files right-click menu, Open With, command-line tool, background service.

[1.0.0]: https://github.com/Krkr8/RingConvert/releases/tag/v1.0.0
[0.1.0]: https://github.com/Krkr8/RingConvert/commit/750d4da
