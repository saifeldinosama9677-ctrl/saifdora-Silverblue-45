// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Saifeldin Osama

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import PangoCairo from 'gi://PangoCairo';
import Soup from 'gi://Soup?version=3.0';
import St from 'gi://St';
import Cairo from 'cairo';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const NUMERAL_ZERO_CODEPOINTS = {
    latn: 0x0030,
    arab: 0x0660,
    arabext: 0x06F0,
    deva: 0x0966,
    beng: 0x09E6,
    guru: 0x0A66,
    gujr: 0x0AE6,
    orya: 0x0B66,
    tamldec: 0x0BE6,
    telu: 0x0C66,
    knda: 0x0CE6,
    mlym: 0x0D66,
    thai: 0x0E50,
    laoo: 0x0ED0,
    tibt: 0x0F20,
    mymr: 0x1040,
    khmr: 0x17E0,
    nkoo: 0x07C0,
    olck: 0x1C50,
    fullwide: 0xFF10,
    adlm: 0x1E950,
};

const HANI_DIGITS = ['〇', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

function toAsciiDigits(input) {
    if (input === null || input === undefined) return '';
    let str = String(input);
    for (const [, zeroCp] of Object.entries(NUMERAL_ZERO_CODEPOINTS)) {
        if (zeroCp === 0x0030) continue;
        let out = '';
        for (const ch of str) {
            const cp = ch.codePointAt(0);
            if (cp >= zeroCp && cp <= zeroCp + 9) {
                out += String(cp - zeroCp);
            } else {
                out += ch;
            }
        }
        str = out;
    }
    return str;
}

function formatNumerals(input, system = 'latn') {
    const ascii = toAsciiDigits(input);
    if (!system || system === 'latn') return ascii;
    if (system === 'hanidec') {
        return ascii.replace(/[0-9]/g, d => HANI_DIGITS[Number(d)]);
    }
    const zeroCp = NUMERAL_ZERO_CODEPOINTS[system];
    if (!zeroCp) return ascii;
    return ascii.replace(/[0-9]/g, d => String.fromCodePoint(zeroCp + Number(d)));
}

const ACCENT_PALETTES = {
    blue:      { darkHex: '#8cc4ff', darkRgb: [0.55, 0.77, 1.00], lightHex: '#1a5fb4', lightRgb: [0.10, 0.37, 0.71] },
    teal:      { darkHex: '#6be6d3', darkRgb: [0.42, 0.90, 0.83], lightHex: '#0f766e', lightRgb: [0.06, 0.46, 0.43] },
    green:     { darkHex: '#7ee787', darkRgb: [0.49, 0.91, 0.53], lightHex: '#1b7f46', lightRgb: [0.11, 0.50, 0.27] },
    yellow:    { darkHex: '#f9e26b', darkRgb: [0.98, 0.89, 0.42], lightHex: '#9a6700', lightRgb: [0.60, 0.40, 0.00] },
    orange:    { darkHex: '#ffbe6f', darkRgb: [1.00, 0.75, 0.44], lightHex: '#b34700', lightRgb: [0.70, 0.28, 0.00] },
    red:       { darkHex: '#ff8b82', darkRgb: [1.00, 0.55, 0.51], lightHex: '#b91c1c', lightRgb: [0.73, 0.11, 0.11] },
    pink:      { darkHex: '#f995c7', darkRgb: [0.98, 0.58, 0.78], lightHex: '#9d174d', lightRgb: [0.62, 0.09, 0.30] },
    purple:    { darkHex: '#dfa3f8', darkRgb: [0.87, 0.64, 0.97], lightHex: '#6b21a8', lightRgb: [0.42, 0.13, 0.66] },
    slate:     { darkHex: '#b8cce0', darkRgb: [0.72, 0.80, 0.88], lightHex: '#334155', lightRgb: [0.20, 0.25, 0.33] },
    burgundy:  { darkHex: '#f28b9f', darkRgb: [0.95, 0.55, 0.62], lightHex: '#800020', lightRgb: [0.50, 0.00, 0.13] },
    turquoise: { darkHex: '#5ce1e6', darkRgb: [0.36, 0.88, 0.90], lightHex: '#006d6f', lightRgb: [0.00, 0.43, 0.44] },
    silver:    { darkHex: '#e2e8f0', darkRgb: [0.89, 0.91, 0.94], lightHex: '#475569', lightRgb: [0.28, 0.33, 0.41] },
};

function buildMetricSvg(key, strokeHex) {
    const svgs = {
        humidity: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${strokeHex}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/></svg>`,
        wind: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${strokeHex}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/></svg>`,
        feels: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${strokeHex}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/><circle cx="11.5" cy="17.5" r="1.5" fill="${strokeHex}"/></svg>`,
        uv: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${strokeHex}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32l1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32l1.41-1.41"/></svg>`
    };
    const bytes = GLib.Bytes.new(new TextEncoder().encode(svgs[key]));
    return Gio.BytesIcon.new(bytes);
}

const I18N = {
    ar: {
        humidity: 'الرطوبة', wind: 'الرياح', feels: 'الشعور', uv: 'الأشعة UV',
        warmer: 'أدفأ من الأمس بـ', cooler: 'أبرد من الأمس بـ', same: 'مثل درجة حرارة الأمس',
        clear: 'صافٍ', partlyCloudy: 'غائم جزئياً', cloudy: 'غائم', fog: 'ضباب',
        drizzle: 'رذاذ', rain: 'ممطر', snow: 'ثلوج', storm: 'عاصفة رعدية',
        kmh: 'كم/س', ms: 'م/ث', mph: 'ميل/س'
    },
    en: {
        humidity: 'Humidity', wind: 'Wind', feels: 'Feels Like', uv: 'UV Index',
        warmer: 'warmer than yesterday', cooler: 'cooler than yesterday', same: 'Same as yesterday',
        clear: 'Clear', partlyCloudy: 'Partly Cloudy', cloudy: 'Cloudy', fog: 'Foggy',
        drizzle: 'Drizzle', rain: 'Rain', snow: 'Snow', storm: 'Thunderstorm',
        kmh: 'km/h', ms: 'm/s', mph: 'mph'
    },
    es: {
        humidity: 'Humedad', wind: 'Viento', feels: 'Sensación', uv: 'Índice UV',
        warmer: 'más cálido que ayer', cooler: 'más fresco que ayer', same: 'Igual que ayer',
        clear: 'Despejado', partlyCloudy: 'Parc. nublado', cloudy: 'Nublado', fog: 'Niebla',
        drizzle: 'Llovizna', rain: 'Lluvia', snow: 'Nieve', storm: 'Tormenta',
        kmh: 'km/h', ms: 'm/s', mph: 'mph'
    },
    fr: {
        humidity: 'Humidité', wind: 'Vent', feels: 'Ressenti', uv: 'Indice UV',
        warmer: 'plus chaud qu’hier', cooler: 'plus frais qu’hier', same: 'Comme hier',
        clear: 'Dégagé', partlyCloudy: 'Part. nuageux', cloudy: 'Nuageux', fog: 'Brouillard',
        drizzle: 'Bruine', rain: 'Pluie', snow: 'Neige', storm: 'Orage',
        kmh: 'km/h', ms: 'm/s', mph: 'mph'
    },
    fa: {
        humidity: 'رطوبت', wind: 'باد', feels: 'احساس', uv: 'شاخص UV',
        warmer: 'گرم‌تر از دیروز', cooler: 'خنک‌تر از دیروز', same: 'مشابه دیروز',
        clear: 'صاف', partlyCloudy: 'نیمه‌ابری', cloudy: 'ابری', fog: 'مه‌آلود',
        drizzle: 'نم‌نم باران', rain: 'بارانی', snow: 'برفی', storm: 'طوفان',
        kmh: 'km/h', ms: 'm/s', mph: 'mph'
    },
    tr: {
        humidity: 'Nem', wind: 'Rüzgar', feels: 'Hissedilen', uv: 'UV İndeksi',
        warmer: 'dünden sıcak', cooler: 'dünden serin', same: 'Dünle aynı',
        clear: 'Açık', partlyCloudy: 'Parçalı Bulutlu', cloudy: 'Bulutlu', fog: 'Sisli',
        drizzle: 'Çisenti', rain: 'Yağmurlu', snow: 'Karlı', storm: 'Fırtına',
        kmh: 'km/s', ms: 'm/s', mph: 'mph'
    },
    ur: {
        humidity: 'نمی', wind: 'ہوا', feels: 'محسوس', uv: 'UV انڈیکس',
        warmer: 'کل سے گرم', cooler: 'کل سے ٹھنڈا', same: 'کل جیسا',
        clear: 'صاف', partlyCloudy: 'جزوی ابر آلود', cloudy: 'ابر آلود', fog: 'دھند',
        drizzle: 'بوندا باندی', rain: 'بارش', snow: 'برف باری', storm: 'طوفان',
        kmh: 'km/h', ms: 'm/s', mph: 'mph'
    },
    id: {
        humidity: 'Kelembapan', wind: 'Angin', feels: 'Terasa', uv: 'Indeks UV',
        warmer: 'lebih hangat dari kemarin', cooler: 'lebih sejuk dari kemarin', same: 'Sama seperti kemarin',
        clear: 'Cerah', partlyCloudy: 'Berawan Sebagian', cloudy: 'Berawan', fog: 'Berkabut',
        drizzle: 'Gerimis', rain: 'Hujan', snow: 'Salju', storm: 'Badai',
        kmh: 'km/j', ms: 'm/d', mph: 'mph'
    },
    sw: {
        humidity: 'Unyevu', wind: 'Upepo', feels: 'Inahisiwa', uv: 'Kiwango UV',
        warmer: 'joto kuliko jana', cooler: 'baridi kuliko jana', same: 'Sawa na jana',
        clear: 'Shwari', partlyCloudy: 'Mawingu kiasi', cloudy: 'Mawingu', fog: 'Ukungu',
        drizzle: 'Manyunyu', rain: 'Mvua', snow: 'Theluji', storm: 'Dhoruba',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: 'Wiki'
    },
    de: {
        humidity: 'Feuchte', wind: 'Wind', feels: 'Gefühlt', uv: 'UV-Index',
        warmer: 'wärmer als gestern', cooler: 'kühler als gestern', same: 'Wie gestern',
        clear: 'Klar', partlyCloudy: 'Teilw. bewölkt', cloudy: 'Bewölkt', fog: 'Nebel',
        drizzle: 'Nieselregen', rain: 'Regen', snow: 'Schnee', storm: 'Gewitter',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: 'KW'
    },
    ru: {
        humidity: 'Влажность', wind: 'Ветер', feels: 'Ощущается', uv: 'УФ-индекс',
        warmer: 'теплее чем вчера', cooler: 'холоднее чем вчера', same: 'Как вчера',
        clear: 'Ясно', partlyCloudy: 'Перем. облачность', cloudy: 'Облачно', fog: 'Туман',
        drizzle: 'Морось', rain: 'Дождь', snow: 'Снег', storm: 'Гроза',
        kmh: 'км/ч', ms: 'м/с', mph: 'миль/ч', weekPrefix: 'Неделя'
    },
    zh: {
        humidity: '湿度', wind: '风速', feels: '体感', uv: '紫外线',
        warmer: '比昨天暖和', cooler: '比昨天凉爽', same: '与昨天相同',
        clear: '晴朗', partlyCloudy: '多云', cloudy: '阴天', fog: '雾',
        drizzle: '毛毛雨', rain: '雨', snow: '雪', storm: '雷暴',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: '第', weekSuffix: '周'
    },
    ja: {
        humidity: '湿度', wind: '風速', feels: '体感', uv: 'UV指数',
        warmer: '昨日より暖かい', cooler: '昨日より涼しい', same: '昨日と同じ',
        clear: '快晴', partlyCloudy: '晴れ時々曇り', cloudy: '曇り', fog: '霧',
        drizzle: '霧雨', rain: '雨', snow: '雪', storm: '雷雨',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: '第', weekSuffix: '週'
    },
    pt: {
        humidity: 'Humidade', wind: 'Vento', feels: 'Sensação', uv: 'Índice UV',
        warmer: 'mais quente que ontem', cooler: 'mais fresco que ontem', same: 'Igual a ontem',
        clear: 'Limpo', partlyCloudy: 'Parc. nublado', cloudy: 'Nublado', fog: 'Nevoeiro',
        drizzle: 'Chuvisco', rain: 'Chuva', snow: 'Neve', storm: 'Trovoada',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: 'Semana'
    },
    it: {
        humidity: 'Umidità', wind: 'Vento', feels: 'Percepita', uv: 'Indice UV',
        warmer: 'più caldo di ieri', cooler: 'più fresco di ieri', same: 'Come ieri',
        clear: 'Sereno', partlyCloudy: 'Parz. nuvoloso', cloudy: 'Nuvoloso', fog: 'Nebbia',
        drizzle: 'Pioggerella', rain: 'Pioggia', snow: 'Neve', storm: 'Temporale',
        kmh: 'km/h', ms: 'm/s', mph: 'mph', weekPrefix: 'Settimana'
    }
};

const ARABIC_CITY_MAP = {
    'cairo': 'القاهرة',
    'giza': 'الجيزة',
    '6th of october city': 'مدينة 6 أكتوبر',
    '6 october city': 'مدينة 6 أكتوبر',
    'sheikh zayed city': 'الشيخ زايد',
    'alexandria': 'الإسكندرية',
    'mansoura': 'المنصورة',
    'tanta': 'طنطا',
    'aswan': 'أسوان',
    'luxor': 'الأقصر'
};

const MONTHS_LEVANT = ['كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران', 'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول'];
const MONTHS_MAGHREB = ['جانفي', 'فيفري', 'مارس', 'أفريل', 'ماي', 'جوان', 'جويلية', 'أوت', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];
const MONTHS_STANDARD = ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];

