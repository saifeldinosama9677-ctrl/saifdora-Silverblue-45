// Rumman helper (GNOME Shell extension).
//
// What it does, in one paragraph: GNOME on Wayland does not let an ordinary app know that a file is being dragged, nor
// read the Shift key during the drag, nor place its own window under the pointer. The Shell can do all three. So this
// extension watches for drags, polls the Shift key while one is going on, and tells the Rumman app (over D-Bus, by
// activating its actions): drag-begin / wheel-open / drag-end. When the app opens its wheel window (and, after a drop,
// its options page) the extension puts it under the pointer, above all other windows, without the usual window-open
// animation, and gives the options page the keyboard.
// It never reads, copies or changes any file: the files travel from the file manager straight into the Rumman window.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';

import {APP_ID, DragController, isRummanWindow, nudgeTarget, placeWindow, sessionIsWayland, wantsFocus} from './logic.js';
import {activateAction} from './bus.js';
import {ShellService} from './service.js';

const POLL_MS = 16;
const EXPECT_MS = 3000;
const LOG_LINES = 60;

export default class RummanHelper extends Extension {
    enable() {
        this._enabledAt = new Date().toISOString();
        this._lines = [];
        this._drags = 0;
        this._placed = 0;
        this._appOwner = '';
        this._appPid = 0;
        this._expectUntil = 0;
        this._lastSpawn = 0;
        this._pollId = 0;
        this._timeouts = new Set();
        this._signals = [];
        this._watchId = 0;

        this._controller = new DragController({
            getPointer: () => global.get_pointer(),
            send: (name, seq, onFail) => this._callApp(name, seq, onFail),
            log: msg => this._note(msg),
        });
        this._service = new ShellService({
            info: () => this._info(),
            expect: () => this._expect(),
            nudge: () => this._nudge(),
        });

        try {
            this._service.start();
        } catch (e) {
            this._note(`could not offer the D-Bus service: ${e.message}`);
        }
        this._watchId = Gio.bus_watch_name(Gio.BusType.SESSION, APP_ID, Gio.BusNameWatcherFlags.NONE,
            (_c, _name, owner) => this._appAppeared(owner),
            () => this._appVanished());

        this._wayland = sessionIsWayland(name => GLib.getenv(name));
        if (this._wayland) {
            this._connect(Main.xdndHandler, 'drag-begin', () => this._onDragBegin());
            this._connect(Main.xdndHandler, 'drag-end', () => this._onDragEnd());
            this._connect(global.display, 'window-created', (_d, window) => this._onWindowCreated(window));
            this._note(`enabled on GNOME Shell ${Config.PACKAGE_VERSION} (Wayland)`);
            // Give the bus watcher a moment to report an app that is already running before deciding to start one.
            this._later(1500, () => this._ensureApp('enable'));
        } else {
            // On an X11 session the app watches drags itself; two watchers would open the wheel twice.
            this._note(`enabled on GNOME Shell ${Config.PACKAGE_VERSION} (X11 session: staying idle)`);
        }
    }

