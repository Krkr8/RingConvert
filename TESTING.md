# Testing RingConvert by hand

Follow the sections in order. Each step says what you should see. Commands are run in a terminal
from the project folder (the folder you cloned, e.g. `cd ringconvert`).

## Emergency: switch the Shell extension off

If the desktop freezes or misbehaves at any point:

1. Press **Ctrl+Alt+F3**, then log in with your user name and password.
2. Run:
   ```sh
   DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus \
       gnome-extensions disable ringconvert@krkr8.github.io
   ```
3. Press **Ctrl+Alt+F2** (or **F1**) to get back to the desktop.

If GNOME won't start at all, run this in that console instead, then log in again:
```sh
mv ~/.local/share/gnome-shell/extensions/ringconvert@krkr8.github.io ~/ringconvert-extension.off
```

## 0. Automated checks (optional)

```sh
python3 -m pytest -q
RINGCONVERT_SHELL_TESTS=1 python3 -m pytest -q tests/test_shell_extension.py
```
Expected: everything passes. Some tests may be skipped when an optional backend is missing.

## 1. Install

1. Run `./install.sh` and enter your password when dnf asks. It installs `nautilus-python`, among
   other packages.
   Expected: it ends with **Done.** and a short list of next steps. In the "Backend check" every
   line has a ✓, except `pillow avif`, which says "optional" (avifenc does that job).
2. **Log out and log back in.** This loads the Shell extension.
3. Run `gnome-extensions info ringconvert@krkr8.github.io`.
   Expected: it says the extension is enabled and its state is ACTIVE.
4. Run `nautilus -q`, then open Files again. This loads the right-click menu.
5. Run `gnome-extensions list | grep ringconvert`.
   Expected: only `ringconvert@krkr8.github.io`. (An older 0.1.0 install,
   `ringconvert@ringconvert.github.io`, is removed by `install.sh`.)

## 2. Drag + Shift (Shell extension)

Use a folder with a test picture in it, e.g. copy one into `~/Pictures/rc-test/`.

1. In Files, start dragging the picture and keep the mouse button down. Press **Shift** once.
   Expected: the convert ring opens centred under the cursor, showing the picture's size in the centre.
2. Still holding the button, press **Shift** again.
   Expected: the tools ring. Press **Shift** once more: back to the convert ring.
3. Move onto **PNG** and release.
   Expected: the segment pulses, the ring closes, a notification appears, and a `.png` with the
   same name is next to the picture.
4. Drag again, press **Shift**, then release far away from the ring.
   Expected: the ring closes and nothing new is created.
5. Drag again, press **Shift**, then press **Esc**.
   Expected: the ring closes. Release the file anywhere.
6. Hold **Shift** first, then start dragging.
   Expected: no ring. Shift only counts when it's pressed during the drag.
7. Drag two pictures together, press **Shift** twice to get the tools ring.
   Expected: CROP, EDIT, ANNOTATE and REDACT are greyed out.

## 3. Keyboard shortcut

1. Put the cursor somewhere on the screen and press **Super+Shift+C**.
   Expected: the ring opens at the cursor.
2. Press **Super+Shift+C** again.
   Expected: the ring closes. Repeat steps 1 and 2 two more times: it must work every time.
3. Press **Super+Shift+C**, then drag the picture from Files onto **WEBP**.
   Expected: a `.webp` next to the picture.

## 4. Files right-click menu

1. Right-click the picture, then **Convert with RingConvert**.
   Expected: a list of formats without the picture's own format, then **Tools ▸**.
2. Choose **GIF**.
   Expected: a notification, and a `.gif` next to the picture.
3. Right-click the picture, then **Convert with RingConvert → Tools → Crop…**.
   Expected: the Crop Image window. GNOME may open it behind Files without focus; if you don't
   see it, look in the Activities overview. Close it with the round ×.
4. Select two pictures, right-click, then **Convert with RingConvert**.
   Expected: only the formats both can become. Under **Tools**, Crop/Edit/Annotate/Redact are greyed out.
5. Choose **Tools → Compress**.
   Expected: one notification, and a `… Compressed.jpg` (or `.png`) next to each picture.
6. Right-click a folder.
   Expected: no RingConvert entry.

## 5. Open With

1. Right-click the picture, then **Open With…**.
   Expected: **RingConvert** is in the list. Choose it.
   Expected: the ring opens for that picture.
2. Hover **BMP** and click it.
   Expected: a `.bmp` next to the picture.

## 6. Uninstall and reinstall

1. Run `./uninstall.sh`.
   Expected: it lists what it removed, says the extension was "switched off and removed from the
   running shell", and ends with **Done.**
2. Run `nautilus -q`, then open Files again and right-click a picture.
   Expected: no **Convert with RingConvert**.
3. Drag a file and press **Shift**.
   Expected: nothing happens; the extension was switched off at once.
4. Press **Super+Shift+C**.
   Expected: nothing happens.
5. Right-click a picture, then **Open With…**.
   Expected: no RingConvert.
6. Run `gnome-extensions list | grep ringconvert` and `ls ~/.local/bin/ringconvert*`.
   Expected: no output, and "No such file or directory".
7. To keep using RingConvert, run `./install.sh` again, then **log out and back in**, then run
   `nautilus -q`.

## Reporting a problem

Open an [issue](https://github.com/Krkr8/RingConvert/issues/new/choose) and attach:
- the output of `journalctl -b -o cat /usr/bin/gnome-shell | grep ringconvert` (Shell extension)
- for the ring or the menu, a trace from the app: run `ringconvert --quit`, then
  `RINGCONVERT_DEBUG=1 ringconvert --service`, repeat the problem, and copy what it printed. Stop it
  with Ctrl+C; the next drag + Shift or right-click starts the normal background service again.
