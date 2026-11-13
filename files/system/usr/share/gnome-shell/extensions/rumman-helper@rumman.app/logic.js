// Pure logic of the Rumman helper. No GNOME imports here, so the same code is unit-tested with plain Node.

export const APP_ID = 'io.github.Rumman';
export const WHEEL_TITLE = 'rumman-wheel';
export const OPTIONS_TITLE = 'rumman-options';  // the page that opens after a file is dropped on a slice
export const PLACED_TITLES = [WHEEL_TITLE, OPTIONS_TITLE];
export const SHIFT_MASK = 1 << 0;               // Clutter.ModifierType.SHIFT_MASK

export function isShift(mods) {
    return (mods & SHIFT_MASK) !== 0;
}

/**
 * Follows one drag from its beginning to its end and turns it into the three calls the Rumman app needs:
 *   drag-begin   a drag started
 *   wheel-open   Shift was pressed (the first time in this drag; a second press changes nothing: there are no modes)
 *   drag-end     the drag is over
 * Shift that is already down when the drag starts does not count: it has to be released and pressed again.
 * (The app still understands an old `wheel-cycle` call, but this helper no longer sends it.)
 */
export class DragController {
    /**
     * @param {object} deps
     * @param {function(): number[]} deps.getPointer  returns [x, y, modifierMask]
     * @param {function(string, number, function): void} deps.send  send(action, seq, onFail)
     * @param {function(string): void} [deps.log]
     * @param {number} [deps.maxMs]  safety net: a drag never lasts longer than this
     */
    constructor({getPointer, send, log = () => {}, maxMs = 120000}) {
        this._getPointer = getPointer;
        this._send = send;
        this._log = log;
        this._maxMs = maxMs;
        this._seq = 0;
        this._active = false;
        this._opened = false;
        this._shiftPrev = false;
        this._startedAt = 0;
    }

    get active() {
        return this._active;
    }

    get seq() {
        return this._seq;
    }

    dragBegin(nowMs) {
        if (this._active)
            this.dragEnd();                      // never leave an older session hanging
        this._seq++;
        this._active = true;
        this._opened = false;
        this._startedAt = nowMs;
        const [, , mods] = this._getPointer();
        this._shiftPrev = isShift(mods);
        this._log(`drag-begin #${this._seq} (shift already down: ${this._shiftPrev})`);
        this._send('drag-begin', this._seq, () => {});
        return this._seq;
    }

    /** Called every few milliseconds while a drag is active. Returns false when polling can stop. */
    tick(nowMs) {
        if (!this._active)
            return false;
        if (nowMs - this._startedAt > this._maxMs) {
            this._log('drag lasted too long: giving up');
            this.dragEnd();
            return false;
        }
        const [, , mods] = this._getPointer();
        const shift = isShift(mods);
        if (shift && !this._shiftPrev && !this._opened) {
            const seq = this._seq;
            this._opened = true;
            this._log(`wheel-open #${seq}`);
            // if the app was not there yet, the next Shift press must try to open again
            this._send('wheel-open', seq, () => {
                if (this._seq === seq)
                    this._opened = false;
            });
        }
        this._shiftPrev = shift;
        return true;
    }

    dragEnd() {
        if (!this._active)
            return;
        this._active = false;
        this._log(`drag-end #${this._seq}`);
        this._send('drag-end', this._seq, () => {});
    }
}

/**
 * Where one of our windows goes: its centre under the pointer (the wheel is a square whose centre is the wheel's
 * centre; the options page is centred the same way), kept inside the monitor the pointer is on.
 * @param {number[]} pointer  [x, y]
 * @param {{x:number,y:number,width:number,height:number}[]} monitors
 * @param {{width:number,height:number}} frame  the window's size
 */
export function placeWindow(pointer, monitors, frame) {
    const [px, py] = pointer;
    const mon = monitors.find(m => px >= m.x && px < m.x + m.width && py >= m.y && py < m.y + m.height) ?? monitors[0];
    let x = Math.round(px - frame.width / 2);
    let y = Math.round(py - frame.height / 2);
    if (mon) {
        x = Math.max(mon.x, Math.min(x, mon.x + mon.width - frame.width));
        y = Math.max(mon.y, Math.min(y, mon.y + mon.height - frame.height));
    }
    return {x, y};
}

export const placeWheel = placeWindow;          // the name of the first version

/**
 * Is this one of Rumman's own windows that we place (the wheel or the options page) - and not some other window? The
 * title is ours alone; on top of it we want one more sign: the window class / application id, or "it belongs to the
 * Rumman process", or "Rumman just announced it".
 */
export function isRummanWindow({title, wmClass, gtkAppId, ours = false}) {
    return PLACED_TITLES.includes(title) && (wmClass === APP_ID || gtkAppId === APP_ID || ours === true);
}

export const isWheelWindow = isRummanWindow;    // the name of the first version

/** Does this window need the keyboard (Esc / Enter)? Only the options page: the wheel must not take focus mid-drag. */
export function wantsFocus(title) {
    return title === OPTIONS_TITLE;
}

/**
 * Is this GNOME session a Wayland one? (Meta.is_wayland_compositor() is gone from newer Mutter, so we go by the
 * session environment.) On X11 sessions Rumman watches drags by itself, so the helper must stay out of the way.
 * @param {function(string): (string|null)} getenv
 */
export function sessionIsWayland(getenv) {
    const type = (getenv('XDG_SESSION_TYPE') ?? '').toLowerCase();
    if (type === 'wayland')
        return true;
    if (type === 'x11')
        return false;
    return !!getenv('WAYLAND_DISPLAY');
}

/** A pointer position one pixel away that stays on screen (used to nudge the pointer). */
export function nudgeTarget(pointer, monitors) {
    const [px, py] = pointer;
    const mon = monitors.find(m => px >= m.x && px < m.x + m.width && py >= m.y && py < m.y + m.height);
    const right = mon ? mon.x + mon.width - 1 : Infinity;
    return [px + 1 <= right ? px + 1 : px - 1, py];
}
