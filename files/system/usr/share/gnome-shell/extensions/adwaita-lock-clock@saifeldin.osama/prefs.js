// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Saifeldin Osama

import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gdk from 'gi://Gdk';
import Gtk from 'gi://Gtk';
import Pango from 'gi://Pango';
import PangoCairo from 'gi://PangoCairo';
import Cairo from 'cairo';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const NUMERAL_ZERO_CODEPOINTS = {
    latn: 0x0030, arab: 0x0660, arabext: 0x06F0, deva: 0x0966, beng: 0x09E6,
    guru: 0x0A66, gujr: 0x0AE6, orya: 0x0B66, tamldec: 0x0BE6, telu: 0x0C66,
    knda: 0x0CE6, mlym: 0x0D66, thai: 0x0E50, laoo: 0x0ED0, tibt: 0x0F20,
    mymr: 0x1040, khmr: 0x17E0, nkoo: 0x07C0, olck: 0x1C50, fullwide: 0xFF10,
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
            if (cp >= zeroCp && cp <= zeroCp + 9) out += String(cp - zeroCp);
            else out += ch;
        }
        str = out;
    }
    return str;
}

function formatNumerals(input, system = 'latn') {
    const ascii = toAsciiDigits(input);
    if (!system || system === 'latn') return ascii;
    if (system === 'hanidec') return ascii.replace(/[0-9]/g, d => HANI_DIGITS[Number(d)]);
    const zeroCp = NUMERAL_ZERO_CODEPOINTS[system];
    if (!zeroCp) return ascii;
    return ascii.replace(/[0-9]/g, d => String.fromCodePoint(zeroCp + Number(d)));
}

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

const NUMERAL_OPTIONS = [
    { id: 'latn',     shortAr: 'إنجليزية (0-9)',   shortEn: 'Western (0-9)',   ar: 'الأرقام الإنجليزية / الغربية (0123456789)',       en: 'Western Arabic / English (0123456789)' },
    { id: 'arab',     shortAr: 'عربية (٠-٩)',      shortEn: 'Arabic (٠-٩)',    ar: 'الأرقام العربية المشرقية (٠١٢٣٤٥٦٧٨٩)',           en: 'Eastern Arabic-Indic (٠١٢٣٤٥٦٧٨٩)' },
    { id: 'arabext',  shortAr: 'فارسية (۰-۹)',     shortEn: 'Persian (۰-۹)',   ar: 'الأرقام الفارسية / الأوردية (۰۱۲۳۴۵۶۷۸۹)',        en: 'Persian / Urdu Extended (۰۱۲۳۴۵۶۷۸۹)' },
    { id: 'deva',     shortAr: 'هندية (०-९)',      shortEn: 'Hindi (०-९)',     ar: 'الأرقام الديفاناغارية / الهندية (०१२३४५६७८९)',     en: 'Devanagari / Hindi (०१२३४५६७८९)' },
    { id: 'beng',     shortAr: 'بنغالية (০-৯)',    shortEn: 'Bengali (০-৯)',   ar: 'الأرقام البنغالية (০১২৩৪৫৬৭৮৯)',                 en: 'Bengali (০১২৩৪৫৬৭৮৯)' },
    { id: 'guru',     shortAr: 'بنجابية (੦-੯)',    shortEn: 'Punjabi (੦-੯)',   ar: 'الأرقام الغورموخية / البنجابية (੦੧੨੩੪੫੬੭੮੯)',     en: 'Gurmukhi / Punjabi (੦੧੨੩੪੫੬੭੮੯)' },
    { id: 'gujr',     shortAr: 'غوجاراتية (૦-૯)',  shortEn: 'Gujarati (૦-૯)',  ar: 'الأرقام الغوجاراتية (૦૧૨૩૪૫૬૭૮૯)',               en: 'Gujarati (૦૧૨૩૪૫૬૭૮૯)' },
    { id: 'orya',     shortAr: 'أوديا (୦-୯)',      shortEn: 'Odia (୦-୯)',      ar: 'أرقام الأوديا (୦୧୨୩୪୫୬୭୮୯)',                     en: 'Odia (୦୧୨୩୪୫୬୭୮୯)' },
    { id: 'tamldec',  shortAr: 'تاميلية (௦-௯)',    shortEn: 'Tamil (௦-௯)',     ar: 'الأرقام التاميلية (௦௧௨௩௪௫௬௭௮௯)',                 en: 'Tamil (௦௧௨௩௪௫௬௭௮௯)' },
    { id: 'telu',     shortAr: 'تيلوغو (౦-౯)',     shortEn: 'Telugu (౦-౯)',    ar: 'أرقام التيلوغو (౦౧౨౩౪౫౬౭౮౯)',                    en: 'Telugu (౦౧౨౩౪౫౬౭౮౯)' },
    { id: 'knda',     shortAr: 'كانادا (೦-೯)',     shortEn: 'Kannada (೦-೯)',   ar: 'أرقام الكانادا (೦೧೨೩೪೫೬೭೮೯)',                    en: 'Kannada (೦೧೨೩೪೫೬೭೮೯)' },
    { id: 'mlym',     shortAr: 'مالايالامية',      shortEn: 'Malayalam',       ar: 'أرقام المالايالامية (൦൧൨൩൪൫൬൭൮൯)',               en: 'Malayalam (൦൧൨൩൪൫൬൭൮൯)' },
    { id: 'thai',     shortAr: 'تايلاندية (๐-๙)',  shortEn: 'Thai (๐-๙)',      ar: 'الأرقام التايلاندية (๐๑๒๓๔๕๖๗๘๙)',                en: 'Thai (๐๑๒๓๔๕๖๗๘๙)' },
    { id: 'laoo',     shortAr: 'لاوية (໐-໙)',      shortEn: 'Lao (໐-໙)',       ar: 'الأرقام اللاوية (໐໑໒໓໔໕໖໗໘໙)',                   en: 'Lao (໐໑໒໓໔໕໖໗໘໙)' },
    { id: 'tibt',     shortAr: 'تبتية (༠-༩)',      shortEn: 'Tibetan (༠-༩)',   ar: 'الأرقام التبتية (༠༡༢༣༤༥༦༧༨༩)',                    en: 'Tibetan (༠༡༢༣༤༥༦༧༨༩)' },
    { id: 'mymr',     shortAr: 'بورمية (၀-၉)',     shortEn: 'Burmese (၀-၉)',   ar: 'أرقام ميانمار / البورمية (၀၁၂၃၄၅၆၇၈၉)',          en: 'Myanmar / Burmese (၀၁၂၃၄၅၆၇၈၉)' },
    { id: 'khmr',     shortAr: 'خميرية (០-៩)',     shortEn: 'Khmer (០-៩)',     ar: 'الأرقام الخميرية / الكمبودية (០១២៣៤៥៦៧៨៩)',       en: 'Khmer / Cambodian (០១២៣៤៥៦៧៨៩)' },
    { id: 'hanidec',  shortAr: 'صينية (〇-九)',    shortEn: 'Han (〇-九)',     ar: 'الأرقام الصينية / اليابانية (〇一二三四五六七八九)', en: 'Chinese / Japanese Han (〇一二三四五六七八九)' },
    { id: 'fullwide', shortAr: 'عريضة (０-９)',    shortEn: 'Fullwidth',       ar: 'أرقام شرق آسيا العريضة (０１２３４５６７８９)',     en: 'Fullwidth East Asian (０１２３４５６７８９)' },
    { id: 'nkoo',     shortAr: 'نكو (߀-߉)',        shortEn: 'N’Ko (߀-߉)',      ar: 'أرقام نكو الأفريقية (߀߁߂߃߄߅߆߇߈߉)',               en: 'West African N’Ko (߀߁߂߃߄߅߆߇߈߉)' },
    { id: 'adlm',     shortAr: 'أدلم (𞥐-𞥙)',       shortEn: 'Adlam (𞥐-𞥙)',     ar: 'أرقام أدلم الفولانية الأفريقية (𞥐𞥑𞥒𞥓𞥔𞥕𞥖𞥗𞥘𞥙)',     en: 'West African Adlam (𞥐𞥑𞥒𞥓𞥔𞥕𞥖𞥗𞥘𞥙)' },
    { id: 'olck',     shortAr: 'أول تشيكي',        shortEn: 'Ol Chiki',        ar: 'أرقام أول تشيكي السانتالية (᱐᱑᱒᱓᱔᱕᱖᱗᱘᱙)',         en: 'Ol Chiki / Santali (᱐᱑᱒᱓᱔᱕᱖᱗᱘᱙)' },
];

