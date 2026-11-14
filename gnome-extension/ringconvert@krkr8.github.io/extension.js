// SPDX-License-Identifier: GPL-3.0-or-later
// RingConvert Shell extension: drag a file, press Shift, and the
// ring opens at the cursor.
//
// - Drags come from Meta.Dnd (dnd-enter / dnd-leave), which mutter emits for
//   real Wayland drag-and-drop only, so a Shift rubber-band selection or a
//   window move never opens the ring. While a drag is active the pointer's
//   modifier state is polled every POLL_MS to catch Shift.
// - Shift opens the ring with Show(x, y); Shift again toggles it with
//   ShowTools / Show. The mode is read from the ring window's title
//   (ring_window.RING_TITLES), so toggles done in the app itself are seen too.
// - The ring window is found on window-created → shown, kept out of window
//   lists, kept above, and moved so it is centred on the cursor (also for
//   `ringconvert --show` from the keyboard shortcut), clamped to the work area
//   of the monitor under the cursor.
// - When the drag ends outside the disc, or is cancelled, the ring is hidden;
//   a drop on the disc is left to the app. Escape hides the ring while it shows.
//
// Every callback is guarded and logs failures with a "[ringconvert]" prefix;
// disable() removes every signal, timer and key grab.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Mtk from 'gi://Mtk';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_ID = 'io.github.krkr8.RingConvert';
const OBJECT_PATH = '/io/github/krkr8/RingConvert';
const INTERFACE = 'io.github.krkr8.RingConvert';
const RING_TITLES = {convert: 'RingConvert', tools: 'RingConvert Tools'};

const POLL_MS = 40;
// After the drag ends, give the drop a moment to reach the app before judging.
const LEAVE_CHECK_MS = 80;
// A Show(x, y) we sent places the next ring window if it appears within this.
const REQUEST_TTL_US = 5 * GLib.USEC_PER_SEC;
// The first call may D-Bus-activate the app (a Python start-up).
const CALL_TIMEOUT_MS = 10000;
// The glass disc's share of the ring window (ring_widget.Geometry: radius 200 + margin 30).
const DISC_RATIO = 200 / 230;

const DEBUG = GLib.getenv('RINGCONVERT_DEBUG') === '1';

function debug(...args) {
    if (DEBUG)
        console.log('[ringconvert]', ...args);
}

function logFailure(where, error) {
    console.error(`[ringconvert] ${where}: ${error}\n${error?.stack ?? ''}`);
}

export default class RingConvertExtension extends Extension {
    enable() {
        this._signals = [];             // [object, handlerId]
        this._windowSignals = new Map(); // Meta.Window → [handlerId]
        this._sources = new Set();      // GLib source ids
        this._cancellable = new Gio.Cancellable();
        this._ring = null;              // the ring's Meta.Window while it is shown
        this._dragging = false;
        this._shiftDown = false;
        this._pollId = 0;
        this._request = null;           // {x, y, time} of our last Show
        this._escapeAction = Meta.KeyBindingAction.NONE;
        try {
            this._connectAll();
        } catch (e) {
            // The shell won't call disable() after a failed enable(): undo here.
            logFailure('enable', e);
            this.disable();
            throw e;
        }
        debug('enabled');
    }

    _connectAll() {
        const dnd = global.backend.get_dnd();
        this._connect(dnd, 'dnd-enter', 'dnd-enter', () => this._onDragBegin());
        this._connect(dnd, 'dnd-leave', 'dnd-leave', () => this._onDragEnd());
        this._connect(global.display, 'window-created', 'window-created',
            (_display, window) => this._watchWindow(window));
        this._connect(global.display, 'accelerator-activated', 'accelerator-activated',
            (_display, action) => this._onAccelerator(action));

        // A ring that is already open (the extension was just enabled).
        for (const actor of global.get_window_actors()) {
            const window = actor.get_meta_window();
            if (this._isRing(window))
                this._adoptRing(window, false);
        }
    }