export default class AdwaitaLockClockExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._interfaceSettings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });

        this._cachePath = GLib.build_filenamev([GLib.get_user_cache_dir(), 'adw_lock_clock_state_v10.json']);
        this._detectSystemLocale();
        this._state = this._loadState();

        this._httpSession = new Soup.Session({ timeout: 10 });
        this._customShell = null;
        this._infoRect = null;
        this._patchedDialog = null;
        this._clockTimerId = null;
        this._weatherTimerId = null;
        this._holdTimerId = null;
        this._alignIdleId = null;
        this._signalIds = [];
        this._dialogSignals = [];

        this._tryPatchUnlockDialog();

        const settingsId = this._settings.connect('changed', (settings, key) => {
            if (key.startsWith('manual-') || key === 'location-mode') {
                this._fetchGeoAndWeather();
            } else if (key === 'show-seconds') {
                this._restartClockTimer();
            }
            this._applyCustomizations();
        });
        this._signalIds.push({ obj: this._settings, id: settingsId });

        for (const sigName of ['changed::color-scheme', 'changed::gtk-theme', 'changed::accent-color']) {
            try {
                const id = this._interfaceSettings.connect(sigName, () => this._applyCustomizations());
                this._signalIds.push({ obj: this._interfaceSettings, id });
            } catch (e) {}
        }

        if (Main.sessionMode) {
            const id = Main.sessionMode.connect('updated', () => this._tryPatchUnlockDialog());
            this._signalIds.push({ obj: Main.sessionMode, id });
        }
        if (Main.screenShield) {
            const id1 = Main.screenShield.connect('active-changed', () => this._tryPatchUnlockDialog());
            const id2 = Main.screenShield.connect('locked-changed', () => this._tryPatchUnlockDialog());
            this._signalIds.push({ obj: Main.screenShield, id: id1 });
            this._signalIds.push({ obj: Main.screenShield, id: id2 });
        }

        this._restartClockTimer();

        this._fetchGeoAndWeather();
        this._weatherTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1800, () => {
            this._fetchGeoAndWeather();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        for (const sig of this._signalIds) {
            if (sig.obj && sig.id) {
                try { sig.obj.disconnect(sig.id); } catch (e) {}
            }
        }
        this._signalIds = [];

        this._disconnectDialogSignals();

        if (this._alignIdleId) {
            GLib.source_remove(this._alignIdleId);
            this._alignIdleId = null;
        }
        if (this._clockTimerId) {
            GLib.source_remove(this._clockTimerId);
            this._clockTimerId = null;
        }
        if (this._weatherTimerId) {
            GLib.source_remove(this._weatherTimerId);
            this._weatherTimerId = null;
        }
        if (this._holdTimerId) {
            GLib.source_remove(this._holdTimerId);
            this._holdTimerId = null;
        }
        if (this._httpSession) {
            this._httpSession.abort();
            this._httpSession = null;
        }
        this._restoreUnlockDialog();
        this._settings = null;
        this._interfaceSettings = null;
    }

    _disconnectDialogSignals() {
        for (const sig of this._dialogSignals) {
            if (sig.obj && sig.id) {
                try { sig.obj.disconnect(sig.id); } catch (e) {}
            }
        }
        this._dialogSignals = [];
    }

    _restartClockTimer() {
        if (this._clockTimerId) {
            GLib.source_remove(this._clockTimerId);
            this._clockTimerId = null;
        }
        const intervalSec = (this._settings && !this._settings.get_boolean('show-seconds')) ? 5 : 1;
        this._clockTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, intervalSec, () => {
            if (!this._patchedDialog || (Main.screenShield && !Main.screenShield._dialog)) {
                this._tryPatchUnlockDialog();
            } else {
                this._syncAuthPromptIsolation();
            }
            if (this._clockCanvas && this._clockCanvas.visible) {
                this._clockCanvas.queue_repaint();
            }
            const now = new Date();
            if (now.getSeconds() < intervalSec) {
                this._updateDateUI();
            }
            return GLib.SOURCE_CONTINUE;
        });
    }

    _detectSystemLocale() {
        const langs = GLib.get_language_names();
        let raw = 'en-US';
        for (const l of langs) {
            if (l && l !== 'C' && l !== 'POSIX') {
                const candidate = l.replace('_', '-').split('.')[0];
                if (candidate.includes('-')) {
                    raw = candidate;
                    break;
                }
                if (raw === 'en-US') raw = candidate;
            }
        }
        this._rawLocale = raw;
        const parts = raw.split('-');
        const base = parts[0].toLowerCase();
        this._systemCountry = (parts[1] || (base === 'ar' ? 'EG' : 'US')).toUpperCase();
        this._langCode = I18N[base] ? base : 'en';
    }

    _t(key) {
        const pack = I18N[this._langCode] || I18N.en;
        return pack[key] || I18N.en[key] || key;
    }

    _num(val) {
        const sys = this._settings ? this._settings.get_string('numeral-system') : 'latn';
        return formatNumerals(val, sys);
    }

    _isLightTheme() {
        const mode = this._settings ? this._settings.get_string('theme-mode') : 'system';
        if (mode === 'light') return true;
        if (mode === 'dark') return false;
        if (this._interfaceSettings) {
            try {
                const scheme = this._interfaceSettings.get_string('color-scheme');
                if (scheme === 'prefer-light') return true;
                if (scheme === 'prefer-dark') return false;
            } catch (e) {}
        }
        return false;
    }

    _getActiveAccent() {
        let accentName = 'blue';
        if (this._settings && this._settings.get_boolean('use-system-accent') && this._interfaceSettings) {
            try {
                accentName = this._interfaceSettings.get_string('accent-color') || 'blue';
            } catch (e) {
                accentName = 'blue';
            }
        } else if (this._settings) {
            accentName = this._settings.get_string('custom-accent') || 'blue';
        }
        const pal = ACCENT_PALETTES[accentName] || ACCENT_PALETTES.blue;
        const isLight = this._isLightTheme();
        return {
            hex: isLight ? pal.lightHex : pal.darkHex,
            rgb: isLight ? pal.lightRgb : pal.darkRgb
        };
    }

    _getEffectiveLocation() {
        if (this._settings && this._settings.get_string('location-mode') === 'manual') {
            return {
                city: this._settings.get_string('manual-city') || 'القاهرة',
                countryCode: (this._settings.get_string('manual-country') || 'EG').toUpperCase(),
                timeZone: this._settings.get_string('manual-timezone') || 'Africa/Cairo',
                lat: this._settings.get_double('manual-lat'),
                lon: this._settings.get_double('manual-lon')
            };
        }
        return {
            city: this._state.city,
            countryCode: this._state.countryCode,
            timeZone: this._state.timeZone,
            lat: this._state.lat,
            lon: this._state.lon
        };
    }

    _localizeCityName(rawCity) {
        if (!rawCity) return '';
        if (this._langCode === 'ar') {
            const key = String(rawCity).trim().toLowerCase();
            if (ARABIC_CITY_MAP[key]) return this._num(ARABIC_CITY_MAP[key]);
        }
        return this._num(rawCity);
    }

    _loadState() {
        let sysTz = 'UTC';
        try {
            sysTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
        } catch (e) {}
        const tzCity = sysTz.includes('/') ? sysTz.split('/').pop().replace(/_/g, ' ') : (this._langCode === 'ar' ? 'القاهرة' : 'London');
        const defaults = {
            city: (this._langCode === 'ar' && sysTz === 'Africa/Cairo') ? 'القاهرة' : tzCity,
            countryCode: this._systemCountry || (this._langCode === 'ar' ? 'EG' : 'US'),
            timeZone: sysTz,
            lat: 30.0444,
            lon: 31.2357,
            weather: {
                currentTemp: 18,
                yesterdayTemp: 19,
                weatherCode: 0,
                todayMax: 24,
                todayMin: 16,
                humidity: 76,
                windSpeed: 5,
                feelsLike: 19,
                uvIndex: 0,
                forecast: []
            }
        };
        try {
            const file = Gio.File.new_for_path(this._cachePath);
            if (file.query_exists(null)) {
                const [ok, bytes] = file.load_contents(null);
                if (ok) {
                    const parsed = JSON.parse(new TextDecoder().decode(bytes));
                    return Object.assign({}, defaults, parsed);
                }
            }
        } catch (e) {}
        return defaults;
    }

    _saveState() {
        try {
            const file = Gio.File.new_for_path(this._cachePath);
            const data = JSON.stringify(this._state);
            file.replace_contents(new TextEncoder().encode(data), null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        } catch (e) {}
    }

    _syncAuthPromptIsolation() {
        if (!this._patchedDialog || !this._customShell) return;
        const clockBox = this._patchedDialog._clock;
        if (!clockBox) return;

        if (clockBox._hint) {
            clockBox._hint.opacity = 0;
            clockBox._hint.height = 0;
            clockBox._hint.hide();
        }

        const promptBox = this._patchedDialog._promptBox;
        const promptActive = (promptBox && promptBox.visible && promptBox.opacity > 10) ||
                             (!clockBox.visible) ||
                             (clockBox.opacity < 200);

        if (promptActive) {
            if (this._customShell.visible) {
                this._customShell.translation_y = 0;
                this._customShell.hide();
            }
        } else {
            if (!this._customShell.visible) {
                this._customShell.show();
                this._scheduleAlign();
            }
        }
    }

    _scheduleAlign() {
        if (this._alignIdleId) return;
        this._alignIdleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._alignIdleId = null;
            this._alignToScreenTarget();
            return GLib.SOURCE_REMOVE;
        });
    }

    _alignToScreenTarget() {
        if (!this._customShell || !this._customShell.mapped || !this._customShell.visible) return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor) return;

        const anchorActor = (this._infoRect && this._infoRect.visible)
            ? this._infoRect
            : this._clockCanvas;
        if (!anchorActor || !anchorActor.visible) return;

        const [, anchorY] = anchorActor.get_transformed_position();
        const [, anchorH] = anchorActor.get_transformed_size();
        const [, shellY] = this._customShell.get_transformed_position();
        if (anchorH <= 0) return;

        const vPct = (this._settings ? this._settings.get_int('vertical-position') : 50) / 100.0;
        const anchorCenterY = anchorY + (anchorH / 2);
        const targetCenterY = monitor.height * vPct;
        let deltaY = targetCenterY - anchorCenterY;

        const projectedTopY = shellY + deltaY;
        if (projectedTopY < 42) {
            deltaY += (42 - projectedTopY);
        }

        if (Math.abs(deltaY) > 1.5) {
            this._customShell.translation_y += Math.round(deltaY);
        }
    }

    _tryPatchUnlockDialog() {
        const shield = Main.screenShield;
        if (!shield || !shield._dialog || !shield._dialog._clock) return;

        const dialog = shield._dialog;
        if (this._patchedDialog === dialog && this._customShell) {
            this._syncAuthPromptIsolation();
            this._scheduleAlign();
            return;
        }

        this._disconnectDialogSignals();
        this._patchedDialog = dialog;
        const clockBox = dialog._clock;

        if (clockBox._time) clockBox._time.hide();
        if (clockBox._date) clockBox._date.hide();
        if (clockBox._hint) {
            clockBox._hint.opacity = 0;
            clockBox._hint.height = 0;
            clockBox._hint.hide();
        }

        this._buildCustomLockUI();
        clockBox.insert_child_at_index(this._customShell, 0);

        for (const prop of ['notify::visible', 'notify::opacity']) {
            const id = clockBox.connect(prop, () => this._syncAuthPromptIsolation());
            this._dialogSignals.push({ obj: clockBox, id });
        }
        if (dialog._promptBox) {
            for (const prop of ['notify::visible', 'notify::opacity']) {
                const id = dialog._promptBox.connect(prop, () => this._syncAuthPromptIsolation());
                this._dialogSignals.push({ obj: dialog._promptBox, id });
            }
        }

        this._applyCustomizations();
    }

    _restoreUnlockDialog() {
        this._disconnectDialogSignals();
        if (this._customShell) {
            this._customShell.destroy();
            this._customShell = null;
            this._clockCanvas = null;
            this._infoRect = null;
        }
        if (this._patchedDialog && this._patchedDialog._clock) {
            const clockBox = this._patchedDialog._clock;
            if (clockBox._time) clockBox._time.show();
            if (clockBox._date) clockBox._date.show();
            if (clockBox._hint) {
                clockBox._hint.opacity = 255;
                clockBox._hint.height = -1;
                clockBox._hint.show();
            }
        }
        this._patchedDialog = null;
    }

    _forceLtrLabel(label) {
        if (!label) return;
        label.set_text_direction(Clutter.TextDirection.LTR);
        if (label.clutter_text) {
            label.clutter_text.set_text_direction(Clutter.TextDirection.LTR);
        }
    }

    _buildCustomLockUI() {
        if (this._customShell) {
            this._customShell.destroy();
        }

        this._customShell = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'adw-lock-vertical-shell',
            x_align: Clutter.ActorAlign.CENTER,
        });

        this._customShell.connect('notify::mapped', () => this._scheduleAlign());
        this._customShell.connect('notify::allocation', () => this._scheduleAlign());

        this._clockCanvas = new St.DrawingArea({
            style_class: 'adw-clock-frame',
            width: 236,
            height: 236,
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._clockCanvas.connect('repaint', area => this._drawAnalogClock(area));
        this._customShell.add_child(this._clockCanvas);

        this._infoRect = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'adw-info-rectangle',
            x_align: Clutter.ActorAlign.CENTER,
        });

        this._weatherSection = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'adw-weather-section',
            x_expand: true,
        });

        const heroRow = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-weather-hero-row',
            x_expand: true,
        });

        const startStack = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
            x_align: Clutter.ActorAlign.START,
            y_align: Clutter.ActorAlign.CENTER,
        });

        const tempIconRow = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-hero-temp-row',
            x_align: Clutter.ActorAlign.START,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._lblCurrentTemp = new St.Label({
            text: '--°',
            style_class: 'adw-current-temp',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._forceLtrLabel(this._lblCurrentTemp);

        this._iconMainWeather = new St.Icon({
            icon_name: 'weather-clear-symbolic',
            style_class: 'adw-weather-main-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        tempIconRow.add_child(this._lblCurrentTemp);
        tempIconRow.add_child(this._iconMainWeather);

        this._lblCityStatusSub = new St.Label({
            text: '...',
            style_class: 'adw-city-status-sub',
            x_align: Clutter.ActorAlign.START,
        });
        startStack.add_child(tempIconRow);
        startStack.add_child(this._lblCityStatusSub);

        const endStack = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._lblHiLo = new St.Label({
            text: '↑ --°   ↓ --°',
            style_class: 'adw-hilo-pill',
            x_align: Clutter.ActorAlign.END,
        });
        this._forceLtrLabel(this._lblHiLo);

        this._yesterdayRow = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-yesterday-row',
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._lblYesterdayText = new St.Label({
            text: '...',
            style_class: 'adw-yesterday-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._lblYesterdayVal = new St.Label({
            text: '',
            style_class: 'adw-yesterday-label',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._forceLtrLabel(this._lblYesterdayVal);

        this._yesterdayRow.add_child(this._lblYesterdayText);
        this._yesterdayRow.add_child(this._lblYesterdayVal);

        endStack.add_child(this._lblHiLo);
        endStack.add_child(this._yesterdayRow);

        heroRow.add_child(startStack);
        heroRow.add_child(endStack);
        this._weatherSection.add_child(heroRow);

        this._metricsBar = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-metrics-bar',
            x_expand: true,
        });

        this._metricWidgets = {};
        const metricKeys = ['humidity', 'wind', 'feels', 'uv'];

        for (const key of metricKeys) {
            const cluster = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                style_class: 'adw-metric-cluster',
                x_expand: true,
                x_align: Clutter.ActorAlign.CENTER,
            });
            const valRow = new St.BoxLayout({
                orientation: Clutter.Orientation.HORIZONTAL,
                style_class: 'adw-metric-val-row',
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
            });
            const mIcon = new St.Icon({
                gicon: buildMetricSvg(key, '#ffffff'),
                style_class: 'adw-metric-mini-icon',
                y_align: Clutter.ActorAlign.CENTER,
            });
            const valLbl = new St.Label({
                text: '--',
                style_class: 'adw-metric-val',
                y_align: Clutter.ActorAlign.CENTER,
            });
            this._forceLtrLabel(valLbl);
            valRow.add_child(mIcon);
            valRow.add_child(valLbl);

            let unitLbl = null;
            if (key === 'wind') {
                unitLbl = new St.Label({
                    text: this._t('kmh'),
                    style_class: 'adw-metric-unit',
                    y_align: Clutter.ActorAlign.CENTER,
                });
                valRow.add_child(unitLbl);
            }

            const titleLbl = new St.Label({
                text: this._t(key),
                style_class: 'adw-metric-lbl',
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
            });
            cluster.add_child(valRow);
            cluster.add_child(titleLbl);
            this._metricsBar.add_child(cluster);
            this._metricWidgets[key] = { cluster, mIcon, valLbl, unitLbl, titleLbl };
        }
        this._weatherSection.add_child(this._metricsBar);

        this._forecastRow = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-forecast-cards-row',
            x_expand: true,
        });

        this._forecastWidgets = [];
        for (let i = 0; i < 3; i++) {
            const pill = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                style_class: 'adw-forecast-pill',
                x_expand: true,
                x_align: Clutter.ActorAlign.FILL,
            });
            const head = new St.BoxLayout({
                orientation: Clutter.Orientation.HORIZONTAL,
                style_class: 'adw-fc-header',
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
            });
            const dayLbl = new St.Label({ text: '--', style_class: 'adw-fc-day', y_align: Clutter.ActorAlign.CENTER });
            const icon = new St.Icon({ icon_name: 'weather-clear-symbolic', style_class: 'adw-fc-icon', y_align: Clutter.ActorAlign.CENTER });
            head.add_child(dayLbl);
            head.add_child(icon);

            const tempLbl = new St.Label({
                text: '--° / --°',
                style_class: 'adw-fc-temps',
                x_align: Clutter.ActorAlign.CENTER,
            });
            this._forceLtrLabel(tempLbl);

            pill.add_child(head);
            pill.add_child(tempLbl);
            this._forecastRow.add_child(pill);
            this._forecastWidgets.push({ pill, dayLbl, icon, tempLbl });
        }
        this._weatherSection.add_child(this._forecastRow);
        this._infoRect.add_child(this._weatherSection);

        this._sectionDivider = new St.Widget({ style_class: 'adw-section-divider', x_expand: true });
        this._infoRect.add_child(this._sectionDivider);

        this._dateSection = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'adw-date-section',
            reactive: true,
            track_hover: true,
            x_expand: true,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });

        this._lblWeekday = new St.Label({
            text: '--',
            style_class: 'adw-date-weekday',
            x_align: Clutter.ActorAlign.CENTER,
        });

        this._weekdayStrip = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-weekday-strip',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._weekdayChips = [];
        for (let i = 0; i < 7; i++) {
            const chip = new St.Label({
                text: '--',
                style_class: 'adw-weekday-chip',
                y_align: Clutter.ActorAlign.CENTER,
            });
            this._weekdayStrip.add_child(chip);
            this._weekdayChips.push(chip);
        }

        this._calRow = new St.BoxLayout({
            orientation: Clutter.Orientation.HORIZONTAL,
            style_class: 'adw-date-calendars-row',
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._lblGregorian = new St.Label({ text: '--', style_class: 'adw-date-gregorian' });
        this._lblDateSep = new St.Label({ text: '|', style_class: 'adw-date-sep' });
        this._lblSecondaryDate = new St.Label({ text: '--', style_class: 'adw-date-secondary' });

        this._calRow.add_child(this._lblGregorian);
        this._calRow.add_child(this._lblDateSep);
        this._calRow.add_child(this._lblSecondaryDate);

        this._dateSection.add_child(this._lblWeekday);
        this._dateSection.add_child(this._weekdayStrip);
        this._dateSection.add_child(this._calRow);

        this._dateSection.connect('button-press-event', () => {
            this._startDateHold();
            return Clutter.EVENT_STOP;
        });
        this._dateSection.connect('button-release-event', () => {
            this._cancelDateHold();
            return Clutter.EVENT_STOP;
        });
        this._dateSection.connect('leave-event', () => {
            this._cancelDateHold();
            return Clutter.EVENT_PROPAGATE;
        });

        this._infoRect.add_child(this._dateSection);
        this._customShell.add_child(this._infoRect);
    }

    _startDateHold() {
        this._cancelDateHold();
        this._holdTimerId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
            this._holdTimerId = null;
            if (this._settings) {
                const cur = this._settings.get_boolean('show-secondary-date');
                this._settings.set_boolean('show-secondary-date', !cur);
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _cancelDateHold() {
        if (this._holdTimerId) {
            GLib.source_remove(this._holdTimerId);
            this._holdTimerId = null;
        }
    }

    _applyCustomizations() {
        if (!this._customShell || !this._settings) return;

        const scale = this._settings.get_int('ui-scale') / 100.0;
        const opacityPct = this._settings.get_int('card-opacity') / 100.0;
        const isLight = this._isLightTheme();
        const accent = this._getActiveAccent();

        const showClock = this._settings.get_boolean('show-clock');
        const showRect = this._settings.get_boolean('show-rectangle');
        this._clockCanvas.visible = showClock;

        const clockSize = Math.round(236 * scale);
        this._clockCanvas.set_size(clockSize, clockSize);

        const showWeather = this._settings.get_boolean('show-weather');
        const showDateSection = this._settings.get_boolean('show-date-section');

        this._weatherSection.visible = showWeather;
        this._dateSection.visible = showDateSection;
        this._sectionDivider.visible = showWeather && showDateSection;
        this._infoRect.visible = showRect && (showWeather || showDateSection);

        const showHum = this._settings.get_boolean('show-humidity');
        const showWind = this._settings.get_boolean('show-wind');
        const showFeels = this._settings.get_boolean('show-feels-like');
        const showUv = this._settings.get_boolean('show-uv');

        if (this._metricWidgets.humidity) this._metricWidgets.humidity.cluster.visible = showHum;
        if (this._metricWidgets.wind) this._metricWidgets.wind.cluster.visible = showWind;
        if (this._metricWidgets.feels) this._metricWidgets.feels.cluster.visible = showFeels;
        if (this._metricWidgets.uv) this._metricWidgets.uv.cluster.visible = showUv;

        this._metricsBar.visible = showHum || showWind || showFeels || showUv;
        this._forecastRow.visible = this._settings.get_boolean('show-forecast');

        const rectWidth = Math.round(456 * scale);
        const fgMain = isLight ? '#121826' : '#ffffff';
        const fgSub = isLight ? 'rgba(18, 24, 38, 0.82)' : 'rgba(255, 255, 255, 0.86)';
        const fgMuted = isLight ? 'rgba(18, 24, 38, 0.68)' : 'rgba(255, 255, 255, 0.74)';
        const hairlineColor = isLight ? 'rgba(18, 24, 38, 0.12)' : 'rgba(255, 255, 255, 0.12)';

        const rectBgAlpha = (isLight ? 0.78 : 0.12) * opacityPct;
        const rectBorderAlpha = (isLight ? 0.65 : 0.20) * opacityPct;
        const rectShadowAlpha = (isLight ? 0.15 : 0.22) * opacityPct;
        const rectRgb = isLight ? '248, 250, 252' : '255, 255, 255';

        this._infoRect.style = `width: ${rectWidth}px; background-color: rgba(${rectRgb}, ${rectBgAlpha.toFixed(3)}); border: 1px solid rgba(255, 255, 255, ${rectBorderAlpha.toFixed(3)}); box-shadow: 0 14px 36px rgba(0, 0, 0, ${rectShadowAlpha.toFixed(3)});`;

        this._lblCurrentTemp.style = `color: ${fgMain}; font-size: ${Math.round(28 * scale)}pt;`;
        this._iconMainWeather.style = `color: ${fgMain}; icon-size: ${Math.round(32 * scale)}px;`;
        this._lblCityStatusSub.style = `color: ${fgSub}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;

        const pillBg = isLight
            ? `rgba(18, 24, 38, ${(0.07 * opacityPct).toFixed(3)})`
            : `rgba(255, 255, 255, ${(0.10 * opacityPct).toFixed(3)})`;
        const pillBorder = isLight
            ? `rgba(18, 24, 38, ${(0.14 * opacityPct).toFixed(3)})`
            : `rgba(255, 255, 255, ${(0.14 * opacityPct).toFixed(3)})`;

        this._lblHiLo.style = `color: ${fgMain}; background-color: ${pillBg}; border: 1px solid ${pillBorder}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
        this._lblYesterdayText.style = `color: ${accent.hex}; font-size: ${Math.round(8.5 * scale * 10) / 10}pt;`;
        this._lblYesterdayVal.style = `color: ${accent.hex}; font-size: ${Math.round(8.5 * scale * 10) / 10}pt;`;

        this._metricsBar.style = `border-top: 1px solid ${hairlineColor}; border-bottom: 1px solid ${hairlineColor};`;

        for (const [k, mw] of Object.entries(this._metricWidgets)) {
            mw.mIcon.gicon = buildMetricSvg(k, fgMain);
            mw.valLbl.style = `color: ${fgMain}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
            if (mw.unitLbl) mw.unitLbl.style = `color: ${fgSub}; font-size: ${Math.round(8 * scale * 10) / 10}pt;`;
            mw.titleLbl.style = `color: ${fgMuted}; font-size: ${Math.round(8.5 * scale * 10) / 10}pt;`;
        }

        for (const fw of this._forecastWidgets) {
            fw.pill.style = `background-color: ${pillBg}; border: 1px solid ${pillBorder};`;
            fw.dayLbl.style = `color: ${fgMain}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
            fw.icon.style = `color: ${fgMain}; icon-size: ${Math.round(15 * scale)}px;`;
            fw.tempLbl.style = `color: ${fgSub}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
        }

        this._sectionDivider.style = `background-color: ${hairlineColor};`;
        this._lblWeekday.style = `color: ${fgMain}; font-size: ${Math.round(12.5 * scale * 10) / 10}pt;`;
        this._lblGregorian.style = `color: ${fgMain}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
        this._lblDateSep.style = `color: ${fgMuted}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;
        this._lblSecondaryDate.style = `color: ${accent.hex}; font-size: ${Math.round(9.5 * scale * 10) / 10}pt;`;

        this._updateDateUI();
        this._updateWeatherUI();
        if (this._clockCanvas && this._clockCanvas.visible) {
            this._clockCanvas.queue_repaint();
        }
        this._syncAuthPromptIsolation();
        this._scheduleAlign();
    }

    _getNowInZone() {
        const now = new Date();
        const loc = this._getEffectiveLocation();
        try {
            const parts = new Intl.DateTimeFormat('en-US', {
                timeZone: loc.timeZone || 'Africa/Cairo',
                hour12: false,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                numberingSystem: 'latn'
            }).formatToParts(now);
            const map = {};
            for (const p of parts) map[p.type] = p.value;
            let h = parseInt(toAsciiDigits(map.hour), 10);
            if (h === 24) h = 0;
            const m = parseInt(toAsciiDigits(map.minute), 10);
            const s = parseInt(toAsciiDigits(map.second), 10);
            return { h, m, s };
        } catch (e) {
            return { h: now.getHours(), m: now.getMinutes(), s: now.getSeconds() };
        }
    }

    _computeDigitLayout(cr, cx, cy, deg, orbitRadius, textStr, fontSpec, clipR) {
        const rad = (deg - 90) * (Math.PI / 180);
        const nx = cx + orbitRadius * Math.cos(rad);
        const ny = cy + orbitRadius * Math.sin(rad);

        const layout = PangoCairo.create_layout(cr);
        layout.set_text(textStr, -1);
        layout.set_font_description(Pango.FontDescription.from_string(fontSpec));

        const [tw, th] = layout.get_pixel_size();
        return {
            layout,
            nx,
            ny,
            tx: nx - tw / 2,
            ty: ny - th / 2,
            tw,
            th,
            clipR
        };
    }

    _clipOutCircle(cr, width, height, d) {
        cr.setFillRule(Cairo.FillRule.EVEN_ODD);
        cr.newPath();
        cr.rectangle(0, 0, width, height);
        cr.newSubPath();
        cr.arc(d.nx, d.ny, d.clipR, 0, 2 * Math.PI);
        cr.clip();
    }

    _drawAnalogClock(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const cx = width / 2;
        const cy = height / 2;
        const radius = Math.min(width, height) / 2 - 2;

        const scale = (this._settings ? this._settings.get_int('ui-scale') : 100) / 100.0;
        const opacityPct = (this._settings ? this._settings.get_int('card-opacity') : 100) / 100.0;
        const is24h = this._settings ? this._settings.get_boolean('clock-format-24h') : true;
        const showSeconds = this._settings ? this._settings.get_boolean('show-seconds') : true;
        const isLight = this._isLightTheme();
        const accent = this._getActiveAccent();

        const dialGrad = new Cairo.RadialGradient(cx - radius * 0.3, cy - radius * 0.3, radius * 0.1, cx, cy, radius);
        if (isLight) {
            dialGrad.addColorStopRGBA(0.0, 1.0, 1.0, 1.0, 0.84 * opacityPct);
            dialGrad.addColorStopRGBA(1.0, 0.94, 0.96, 0.98, 0.72 * opacityPct);
        } else {
            dialGrad.addColorStopRGBA(0.0, 1.0, 1.0, 1.0, 0.16 * opacityPct);
            dialGrad.addColorStopRGBA(1.0, 1.0, 1.0, 1.0, 0.10 * opacityPct);
        }

        cr.arc(cx, cy, radius, 0, 2 * Math.PI);
        cr.setSource(dialGrad);
        cr.fillPreserve();
        cr.setLineWidth(1.2);
        if (isLight) {
            cr.setSourceRGBA(1.0, 1.0, 1.0, 0.70 * opacityPct);
        } else {
            cr.setSourceRGBA(1.0, 1.0, 1.0, 0.22 * opacityPct);
        }
        cr.stroke();

        const { h, m, s } = this._getNowInZone();

        const degHour = is24h ? ((h % 24) * 15) : ((h % 12) * 30);
        const degMin = m * 6;
        const degSec = s * 6;

        const hourNum = is24h ? (h === 0 ? 24 : h) : (h % 12 || 12);
        const hourLabel = this._num(String(hourNum).padStart(2, '0'));
        const minLabel = this._num(String(m).padStart(2, '0'));
        const secLabel = this._num(String(s).padStart(2, '0'));

        const fgRgb = isLight ? [0.07, 0.09, 0.15] : [1.0, 1.0, 1.0];
        const secRgb = [accent.rgb[0], accent.rgb[1], accent.rgb[2]];

        const orbitH = showSeconds ? 0.48 : 0.50;
        const orbitM = showSeconds ? 0.67 : 0.76;
        const orbitS = 0.85;

        const dHour = this._computeDigitLayout(cr, cx, cy, degHour, radius * orbitH, hourLabel, `Cantarell ExtraBold ${Math.round(12.5 * scale * 10) / 10}`, 14.5 * scale);
        const dMin = this._computeDigitLayout(cr, cx, cy, degMin, radius * orbitM, minLabel, `Cantarell Bold ${Math.round(11.5 * scale * 10) / 10}`, 14.0 * scale);
        const dSec = showSeconds
            ? this._computeDigitLayout(cr, cx, cy, degSec, radius * orbitS, secLabel, `Cantarell Bold ${Math.round(10.5 * scale * 10) / 10}`, 13.5 * scale)
            : null;

        cr.save();
        this._clipOutCircle(cr, width, height, dHour);
        this._drawTaperedHand(cr, cx, cy, degHour, radius * (orbitH - 0.13), 6.2 * scale, 2.0 * scale, [...fgRgb, 0.98]);
        cr.restore();

        cr.save();
        this._clipOutCircle(cr, width, height, dHour);
        this._clipOutCircle(cr, width, height, dMin);
        this._drawTaperedHand(cr, cx, cy, degMin, radius * (orbitM - 0.13), 4.6 * scale, 1.6 * scale, [...fgRgb, 0.90]);
        cr.restore();

        if (showSeconds && dSec) {
            cr.save();
            this._clipOutCircle(cr, width, height, dHour);
            this._clipOutCircle(cr, width, height, dMin);
            this._clipOutCircle(cr, width, height, dSec);
            this._drawTaperedHand(cr, cx, cy, degSec, radius * (orbitS - 0.13), 2.2 * scale, 1.0 * scale, [...secRgb, 0.98]);
            cr.restore();
        }

        cr.arc(cx, cy, 5.2 * scale, 0, 2 * Math.PI);
        cr.setSourceRGBA(fgRgb[0], fgRgb[1], fgRgb[2], 0.98);
        cr.fill();
        cr.arc(cx, cy, 2.4 * scale, 0, 2 * Math.PI);
        cr.setSourceRGBA(secRgb[0], secRgb[1], secRgb[2], 1.0);
        cr.fill();

        this._renderDigitLayout(cr, dHour, [...fgRgb, 1.0], isLight);
        this._renderDigitLayout(cr, dMin, [...fgRgb, 0.95], isLight);
        if (showSeconds && dSec) {
            this._renderDigitLayout(cr, dSec, [...secRgb, 1.0], isLight);
        }

        cr.$dispose();
    }

    _drawTaperedHand(cr, cx, cy, deg, tipLen, baseWidth, tipWidth, rgba) {
        const rad = (deg - 90) * (Math.PI / 180);
        cr.save();
        cr.translate(cx, cy);
        cr.rotate(rad);

        cr.save();
        cr.translate(0, 1.5);
        this._traceTaperedPath(cr, tipLen, baseWidth, tipWidth);
        cr.setSourceRGBA(0, 0, 0, 0.20);
        cr.fill();
        cr.restore();

        this._traceTaperedPath(cr, tipLen, baseWidth, tipWidth);
        cr.setSourceRGBA(rgba[0], rgba[1], rgba[2], rgba[3]);
        cr.fill();

        cr.restore();
    }

    _traceTaperedPath(cr, tipLen, baseWidth, tipWidth) {
        const bw = baseWidth / 2;
        const tw = tipWidth / 2;
        cr.newPath();
        cr.moveTo(0, -bw);
        cr.lineTo(tipLen, -tw);
        cr.arc(tipLen, 0, tw, -Math.PI / 2, Math.PI / 2);
        cr.lineTo(0, bw);
        cr.arc(0, 0, bw, Math.PI / 2, -Math.PI / 2);
        cr.closePath();
    }

    _renderDigitLayout(cr, d, rgba, isLight) {
        cr.save();
        cr.moveTo(d.tx, d.ty + 1.2);
        if (isLight) {
            cr.setSourceRGBA(1.0, 1.0, 1.0, 0.55);
        } else {
            cr.setSourceRGBA(0.0, 0.0, 0.0, 0.35);
        }
        PangoCairo.show_layout(cr, d.layout);

        cr.moveTo(d.tx, d.ty);
        cr.setSourceRGBA(rgba[0], rgba[1], rgba[2], rgba[3]);
        PangoCairo.show_layout(cr, d.layout);
        cr.restore();
    }

    _getSymbolicWeatherIcon(code) {
        const c = Number(code) || 0;
        if (c === 0 || c === 1) return 'weather-clear-symbolic';
        if (c === 2) return 'weather-few-clouds-symbolic';
        if (c === 3) return 'weather-overcast-symbolic';
        if (c === 45 || c === 48) return 'weather-fog-symbolic';
        if (c >= 51 && c <= 57) return 'weather-showers-scattered-symbolic';
        if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return 'weather-showers-symbolic';
        if ((c >= 71 && c <= 77) || c === 85 || c === 86) return 'weather-snow-symbolic';
        return 'weather-storm-symbolic';
    }

    _getConditionText(code) {
        const c = Number(code) || 0;
        if (c === 0 || c === 1) return this._t('clear');
        if (c === 2) return this._t('partlyCloudy');
        if (c === 3) return this._t('cloudy');
        if (c === 45 || c === 48) return this._t('fog');
        if (c >= 51 && c <= 57) return this._t('drizzle');
        if ((c >= 61 && c <= 67) || (c >= 80 && c <= 82)) return this._t('rain');
        if ((c >= 71 && c <= 77) || c === 85 || c === 86) return this._t('snow');
        return this._t('storm');
    }

    _useFahrenheit() {
        const mode = this._settings ? this._settings.get_string('temp-unit') : 'auto';
        if (mode === 'fahrenheit') return true;
        if (mode === 'celsius') return false;
        const cc = (this._getEffectiveLocation().countryCode || this._systemCountry || 'EG').toUpperCase();
        return ['US', 'BS', 'KY', 'LR', 'PW', 'FM', 'MH'].includes(cc);
    }

    _resolveWindUnit() {
        const mode = this._settings ? this._settings.get_string('wind-unit') : 'auto';
        if (mode === 'kmh' || mode === 'ms' || mode === 'mph') return mode;
        const cc = (this._getEffectiveLocation().countryCode || this._systemCountry || 'EG').toUpperCase();
        if (['US', 'GB', 'LR', 'MM'].includes(cc)) return 'mph';
        return 'kmh';
    }

    _convertTemp(celsiusVal) {
        const c = Number(celsiusVal) || 0;
        if (this._useFahrenheit()) return Math.round((c * 9) / 5 + 32);
        return Math.round(c);
    }

    _convertTempDelta(celsiusDelta) {
        const d = Number(celsiusDelta) || 0;
        if (this._useFahrenheit()) return Math.round((d * 9) / 5);
        return Math.round(d);
    }

    _convertWind(kmhVal) {
        const k = Number(kmhVal) || 0;
        const unit = this._resolveWindUnit();
        if (unit === 'ms') return { val: Math.round(k / 3.6), unitStr: this._t('ms') };
        if (unit === 'mph') return { val: Math.round(k * 0.621371), unitStr: this._t('mph') };
        return { val: Math.round(k), unitStr: this._t('kmh') };
    }

    _updateWeatherUI() {
        if (!this._lblCurrentTemp) return;
        const w = this._state.weather;
        const loc = this._getEffectiveLocation();

        this._lblCurrentTemp.text = `\u200E${this._num(this._convertTemp(w.currentTemp))}°\u200E`;
        this._forceLtrLabel(this._lblCurrentTemp);
        this._iconMainWeather.icon_name = this._getSymbolicWeatherIcon(w.weatherCode);

        const cityDisplay = this._localizeCityName(loc.city);
        const condDisplay = this._getConditionText(w.weatherCode);
        this._lblCityStatusSub.text = `${cityDisplay}  ·  ${condDisplay}`;

        this._lblHiLo.text = `\u200E↑ ${this._num(this._convertTemp(w.todayMax))}°   ↓ ${this._num(this._convertTemp(w.todayMin))}°\u200E`;
        this._forceLtrLabel(this._lblHiLo);

        const showYesterday = this._settings ? this._settings.get_boolean('show-yesterday') : true;
        this._yesterdayRow.visible = showYesterday;

        const diff = this._convertTempDelta(w.currentTemp - w.yesterdayTemp);
        const absVal = this._num(Math.abs(diff));
        if (diff === 0) {
            this._lblYesterdayText.text = this._t('same');
            this._lblYesterdayVal.hide();
        } else if (diff > 0) {
            this._lblYesterdayText.text = this._t('warmer');
            this._lblYesterdayVal.text = this._langCode === 'ar' ? `\u200E${absVal}°\u200E` : `\u200E+${absVal}°\u200E`;
            this._forceLtrLabel(this._lblYesterdayVal);
            this._lblYesterdayVal.show();
        } else {
            this._lblYesterdayText.text = this._t('cooler');
            this._lblYesterdayVal.text = this._langCode === 'ar' ? `\u200E${absVal}°\u200E` : `\u200E-${absVal}°\u200E`;
            this._forceLtrLabel(this._lblYesterdayVal);
            this._lblYesterdayVal.show();
        }

        this._metricWidgets.humidity.valLbl.text = `\u200E${this._num(Math.round(w.humidity))}%\u200E`;
        this._forceLtrLabel(this._metricWidgets.humidity.valLbl);

        const windObj = this._convertWind(w.windSpeed);
        this._metricWidgets.wind.valLbl.text = `\u200E${this._num(windObj.val)}\u200E`;
        this._forceLtrLabel(this._metricWidgets.wind.valLbl);
        if (this._metricWidgets.wind.unitLbl) {
            this._metricWidgets.wind.unitLbl.text = windObj.unitStr;
        }

        this._metricWidgets.feels.valLbl.text = `\u200E${this._num(this._convertTemp(w.feelsLike))}°\u200E`;
        this._forceLtrLabel(this._metricWidgets.feels.valLbl);

        this._metricWidgets.uv.valLbl.text = `\u200E${this._num(Number(w.uvIndex).toFixed(0))}\u200E`;
        this._forceLtrLabel(this._metricWidgets.uv.valLbl);

        for (let i = 0; i < 3; i++) {
            const fc = w.forecast && w.forecast[i];
            const fw = this._forecastWidgets[i];
            if (!fw) continue;
            if (!fc) {
                const fallbackDate = new Date(Date.now() + (i + 1) * 86400000);
                fw.dayLbl.text = this._num(this._formatWeekday(fallbackDate, 'long'));
                continue;
            }
            const dObj = new Date(fc.dateStr + 'T12:00:00');
            fw.dayLbl.text = this._num(this._formatWeekday(dObj, 'long'));
            fw.icon.icon_name = this._getSymbolicWeatherIcon(fc.code);
            fw.tempLbl.text = `\u200E${this._num(this._convertTemp(fc.max))}° / ${this._num(this._convertTemp(fc.min))}°\u200E`;
            this._forceLtrLabel(fw.tempLbl);
        }
    }

    _formatWeekday(dateObj, lengthStyle = 'long') {
        const loc = this._getEffectiveLocation();
        try {
            return new Intl.DateTimeFormat(this._rawLocale, {
                timeZone: loc.timeZone || 'Africa/Cairo',
                weekday: lengthStyle,
                numberingSystem: 'latn'
            }).format(dateObj);
        } catch (e) {
            return new Intl.DateTimeFormat('en-US', { weekday: lengthStyle }).format(dateObj);
        }
    }

    _getFirstDayOfWeek(cc) {
        const pref = this._settings ? this._settings.get_string('week-start') : 'auto';
        if (pref === 'sat') return 6;
        if (pref === 'sun') return 0;
        if (pref === 'mon') return 1;
        const code = (cc || this._systemCountry || 'US').toUpperCase();
        const satCountries = ['EG', 'DZ', 'BH', 'DJ', 'IQ', 'JO', 'KW', 'LY', 'OM', 'QA', 'SD', 'SY', 'AE', 'YE', 'AF', 'IR', 'PS'];
        const sunCountries = ['US', 'CA', 'BR', 'MX', 'AR', 'CO', 'CL', 'PE', 'JP', 'KR', 'TW', 'HK', 'PH', 'TH', 'SG', 'IN', 'IL', 'ZA', 'SA', 'AU', 'NZ'];
        if (satCountries.includes(code)) return 6;
        if (sunCountries.includes(code)) return 0;
        return 1;
    }

    _getIsoWeekNumber(dateObj) {
        const d = new Date(Date.UTC(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate()));
        const dayNum = d.getUTCDay() || 7;
        d.setUTCDate(d.getUTCDate() + 4 - dayNum);
        const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
        return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
    }

    _resolveSecondaryCalendar(cc) {
        const pref = this._settings ? this._settings.get_string('secondary-calendar-type') : 'auto';
        if (pref && pref !== 'auto') {
            if (pref === 'isoweek') return { type: 'isoweek', cal: 'iso8601' };
            return { type: 'intl', cal: pref };
        }
        const code = (cc || this._systemCountry || 'EG').toUpperCase();
        if (code === 'IR' || code === 'AF') return { type: 'intl', cal: 'persian' };
        if (code === 'ET' || code === 'ER') return { type: 'intl', cal: 'ethiopic' };
        if (code === 'TH' || code === 'KH' || code === 'LA') return { type: 'intl', cal: 'buddhist' };
        if (code === 'IN') return { type: 'intl', cal: 'indian' };
        if (code === 'CN' || code === 'TW' || code === 'HK' || code === 'SG') return { type: 'intl', cal: 'chinese' };
        if (code === 'JP') return { type: 'intl', cal: 'japanese' };
        const islamicCountries = [
            'EG', 'SA', 'AE', 'KW', 'QA', 'BH', 'OM', 'YE', 'IQ', 'SY', 'JO', 'LB', 'PS',
            'SD', 'LY', 'TN', 'DZ', 'MA', 'MR', 'SO', 'DJ', 'KM', 'TR', 'PK', 'BD', 'ID',
            'MY', 'BN', 'MV', 'SN', 'ML', 'NE', 'NG', 'TD', 'GM', 'GN', 'SL', 'AZ', 'UZ', 'KZ', 'TJ', 'TM'
        ];
        if (islamicCountries.includes(code) || this._langCode === 'ar' || this._langCode === 'fa' || this._langCode === 'ur') {
            return { type: 'intl', cal: 'islamic-umalqura' };
        }
        return { type: 'isoweek', cal: 'iso8601' };
    }

    _updateDateUI() {
        if (!this._lblWeekday) return;
        const now = new Date();
        const loc = this._getEffectiveLocation();
        const cc = (loc.countryCode || 'EG').toUpperCase();

        const showWeekday = this._settings ? this._settings.get_boolean('show-weekday') : true;
        const weekdayStyle = this._settings ? this._settings.get_string('weekday-style') : 'single';
        const scale = (this._settings ? this._settings.get_int('ui-scale') : 100) / 100.0;
        const isLight = this._isLightTheme();
        const accent = this._getActiveAccent();

        if (!showWeekday) {
            this._lblWeekday.hide();
            this._weekdayStrip.hide();
        } else if (weekdayStyle === 'strip') {
            this._lblWeekday.hide();
            this._weekdayStrip.show();

            const todayDow = now.getDay();
            const startDow = this._getFirstDayOfWeek(cc);
            const nameFmt = this._langCode === 'ar' ? 'long' : 'short';
            const fgMain = isLight ? '#121826' : '#ffffff';
            const fgDim = isLight ? 'rgba(18, 24, 38, 0.56)' : 'rgba(255, 255, 255, 0.58)';

            for (let i = 0; i < 7; i++) {
                const targetDow = (startDow + i) % 7;
                const diffDays = targetDow - todayDow;
                const refDate = new Date(now.getTime() + diffDays * 86400000);
                const chip = this._weekdayChips[i];
                chip.text = this._num(this._formatWeekday(refDate, nameFmt));

                if (targetDow === todayDow) {
                    const bgRgba = `rgba(${Math.round(accent.rgb[0] * 255)}, ${Math.round(accent.rgb[1] * 255)}, ${Math.round(accent.rgb[2] * 255)}, ${isLight ? '0.18' : '0.26'})`;
                    chip.style = `color: ${fgMain}; background-color: ${bgRgba}; border: 1.5px solid ${accent.hex}; border-radius: 99px; padding: 3px 8px; font-weight: 800; font-size: ${Math.round(8.2 * scale * 10) / 10}pt;`;
                } else {
                    chip.style = `color: ${fgDim}; background-color: transparent; border: 1.5px solid transparent; padding: 3px 6px; font-weight: 600; font-size: ${Math.round(8.0 * scale * 10) / 10}pt;`;
                }
            }
        } else {
            this._weekdayStrip.hide();
            this._lblWeekday.show();
            this._lblWeekday.text = this._num(this._formatWeekday(now, 'long'));
        }

        let gregText = '';
        if (this._langCode === 'ar') {
            let monthList = MONTHS_STANDARD;
            if (['SY', 'LB', 'JO', 'PS', 'IQ'].includes(cc)) monthList = MONTHS_LEVANT;
            else if (['TN', 'DZ'].includes(cc)) monthList = MONTHS_MAGHREB;
            const d = this._num(now.getDate());
            const m = monthList[now.getMonth()];
            const y = this._num(now.getFullYear());
            gregText = `${d} ${m} ${y}`;
        } else {
            try {
                const rawGreg = new Intl.DateTimeFormat(this._rawLocale, {
                    timeZone: loc.timeZone || 'Africa/Cairo',
                    day: 'numeric',
                    month: 'long',
                    year: 'numeric',
                    numberingSystem: 'latn'
                }).format(now);
                gregText = this._num(rawGreg);
            } catch (e) {
                gregText = this._num(now.toDateString());
            }
        }
        this._lblGregorian.text = gregText;

        const spec = this._resolveSecondaryCalendar(cc);
        let secText = '';
        if (spec.type === 'isoweek') {
            const wk = this._num(this._getIsoWeekNumber(now));
            const pack = I18N[this._langCode] || {};
            if (pack.weekSuffix) {
                secText = `${pack.weekPrefix || ''}${wk}${pack.weekSuffix}`;
            } else {
                const prefix = pack.weekPrefix || (this._langCode === 'ar' ? 'الأسبوع' : 'Week');
                secText = `${prefix} ${wk}`;
            }
        } else {
            try {
                const rawSec = new Intl.DateTimeFormat(`${this._rawLocale}-u-ca-${spec.cal}-nu-latn`, {
                    timeZone: loc.timeZone || 'UTC',
                    day: 'numeric',
                    month: 'long',
                    year: 'numeric',
                    numberingSystem: 'latn'
                }).format(now);
                secText = this._num(rawSec);
            } catch (e) {
                try {
                    const fallbackLoc = spec.cal.startsWith('islamic') ? 'ar-SA' : 'en-US';
                    const rawSec2 = new Intl.DateTimeFormat(`${fallbackLoc}-u-ca-${spec.cal}-nu-latn`, {
                        day: 'numeric',
                        month: 'long',
                        year: 'numeric'
                    }).format(now);
                    secText = this._num(rawSec2);
                } catch (e2) {}
            }
        }
        this._lblSecondaryDate.text = secText;

        const showGreg = this._settings ? this._settings.get_boolean('show-gregorian') : true;
        const showSec = this._settings ? this._settings.get_boolean('show-secondary-date') : true;

        this._lblGregorian.visible = showGreg;
        this._lblSecondaryDate.visible = showSec;
        this._lblDateSep.visible = showGreg && showSec;
        this._calRow.visible = showGreg || showSec;
    }

    _httpGetJson(url) {
        return new Promise(resolve => {
            if (!this._httpSession) {
                resolve(null);
                return;
            }
            const msg = Soup.Message.new('GET', url);
            this._httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (session, res) => {
                try {
                    const bytes = session.send_and_read_finish(res);
                    if (msg.get_status() !== Soup.Status.OK) {
                        resolve(null);
                        return;
                    }
                    const str = new TextDecoder('utf-8').decode(bytes.get_data());
                    resolve(JSON.parse(str));
                } catch (e) {
                    resolve(null);
                }
            });
        });
    }

    async _fetchGeoAndWeather() {
        const isManual = this._settings && this._settings.get_string('location-mode') === 'manual';

        if (!isManual) {
            const geo = await this._httpGetJson('https://ipwho.is/');
            if (geo && geo.success !== false && geo.latitude && geo.longitude) {
                this._state.lat = Number(geo.latitude);
                this._state.lon = Number(geo.longitude);
                if (geo.city) this._state.city = geo.city;
                if (geo.country_code) this._state.countryCode = String(geo.country_code).toUpperCase();
                if (geo.timezone && geo.timezone.id) this._state.timeZone = geo.timezone.id;
            }

            const revUrl = `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${this._state.lat}&longitude=${this._state.lon}&localityLanguage=${this._langCode}`;
            const revGeo = await this._httpGetJson(revUrl);
            if (revGeo && (revGeo.city || revGeo.locality)) {
                this._state.city = revGeo.city || revGeo.locality;
            }
        }

        const loc = this._getEffectiveLocation();
        const url = `https://api.open-meteo.com/v1/forecast?latitude=${loc.lat}&longitude=${loc.lon}` +
            `&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,uv_index` +
            `&hourly=temperature_2m` +
            `&daily=weather_code,temperature_2m_max,temperature_2m_min,uv_index_max` +
            `&past_days=1&forecast_days=4&timezone=auto`;

        const wJson = await this._httpGetJson(url);
        if (wJson && wJson.current && wJson.daily) {
            const nowHour = this._getNowInZone().h;
            let yesterdayTemp = wJson.current.temperature_2m;
            if (wJson.hourly && Array.isArray(wJson.hourly.temperature_2m) && wJson.hourly.temperature_2m.length > nowHour) {
                yesterdayTemp = wJson.hourly.temperature_2m[nowHour];
            }
            const todayIdx = wJson.daily.time.length > 1 ? 1 : 0;
            const forecast = [];
            for (let offset = 1; offset <= 3; offset++) {
                const idx = todayIdx + offset;
                if (wJson.daily.time[idx]) {
                    forecast.push({
                        dateStr: wJson.daily.time[idx],
                        code: wJson.daily.weather_code[idx],
                        max: wJson.daily.temperature_2m_max[idx],
                        min: wJson.daily.temperature_2m_min[idx]
                    });
                }
            }
            this._state.weather = {
                currentTemp: wJson.current.temperature_2m,
                yesterdayTemp: yesterdayTemp,
                weatherCode: wJson.current.weather_code,
                todayMax: wJson.daily.temperature_2m_max[todayIdx],
                todayMin: wJson.daily.temperature_2m_min[todayIdx],
                humidity: wJson.current.relative_humidity_2m,
                windSpeed: wJson.current.wind_speed_10m,
                feelsLike: wJson.current.apparent_temperature,
                uvIndex: wJson.current.uv_index ?? (wJson.daily.uv_index_max ? wJson.daily.uv_index_max[todayIdx] : 0),
                forecast: forecast
            };
            this._saveState();
            this._applyCustomizations();
        }
    }
}