const COUNTRIES_DB = [
    {
        code: 'EG', shortAr: 'مصر (EG)', shortEn: 'Egypt (EG)', ar: 'مصر (Egypt)', en: 'Egypt (EG)', tz: 'Africa/Cairo',
        cities: [
            { ar: 'القاهرة', en: 'Cairo', lat: 30.0444, lon: 31.2357 },
            { ar: 'مدينة 6 أكتوبر', en: '6th of October City', lat: 29.9668, lon: 30.9270 },
            { ar: 'الجيزة', en: 'Giza', lat: 30.0131, lon: 31.2089 },
            { ar: 'الشيخ زايد', en: 'Sheikh Zayed City', lat: 30.0444, lon: 30.9766 },
            { ar: 'الإسكندرية', en: 'Alexandria', lat: 31.2001, lon: 29.9187 },
            { ar: 'المنصورة', en: 'Mansoura', lat: 31.0409, lon: 31.3785 },
            { ar: 'طنطا', en: 'Tanta', lat: 30.7865, lon: 31.0004 },
            { ar: 'بورسعيد', en: 'Port Said', lat: 31.2653, lon: 32.3019 },
            { ar: 'السويس', en: 'Suez', lat: 29.9668, lon: 32.5498 },
            { ar: 'الإسماعيلية', en: 'Ismailia', lat: 30.5965, lon: 32.2715 },
            { ar: 'أسيوط', en: 'Asyut', lat: 27.1783, lon: 31.1859 },
            { ar: 'الأقصر', en: 'Luxor', lat: 25.6872, lon: 32.6396 },
            { ar: 'أسوان', en: 'Aswan', lat: 24.0889, lon: 32.8998 },
            { ar: 'الغردقة', en: 'Hurghada', lat: 27.2579, lon: 33.8116 },
            { ar: 'شرم الشيخ', en: 'Sharm El Sheikh', lat: 27.9158, lon: 34.3300 },
            { ar: 'مرسى مطروح', en: 'Marsa Matruh', lat: 31.3543, lon: 27.2373 },
        ]
    },
    {
        code: 'SA', shortAr: 'السعودية (SA)', shortEn: 'Saudi Arabia (SA)', ar: 'السعودية (Saudi Arabia)', en: 'Saudi Arabia (SA)', tz: 'Asia/Riyadh',
        cities: [
            { ar: 'الرياض', en: 'Riyadh', lat: 24.7136, lon: 46.6753 },
            { ar: 'مكة المكرمة', en: 'Makkah', lat: 21.3891, lon: 39.8579 },
            { ar: 'المدينة المنورة', en: 'Madinah', lat: 24.5247, lon: 39.5692 },
            { ar: 'جدة', en: 'Jeddah', lat: 21.4858, lon: 39.1925 },
            { ar: 'الدمام', en: 'Dammam', lat: 26.4207, lon: 50.0888 },
            { ar: 'الخبر', en: 'Khobar', lat: 26.2172, lon: 50.1971 },
            { ar: 'أبها', en: 'Abha', lat: 18.2164, lon: 42.5053 },
            { ar: 'تبوك', en: 'Tabuk', lat: 28.3838, lon: 36.5550 },
        ]
    },
    {
        code: 'AE', shortAr: 'الإمارات (AE)', shortEn: 'UAE (AE)', ar: 'الإمارات (UAE)', en: 'United Arab Emirates (AE)', tz: 'Asia/Dubai',
        cities: [
            { ar: 'أبوظبي', en: 'Abu Dhabi', lat: 24.4539, lon: 54.3773 },
            { ar: 'دبي', en: 'Dubai', lat: 25.2048, lon: 55.2708 },
            { ar: 'الشارقة', en: 'Sharjah', lat: 25.3463, lon: 55.4209 },
            { ar: 'العين', en: 'Al Ain', lat: 24.1302, lon: 55.8023 },
        ]
    },
    {
        code: 'PS', shortAr: 'فلسطين (PS)', shortEn: 'Palestine (PS)', ar: 'فلسطين (Palestine)', en: 'Palestine (PS)', tz: 'Asia/Gaza',
        cities: [
            { ar: 'القدس', en: 'Jerusalem', lat: 31.7683, lon: 35.2137 },
            { ar: 'غزة', en: 'Gaza', lat: 31.5017, lon: 34.4668 },
            { ar: 'رام الله', en: 'Ramallah', lat: 31.9038, lon: 35.2034 },
            { ar: 'نابلس', en: 'Nablus', lat: 32.2211, lon: 35.2544 },
            { ar: 'الخليل', en: 'Hebron', lat: 31.5326, lon: 35.0998 },
        ]
    },
    {
        code: 'JO', shortAr: 'الأردن (JO)', shortEn: 'Jordan (JO)', ar: 'الأردن (Jordan)', en: 'Jordan (JO)', tz: 'Asia/Amman',
        cities: [
            { ar: 'عمّان', en: 'Amman', lat: 31.9454, lon: 35.9284 },
            { ar: 'إربد', en: 'Irbid', lat: 32.5568, lon: 35.8469 },
            { ar: 'الزرقاء', en: 'Zarqa', lat: 32.0608, lon: 36.0942 },
            { ar: 'العقبة', en: 'Aqaba', lat: 29.5321, lon: 35.0063 },
        ]
    },
    {
        code: 'SY', shortAr: 'سوريا (SY)', shortEn: 'Syria (SY)', ar: 'سوريا (Syria)', en: 'Syria (SY)', tz: 'Asia/Damascus',
        cities: [
            { ar: 'دمشق', en: 'Damascus', lat: 33.5138, lon: 36.2765 },
            { ar: 'حلب', en: 'Aleppo', lat: 36.2021, lon: 37.1343 },
            { ar: 'حمص', en: 'Homs', lat: 34.7324, lon: 36.7137 },
            { ar: 'اللاذقية', en: 'Latakia', lat: 35.5317, lon: 35.7918 },
        ]
    },
    {
        code: 'LB', shortAr: 'لبنان (LB)', shortEn: 'Lebanon (LB)', ar: 'لبنان (Lebanon)', en: 'Lebanon (LB)', tz: 'Asia/Beirut',
        cities: [
            { ar: 'بيروت', en: 'Beirut', lat: 33.8938, lon: 35.5018 },
            { ar: 'طرابلس', en: 'Tripoli', lat: 34.4367, lon: 35.8497 },
            { ar: 'صيدا', en: 'Sidon', lat: 33.5571, lon: 35.3729 },
        ]
    },
    {
        code: 'IQ', shortAr: 'العراق (IQ)', shortEn: 'Iraq (IQ)', ar: 'العراق (Iraq)', en: 'Iraq (IQ)', tz: 'Asia/Baghdad',
        cities: [
            { ar: 'بغداد', en: 'Baghdad', lat: 33.3152, lon: 44.3661 },
            { ar: 'البصرة', en: 'Basra', lat: 30.5081, lon: 47.7835 },
            { ar: 'الموصل', en: 'Mosul', lat: 36.3489, lon: 43.1577 },
            { ar: 'أربيل', en: 'Erbil', lat: 36.1901, lon: 44.0091 },
        ]
    },
    {
        code: 'KW', shortAr: 'الكويت (KW)', shortEn: 'Kuwait (KW)', ar: 'الكويت (Kuwait)', en: 'Kuwait (KW)', tz: 'Asia/Kuwait',
        cities: [
            { ar: 'مدينة الكويت', en: 'Kuwait City', lat: 29.3759, lon: 47.9774 },
            { ar: 'حولي', en: 'Hawalli', lat: 29.3328, lon: 48.0286 },
        ]
    },
    {
        code: 'QA', shortAr: 'قطر (QA)', shortEn: 'Qatar (QA)', ar: 'قطر (Qatar)', en: 'Qatar (QA)', tz: 'Asia/Qatar',
        cities: [
            { ar: 'الدوحة', en: 'Doha', lat: 25.2854, lon: 51.5310 },
            { ar: 'الريان', en: 'Al Rayyan', lat: 25.2919, lon: 51.4244 },
        ]
    },
    {
        code: 'BH', shortAr: 'البحرين (BH)', shortEn: 'Bahrain (BH)', ar: 'البحرين (Bahrain)', en: 'Bahrain (BH)', tz: 'Asia/Bahrain',
        cities: [
            { ar: 'المنامة', en: 'Manama', lat: 26.2285, lon: 50.5860 },
        ]
    },
    {
        code: 'OM', shortAr: 'عُمان (OM)', shortEn: 'Oman (OM)', ar: 'عُمان (Oman)', en: 'Oman (OM)', tz: 'Asia/Muscat',
        cities: [
            { ar: 'مسقط', en: 'Muscat', lat: 23.5880, lon: 58.3829 },
            { ar: 'صلالة', en: 'Salalah', lat: 17.0151, lon: 54.0924 },
        ]
    },
    {
        code: 'YE', shortAr: 'اليمن (YE)', shortEn: 'Yemen (YE)', ar: 'اليمن (Yemen)', en: 'Yemen (YE)', tz: 'Asia/Aden',
        cities: [
            { ar: 'صنعاء', en: 'Sanaa', lat: 15.3694, lon: 44.1910 },
            { ar: 'عدن', en: 'Aden', lat: 12.7855, lon: 45.0187 },
        ]
    },
    {
        code: 'SD', shortAr: 'السودان (SD)', shortEn: 'Sudan (SD)', ar: 'السودان (Sudan)', en: 'Sudan (SD)', tz: 'Africa/Khartoum',
        cities: [
            { ar: 'الخرطوم', en: 'Khartoum', lat: 15.5007, lon: 32.5599 },
            { ar: 'أم درمان', en: 'Omdurman', lat: 15.6445, lon: 32.4777 },
        ]
    },
    {
        code: 'LY', shortAr: 'ليبيا (LY)', shortEn: 'Libya (LY)', ar: 'ليبيا (Libya)', en: 'Libya (LY)', tz: 'Africa/Tripoli',
        cities: [
            { ar: 'طرابلس', en: 'Tripoli', lat: 32.8872, lon: 13.1913 },
            { ar: 'بنغازي', en: 'Benghazi', lat: 32.1194, lon: 20.0868 },
        ]
    },
    {
        code: 'TN', shortAr: 'تونس (TN)', shortEn: 'Tunisia (TN)', ar: 'تونس (Tunisia)', en: 'Tunisia (TN)', tz: 'Africa/Tunis',
        cities: [
            { ar: 'تونس العاصمة', en: 'Tunis', lat: 36.8065, lon: 10.1815 },
            { ar: 'صفاقس', en: 'Sfax', lat: 34.7406, lon: 10.7603 },
        ]
    },
    {
        code: 'DZ', shortAr: 'الجزائر (DZ)', shortEn: 'Algeria (DZ)', ar: 'الجزائر (Algeria)', en: 'Algeria (DZ)', tz: 'Africa/Algiers',
        cities: [
            { ar: 'الجزائر العاصمة', en: 'Algiers', lat: 36.7538, lon: 3.0588 },
            { ar: 'وهران', en: 'Oran', lat: 35.6971, lon: -0.6308 },
        ]
    },
    {
        code: 'MA', shortAr: 'المغرب (MA)', shortEn: 'Morocco (MA)', ar: 'المغرب (Morocco)', en: 'Morocco (MA)', tz: 'Africa/Casablanca',
        cities: [
            { ar: 'الرباط', en: 'Rabat', lat: 34.0209, lon: -6.8416 },
            { ar: 'الدار البيضاء', en: 'Casablanca', lat: 33.5731, lon: -7.5898 },
            { ar: 'مراكش', en: 'Marrakesh', lat: 31.6295, lon: -7.9811 },
        ]
    },
    {
        code: 'TR', shortAr: 'تركيا (TR)', shortEn: 'Turkey (TR)', ar: 'تركيا (Turkey)', en: 'Turkey (TR)', tz: 'Europe/Istanbul',
        cities: [
            { ar: 'أنقرة', en: 'Ankara', lat: 39.9334, lon: 32.8597 },
            { ar: 'إسطنبول', en: 'Istanbul', lat: 41.0082, lon: 28.9784 },
        ]
    },
    {
        code: 'IR', shortAr: 'إيران (IR)', shortEn: 'Iran (IR)', ar: 'إيران (Iran)', en: 'Iran (IR)', tz: 'Asia/Tehran',
        cities: [
            { ar: 'طهران', en: 'Tehran', lat: 35.6892, lon: 51.3890 },
        ]
    },
    {
        code: 'PK', shortAr: 'باكستان (PK)', shortEn: 'Pakistan (PK)', ar: 'باكستان (Pakistan)', en: 'Pakistan (PK)', tz: 'Asia/Karachi',
        cities: [
            { ar: 'إسلام آباد', en: 'Islamabad', lat: 33.6844, lon: 73.0479 },
            { ar: 'كراتشي', en: 'Karachi', lat: 24.8607, lon: 67.0011 },
        ]
    },
    {
        code: 'ID', shortAr: 'إندونيسيا (ID)', shortEn: 'Indonesia (ID)', ar: 'إندونيسيا (Indonesia)', en: 'Indonesia (ID)', tz: 'Asia/Jakarta',
        cities: [
            { ar: 'جاكرتا', en: 'Jakarta', lat: -6.2088, lon: 106.8456 },
        ]
    },
    {
        code: 'MY', shortAr: 'ماليزيا (MY)', shortEn: 'Malaysia (MY)', ar: 'ماليزيا (Malaysia)', en: 'Malaysia (MY)', tz: 'Asia/Kuala_Lumpur',
        cities: [
            { ar: 'كوالالمبور', en: 'Kuala Lumpur', lat: 3.1390, lon: 101.6869 },
        ]
    },
    {
        code: 'ET', shortAr: 'إثيوبيا (ET)', shortEn: 'Ethiopia (ET)', ar: 'إثيوبيا (Ethiopia)', en: 'Ethiopia (ET)', tz: 'Africa/Addis_Ababa',
        cities: [
            { ar: 'أديس أبابا', en: 'Addis Ababa', lat: 9.0320, lon: 38.7469 },
        ]
    },
    {
        code: 'NG', shortAr: 'نيجيريا (NG)', shortEn: 'Nigeria (NG)', ar: 'نيجيريا (Nigeria)', en: 'Nigeria (NG)', tz: 'Africa/Lagos',
        cities: [
            { ar: 'أبوجا', en: 'Abuja', lat: 9.0765, lon: 7.3986 },
            { ar: 'لاغوس', en: 'Lagos', lat: 6.5244, lon: 3.3792 },
        ]
    },
    {
        code: 'IN', shortAr: 'الهند (IN)', shortEn: 'India (IN)', ar: 'الهند (India)', en: 'India (IN)', tz: 'Asia/Kolkata',
        cities: [
            { ar: 'نيودلهي', en: 'New Delhi', lat: 28.6139, lon: 77.2090 },
            { ar: 'مومباي', en: 'Mumbai', lat: 19.0760, lon: 72.8777 },
        ]
    },
    {
        code: 'GB', shortAr: 'بريطانيا (GB)', shortEn: 'UK (GB)', ar: 'المملكة المتحدة (UK)', en: 'United Kingdom (GB)', tz: 'Europe/London',
        cities: [
            { ar: 'لندن', en: 'London', lat: 51.5074, lon: -0.1278 },
        ]
    },
    {
        code: 'DE', shortAr: 'ألمانيا (DE)', shortEn: 'Germany (DE)', ar: 'ألمانيا (Germany)', en: 'Germany (DE)', tz: 'Europe/Berlin',
        cities: [
            { ar: 'برلين', en: 'Berlin', lat: 52.5200, lon: 13.4050 },
        ]
    },
    {
        code: 'FR', shortAr: 'فرنسا (FR)', shortEn: 'France (FR)', ar: 'فرنسا (France)', en: 'France (FR)', tz: 'Europe/Paris',
        cities: [
            { ar: 'باريس', en: 'Paris', lat: 48.8566, lon: 2.3522 },
        ]
    },
    {
        code: 'ES', shortAr: 'إسبانيا (ES)', shortEn: 'Spain (ES)', ar: 'إسبانيا (Spain)', en: 'Spain (ES)', tz: 'Europe/Madrid',
        cities: [
            { ar: 'مدريد', en: 'Madrid', lat: 40.4168, lon: -3.7038 },
        ]
    },
    {
        code: 'US', shortAr: 'أمريكا (US)', shortEn: 'USA (US)', ar: 'الولايات المتحدة (USA)', en: 'United States (US)', tz: 'America/New_York',
        cities: [
            { ar: 'واشنطن العاصمة', en: 'Washington, D.C.', lat: 38.9072, lon: -77.0369, tz: 'America/New_York' },
            { ar: 'نيويورك', en: 'New York', lat: 40.7128, lon: -74.0060, tz: 'America/New_York' },
        ]
    },
    {
        code: 'JP', shortAr: 'اليابان (JP)', shortEn: 'Japan (JP)', ar: 'اليابان (Japan)', en: 'Japan (JP)', tz: 'Asia/Tokyo',
        cities: [
            { ar: 'طوكيو', en: 'Tokyo', lat: 35.6762, lon: 139.6503 },
        ]
    },
];