    disable() {
        // Each step on its own, so one failure can't leave the rest behind.
        const attempt = (where, func) => {
            try {
                func();
            } catch (e) {
                logFailure(`disable: ${where}`, e);
            }
        };
        attempt('poll', () => this._stopPolling());
        for (const id of this._sources)
            attempt('timer', () => GLib.source_remove(id));
        this._sources.clear();
        for (const [object, id] of this._signals)
            attempt('signal', () => object.disconnect(id));
        this._signals = [];
        for (const [window, ids] of this._windowSignals) {
            for (const id of ids)
                attempt('window signal', () => window.disconnect(id));
        }
        this._windowSignals.clear();
        attempt('Escape', () => this._releaseEscape());
        attempt('calls', () => this._cancellable?.cancel());
        this._cancellable = null;
        this._ring = null;
        this._request = null;
        this._dragging = false;
        debug('disabled');
    }

    // ------------------------------------------------------------ helpers

    _guard(where, func) {
        return (...args) => {
            try {
                return func(...args);
            } catch (e) {
                logFailure(where, e);
                return undefined;
            }
        };
    }

    _connect(object, signal, where, func) {
        this._signals.push([object, object.connect(signal, this._guard(where, func))]);
    }

    // A one-shot timer, removed in disable() if still pending.
    _timeout(ms, where, func) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._sources.delete(id);
            this._guard(where, func)();
            return GLib.SOURCE_REMOVE;
        });
        this._sources.add(id);
    }

    _call(method, x = 0, y = 0) {
        const params = method === 'Hide' ? null : new GLib.Variant('(ii)', [Math.round(x), Math.round(y)]);
        debug(`call ${method}(${method === 'Hide' ? '' : `${Math.round(x)}, ${Math.round(y)}`})`);
        Gio.DBus.session.call(APP_ID, OBJECT_PATH, INTERFACE, method, params, null,
            Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, this._cancellable,
            (connection, result) => {
                try {
                    connection.call_finish(result);
                } catch (e) {
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        logFailure(`${method} call`, e);
                }
            });
    }

    // ------------------------------------------------------------ the drag

    _onDragBegin() {
        if (Main.sessionMode.isLocked)
            return;
        this._dragging = true;
        // Only a Shift *press* during the drag counts, not one held from the start.
        this._shiftDown = (global.get_pointer()[2] & Clutter.ModifierType.SHIFT_MASK) !== 0;
        debug(`drag begin (shift held: ${this._shiftDown})`);
        this._startPolling();
    }

    _onDragEnd() {
        if (!this._dragging)
            return;
        this._dragging = false;
        this._stopPolling();
        debug('drag end');
        if (!this._ring && !this._request)
            return;
        this._timeout(LEAVE_CHECK_MS, 'drag end check', () => {
            const [x, y, mods] = global.get_pointer();
            const held = (mods & Clutter.ModifierType.BUTTON1_MASK) !== 0;
            const overDisc = this._overDisc(x, y);
            debug(`after drag: button held ${held}, over disc ${overDisc}`);
            // Button still down: the drag was cancelled (Escape). Released off
            // the disc: nothing will be dropped. Otherwise the app has the drop.
            if (held || !overDisc)
                this._call('Hide');
        });
    }

    _startPolling() {
        if (this._pollId)
            return;
        this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_MS, () => {
            try {
                return this._poll();
            } catch (e) {
                logFailure('poll', e);
                this._pollId = 0;
                return GLib.SOURCE_REMOVE;
            }
        });
    }

    _stopPolling() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
    }

    _poll() {
        if (!this._dragging) {
            this._pollId = 0;
            return GLib.SOURCE_REMOVE;
        }
        const [x, y, mods] = global.get_pointer();
        const shift = (mods & Clutter.ModifierType.SHIFT_MASK) !== 0;
        if (shift && !this._shiftDown)
            this._onShift(x, y);
        this._shiftDown = shift;
        return GLib.SOURCE_CONTINUE;
    }

    _onShift(x, y) {
        if (this._ring) {
            const tools = this._ring.get_title() === RING_TITLES.tools;
            this._call(tools ? 'Show' : 'ShowTools', x, y);
        } else {
            this._request = {x, y, time: GLib.get_monotonic_time()};
            this._call('Show', x, y);
        }
    }

    // ------------------------------------------------------------ the ring window

    _isRing(window) {
        return window?.get_wm_class() === APP_ID &&
            Object.values(RING_TITLES).includes(window.get_title());
    }

    _watchWindow(window) {
        if (!window || this._windowSignals.has(window))
            return;
        const appId = window.get_wm_class();
        debug(`window created: ${appId} "${window.get_title()}"`);
        if (appId && appId !== APP_ID)
            return;
        const ids = [
            window.connect('shown', this._guard('shown', () => {
                debug(`window shown: ${window.get_wm_class()} "${window.get_title()}"`);
                if (this._isRing(window))
                    this._adoptRing(window, true);
            })),
            window.connect('unmanaged', this._guard('unmanaged', () => this._forget(window))),
        ];
        this._windowSignals.set(window, ids);
    }

    _forget(window) {
        const ids = this._windowSignals.get(window);
        if (ids) {
            for (const id of ids)
                window.disconnect(id);
            this._windowSignals.delete(window);
        }
        if (this._ring === window) {
            debug('ring closed');
            this._ring = null;
            this._releaseEscape();
        }
    }

    _adoptRing(window, place) {
        this._watchWindow(window);
        this._ring = window;
        window.hide_from_window_list();
        window.make_above();
        this._grabEscape();
        if (!place)
            return;

        let x, y;
        const request = this._request;
        this._request = null;
        if (request && GLib.get_monotonic_time() - request.time < REQUEST_TTL_US)
            ({x, y} = request);
        else
            [x, y] = global.get_pointer();
        this._centreOn(window, x, y);
    }

    _centreOn(window, x, y) {
        const frame = window.get_frame_rect();
        const monitor = global.display.get_monitor_index_for_rect(
            new Mtk.Rectangle({x, y, width: 1, height: 1}));
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor);
        // The transparent margin (shadow room) may hang off the edge; the disc may not.
        const slack = Math.floor(frame.width / 2 * (1 - DISC_RATIO));
        const clamp = (value, low, high) => Math.max(low, Math.min(value, Math.max(low, high)));
        const left = clamp(Math.round(x - frame.width / 2),
            area.x - slack, area.x + area.width - frame.width + slack);
        const top = clamp(Math.round(y - frame.height / 2),
            area.y - slack, area.y + area.height - frame.height + slack);
        window.move_frame(true, left, top);
        debug(`ring ${frame.width}x${frame.height} centred on (${Math.round(x)}, ${Math.round(y)}) ` +
            `at (${left}, ${top}) on monitor ${monitor}`);
    }

    _overDisc(x, y) {
        if (!this._ring)
            return false;
        const frame = this._ring.get_frame_rect();
        const radius = frame.width / 2 * DISC_RATIO;
        const dx = x - (frame.x + frame.width / 2);
        const dy = y - (frame.y + frame.height / 2);
        return dx * dx + dy * dy <= radius * radius;
    }

    // ------------------------------------------------------------ Escape

    // Only grabbed while the ring shows: during a drag the source app has the
    // keyboard focus, so the ring itself never sees the key.
    _grabEscape() {
        if (this._escapeAction !== Meta.KeyBindingAction.NONE)
            return;
        const action = global.display.grab_accelerator('Escape', Meta.KeyBindingFlags.NONE);
        if (action === Meta.KeyBindingAction.NONE) {
            debug('could not grab Escape');
            return;
        }
        this._escapeAction = action;
        Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
    }

    _releaseEscape() {
        if (this._escapeAction === Meta.KeyBindingAction.NONE)
            return;
        const action = this._escapeAction;
        this._escapeAction = Meta.KeyBindingAction.NONE;
        Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
        global.display.ungrab_accelerator(action);
    }

    _onAccelerator(action) {
        if (action !== this._escapeAction || this._escapeAction === Meta.KeyBindingAction.NONE)
            return;
        debug('Escape');
        this._call('Hide');
    }
}