    disable() {
        this._note('disabled');
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];
        for (const actor of global.get_window_actors())
            actor.meta_window.disconnectObject(this);
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        this._service?.stop();
        this._service = null;
        this._controller = null;
    }

    // ------------------------------------------------------------------ helpers --
    _connect(obj, name, cb) {
        this._signals.push([obj, obj.connect(name, cb)]);
    }

    _later(ms, fn) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            this._timeouts.delete(id);
            try {
                fn();
            } catch (e) {
                this._note(`error: ${e.message}`);
            }
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
    }

    _nowMs() {
        return GLib.get_monotonic_time() / 1000;
    }

    _note(msg) {
        const line = `${new Date().toISOString().slice(11, 23)} ${msg}`;
        this._lines.push(line);
        if (this._lines.length > LOG_LINES)
            this._lines.shift();
        console.log(`[rumman-helper] ${msg}`);
    }

    _monitors() {
        return Main.layoutManager.monitors.map(m => ({x: m.x, y: m.y, width: m.width, height: m.height}));
    }

    // ------------------------------------------------------------- the app side --
    _appAppeared(owner) {
        this._appOwner = owner;
        this._note(`app is on the bus (${owner})`);
        Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'GetConnectionUnixProcessID', new GLib.Variant('(s)', [owner]), new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE, 1000, null, (conn, res) => {
                try {
                    this._appPid = conn.call_finish(res).deepUnpack()[0];
                } catch (e) {
                    this._appPid = 0;
                }
            });
    }

    _appVanished() {
        this._appOwner = '';
        this._appPid = 0;
    }

    _findLauncher() {
        const own = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'rumman']);
        if (GLib.file_test(own, GLib.FileTest.IS_EXECUTABLE))
            return own;
        return GLib.find_program_in_path('rumman');
    }

    _ensureApp(why) {
        if (this._appOwner)
            return;
        const now = this._nowMs();
        if (now - this._lastSpawn < 5000)
            return;
        this._lastSpawn = now;
        const exe = this._findLauncher();
        if (!exe) {
            this._note('cannot start Rumman: launcher not found (run: python3 rumman.py --install)');
            return;
        }
        try {
            Gio.Subprocess.new([exe, '--service'], Gio.SubprocessFlags.NONE);
            this._note(`started the Rumman service (${why})`);
        } catch (e) {
            this._note(`could not start Rumman: ${e.message}`);
        }
    }

    /** Activates one of the app's GActions over D-Bus (see bus.js). */
    _callApp(action, seq, onFail) {
        activateAction(Gio.DBus.session, action, seq, error => {
            if (!error)
                return;
            this._note(`${action} #${seq} failed: ${error.message}`);
            onFail?.();
            this._ensureApp(`${action} failed`);
        });
    }

    // ------------------------------------------------------------------- drags --
    _onDragBegin() {
        this._drags++;
        this._ensureApp('drag-begin');
        this._controller.dragBegin(this._nowMs());
        if (!this._pollId) {
            this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_MS, () => {
                if (this._controller?.tick(this._nowMs()))
                    return GLib.SOURCE_CONTINUE;
                this._pollId = 0;
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _onDragEnd() {
        this._controller.dragEnd();
    }

    // ----------------------------------------------------------------- windows --
    _expect() {
        this._expectUntil = this._nowMs() + EXPECT_MS;
    }

    _onWindowCreated(window) {
        try {
            const expecting = this._nowMs() < this._expectUntil;
            const samePid = this._appPid !== 0 && window.get_pid() === this._appPid;
            if (expecting) {
                // At this moment the window has no title yet, so we go by "the app said it is about to open one".
                const actor = window.get_compositor_private();
                if (actor)
                    Main.wm.skipNextEffect(actor);          // the wheel has its own pop-in; no scale-in of the window
            }
            // Every new window gets a "shown" listener: it is cheap, and only the wheel passes the title test there.
            window.connectObject(
                'shown', () => this._onShown(window, samePid || expecting),
                'unmanaged', () => window.disconnectObject(this),
                this);
        } catch (e) {
            this._note(`window-created: ${e.message}`);
        }
    }

    _onShown(window, ours) {
        window.disconnectObject(this);
        const title = window.get_title();
        const id = {
            title,
            wmClass: window.get_wm_class(),
            gtkAppId: window.get_gtk_application_id(),
            ours,
        };
        if (!isRummanWindow(id))
            return;
        const [px, py] = global.get_pointer();
        const rect = window.get_frame_rect();
        const {x, y} = placeWindow([px, py], this._monitors(), {width: rect.width, height: rect.height});
        window.move_frame(true, x, y);
        window.make_above();                    // a window opened without focus would sit below the file manager
        window.unset_demands_attention();       // ... and would ask for attention ("is ready"): we do not want that
        if (wantsFocus(title))
            window.activate(global.get_current_time());      // the options page is typed on: Esc, Enter
        this._placed++;
        this._note(`${title} placed at ${x},${y} (pointer ${px},${py}, window ${rect.width}x${rect.height})`);
    }

    // ------------------------------------------------------------------ nudge ----
    /** The wheel opened under a pointer that does not move: move it by one pixel and back so that the compositor
     *  looks again at what is under it (and tells the drag source about the new window). */
    _nudge() {
        const seat = global.stage.context.get_backend().get_default_seat();
        const [px, py] = global.get_pointer();
        const [nx, ny] = nudgeTarget([px, py], this._monitors());
        seat.warp_pointer(nx, ny);
        this._later(25, () => {
            const [cx, cy] = global.get_pointer();
            if (cx === nx && cy === ny)             // the user did not move meanwhile: put it back
                seat.warp_pointer(px, py);
        });
        this._note(`nudged the pointer (${px},${py}) -> (${nx},${ny})`);
    }

    // -------------------------------------------------------------------- info ---
    _info() {
        return JSON.stringify({
            helper: this.metadata.version,
            shell: Config.PACKAGE_VERSION,
            wayland: this._wayland,
            enabledAt: this._enabledAt,
            appOnBus: !!this._appOwner,
            appPid: this._appPid,
            drags: this._drags,
            placed: this._placed,
            dragActive: this._controller?.active ?? false,
            log: this._lines.slice(-30),
        });
    }
}
