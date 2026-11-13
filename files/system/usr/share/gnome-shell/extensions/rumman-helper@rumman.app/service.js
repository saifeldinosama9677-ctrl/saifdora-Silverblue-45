// The little D-Bus object the extension offers to the Rumman app (io.github.Rumman.Shell). It needs nothing from the
// Shell itself, so it can be tested with plain `gjs`.
import Gio from 'gi://Gio';

export const SHELL_NAME = 'io.github.Rumman.Shell';
export const SHELL_PATH = '/io/github/Rumman/Shell';

const IFACE = `<node>
  <interface name="${SHELL_NAME}">
    <method name="Info"><arg type="s" direction="out" name="info"/></method>
    <method name="Expect"/>
    <method name="Nudge"/>
  </interface>
</node>`;

export class ShellService {
    /** @param {{info: function(): string, expect: function(): void, nudge: function(): void}} handlers */
    constructor(handlers) {
        this._handlers = handlers;
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._ownId = 0;
        this._connection = null;
    }

    start(connection = Gio.DBus.session) {
        this._connection = connection;
        this._impl.export(connection, SHELL_PATH);
        this._ownId = Gio.bus_own_name_on_connection(connection, SHELL_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    stop() {
        if (this._ownId) {
            Gio.bus_unown_name(this._ownId);
            this._ownId = 0;
        }
        try {
            this._impl.unexport();
        } catch (e) {
            // already gone
        }
        this._connection = null;
    }

    // --- the exported methods (the app calls them) ---
    Info() {
        return this._handlers.info();
    }

    Expect() {
        this._handlers.expect();
    }

    Nudge() {
        this._handlers.nudge();
    }
}