const ALL_CITIES_FLAT = [];
for (const c of COUNTRIES_DB) {
    c.cities.forEach((city, idx) => {
        ALL_CITIES_FLAT.push({
            countryCode: c.code,
            countryAr: c.ar,
            countryEn: c.en,
            ar: city.ar,
            en: city.en,
            lat: city.lat,
            lon: city.lon,
            tz: city.tz || c.tz,
            isCapital: idx === 0,
        });
    });
}

const MONTHS_LEVANT = ['كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران', 'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول'];
const MONTHS_MAGHREB = ['جانفي', 'فيفري', 'مارس', 'أفريل', 'ماي', 'جوان', 'جويلية', 'أوت', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];
const MONTHS_STANDARD = ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر'];

export default class AdwaitaLockClockPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const interfaceSettings = new Gio.Settings({ schema_id: 'org.gnome.desktop.interface' });
        const isAr = GLib.get_language_names().some(l => l.startsWith('ar'));
        const tr = (arStr, enStr) => (isAr ? arStr : enStr);

        window.set_default_size(880, 840);

        const cssProvider = new Gtk.CssProvider();
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            cssProvider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        );

        const previewInstances = [];

        const getThemeColors = () => {
            let isLight = false;
            const themeMode = settings.get_string('theme-mode');
            if (themeMode === 'light') isLight = true;
            else if (themeMode === 'system') {
                try { isLight = interfaceSettings.get_string('color-scheme') === 'prefer-light'; } catch (e) {}
            }
            let accentName = 'blue';
            if (settings.get_boolean('use-system-accent')) {
                try { accentName = interfaceSettings.get_string('accent-color') || 'blue'; } catch (e) {}
            } else {
                accentName = settings.get_string('custom-accent') || 'blue';
            }
            const pal = ACCENT_PALETTES[accentName] || ACCENT_PALETTES.blue;
            return {
                isLight,
                accentHex: isLight ? pal.lightHex : pal.darkHex,
                accentRgb: isLight ? pal.lightRgb : pal.darkRgb,
                fgHex: isLight ? '#121826' : '#ffffff',
                fgRgb: isLight ? [0.07, 0.09, 0.15] : [1.0, 1.0, 1.0],
            };
        };

        const updatePreviewCss = () => {
            const { isLight, accentHex, accentRgb, fgHex } = getThemeColors();
            const scale = (settings.get_int('ui-scale') / 100.0) * 0.72;
            const opacityPct = settings.get_int('card-opacity') / 100.0;

            const cardRgb = isLight ? '248, 250, 252' : '255, 255, 255';
            const cardAlpha = ((isLight ? 0.78 : 0.12) * opacityPct).toFixed(3);
            const borderAlpha = ((isLight ? 0.65 : 0.20) * opacityPct).toFixed(3);
            const pillRgb = isLight ? '18, 24, 38' : '255, 255, 255';
            const pillAlpha = ((isLight ? 0.07 : 0.09) * opacityPct).toFixed(3);
            const pillBorderAlpha = ((isLight ? 0.14 : 0.13) * opacityPct).toFixed(3);
            const lineAlpha = isLight ? '0.12' : '0.12';
            const subColor = isLight ? 'rgba(18, 24, 38, 0.84)' : 'rgba(255, 255, 255, 0.86)';
            const mutedColor = isLight ? 'rgba(18, 24, 38, 0.68)' : 'rgba(255, 255, 255, 0.72)';
            const chipBg = `rgba(${Math.round(accentRgb[0] * 255)}, ${Math.round(accentRgb[1] * 255)}, ${Math.round(accentRgb[2] * 255)}, ${isLight ? '0.20' : '0.28'})`;

            const css = `
                .adw-live-monitor {
                    background: linear-gradient(145deg, #0c1832 0%, #1d3c6e 52%, #102347 100%);
                    border-radius: 18px;
                    border: 1px solid rgba(255, 255, 255, 0.14);
                    padding: 14px 20px;
                }
                .adw-live-card {
                    background-color: rgba(${cardRgb}, ${cardAlpha});
                    border: 1px solid rgba(255, 255, 255, ${borderAlpha});
                    border-radius: ${Math.round(24 * scale)}px;
                    padding: 0px;
                }
                .adw-live-weather-sec {
                    padding: ${Math.round(13 * scale)}px ${Math.round(18 * scale)}px ${Math.round(11 * scale)}px ${Math.round(18 * scale)}px;
                }
                .adw-live-temp {
                    color: ${fgHex};
                    font-size: ${Math.round(25 * scale)}pt;
                    font-weight: 800;
                }
                .adw-live-icon-main {
                    color: ${fgHex};
                }
                .adw-live-city-sub {
                    color: ${subColor};
                    font-size: ${Math.round(9.2 * scale * 10) / 10}pt;
                    font-weight: 700;
                }
                .adw-live-hilo-pill {
                    color: ${fgHex};
                    background-color: rgba(${pillRgb}, ${pillAlpha});
                    border: 1px solid rgba(${pillRgb}, ${pillBorderAlpha});
                    border-radius: 99px;
                    padding: ${Math.round(2 * scale)}px ${Math.round(10 * scale)}px;
                    font-size: ${Math.round(9.0 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-yesterday {
                    color: ${accentHex};
                    font-size: ${Math.round(8.2 * scale * 10) / 10}pt;
                    font-weight: 700;
                }
                .adw-live-metrics-bar {
                    border-top: 1px solid rgba(${pillRgb}, ${lineAlpha});
                    border-bottom: 1px solid rgba(${pillRgb}, ${lineAlpha});
                    padding: ${Math.round(8 * scale)}px 2px;
                }
                .adw-live-m-val {
                    color: ${fgHex};
                    font-size: ${Math.round(9.2 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-m-unit {
                    color: ${subColor};
                    font-size: ${Math.round(7.8 * scale * 10) / 10}pt;
                    font-weight: 700;
                }
                .adw-live-m-lbl {
                    color: ${mutedColor};
                    font-size: ${Math.round(8.0 * scale * 10) / 10}pt;
                    font-weight: 600;
                }
                .adw-live-fc-pill {
                    background-color: rgba(${pillRgb}, ${pillAlpha});
                    border: 1px solid rgba(${pillRgb}, ${pillBorderAlpha});
                    border-radius: ${Math.round(14 * scale)}px;
                    padding: ${Math.round(6 * scale)}px ${Math.round(8 * scale)}px;
                }
                .adw-live-fc-day {
                    color: ${fgHex};
                    font-size: ${Math.round(9.0 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-fc-temps {
                    color: ${subColor};
                    font-size: ${Math.round(9.0 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-divider {
                    background-color: rgba(${pillRgb}, 0.16);
                    min-height: 1px;
                    margin: 0px ${Math.round(18 * scale)}px;
                }
                .adw-live-date-sec {
                    padding: ${Math.round(10 * scale)}px ${Math.round(18 * scale)}px ${Math.round(12 * scale)}px ${Math.round(18 * scale)}px;
                }
                .adw-live-weekday-single {
                    color: ${fgHex};
                    font-size: ${Math.round(11.8 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-chip-active {
                    color: ${fgHex};
                    background-color: ${chipBg};
                    border: 1.5px solid ${accentHex};
                    border-radius: 99px;
                    padding: 2px 7px;
                    font-size: ${Math.round(8.0 * scale * 10) / 10}pt;
                    font-weight: 800;
                }
                .adw-live-chip-dim {
                    color: ${mutedColor};
                    padding: 2px 5px;
                    font-size: ${Math.round(7.8 * scale * 10) / 10}pt;
                    font-weight: 600;
                }
                .adw-live-greg {
                    color: ${fgHex};
                    font-size: ${Math.round(9.2 * scale * 10) / 10}pt;
                    font-weight: 700;
                }
                .adw-live-sep {
                    color: ${mutedColor};
                    font-size: ${Math.round(9.2 * scale * 10) / 10}pt;
                }
                .adw-live-hijri {
                    color: ${accentHex};
                    font-size: ${Math.round(9.2 * scale * 10) / 10}pt;
                    font-weight: 700;
                }
            `;
            cssProvider.load_from_data(css, -1);
        };

        const drawClockOnly = (area, cr, width, height) => {
            const cx = width / 2;
            const cy = height / 2;
            const radius = Math.min(width, height) / 2 - 2;
            const scale = (settings.get_int('ui-scale') / 100.0) * 0.72;
            const opacityPct = settings.get_int('card-opacity') / 100.0;
            const is24h = settings.get_boolean('clock-format-24h');
            const showSeconds = settings.get_boolean('show-seconds');
            const numSys = settings.get_string('numeral-system') || 'latn';
            const num = v => formatNumerals(v, numSys);
            const { isLight, accentRgb, fgRgb } = getThemeColors();

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
            cr.setSourceRGBA(1.0, 1.0, 1.0, (isLight ? 0.70 : 0.22) * opacityPct);
            cr.stroke();

            const now = new Date();
            const h = now.getHours();
            const m = now.getMinutes();
            const s = now.getSeconds();

            const degHour = is24h ? ((h % 24) * 15) : ((h % 12) * 30);
            const degMin = m * 6;
            const degSec = s * 6;

            const hourNum = is24h ? (h === 0 ? 24 : h) : (h % 12 || 12);
            const hourLabel = num(String(hourNum).padStart(2, '0'));
            const minLabel = num(String(m).padStart(2, '0'));
            const secLabel = num(String(s).padStart(2, '0'));

            const orbitH = showSeconds ? 0.48 : 0.50;
            const orbitM = showSeconds ? 0.67 : 0.76;
            const orbitS = 0.85;

            const computeDigit = (deg, orbitRatio, txt, ptSize, clipR) => {
                const rad = (deg - 90) * (Math.PI / 180);
                const nx = cx + radius * orbitRatio * Math.cos(rad);
                const ny = cy + radius * orbitRatio * Math.sin(rad);
                const layout = PangoCairo.create_layout(cr);
                layout.set_text(String(txt), -1);
                layout.set_font_description(Pango.FontDescription.from_string(`Cantarell Bold ${Math.round(ptSize * scale * 10) / 10}`));
                const [tw, th] = layout.get_pixel_size();
                return { layout, nx, ny, tx: nx - tw / 2, ty: ny - th / 2, clipR: clipR * scale };
            };

            const dHour = computeDigit(degHour, orbitH, hourLabel, 12.5, 14.5);
            const dMin = computeDigit(degMin, orbitM, minLabel, 11.5, 14.0);
            const dSec = showSeconds ? computeDigit(degSec, orbitS, secLabel, 10.5, 13.5) : null;

            const clipCircle = d => {
                cr.setFillRule(Cairo.FillRule.EVEN_ODD);
                cr.newPath();
                cr.rectangle(0, 0, width, height);
                cr.newSubPath();
                cr.arc(d.nx, d.ny, d.clipR, 0, 2 * Math.PI);
                cr.clip();
            };

            const drawHand = (deg, tipLen, baseW, tipW, rgba) => {
                const rad = (deg - 90) * (Math.PI / 180);
                const bw = baseW / 2;
                const tw = tipW / 2;
                cr.save();
                cr.translate(cx, cy);
                cr.rotate(rad);
                cr.newPath();
                cr.moveTo(0, -bw);
                cr.lineTo(tipLen, -tw);
                cr.arc(tipLen, 0, tw, -Math.PI / 2, Math.PI / 2);
                cr.lineTo(0, bw);
                cr.arc(0, 0, bw, Math.PI / 2, -Math.PI / 2);
                cr.closePath();
                cr.setSourceRGBA(rgba[0], rgba[1], rgba[2], rgba[3]);
                cr.fill();
                cr.restore();
            };

            cr.save();
            clipCircle(dHour);
            drawHand(degHour, radius * (orbitH - 0.13), 5.4 * scale, 1.8 * scale, [...fgRgb, 0.98]);
            cr.restore();

            cr.save();
            clipCircle(dHour);
            clipCircle(dMin);
            drawHand(degMin, radius * (orbitM - 0.13), 4.2 * scale, 1.5 * scale, [...fgRgb, 0.90]);
            cr.restore();

            if (showSeconds && dSec) {
                cr.save();
                clipCircle(dHour);
                clipCircle(dMin);
                clipCircle(dSec);
                drawHand(degSec, radius * (orbitS - 0.13), 2.0 * scale, 1.0 * scale, [...accentRgb, 0.98]);
                cr.restore();
            }

            cr.arc(cx, cy, 4.8 * scale, 0, 2 * Math.PI);
            cr.setSourceRGBA(fgRgb[0], fgRgb[1], fgRgb[2], 0.98);
            cr.fill();
            cr.arc(cx, cy, 2.2 * scale, 0, 2 * Math.PI);
            cr.setSourceRGBA(accentRgb[0], accentRgb[1], accentRgb[2], 1.0);
            cr.fill();

            const renderDigit = (d, rgba) => {
                cr.save();
                cr.moveTo(d.tx, d.ty);
                cr.setSourceRGBA(rgba[0], rgba[1], rgba[2], rgba[3]);
                PangoCairo.show_layout(cr, d.layout);
                cr.restore();
            };
            renderDigit(dHour, [...fgRgb, 1.0]);
            renderDigit(dMin, [...fgRgb, 0.95]);
            if (showSeconds && dSec) renderDigit(dSec, [...accentRgb, 1.0]);
        };

        const createLivePreviewWidget = () => {
            const monitorBox = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                spacing: 14,
                halign: Gtk.Align.CENTER,
                valign: Gtk.Align.CENTER,
                width_request: 640,
                css_classes: ['adw-live-monitor'],
            });

            const clockArea = new Gtk.DrawingArea({
                content_width: 170,
                content_height: 170,
                halign: Gtk.Align.CENTER,
            });
            clockArea.set_draw_func(drawClockOnly);
            monitorBox.append(clockArea);

            const cardBox = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                halign: Gtk.Align.CENTER,
                width_request: 340,
                css_classes: ['adw-live-card'],
            });
            monitorBox.append(cardBox);

            const weatherSec = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                spacing: 9,
                css_classes: ['adw-live-weather-sec'],
            });
            cardBox.append(weatherSec);

            const heroRow = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 12,
                hexpand: true,
            });
            weatherSec.append(heroRow);

            const startStack = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                hexpand: true,
                halign: Gtk.Align.START,
                valign: Gtk.Align.CENTER,
            });
            const tempIconRow = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 8,
                halign: Gtk.Align.START,
                valign: Gtk.Align.CENTER,
            });
            const lblCurrentTemp = new Gtk.Label({
                label: '\u200E18°\u200E',
                css_classes: ['adw-live-temp'],
            });
            const iconMainWeather = new Gtk.Image({
                icon_name: 'weather-clear-symbolic',
                pixel_size: 24,
                css_classes: ['adw-live-icon-main'],
            });
            tempIconRow.append(lblCurrentTemp);
            tempIconRow.append(iconMainWeather);

            const lblCityStatus = new Gtk.Label({
                label: 'القاهرة  ·  صافٍ',
                halign: Gtk.Align.START,
                css_classes: ['adw-live-city-sub'],
            });
            startStack.append(tempIconRow);
            startStack.append(lblCityStatus);

            const endStack = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                spacing: 4,
                halign: Gtk.Align.END,
                valign: Gtk.Align.CENTER,
            });
            const lblHiLo = new Gtk.Label({
                label: '\u200E↑ 24°   ↓ 16°\u200E',
                halign: Gtk.Align.END,
                css_classes: ['adw-live-hilo-pill'],
            });
            const yesterdayRow = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 4,
                halign: Gtk.Align.END,
            });
            const lblYestText = new Gtk.Label({
                label: tr('أبرد من الأمس بـ', 'Cooler than yesterday by'),
                css_classes: ['adw-live-yesterday'],
            });
            const lblYestVal = new Gtk.Label({
                label: '\u200E1°\u200E',
                css_classes: ['adw-live-yesterday'],
            });
            yesterdayRow.append(lblYestText);
            yesterdayRow.append(lblYestVal);

            endStack.append(lblHiLo);
            endStack.append(yesterdayRow);

            heroRow.append(startStack);
            heroRow.append(endStack);

            const metricsBar = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                hexpand: true,
                homogeneous: true,
                css_classes: ['adw-live-metrics-bar'],
            });
            weatherSec.append(metricsBar);

            const metricMap = {};
            const metricTitles = {
                humidity: tr('الرطوبة', 'Humidity'),
                wind: tr('الرياح', 'Wind'),
                feels: tr('الشعور', 'Feels Like'),
                uv: tr('الأشعة UV', 'UV Index'),
            };
            for (const key of ['humidity', 'wind', 'feels', 'uv']) {
                const col = new Gtk.Box({
                    orientation: Gtk.Orientation.VERTICAL,
                    spacing: 2,
                    halign: Gtk.Align.CENTER,
                });
                const valRow = new Gtk.Box({
                    orientation: Gtk.Orientation.HORIZONTAL,
                    spacing: 4,
                    halign: Gtk.Align.CENTER,
                    valign: Gtk.Align.CENTER,
                });
                const mIcon = new Gtk.Image({
                    gicon: buildMetricSvg(key, '#ffffff'),
                    pixel_size: 12,
                });
                const valLbl = new Gtk.Label({
                    label: '--',
                    css_classes: ['adw-live-m-val'],
                });
                valRow.append(mIcon);
                valRow.append(valLbl);

                let unitLbl = null;
                if (key === 'wind') {
                    unitLbl = new Gtk.Label({
                        label: tr('كم/س', 'km/h'),
                        css_classes: ['adw-live-m-unit'],
                    });
                    valRow.append(unitLbl);
                }

                const titleLbl = new Gtk.Label({
                    label: metricTitles[key],
                    css_classes: ['adw-live-m-lbl'],
                });
                col.append(valRow);
                col.append(titleLbl);
                metricsBar.append(col);
                metricMap[key] = { col, mIcon, valLbl, unitLbl };
            }

            const forecastRow = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 6,
                hexpand: true,
                homogeneous: true,
            });
            weatherSec.append(forecastRow);

            const fcList = [];
            const sampleDays = [tr('الأحد', 'Sun'), tr('الاثنين', 'Mon'), tr('الثلاثاء', 'Tue')];
            for (let i = 0; i < 3; i++) {
                const pill = new Gtk.Box({
                    orientation: Gtk.Orientation.VERTICAL,
                    spacing: 2,
                    css_classes: ['adw-live-fc-pill'],
                });
                const head = new Gtk.Box({
                    orientation: Gtk.Orientation.HORIZONTAL,
                    spacing: 5,
                    halign: Gtk.Align.CENTER,
                });
                const dLbl = new Gtk.Label({ label: sampleDays[i], css_classes: ['adw-live-fc-day'] });
                const dIcon = new Gtk.Image({ icon_name: 'weather-few-clouds-symbolic', pixel_size: 13, css_classes: ['adw-live-icon-main'] });
                head.append(dLbl);
                head.append(dIcon);

                const tLbl = new Gtk.Label({
                    label: '\u200E25° / 15°\u200E',
                    halign: Gtk.Align.CENTER,
                    css_classes: ['adw-live-fc-temps'],
                });
                pill.append(head);
                pill.append(tLbl);
                forecastRow.append(pill);
                fcList.push({ dLbl, dIcon, tLbl });
            }

            const divider = new Gtk.Box({ css_classes: ['adw-live-divider'] });
            cardBox.append(divider);

            const dateSec = new Gtk.Box({
                orientation: Gtk.Orientation.VERTICAL,
                spacing: 4,
                halign: Gtk.Align.CENTER,
                css_classes: ['adw-live-date-sec'],
            });
            cardBox.append(dateSec);

            const lblWeekdaySingle = new Gtk.Label({
                label: tr('السبت', 'Saturday'),
                halign: Gtk.Align.CENTER,
                css_classes: ['adw-live-weekday-single'],
            });
            dateSec.append(lblWeekdaySingle);

            const weekdayStrip = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 3,
                halign: Gtk.Align.CENTER,
            });
            const stripChips = [];
            const stripNames = isAr
                ? ['السبت', 'الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة']
                : ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            for (let i = 0; i < 7; i++) {
                const chip = new Gtk.Label({
                    label: stripNames[i],
                    css_classes: [i === 0 ? 'adw-live-chip-active' : 'adw-live-chip-dim'],
                });
                weekdayStrip.append(chip);
                stripChips.push(chip);
            }
            dateSec.append(weekdayStrip);

            const calRow = new Gtk.Box({
                orientation: Gtk.Orientation.HORIZONTAL,
                spacing: 8,
                halign: Gtk.Align.CENTER,
            });
            const lblGreg = new Gtk.Label({ label: '--', css_classes: ['adw-live-greg'] });
            const lblSep = new Gtk.Label({ label: '|', css_classes: ['adw-live-sep'] });
            const lblHijri = new Gtk.Label({ label: '--', css_classes: ['adw-live-hijri'] });
            calRow.append(lblGreg);
            calRow.append(lblSep);
            calRow.append(lblHijri);
            dateSec.append(calRow);

            const instance = {
                monitorBox, clockArea, cardBox, weatherSec, yesterdayRow, lblCurrentTemp, iconMainWeather,
                lblCityStatus, lblHiLo, lblYestText, lblYestVal, metricsBar, metricMap, forecastRow, fcList,
                divider, dateSec, lblWeekdaySingle, weekdayStrip, stripChips, calRow, lblGreg, lblSep, lblHijri,
            };
            previewInstances.push(instance);
            return monitorBox;
        };

        const syncAllPreviewWidgets = () => {
            updatePreviewCss();
            const scale = (settings.get_int('ui-scale') / 100.0) * 0.72;
            const numSys = settings.get_string('numeral-system') || 'latn';
            const num = v => formatNumerals(v, numSys);
            const { fgHex } = getThemeColors();

            const showClock = settings.get_boolean('show-clock');
            const showRect = settings.get_boolean('show-rectangle');
            const showWeather = settings.get_boolean('show-weather');
            const showDateSec = settings.get_boolean('show-date-section');
            const showYesterday = settings.get_boolean('show-yesterday');
            const showForecast = settings.get_boolean('show-forecast');
            const showHum = settings.get_boolean('show-humidity');
            const showWind = settings.get_boolean('show-wind');
            const showFeels = settings.get_boolean('show-feels-like');
            const showUv = settings.get_boolean('show-uv');
            const showWeekday = settings.get_boolean('show-weekday');
            const weekdayStyle = settings.get_string('weekday-style');
            const showGreg = settings.get_boolean('show-gregorian');
            const showSecDate = settings.get_boolean('show-secondary-date');

            const ccActive = (settings.get_string('manual-country') || 'EG').toUpperCase();
            const tempMode = settings.get_string('temp-unit');
            const isFahr = tempMode === 'fahrenheit' || (tempMode === 'auto' && ['US', 'BS', 'KY', 'LR'].includes(ccActive));
            const tMain = isFahr ? 64 : 18;
            const tMax = isFahr ? 75 : 24;
            const tMin = isFahr ? 61 : 16;

            const windSetting = settings.get_string('wind-unit');
            const effWind = (windSetting === 'auto') ? (['US', 'GB', 'LR', 'MM'].includes(ccActive) ? 'mph' : 'kmh') : windSetting;
            const windUnitStr = effWind === 'ms' ? tr('م/ث', 'm/s') : (effWind === 'mph' ? tr('ميل/س', 'mph') : tr('كم/س', 'km/h'));

            const cityStr = settings.get_string('location-mode') === 'manual'
                ? settings.get_string('manual-city')
                : tr('القاهرة', 'Cairo');

            const now = new Date();
            const cc = (settings.get_string('manual-country') || 'EG').toUpperCase();
            let monthList = MONTHS_STANDARD;
            if (['SY', 'LB', 'JO', 'PS', 'IQ'].includes(cc)) monthList = MONTHS_LEVANT;
            else if (['TN', 'DZ'].includes(cc)) monthList = MONTHS_MAGHREB;

            const gregStr = isAr
                ? `${num(now.getDate())} ${monthList[now.getMonth()]} ${num(now.getFullYear())}`
                : num(now.toLocaleDateString('en-US', { day: 'numeric', month: 'long', year: 'numeric' }));
            const hijriStr = isAr
                ? `${num(27)} جمادى الأولى ${num(1448)} هـ`
                : `${num(27)} Jumada I ${num(1448)} AH`;

            const clockSize = Math.round(236 * scale);
            const cardWidth = Math.round(456 * scale);

            for (const inst of previewInstances) {
                inst.clockArea.visible = showClock;
                inst.clockArea.set_content_width(clockSize);
                inst.clockArea.set_content_height(clockSize);
                inst.clockArea.queue_draw();

                inst.cardBox.width_request = cardWidth;
                inst.weatherSec.visible = showWeather;
                inst.dateSec.visible = showDateSec;
                inst.divider.visible = showWeather && showDateSec;
                inst.cardBox.visible = showRect && (showWeather || showDateSec);

                inst.lblCurrentTemp.label = `\u200E${num(tMain)}°\u200E`;
                inst.lblCityStatus.label = `${num(cityStr)}  ·  ${tr('صافٍ', 'Clear')}`;
                inst.lblHiLo.label = `\u200E↑ ${num(tMax)}°   ↓ ${num(tMin)}°\u200E`;
                inst.yesterdayRow.visible = showYesterday;
                inst.lblYestVal.label = `\u200E${num(1)}°\u200E`;

                inst.metricMap.humidity.col.visible = showHum;
                inst.metricMap.wind.col.visible = showWind;
                inst.metricMap.feels.col.visible = showFeels;
                inst.metricMap.uv.col.visible = showUv;
                inst.metricsBar.visible = showHum || showWind || showFeels || showUv;

                for (const [k, mObj] of Object.entries(inst.metricMap)) {
                    mObj.mIcon.gicon = buildMetricSvg(k, fgHex);
                }
                inst.metricMap.humidity.valLbl.label = `\u200E${num(76)}%\u200E`;
                inst.metricMap.wind.valLbl.label = `\u200E${num(5)}\u200E`;
                if (inst.metricMap.wind.unitLbl) inst.metricMap.wind.unitLbl.label = windUnitStr;
                inst.metricMap.feels.valLbl.label = `\u200E${num(tMain + 1)}°\u200E`;
                inst.metricMap.uv.valLbl.label = `\u200E${num(0)}\u200E`;

                inst.forecastRow.visible = showForecast;
                const fcTemps = [
                    `\u200E${num(tMax + 1)}° / ${num(tMin - 1)}°\u200E`,
                    `\u200E${num(tMax + 2)}° / ${num(tMin - 3)}°\u200E`,
                    `\u200E${num(tMax)}° / ${num(tMin - 3)}°\u200E`,
                ];
                inst.fcList.forEach((f, idx) => { f.tLbl.label = fcTemps[idx]; });

                inst.lblWeekdaySingle.visible = showWeekday && weekdayStyle !== 'strip';
                inst.weekdayStrip.visible = showWeekday && weekdayStyle === 'strip';

                inst.lblGreg.label = gregStr;
                inst.lblHijri.label = hijriStr;
                inst.lblGreg.visible = showGreg;
                inst.lblHijri.visible = showSecDate;
                inst.lblSep.visible = showGreg && showSecDate;
                inst.calRow.visible = showGreg || showSecDate;
            }
        };

        const attachLivePreviewHeader = page => {
            const grp = new Adw.PreferencesGroup();
            grp.add(createLivePreviewWidget());
            page.add(grp);
        };

        const previewTickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            for (const inst of previewInstances) {
                if (inst.clockArea.visible) inst.clockArea.queue_draw();
            }
            return GLib.SOURCE_CONTINUE;
        });
        window.connect('close-request', () => {
            if (previewTickId) GLib.source_remove(previewTickId);
            return false;
        });
        settings.connect('changed', syncAllPreviewWidgets);

        const addModalChoiceRow = (group, key, title, subtitle, options) => {
            const getShortLabel = id => {
                const found = options.find(o => o.id === id);
                if (!found) return id;
                return isAr ? (found.shortAr || found.ar) : (found.shortEn || found.en);
            };

            const row = new Adw.ActionRow({ title, subtitle, activatable: true });
            const valBox = new Gtk.Box({ orientation: Gtk.Orientation.HORIZONTAL, spacing: 6, valign: Gtk.Align.CENTER });
            const valLbl = new Gtk.Label({ label: getShortLabel(settings.get_string(key)), css_classes: ['dim-label'], single_line_mode: true });
            const arrowIcon = new Gtk.Image({ icon_name: 'pan-down-symbolic', css_classes: ['dim-label'] });
            valBox.append(valLbl);
            valBox.append(arrowIcon);
            row.add_suffix(valBox);

            row.connect('activated', () => {
                const dlg = new Gtk.Window({
                    title,
                    transient_for: window,
                    modal: true,
                    default_width: 580,
                    default_height: Math.min(600, 150 + options.length * 50),
                });
                const rootBox = new Gtk.Box({
                    orientation: Gtk.Orientation.VERTICAL,
                    spacing: 10,
                    margin_top: 14, margin_bottom: 14, margin_start: 16, margin_end: 16,
                });
                dlg.set_child(rootBox);

                let searchEntry = null;
                if (options.length > 5) {
                    searchEntry = new Gtk.SearchEntry({ placeholder_text: tr('ابحث هنا...', 'Search options...'), hexpand: true });
                    rootBox.append(searchEntry);
                }

                const scrolled = new Gtk.ScrolledWindow({ vexpand: true, hexpand: true, hscrollbar_policy: Gtk.PolicyType.NEVER });
                rootBox.append(scrolled);

                const listBox = new Gtk.ListBox({ selection_mode: Gtk.SelectionMode.NONE, css_classes: ['boxed-list'] });
                scrolled.set_child(listBox);

                const populate = filterText => {
                    let child = listBox.get_first_child();
                    while (child) { const next = child.get_next_sibling(); listBox.remove(child); child = next; }
                    const q = (filterText || '').trim().toLowerCase();
                    const curId = settings.get_string(key);

                    for (const opt of options) {
                        const fullText = isAr ? opt.ar : opt.en;
                        if (q && !fullText.toLowerCase().includes(q) && !opt.ar.toLowerCase().includes(q) && !opt.en.toLowerCase().includes(q)) continue;
                        const itemRow = new Adw.ActionRow({ title: fullText, activatable: true });
                        if (opt.id === curId) {
                            itemRow.add_suffix(new Gtk.Image({ icon_name: 'object-select-symbolic', css_classes: ['accent'] }));
                        }
                        itemRow.connect('activated', () => {
                            settings.set_string(key, opt.id);
                            valLbl.label = getShortLabel(opt.id);
                            dlg.close();
                        });
                        listBox.append(itemRow);
                    }
                };
                populate('');
                if (searchEntry) searchEntry.connect('search-changed', () => populate(searchEntry.get_text()));
                dlg.present();
            });

            settings.connect(`changed::${key}`, () => { valLbl.label = getShortLabel(settings.get_string(key)); });
            group.add(row);
            return row;
        };

        const addSwitch = (group, key, title, subtitle = '') => {
            const row = new Adw.SwitchRow({ title, subtitle });
            settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT);
            group.add(row);
            return row;
        };

        const addIntSpin = (group, key, title, subtitle, min, max, step = 1) => {
            const adj = new Gtk.Adjustment({ lower: min, upper: max, step_increment: step, page_increment: step * 5, value: settings.get_int(key) });
            const row = new Adw.SpinRow({ title, subtitle, adjustment: adj });
            settings.bind(key, adj, 'value', Gio.SettingsBindFlags.DEFAULT);
            group.add(row);
            return row;
        };

        // Page 1: Clock & Layout
        const pageClock = new Adw.PreferencesPage({ title: tr('الساعة والتخطيط', 'Clock & Layout'), icon_name: 'preferences-system-time-symbolic' });
        window.add(pageClock);
        

        const grpClock = new Adw.PreferencesGroup({ title: tr('إعدادات الساعة الهجينة', 'Hybrid Clock Settings') });
        pageClock.add(grpClock);
        addSwitch(grpClock, 'show-clock', tr('إظهار الساعة الدائرية', 'Show Analog-Digital Clock'), tr('عند التعطيل تختفي الساعة ويبقى مستطيل الطقس والتاريخ فقط', 'Hide the clock and keep only the info rectangle'));
        addSwitch(grpClock, 'clock-format-24h', tr('نظام 24 ساعة (الافتراضي)', '24-Hour Dial Format (Default)'), tr('عند التعطيل تتحول الساعة إلى نظام 12 ساعة', 'Turn off to switch to a 12-hour dial'));
        addSwitch(grpClock, 'show-seconds', tr('إظهار عقرب ورقم الثواني', 'Show Seconds Hand & Digit'), tr('عند التعطيل يظهر عقربا الساعات والدقائق فقط مع توفير استهلاك الطاقة', 'Hide seconds hand and digit for a cleaner look and power saving'));

        const grpLayout = new Adw.PreferencesGroup({ title: tr('الأبعاد والتموضع الرأسي', 'Scale & Vertical Position') });
        pageClock.add(grpLayout);
        addIntSpin(grpLayout, 'ui-scale', tr('الحجم الكلي للعناصر (%)', 'Overall UI Scale (%)'), tr('تكبير أو تصغير الساعة والمستطيل والخطوط (75% إلى 135%)', 'Scale clock, card, and typography (75% to 135%)'), 75, 135, 5);
        addIntSpin(grpLayout, 'vertical-position', tr('الموضع الرأسي على الشاشة (%)', 'Vertical Screen Position (%)'), tr('نسبة الارتفاع الرأسي لنقطة الارتكاز على الشاشة (50% = المنتصف تماماً)', 'Vertical anchor point on screen (50% = exact center)'), 30, 70, 1);

        // Page 2: Weather & Location
        const pageWeather = new Adw.PreferencesPage({ title: tr('الطقس والموقع', 'Weather & Location'), icon_name: 'weather-clear-symbolic' });
        window.add(pageWeather);
        

        const grpWeatherMain = new Adw.PreferencesGroup({ title: tr('عناصر بطاقة الطقس', 'Weather Card Elements') });
        pageWeather.add(grpWeatherMain);
        addSwitch(grpWeatherMain, 'show-rectangle', tr('إظهار المستطيل السفلي (الطقس والتاريخ)', 'Show Info Rectangle'), tr('عند التعطيل يختفي المستطيل بالكامل وتبقى الساعة وحدها', 'Hide the entire bottom card and keep only the clock'));
        addSwitch(grpWeatherMain, 'show-weather', tr('إظهار قسم الطقس', 'Show Weather Section'));
        addSwitch(grpWeatherMain, 'show-yesterday', tr('إظهار المقارنة مع درجة حرارة الأمس', 'Show Comparison with Yesterday'));
        addSwitch(grpWeatherMain, 'show-forecast', tr('إظهار توقعات الأيام الثلاثة القادمة', 'Show 3-Day Forecast Row'));

        const grpMetrics = new Adw.PreferencesGroup({ title: tr('المقاييس التفصيلية الأربعة', 'Detailed Weather Metrics') });
        pageWeather.add(grpMetrics);
        addSwitch(grpMetrics, 'show-humidity', tr('الرطوبة (Humidity)', 'Humidity'));
        addSwitch(grpMetrics, 'show-wind', tr('سرعة الرياح (Wind Speed)', 'Wind Speed'));
        addSwitch(grpMetrics, 'show-feels-like', tr('الشعور الفعلي (Feels Like)', 'Feels Like'));
        addSwitch(grpMetrics, 'show-uv', tr('مؤشر الأشعة (UV Index)', 'UV Index'));

        const grpUnitsLoc = new Adw.PreferencesGroup({ title: tr('وحدات القياس والموقع الجغرافي التفاعلي', 'Units & Interactive Location') });
        pageWeather.add(grpUnitsLoc);

        addModalChoiceRow(grpUnitsLoc, 'temp-unit', tr('وحدة قياس درجة الحرارة', 'Temperature Unit'), '', [
            { id: 'auto',       shortAr: 'تلقائي',         shortEn: 'Auto',            ar: 'تلقائي حسب الدولة (°C / °F)', en: 'Automatic by Country (°C / °F)' },
            { id: 'celsius',    shortAr: 'مئوية (°C)',     shortEn: 'Celsius (°C)',    ar: 'الدرجة المئوية (°C)',         en: 'Celsius (°C)' },
            { id: 'fahrenheit', shortAr: 'فهرنهايت (°F)', shortEn: 'Fahrenheit (°F)', ar: 'الفهرنهايت (°F)',             en: 'Fahrenheit (°F)' },
        ]);
        addModalChoiceRow(grpUnitsLoc, 'wind-unit', tr('وحدة سرعة الرياح', 'Wind Speed Unit'), '', [
            { id: 'auto', shortAr: 'تلقائي', shortEn: 'Auto', ar: 'تلقائي حسب الدولة (كم/س أو ميل/س)', en: 'Automatic by Country (km/h or mph)' },
            { id: 'kmh',  shortAr: 'كم/س',   shortEn: 'km/h', ar: 'كيلومتر في الساعة (كم/س)',          en: 'Kilometers per hour (km/h)' },
            { id: 'ms',   shortAr: 'م/ث',    shortEn: 'm/s',  ar: 'متر في الثانية (م/ث)',               en: 'Meters per second (m/s)' },
            { id: 'mph',  shortAr: 'ميل/س',  shortEn: 'mph',  ar: 'ميل في الساعة (mph)',                en: 'Miles per hour (mph)' },
        ]);
        addModalChoiceRow(grpUnitsLoc, 'location-mode', tr('طريقة تحديد الموقع', 'Location Detection Mode'), tr('تُقفل الخانات اليدوية أدناه في الوضع التلقائي وتُفتح عند اختيار الوضع اليدوي', 'Manual fields below are locked unless Manual mode is selected'), [
            { id: 'auto',   shortAr: 'تلقائي', shortEn: 'Auto',   ar: 'اكتشاف تلقائي عبر الشبكة (Auto IP)', en: 'Automatic Network Detection' },
            { id: 'manual', shortAr: 'يدوي',   shortEn: 'Manual', ar: 'اختيار يدوي للدولة والمدينة (Manual)', en: 'Manual Country & City Selection' },
        ]);

        const getCountryShort = code => {
            const c = COUNTRIES_DB.find(x => x.code === (code || '').toUpperCase());
            return c ? (isAr ? c.shortAr : c.shortEn) : code;
        };

        const rowCountryPicker = new Adw.ActionRow({
            title: tr('الدولة', 'Country'),
            subtitle: tr('عند تغيير الدولة تتغير المدينة تلقائياً إلى عاصمتها', 'Changing country automatically sets its capital city'),
            activatable: true,
        });
        const lblCountryVal = new Gtk.Label({ label: getCountryShort(settings.get_string('manual-country')), css_classes: ['dim-label'], single_line_mode: true });
        const boxCountrySuffix = new Gtk.Box({ spacing: 6, valign: Gtk.Align.CENTER });
        boxCountrySuffix.append(lblCountryVal);
        boxCountrySuffix.append(new Gtk.Image({ icon_name: 'pan-down-symbolic', css_classes: ['dim-label'] }));
        rowCountryPicker.add_suffix(boxCountrySuffix);
        grpUnitsLoc.add(rowCountryPicker);

        const rowCityPicker = new Adw.ActionRow({
            title: tr('المدينة', 'City'),
            subtitle: tr('اختر من مدن الدولة أو ابحث بالكتابة عن أي مدينة؛ ويتغير رمز الدولة تلقائياً', 'Pick from country cities or type to search; country updates automatically'),
            activatable: true,
        });
        const lblCityVal = new Gtk.Label({ label: settings.get_string('manual-city'), css_classes: ['dim-label'], single_line_mode: true });
        const boxCitySuffix = new Gtk.Box({ spacing: 6, valign: Gtk.Align.CENTER });
        boxCitySuffix.append(lblCityVal);
        boxCitySuffix.append(new Gtk.Image({ icon_name: 'pan-down-symbolic', css_classes: ['dim-label'] }));
        rowCityPicker.add_suffix(boxCitySuffix);
        grpUnitsLoc.add(rowCityPicker);

        const applyCityRecord = rec => {
            settings.set_string('manual-country', rec.countryCode);
            settings.set_string('manual-city', isAr ? rec.ar : rec.en);
            settings.set_string('manual-timezone', rec.tz);
            settings.set_double('manual-lat', rec.lat);
            settings.set_double('manual-lon', rec.lon);
            lblCountryVal.label = getCountryShort(rec.countryCode);
            lblCityVal.label = isAr ? rec.ar : rec.en;
        };

        rowCountryPicker.connect('activated', () => {
            const dlg = new Gtk.Window({ title: tr('اختر الدولة أو ابحث عنها', 'Search & Select Country'), transient_for: window, modal: true, default_width: 580, default_height: 560 });
            const box = new Gtk.Box({ orientation: Gtk.Orientation.VERTICAL, spacing: 10, margin_top: 14, margin_bottom: 14, margin_start: 16, margin_end: 16 });
            dlg.set_child(box);
            const searchEntry = new Gtk.SearchEntry({ placeholder_text: tr('اكتب اسم الدولة أو رمزها (مثال: مصر، السعودية، EG)...', 'Type country name or ISO code...'), hexpand: true });
            box.append(searchEntry);
            const scrolled = new Gtk.ScrolledWindow({ vexpand: true, hexpand: true, hscrollbar_policy: Gtk.PolicyType.NEVER });
            box.append(scrolled);
            const listBox = new Gtk.ListBox({ selection_mode: Gtk.SelectionMode.NONE, css_classes: ['boxed-list'] });
            scrolled.set_child(listBox);

            const populateCountries = q => {
                let ch = listBox.get_first_child();
                while (ch) { const nx = ch.get_next_sibling(); listBox.remove(ch); ch = nx; }
                const query = (q || '').trim().toLowerCase();
                const curCode = (settings.get_string('manual-country') || 'EG').toUpperCase();
                for (const c of COUNTRIES_DB) {
                    if (query && !c.ar.toLowerCase().includes(query) && !c.en.toLowerCase().includes(query) && !c.code.toLowerCase().includes(query)) continue;
                    const cap = c.cities[0];
                    const capName = isAr ? cap.ar : cap.en;
                    const item = new Adw.ActionRow({
                        title: isAr ? c.ar : c.en,
                        subtitle: tr(`العاصمة الافتراضية: ${capName} (${c.cities.length} مدن مدعومة)`, `Capital: ${capName} (${c.cities.length} supported cities)`),
                        activatable: true,
                    });
                    if (c.code === curCode) item.add_suffix(new Gtk.Image({ icon_name: 'object-select-symbolic', css_classes: ['accent'] }));
                    item.connect('activated', () => {
                        applyCityRecord({ countryCode: c.code, ar: cap.ar, en: cap.en, lat: cap.lat, lon: cap.lon, tz: cap.tz || c.tz });
                        dlg.close();
                    });
                    listBox.append(item);
                }
            };
            populateCountries('');
            searchEntry.connect('search-changed', () => populateCountries(searchEntry.get_text()));
            dlg.present();
        });

        rowCityPicker.connect('activated', () => {
            const dlg = new Gtk.Window({ title: tr('اختر المدينة أو ابحث بالكتابة', 'Search & Select City'), transient_for: window, modal: true, default_width: 600, default_height: 580 });
            const box = new Gtk.Box({ orientation: Gtk.Orientation.VERTICAL, spacing: 10, margin_top: 14, margin_bottom: 14, margin_start: 16, margin_end: 16 });
            dlg.set_child(box);
            const searchEntry = new Gtk.SearchEntry({ placeholder_text: tr('ابدأ بكتابة اسم أي مدينة (مثال: القاهرة، 6 أكتوبر، مكة، القدس، دبي)...', 'Start typing any city name...'), hexpand: true });
            box.append(searchEntry);
            const scrolled = new Gtk.ScrolledWindow({ vexpand: true, hexpand: true, hscrollbar_policy: Gtk.PolicyType.NEVER });
            box.append(scrolled);
            const listBox = new Gtk.ListBox({ selection_mode: Gtk.SelectionMode.NONE, css_classes: ['boxed-list'] });
            scrolled.set_child(listBox);

            const populateCities = q => {
                let ch = listBox.get_first_child();
                while (ch) { const nx = ch.get_next_sibling(); listBox.remove(ch); ch = nx; }
                const query = (q || '').trim().toLowerCase();
                const activeCountry = (settings.get_string('manual-country') || 'EG').toUpperCase();
                const activeCity = settings.get_string('manual-city');
                const sortedCities = [...ALL_CITIES_FLAT].sort((a, b) => (a.countryCode === activeCountry ? 0 : 1) - (b.countryCode === activeCountry ? 0 : 1));
                for (const rec of sortedCities) {
                    if (query && !rec.ar.toLowerCase().includes(query) && !rec.en.toLowerCase().includes(query) && !rec.countryAr.toLowerCase().includes(query) && !rec.countryEn.toLowerCase().includes(query)) continue;
                    const cTitle = isAr ? `${rec.ar} (${rec.en})` : `${rec.en} (${rec.ar})`;
                    const cSub = isAr
                        ? `${rec.countryAr}  ·  ${rec.isCapital ? 'العاصمة' : 'مدينة مدعومة'}  ·  [${rec.lat.toFixed(2)}, ${rec.lon.toFixed(2)}]`
                        : `${rec.countryEn}  ·  ${rec.isCapital ? 'Capital' : 'City'}  ·  [${rec.lat.toFixed(2)}, ${rec.lon.toFixed(2)}]`;
                    const item = new Adw.ActionRow({ title: cTitle, subtitle: cSub, activatable: true });
                    if (rec.ar === activeCity || rec.en === activeCity) item.add_suffix(new Gtk.Image({ icon_name: 'object-select-symbolic', css_classes: ['accent'] }));
                    item.connect('activated', () => { applyCityRecord(rec); dlg.close(); });
                    listBox.append(item);
                }
            };
            populateCities('');
            searchEntry.connect('search-changed', () => populateCities(searchEntry.get_text()));
            dlg.present();
        });

        const entryCityCustom = new Adw.EntryRow({ title: tr('تخصيص اسم العرض للمدينة', 'Custom City Display Name') });
        settings.bind('manual-city', entryCityCustom, 'text', Gio.SettingsBindFlags.DEFAULT);
        grpUnitsLoc.add(entryCityCustom);

        const entryTz = new Adw.EntryRow({ title: tr('المنطقة الزمنية', 'Timezone') });
        settings.bind('manual-timezone', entryTz, 'text', Gio.SettingsBindFlags.DEFAULT);
        grpUnitsLoc.add(entryTz);

        const adjLat = new Gtk.Adjustment({ lower: -90.0, upper: 90.0, step_increment: 0.01, page_increment: 1.0, value: settings.get_double('manual-lat') });
        const spinLat = new Adw.SpinRow({ title: tr('خط العرض (Latitude)', 'Latitude'), digits: 4, adjustment: adjLat });
        settings.bind('manual-lat', adjLat, 'value', Gio.SettingsBindFlags.DEFAULT);
        grpUnitsLoc.add(spinLat);

        const adjLon = new Gtk.Adjustment({ lower: -180.0, upper: 180.0, step_increment: 0.01, page_increment: 1.0, value: settings.get_double('manual-lon') });
        const spinLon = new Adw.SpinRow({ title: tr('خط الطول (Longitude)', 'Longitude'), digits: 4, adjustment: adjLon });
        settings.bind('manual-lon', adjLon, 'value', Gio.SettingsBindFlags.DEFAULT);
        grpUnitsLoc.add(spinLon);

        const syncLocationSensitivity = () => {
            const isManual = settings.get_string('location-mode') === 'manual';
            rowCountryPicker.sensitive = isManual;
            rowCityPicker.sensitive = isManual;
            entryCityCustom.sensitive = isManual;
            entryTz.sensitive = isManual;
            spinLat.sensitive = isManual;
            spinLon.sensitive = isManual;
        };
        syncLocationSensitivity();
        settings.connect('changed::location-mode', syncLocationSensitivity);
        settings.connect('changed::manual-country', () => { lblCountryVal.label = getCountryShort(settings.get_string('manual-country')); });
        settings.connect('changed::manual-city', () => { lblCityVal.label = settings.get_string('manual-city'); });

        // Page 3: Date & Calendars
        const pageDate = new Adw.PreferencesPage({ title: tr('التاريخ والتقاويم', 'Date & Calendars'), icon_name: 'x-office-calendar-symbolic' });
        window.add(pageDate);
        

        const grpDate = new Adw.PreferencesGroup({
            title: tr('تخصيص يوم الأسبوع والتقاويم', 'Weekday & Calendar Options'),
            description: tr('يمكنك أيضاً الضغط مطولاً (700ms) على التاريخ في شاشة القفل لإخفاء/إظهار التاريخ المحلي', 'You can also long-press (700ms) the date on the lock screen to toggle the secondary date'),
        });
        pageDate.add(grpDate);

        addSwitch(grpDate, 'show-date-section', tr('إظهار قسم التاريخ', 'Show Date Section'));
        addSwitch(grpDate, 'show-weekday', tr('إظهار يوم الأسبوع', 'Show Day of the Week'));

        const rowWeekdayStyle = addModalChoiceRow(grpDate, 'weekday-style',
            tr('نمط عرض يوم الأسبوع', 'Weekday Display Style'),
            tr('اختر بين عرض اليوم منفرداً أو عرض شريط أيام الأسبوع السبعة مع دائرة حول اليوم الحالي', 'Single centered title or 7-day horizontal strip with a circle around today'),
            [
                { id: 'single', shortAr: 'منفرد',       shortEn: 'Single',      ar: 'اليوم الحالي منفرداً في المنتصف', en: 'Single Centered Weekday' },
                { id: 'strip',  shortAr: 'شريط أسبوعي', shortEn: '7-Day Strip', ar: 'شريط أفقي لكل أيام الأسبوع مع دائرة حول اليوم الحالي', en: 'Horizontal 7-Day Strip with Highlight Circle' },
            ]);

        const syncWeekdaySensitivity = () => { rowWeekdayStyle.sensitive = settings.get_boolean('show-weekday'); };
        syncWeekdaySensitivity();
        settings.connect('changed::show-weekday', syncWeekdaySensitivity);

        addModalChoiceRow(grpDate, 'week-start',
            tr('أول يوم في شريط الأسبوع', 'First Day of the Week'),
            tr('يتكيف تلقائياً مع معيار الدولة (السبت للعرب، الأحد لأمريكا، الإثنين لأوروبا) أو يمكنك تحديده يدوياً', 'Auto-adapts to country standard (Sat, Sun, or Mon) or select manually'),
            [
                { id: 'auto', shortAr: 'تلقائي',  shortEn: 'Auto',     ar: 'تلقائي حسب الدولة والمنطقة', en: 'Automatic by Country/Region' },
                { id: 'sat',  shortAr: 'السبت',   shortEn: 'Saturday', ar: 'السبت (Saturday)',            en: 'Saturday' },
                { id: 'sun',  shortAr: 'الأحد',   shortEn: 'Sunday',   ar: 'الأحد (Sunday)',              en: 'Sunday' },
                { id: 'mon',  shortAr: 'الإثنين', shortEn: 'Monday',   ar: 'الإثنين (Monday - ISO 8601)', en: 'Monday (ISO 8601)' },
            ]);

        addSwitch(grpDate, 'show-gregorian', tr('إظهار التاريخ الميلادي', 'Show Gregorian Date'), tr('يمكنك تعطيله للإبقاء على التاريخ الهجري / المحلي فقط', 'Disable to show only the Hijri / Local date'));
        addSwitch(grpDate, 'show-secondary-date', tr('إظهار التاريخ الثانوي / المحلي', 'Show Secondary / Regional Date'), tr('يمكنك تعطيله للإبقاء على التاريخ الميلادي فقط', 'Disable to show only the Gregorian date'));

        addModalChoiceRow(grpDate, 'secondary-calendar-type',
            tr('نظام التقويم الثانوي', 'Secondary Calendar System'),
            tr('يتعرف تلقائياً على تقويم الدولة أو يتيح لك اختيار أي تقويم عالمي', 'Auto-detects regional calendar or choose any world calendar'),
            [
                { id: 'auto',             shortAr: 'تلقائي',       shortEn: 'Auto',       ar: 'تلقائي حسب الدولة والمنطقة',        en: 'Automatic by Region' },
                { id: 'islamic-umalqura', shortAr: 'هجري',         shortEn: 'Hijri',      ar: 'التقويم الهجري (أم القرى)',         en: 'Islamic Hijri (Umm al-Qura)' },
                { id: 'persian',          shortAr: 'فارسي',        shortEn: 'Persian',    ar: 'التقويم الفارسي الشمسي (جلالي)',    en: 'Persian Solar Hijri' },
                { id: 'chinese',          shortAr: 'صيني',         shortEn: 'Chinese',    ar: 'التقويم القمري الصيني',             en: 'Chinese Lunar Calendar' },
                { id: 'japanese',         shortAr: 'ياباني',       shortEn: 'Japanese',   ar: 'التقويم الياباني الإمبراطوري',      en: 'Japanese Imperial Calendar' },
                { id: 'buddhist',         shortAr: 'بوذي',         shortEn: 'Buddhist',   ar: 'التقويم البوذي',                    en: 'Buddhist Calendar' },
                { id: 'indian',           shortAr: 'هندي',         shortEn: 'Indian',     ar: 'التقويم الوطني الهندي (شاكا)',      en: 'Indian National Calendar' },
                { id: 'ethiopic',         shortAr: 'إثيوبي',       shortEn: 'Ethiopic',   ar: 'التقويم الإثيوبي / القبطي',         en: 'Ethiopic Calendar' },
                { id: 'isoweek',          shortAr: 'رقم الأسبوع',  shortEn: 'ISO Week',   ar: 'رقم الأسبوع القياسي في السنة (ISO)', en: 'ISO Week Number (Week XX)' },
            ]);

        // Page 4: Appearance & World Numerals
        const pageStyle = new Adw.PreferencesPage({ title: tr('المظهر والأرقام', 'Appearance & Numerals'), icon_name: 'applications-graphics-symbolic' });
        window.add(pageStyle);
        

        const grpTheme = new Adw.PreferencesGroup({ title: tr('السمة اللونية وألوان النظام', 'System Theme & Accent Colors') });
        pageStyle.add(grpTheme);

        addModalChoiceRow(grpTheme, 'theme-mode', tr('سمة الساعة والمستطيل', 'Surface Theme Mode'), tr('في الوضع الفاتح تتحول البطاقة للأبيض الثلجي والنصوص والعقارب للداكن تلقائياً', 'Light mode uses frosted white surfaces with deep slate text and hands'), [
            { id: 'system', shortAr: 'تلقائي', shortEn: 'System', ar: 'تلقائي حسب سمة النظام (فاتح / داكن)', en: 'Follow System Style (Light / Dark)' },
            { id: 'dark',   shortAr: 'داكن',   shortEn: 'Dark',   ar: 'داكن دائماً (الافتراضي لشاشة القفل)',  en: 'Always Dark Frosted Glass' },
            { id: 'light',  shortAr: 'فاتح',   shortEn: 'Light',  ar: 'فاتح دائماً (زجاج ثلجي فاتح)',         en: 'Always Light Frosted Glass' },
        ]);

        addSwitch(grpTheme, 'use-system-accent', tr('استخدام لون تمييز النظام تلقائياً (System Accent Color)', 'Follow System Accent Color'), tr('عطّل هذا الخيار لتتمكن من اختيار لون مخصص من القائمة أدناه', 'Disable this switch to unlock the Custom Accent Color picker below'));

        const rowCustomAccent = addModalChoiceRow(grpTheme, 'custom-accent', tr('لون التمييز المخصص', 'Custom Accent Color'), tr('يلوّن عقرب الثواني، ورقم الثواني، ودائرة اليوم النشط، والتاريخ الهجري', 'Applies custom accent color when system accent is off'), [
            { id: 'blue',      shortAr: 'أزرق جنوم', shortEn: 'Blue',      ar: 'أزرق جنوم (Adwaita Blue)', en: 'Adwaita Blue' },
            { id: 'teal',      shortAr: 'فيروزي',    shortEn: 'Teal',      ar: 'تيل / فيروزي (Teal)',      en: 'Teal' },
            { id: 'turquoise', shortAr: 'تركواز',    shortEn: 'Turquoise', ar: 'تركواز داكن (Turquoise)',   en: 'Dark Turquoise' },
            { id: 'burgundy',  shortAr: 'عنابي',     shortEn: 'Burgundy',  ar: 'عنابي (Burgundy)',         en: 'Deep Burgundy' },
            { id: 'silver',    shortAr: 'فضي',       shortEn: 'Silver',    ar: 'فضي نقي (Silver)',          en: 'Pure Silver' },
            { id: 'green',     shortAr: 'أخضر',      shortEn: 'Green',     ar: 'أخضر (Green)',              en: 'Green' },
            { id: 'yellow',    shortAr: 'ذهبي',      shortEn: 'Yellow',    ar: 'أصفر ذهبي (Yellow)',        en: 'Yellow / Gold' },
            { id: 'orange',    shortAr: 'برتقالي',   shortEn: 'Orange',    ar: 'برتقالي (Orange)',          en: 'Orange' },
            { id: 'red',       shortAr: 'أحمر',      shortEn: 'Red',       ar: 'أحمر (Red)',                en: 'Red' },
            { id: 'pink',      shortAr: 'وردي',      shortEn: 'Pink',      ar: 'وردي (Pink)',               en: 'Pink' },
            { id: 'purple',    shortAr: 'أرجواني',   shortEn: 'Purple',    ar: 'أرجواني (Purple)',          en: 'Purple' },
            { id: 'slate',     shortAr: 'صخري',      shortEn: 'Slate',     ar: 'رمادي صخري (Slate)',        en: 'Slate' },
        ]);

        const syncAccentSensitivity = () => { rowCustomAccent.sensitive = !settings.get_boolean('use-system-accent'); };
        syncAccentSensitivity();
        settings.connect('changed::use-system-accent', syncAccentSensitivity);

        addIntSpin(grpTheme, 'card-opacity', tr('شفافية خلفية الساعة والمستطيل (%)', 'Surface Opacity (%)'), tr('اجعلها 0% لإخفاء الخلفية الزجاجية تماماً وترك العقارب والنصوص عائمة', 'Set to 0% for completely transparent floating clock and text'), 0, 100, 5);

        const grpNumerals = new Adw.PreferencesGroup({ title: tr('نظام الأرقام العالمي (22 نظام أرقام)', 'World Numeral Systems (22 Scripts)') });
        pageStyle.add(grpNumerals);
        addModalChoiceRow(grpNumerals, 'numeral-system', tr('شكل الأرقام', 'Numeral System Script'), tr('الأرقام الإنجليزية (0-9) هي الافتراضية، مع دعم الأرقام العربية والآسيوية والأفريقية', 'Western (0-9) is default; supports Arabic, Persian, Indic, Asian, and African numerals'), NUMERAL_OPTIONS);

        // Pin the Live Preview permanently at the top of the window above the scrollable pages
        const viewStack = new Adw.ViewStack();
        const pages = [
            { page: pageClock, name: 'clock', title: pageClock.title, icon: pageClock.icon_name },
            { page: pageWeather, name: 'weather', title: pageWeather.title, icon: pageWeather.icon_name },
            { page: pageDate, name: 'date', title: pageDate.title, icon: pageDate.icon_name },
            { page: pageStyle, name: 'style', title: pageStyle.title, icon: pageStyle.icon_name },
        ];

        for (const p of pages) {
            window.remove(p.page);
            const stackPage = viewStack.add_titled(p.page, p.name, p.title);
            stackPage.set_icon_name(p.icon);
        }

        const headerBar = new Adw.HeaderBar();
        const switcherTitle = new Adw.ViewSwitcherTitle({
            stack: viewStack,
            title: tr('إعدادات ساعة وطقس شاشة القفل', 'Lockscreen Clock & Weather'),
        });
        headerBar.set_title_widget(switcherTitle);

        const pinnedPreviewContainer = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            halign: Gtk.Align.CENTER,
            margin_top: 8,
            margin_bottom: 8,
            margin_start: 16,
            margin_end: 16,
        });
        pinnedPreviewContainer.append(createLivePreviewWidget());

        const sepLine = new Gtk.Separator({
            orientation: Gtk.Orientation.HORIZONTAL,
        });

        viewStack.set_vexpand(true);
        viewStack.set_hexpand(true);

        const mainVerticalBox = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
        });
        mainVerticalBox.append(pinnedPreviewContainer);
        mainVerticalBox.append(sepLine);
        mainVerticalBox.append(viewStack);

        const toolbarView = new Adw.ToolbarView();
        toolbarView.add_top_bar(headerBar);
        toolbarView.set_content(mainVerticalBox);

        window.set_content(toolbarView);

        syncAllPreviewWidgets();
    }
}
