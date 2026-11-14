# Contributing to RingConvert

Thanks for helping. Bug reports, fixes, new formats and translations of the docs are all welcome.

## Reporting bugs

Open an [issue](https://github.com/Krkr8/RingConvert/issues/new/choose) and include:

- your distribution, `gnome-shell --version`, and whether the session is Wayland
- the output of `ringconvert-cli --check`
- the exact steps, what you expected and what happened
- for ring problems: run `ringconvert --quit`, then `RINGCONVERT_DEBUG=1 ringconvert --service`,
  repeat the problem and attach what it prints (check it for file names you'd rather not share)

## Getting set up

```sh
git clone https://github.com/Krkr8/RingConvert.git
cd RingConvert
./install.sh                  # Fedora packages + pip --user; or --user-only for desktop files only
```

You can also run everything straight from the checkout without installing:

```sh
PYTHONPATH=src python3 -m ringconvert.cli FILE --to jpg
PYTHONPATH=src python3 -m ringconvert.app --stay [FILES]     # Ctrl+C to stop
```

`--stay` keeps that instance running, and every later launch goes to it, so stop it when you're
done. `RINGCONVERT_DEBUG=1` traces launches, hover, drops and dismissals on stderr.

## Project layout

| Path | What |
|---|---|
| `src/ringconvert/core/` | Pure Python, no GTK: detection, naming, converters and tools. The GUI and CLI both call it. |
| `src/ringconvert/ring_widget.py`, `ring_window.py` | The ring: geometry, Cairo painter, drop target window |
| `src/ringconvert/dialogs/` | Tool windows (Crop, Edit, Annotate, Add BG, Redact, Metadata) |
| `src/ringconvert/app.py`, `dbus.py`, `jobs.py` | Single-instance app, D-Bus interface, background jobs and notifications |
| `gnome-extension/ringconvert@krkr8.github.io/` | GNOME Shell extension (drag + Shift, placement at the cursor) |
| `nautilus/` | Files right-click menu (nautilus-python) |
| `data/` | Desktop file, D-Bus service, autostart entry, icon |
| `install.sh`, `uninstall.sh`, `scripts/` | Installer, uninstaller, helpers, screenshot and pack scripts |

## Tests

```sh
python3 -m pytest -q                       # missing back ends are skipped; -rs shows why
RINGCONVERT_SHELL_TESTS=1 python3 -m pytest -q tests/test_shell_extension.py
```

- Tests make their own sample files in temporary folders.
- The Shell extension tests start a private, headless GNOME Shell with its own D-Bus, settings
  and runtime folder, and drive it with simulated pointer and keys. Nothing touches your session.
- The install tests run `install.sh` / `uninstall.sh --user-only` in a throwaway home. In test
  mode both scripts refuse to run unless HOME, the XDG folders and GSettings are sandboxed. Never
  run the scripts against your real home from a test.
- Real drag and drop can't be automated on Wayland, so check GUI changes by hand too:
  [TESTING.md](TESTING.md) has the steps.

**Never try new extension code in your own GNOME session first.** Use the headless tests or
`python3 tests/shell_harness.py`, which keeps a private shell running for poking. A broken
extension in your real session can leave you needing a text console (see the emergency steps in
[TESTING.md](TESTING.md)).

## Code guidelines

- Every source file starts with `SPDX-License-Identifier: GPL-3.0-or-later` (after a shebang, if
  any). Contributions are accepted under the same licence.
- Keep `core/` free of GTK so the CLI and tests can use it.
- Every external program or library is optional: use `backends.require_program` /
  `require_module`, which raise `BackendMissing` with an install hint.
- Converters never overwrite: output names come from `core/naming.py`.
- Ring colours live in `ring_widget.Palette`, dialog colours in `style.css`; no colour literals
  elsewhere.
- In the Shell extension, guard every callback, and undo every signal, timer and key grab in
  `disable()`. Keep the [extensions.gnome.org review
  guidelines](https://gjs.guide/extensions/review-guidelines/review-guidelines.html) in mind: no
  logging in normal use, no work in the constructor, nothing left behind after `disable()`.
- Don't commit personal data: no user names, home paths, e-mail addresses or private files in
  code, tests, screenshots or commit messages.

## Pull requests

1. Branch from `main`.
2. Keep each pull request to one change, with tests where it can be tested.
3. Run the full test suite (and the Shell tests if you touched the extension).
4. Update `README.md`, `TESTING.md` and `CHANGELOG.md` (under an *Unreleased* heading) when
   behaviour changes.
