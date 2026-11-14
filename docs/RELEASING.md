# Making a release

For maintainers. A release is a git tag `vX.Y.Z`, a GitHub release with the extension zip, and
(when the extension changed) a new upload to extensions.gnome.org.

## 1. Prepare

1. Set the version in three places:
   - `pyproject.toml` → `version`
   - `src/ringconvert/__init__.py` → `__version__`
   - `gnome-extension/ringconvert@krkr8.github.io/metadata.json` → `version-name`
2. Supporting a new GNOME release: add it to `shell-version` in `metadata.json` (stable releases
   only, e.g. `"52"`), then run the Shell tests on that GNOME version.
3. Move the *Unreleased* notes in `CHANGELOG.md` under the new version and date.
4. Run everything:
   ```sh
   python3 -m pytest -q
   RINGCONVERT_SHELL_TESTS=1 python3 -m pytest -q tests/test_shell_extension.py
   ```
5. Do the manual checks in [TESTING.md](../TESTING.md) on a real session.

## 2. Build the extension zip

```sh
./scripts/pack-extension.sh
# → dist/ringconvert@krkr8.github.io.shell-extension.zip
```

Check it locally before you upload it (this replaces the installed copy; log out and in after):

```sh
gnome-extensions install --force dist/ringconvert@krkr8.github.io.shell-extension.zip
```

## 3. Tag and publish on GitHub

```sh
git tag -a vX.Y.Z -m "RingConvert X.Y.Z"
git push origin main vX.Y.Z
gh release create vX.Y.Z dist/ringconvert@krkr8.github.io.shell-extension.zip \
    --title "RingConvert X.Y.Z" --notes-file <(release notes from CHANGELOG.md)
```

## 4. Upload to extensions.gnome.org

1. Sign in at <https://extensions.gnome.org> (create an account the first time).
2. Open <https://extensions.gnome.org/upload/>, choose the zip from `dist/`, accept the licence
   terms and upload.
3. The first upload creates the extension page. Add a screenshot there (e.g.
   `docs/screenshots/convert-ring.png`). The description comes from `metadata.json`.
4. Every upload is reviewed by a person before it appears on the site and in Extension Manager.
   This takes from a few days to a few weeks. Reviewers' comments show on the upload's review
   page and arrive by e-mail. Fix the issues, bump `version-name`, and upload again.

Things reviewers check (see the [review
guidelines](https://gjs.guide/extensions/review-guidelines/review-guidelines.html)): nothing is
set up in the constructor or at import time, `disable()` undoes everything `enable()` did, no
logging in normal use, no bundled binaries, and the description says the extension needs the
separate RingConvert app.

The extension's UUID (`ringconvert@krkr8.github.io`) is fixed once it's on extensions.gnome.org:
never change it.
