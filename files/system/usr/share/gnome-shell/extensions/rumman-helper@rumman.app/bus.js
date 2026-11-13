// How the extension talks to the Rumman app: it activates the app's GActions over D-Bus (org.gtk.Actions.Activate).
// Only Gio/GLib here (no Shell imports), so the very same code is run by the tests with plain `gjs`.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {APP_ID} from './logic.js';

export const APP_PATH = '/io/github/Rumman';
const CALL_TIMEOUT_MS = 1500;

/**
 * Activates one action of the app: drag-begin / wheel-open / wheel-cycle / drag-end, each with a sequence number.
 * Never starts the app by itself (NO_AUTO_START): the caller decides when to start it.
 * @param {Gio.DBusConnection} connection
 * @param {string} action
 * @param {number} seq
 * @param {function(Error|null): void} done  called once, with the error if the call failed
 */
export function activateAction(connection, action, seq, done = () => {}) {
    const params = new GLib.Variant('(sava{sv})', [action, [GLib.Variant.new_uint32(seq)], {}]);
    connection.call(APP_ID, APP_PATH, 'org.gtk.Actions', 'Activate', params, null,
        Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, null, (conn, res) => {
            try {
                conn.call_finish(res);
            } catch (e) {
                done(e);
                return;
            }
            done(null);
        });
}
