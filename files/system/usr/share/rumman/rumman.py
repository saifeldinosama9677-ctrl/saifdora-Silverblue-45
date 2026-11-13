#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
رُمّان  Rumman  —  اسحب. أسقط. انتهى.   (Drag. Drop. Done.)
==============================================================

What it does / ما يفعله
-----------------------
Drag a file in your file manager, press Shift while dragging, and a wheel of formats that fit the file opens
under the pointer. Drop the file on a slice: the result is saved next to the original.
اسحب ملفًا من مدير الملفات، واضغط Shift أثناء السحب، فتنبثق عجلة من الصيغ المناسبة تحت المؤشر.
أسقط الملف على الصيغة التي تريدها فيُحفظ الناتج بجوار الأصل.

How it reaches every desktop / كيف يعمل على كل البيئات
------------------------------------------------------
One shared core (this file) + one small "backend" per kind of desktop:
  * X11 sessions (Xfce, Cinnamon, MATE, i3 ...)  : built in, nothing to install.
  * GNOME on Wayland                              : the helper extension in gnome-extension/ (installed by install.sh).
  * KDE / Sway / Hyprland ... on Wayland         : a thin strip on the screen edge (needs the library gtk4-layer-shell).
  * everything else                               : `rumman FILE...` (e.g. "Open with") shows the same wheel.
The backends drive the core through plain GApplication actions (see RummanApp), so they stay tiny.

Usage / التشغيل
---------------
    python3 rumman.py                 open the status window (and start the background service)
    python3 rumman.py --service       background service only (what the helper extension starts)
    python3 rumman.py FILE...         show the wheel for these files at the pointer
    python3 rumman.py --diagnose      print a report to paste when something does not work
Requirements: python3-gi, GTK 4, python3-cairo.   (ffmpeg / 7-Zip / poppler ... are optional.)
"""
import os
import sys
import re
import json
import math
import time
import shutil
import signal
import struct
import secrets
import locale
import queue
import tarfile
import zipfile
import gzip
import bz2
import lzma
import threading
import itertools
import collections
import subprocess
import tempfile
import traceback
import urllib.parse
import ctypes
import ctypes.util

# ---- the edge strip needs the small library gtk4-layer-shell (KDE, Sway, Hyprland ... have the protocol; GNOME does not).
#      It must be loaded BEFORE GTK pulls in libwayland-client, so this happens here, before the GTK import below.
LAYER_LIB = None                 # the loaded library, or None
LAYER_LIB_NAME = ""
LAYER_LIB_ERROR = ""


def _wants_layer_shell():
    """Only a Wayland session that is not GNOME can use it (GNOME has no layer-shell; it has the helper extension)."""
    forced = (os.environ.get("RUMMAN_BACKEND", "") or "").strip().lower()
    if forced and forced != "edge":
        return False
    if (os.environ.get("GDK_BACKEND", "") or "").strip().lower() == "x11":
        return False
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or (os.environ.get("XDG_SESSION_TYPE", "") or "").lower() == "wayland"
    if not wayland:
        return False
    desk = (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").lower()
    return forced == "edge" or "gnome" not in desk


def _load_layer_shell():
    global LAYER_LIB, LAYER_LIB_NAME, LAYER_LIB_ERROR
    explicit = (os.environ.get("RUMMAN_LAYER_SHELL_LIB", "") or "").strip()      # a library built by hand somewhere unusual
    if explicit:
        try:
            LAYER_LIB = ctypes.CDLL(os.path.expanduser(explicit), mode=ctypes.RTLD_GLOBAL)
            LAYER_LIB_NAME = explicit
        except OSError as e:
            LAYER_LIB_ERROR = str(e)
        return
    names = ["libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so"]
    for extra in ("/usr/lib64/libgtk4-layer-shell.so.0", "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0",
                  "/usr/lib/libgtk4-layer-shell.so.0", "/usr/local/lib64/libgtk4-layer-shell.so.0", "/usr/local/lib/libgtk4-layer-shell.so.0"):
        if os.path.exists(extra):
            names.append(extra)
    for name in names:
        try:
            LAYER_LIB = ctypes.CDLL(name, mode=ctypes.RTLD_GLOBAL)
            LAYER_LIB_NAME = name
            LAYER_LIB_ERROR = ""
            return
        except OSError as e:
            LAYER_LIB_ERROR = str(e)


if _wants_layer_shell():
    _load_layer_shell()

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, GObject, Pango, PangoCairo
    import cairo
except (ImportError, ValueError) as _e:  # pragma: no cover
    sys.stderr.write(
        "\nمكتبات GTK 4 غير مثبتة / GTK 4 Python bindings are missing:\n  %s\n\n"
        "Fedora        :  sudo dnf install python3-gobject python3-cairo gtk4\n"
        "Debian/Ubuntu :  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0\n"
        "Arch          :  sudo pacman -S python-gobject python-cairo gtk4\n\n" % _e)
    sys.exit(1)

try:                                       # only needed to find the X11 window id on X11 sessions
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11
except (ImportError, ValueError):
    GdkX11 = None

APP_ID = "io.github.Rumman"                # placeholder id: change once the project has a real home
APP_NAME = "Rumman"
APP_NAME_AR = "رُمّان"
APP_VERSION = "0.5.0"
APP_PATH = "/" + APP_ID.replace(".", "/")
SHELL_BUS_NAME = APP_ID + ".Shell"         # owned by the GNOME helper extension
SHELL_BUS_PATH = APP_PATH + "/Shell"
IN_FLATPAK = os.path.exists("/.flatpak-info")


# ============================================================================
#  [1] Languages: Arabic, Turkish, Spanish, Russian, English.   tr("key", args...) -> text
#      A value that is a list is a set of plural forms:
#        Arabic: zero, one, two, few (3-10), many (11-99), other  |  Russian: one, few, many  |  the rest: one, other
#      The texts themselves live in the p01_i18n_<code>.py parts that follow this one.
# ============================================================================
SUPPORTED_LANGS = ("ar", "tr", "es", "ru", "en")
LANG = "en"
STRINGS = {}


def _plural_index(lang, n):
    n = abs(int(n))
    if lang == "ar":
        if n == 0:
            return 0
        if n == 1:
            return 1
        if n == 2:
            return 2
        m = n % 100
        if 3 <= m <= 10:
            return 3
        if 11 <= m <= 99:
            return 4
        return 5
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return 0
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return 1
        return 2
    return 0 if n == 1 else 1


def tr(key, *args):
    val = STRINGS.get(LANG, {}).get(key)
    if val is None:
        val = STRINGS.get("en", {}).get(key, key)
    if isinstance(val, (list, tuple)):
        try:
            idx = _plural_index(LANG if LANG in STRINGS else "en", args[0] if args else 0)
        except (ValueError, TypeError):
            idx = len(val) - 1
        val = val[min(idx, len(val) - 1)]
    if args:
        if LANG == "ar":     # keep file names, sizes... readable inside right-to-left text
            args = tuple(("⁨%s⁩" % a) if (isinstance(a, str) and a) else a for a in args)
        try:
            return val.format(*args)
        except (IndexError, KeyError, ValueError):
            return val
    return val


def ltr(s):
    """Wrap a Latin / number fragment so it keeps its own direction inside Arabic text."""
    return ("⁨%s⁩" % s) if (LANG == "ar" and s) else s


def detect_language():
    """The first language of the system's list that we have a translation for; English when there is none."""
    names = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v:
            names += [x for x in v.split(":") if x]
    try:
        names += list(GLib.get_language_names())
    except Exception:
        pass
    for n in names:
        code = n.split(".")[0].split("@")[0].replace("-", "_").split("_")[0].lower()
        if code in SUPPORTED_LANGS:
            return code
    return "en"


def set_language(code):
    global LANG
    LANG = detect_language() if code == "auto" else (code if code in SUPPORTED_LANGS else "en")


def is_rtl():
    return LANG == "ar"


# ---- العربية -------------------------------------------------------------------------------------------------------
STRINGS["ar"] = {
    "app_name": "رُمّان",
    "tagline": "اسحب. أسقط. انتهى.",
    # the wheel
    "wheel_loading": "جارٍ القراءة…",
    "wheel_no_files": "لا ملفات هنا",
    "wheel_nothing": "لا خيارات لهذا النوع",
    "wheel_busy": "جارٍ العمل…",
    "wheel_failed": "تعذّر التحويل",
    "wheel_nogain": "لا فائدة من الضغط",
    "wheel_cancelled": "أُلغي",
    "no_options": "لا توجد خيارات لهذا النوع من الملفات",
    "no_options_install": "لا توجد خيارات الآن — ثبّت {0} لتفعيلها",
    "drop_nothing": "لم أتمكن من قراءة ملفات محلية من هذا الإفلات",
    "file_missing": "لم أجد هذا الملف على الجهاز",
    "n_items": ["فارغ", "عنصر واحد", "عنصران", "{0} عناصر", "{0} عنصرًا", "{0} عنصر"],
    "n_files": ["لا ملفات", "ملف واحد", "ملفان", "{0} ملفات", "{0} ملفًا", "{0} ملف"],
    "n_outputs": ["لا ملفات", "ملف واحد", "ملفان", "{0} ملفات", "{0} ملفًا", "{0} ملف"],
    "cat_image": "صورة", "cat_video": "فيديو", "cat_audio": "صوت", "cat_pdf": "PDF", "cat_doc": "مستند",
    "cat_text": "نص", "cat_subtitle": "ترجمة", "cat_archive": "أرشيف", "cat_folder": "مجلد", "cat_other": "ملف",
    "cat_mixed": "ملفات متنوعة",
    "cap_same": "نفس الصيغة", "cap_lossless": "بلا فقد", "cap_icon": "أيقونة", "cap_document": "مستند",
    "cap_animation": "متحركة", "cap_audio_only": "صوت فقط", "cap_merge": "دمج في ملف", "cap_universal": "يعمل في كل مكان",
    "cap_strong": "ضغط أقوى", "cap_video": "فيديو", "cap_pages": "كل صفحة", "cap_text": "نص",
    "cap_extract": "فك الضغط", "cap_repack": "إعادة تعبئة",
    "t_merge": "دمج PDF", "t_split": "تقسيم", "a_extract": "فك الضغط",
    # the options page
    "o_compress": "الضغط", "o_size": "الحجم", "o_rotate": "التدوير",
    "lv_none": "بلا", "lv_light": "خفيف", "lv_balanced": "متوازن", "lv_strong": "قوي", "lv_extreme": "أقصى",
    "lvd_none": "أعلى جودة · أكبر حجم",
    "lvd_light": "جودة عالية جدًا · أصغر قليلًا",
    "lvd_balanced": "أفضل توازن بين الجودة والحجم",
    "lvd_strong": "أصغر بوضوح · فقد بسيط في الجودة",
    "lvd_extreme": "أصغر حجم ممكن · الجودة أقل",
    "lvd_none_vid": "بلا إعادة ترميز إن أمكن، فيكون فوريًّا",
    "size_orig": "الأصلي",
    "rot_none": "بدون", "rot_right": "↻ يمين", "rot_left": "↺ يسار",
    "o_strip": "إزالة البيانات الوصفية", "o_strip_sub": "يحذف موقع GPS ومعلومات الكاميرا من الصورة",
    "o_strip_sub_av": "يحذف الوسوم ومعلومات الجهاز والموقع",
    "o_mono": "صوت أحادي (Mono)", "o_mono_sub": "حجم أصغر، مناسب للكلام",
    "o_normalize": "معايرة مستوى الصوت", "o_normalize_sub": "يقرّب علوّ الصوت بين الملفات",
    "o_mute": "كتم الصوت", "o_mute_sub": "يحذف مسار الصوت من الفيديو",
    "o_arc_level": "مستوى الضغط", "arc_fast": "سريع", "arc_normal": "عادي", "arc_max": "أقصى",
    "o_hidden": "تضمين الملفات المخفية", "o_hidden_sub": "الملفات التي يبدأ اسمها بنقطة",
    "o_keep_root": "إبقاء المجلد الرئيسي داخل الأرشيف", "o_keep_root_sub": "عند أرشفة مجلد واحد",
    "btn_run": "تنفيذ", "btn_cancel": "إلغاء", "btn_close": "إغلاق",
    "o_nothing": "اختر ضغطًا أو أداة أولًا، وإلا فلن يتغيّر شيء.",
    "o_save_next": "يُحفظ الناتج بجانب الملف الأصلي", "o_save_to": "يُحفظ الناتج في: {0}",
    "o_working": "جارٍ العمل…", "o_working_n": "جارٍ العمل… {0} من {1}",
    "show_in_folder": "إظهار في المجلد",
    # the status window
    "st_how_title": "كيف تستخدمه",
    "st_how_1": "اسحب ملفًا من مدير الملفات.",
    "st_how_2": "أثناء السحب اضغط Shift، فتظهر العجلة تحت المؤشر.",
    "st_how_3": "أسقط الملف على الصيغة التي تريدها.",
    "st_how_4": "تظهر صفحة صغيرة: اختر الضغط والأدوات إن شئت، ثم اضغط «تنفيذ». يُحفظ الناتج بجوار الأصل.",
    "st_state_title": "حالة جهازك",
    "st_mode_x11": "جلسة X11: السحب مع Shift يعمل مباشرة، ولا تحتاج إلى أي إضافة.",
    "st_mode_gnome_ok": "GNOME: الإضافة المساعدة تعمل، فالسحب مع Shift جاهز.",
    "st_mode_gnome_missing": "GNOME: الإضافة المساعدة غير مفعّلة بعد. شغّل install.sh ثم سجّل الخروج والدخول مرة واحدة.",
    "st_mode_gnome_old": "GNOME: الإضافة المساعدة من النسخة السابقة. سجّل الخروج ثم الدخول مرة واحدة لإكمال التحديث.",
    "st_mode_other": "هذه البيئة لا تسمح للبرنامج بالتقاط السحب تلقائيًا بعد. جرّب زر الصورة التجريبية، أو الأمر: rumman ملف",
    "st_copy_report": "نسخ تقرير التشخيص",
    "st_copied": "نُسخ التقرير. الصقه في المحادثة.",
    "st_demo": "جرّب على صورة تجريبية",
    "st_stop": "إيقاف الخدمة",
    "st_stage": "نسخة تجريبية — المرحلة الرابعة",
    "st_ask": "صفحة الخيارات بعد الإسقاط",
    "st_ask_sub": "اختر الضغط والأدوات قبل التنفيذ. عند الإيقاف يُنفَّذ التحويل فورًا بالإعدادات الافتراضية.",
    "st_lang": "اللغة", "lang_auto": "تلقائي (لغة النظام)",
    # the edge strip (desktops that can show a strip on the screen edge: KDE, Sway, Hyprland, ...)
    "st_how_2_edge": "اسحبه إلى الشريط الرفيع على حافة الشاشة وأبقِه هناك لحظة، فتنفتح العجلة.",
    "st_mode_edge": "شريط الحافة جاهز. اسحب ملفًا إلى الشريط الرفيع على حافة الشاشة {0} وأبقِه هناك لحظة.",
    "st_mode_edge_missing": "هذه البيئة تستطيع إظهار شريط الحافة، لكن المكتبة الصغيرة «gtk4-layer-shell» غير مثبتة. ثبّتها بالأمر: {0} — ثم افتح رُمّان من جديد.",
    "st_mode_edge_missing_generic": "هذه البيئة تستطيع إظهار شريط الحافة، لكن المكتبة الصغيرة «gtk4-layer-shell» غير مثبتة. ثبّت الحزمة «gtk4-layer-shell» من مستودعات توزيعتك، ثم افتح رُمّان من جديد.",
    "st_mode_edge_nosupport": "هذه البيئة لا تقدّم بروتوكول layer-shell، فلا يمكن إظهار شريط الحافة. جرّب زر الصورة التجريبية، أو الأمر: rumman ملف",
    "st_edge": "موضع شريط الحافة",
    "st_edge_sub": "أي حافة من الشاشة يوضع عليها الشريط الرفيع.",
    "edge_right": "اليمنى",
    "edge_left": "اليسرى",
    "edge_top": "العليا",
    "edge_bottom": "السفلى",
    # notifications
    "toast_done_one": "تم: {0}",
    "toast_done_n": ["", "تمّ ملف واحد", "تمّ ملفان", "تمّت معالجة {0} ملفات", "تمّت معالجة {0} ملفًا", "تمّت معالجة {0} ملف"],
    "toast_failed_n": "فشل: {0}",
    # engine messages
    "err_tool_missing": "البرنامج «{0}» غير مثبّت على جهازك",
    "err_not_jpeg": "هذا الملف ليس JPEG صالحًا",
    "err_not_png": "هذا الملف ليس PNG صالحًا",
    "err_decode": "تعذّرت قراءة «{0}» (قد يكون تالفًا أو بصيغة غير مدعومة)",
    "err_no_encoder": "جهازك لا يستطيع الكتابة بصيغة {0} (ينقصه مُرمِّز مناسب)",
    "err_no_output": "لم ينتج البرنامج أي ملف",
    "err_no_gain": "لا فائدة من الضغط: الناتج لم يصبح أصغر من الأصل، فتُرك الملف كما هو",
    "err_no_video": "لا يحتوي الملف على فيديو",
    "err_password": "الأرشيف محميّ بكلمة مرور (غير مدعوم)",
    "err_empty": "لا يوجد ما يمكن معالجته",
    "err_bad_archive": "الأرشيف تالف أو بصيغة غير مدعومة",
    "archive_default_name": "أرشيف",
    # stage 4: settings page, results, extra tools
    'st_settings': 'الإعدادات…',
    'se_title': 'إعدادات رُمّان',
    'se_h_out': 'النتائج',
    'se_where': 'مكان حفظ النتائج',
    'se_where_source': 'بجوار الملف الأصلي',
    'se_where_custom': 'في مجلد أختاره',
    'se_choose': 'اختيار…',
    'se_no_folder': 'لم يُختر مجلد',
    'se_choose_title': 'اختر مجلد حفظ النتائج',
    'se_conflict': 'إن وُجد ملف بالاسم نفسه',
    'se_c_rename': 'إبقاء الاثنين',
    'se_c_replace': 'استبدال القديم',
    'se_delete': 'نقل الأصل إلى سلة المحذوفات',
    'se_delete_sub': 'بعد التأكد من كتابة الناتج فقط. لا يُحذف شيء نهائيًا.',
    'se_h_lvl': 'الضغط: المستوى الذي تبدأ منه صفحة الخيارات',
    'se_lvl_img': 'الصور',
    'se_lvl_aud': 'الصوت',
    'se_lvl_vid': 'الفيديو',
    'se_lvl_pdf': 'PDF',
    'se_lvl_auto': 'تلقائي',
    'se_h_tools': 'الأدوات: الاختيارات الابتدائية',
    'o_trim': 'قصّ الصمت من الطرفين',
    'o_trim_sub': 'يحذف الجزء الصامت من البداية ومن النهاية',
    'se_h_after': 'عند الانتهاء',
    'se_notify': 'إظهار إشعار',
    'se_open': 'فتح الناتج',
    'o_saved': 'وفّرتَ {0} ({1}%)',
    'o_mixed': 'نجح {0} · فشل {1}',
    'btn_undo': 'تراجع',
    'o_undone': 'نُقل الناتج إلى سلة المحذوفات',
    'o_orig_trashed': 'الأصل في سلة المحذوفات',
    't_join': 'دمج',
    'cap_frame': 'لقطة',
    't_rotate': 'تدوير PDF',
    'o_w_source': 'بجانب الأصل',
    'o_w_folder': 'مجلد',
    'o_c_keep': 'احتفظ بالاثنين',
    'o_c_replace': 'استبدل',
    'o_lbl_where': 'الحفظ في',
    'o_lbl_exists': 'إن وُجد',
    'o_this_run': 'هذه الخيارات لهذا التنفيذ فقط. الدائمة في «الإعدادات».',
    'o_sum_trash': 'الأصل إلى السلة',
    'o_sum_replace': 'يستبدل الأسماء المتطابقة',
    'o_sec_out': 'الناتج',
    'o_where_next_sub': 'بجوار كل ملف أصلي',
    'o_c_keep_sub': 'يُضاف رقم إلى الاسم الجديد',
    'o_c_replace_sub': 'يحلّ الجديد محل القديم',
    'o_open_sub': 'يفتحه بالبرنامج الافتراضي عند الانتهاء',
    'o_notify_sub': 'للأعمال الطويلة أو إذا أغلقت هذه الصفحة',
    'o_saved_note': 'اختياراتك تُحفظ تلقائيًا للمرة القادمة (عدا الحجم والتدوير).',
    'pw_title': 'تعذّر فتح نافذة رُمّان',
    'pw_text': 'حدث خطأ غير متوقع أثناء الفتح. اضغط زر النسخ أدناه ثم ألصق التقرير في المحادثة ليُصلَح.',
    'pw_log': 'السجل محفوظ في: {0}',
}


# ---- English -------------------------------------------------------------------------------------------------------
STRINGS["en"] = {
    "app_name": "Rumman",
    "tagline": "Drag. Drop. Done.",
    # the wheel
    "wheel_loading": "Reading…",
    "wheel_no_files": "No files here",
    "wheel_nothing": "No options for this type",
    "wheel_busy": "Working…",
    "wheel_failed": "Could not convert",
    "wheel_nogain": "Nothing to gain",
    "wheel_cancelled": "Cancelled",
    "no_options": "No options for this kind of file",
    "no_options_install": "No options available — install {0} to enable them",
    "drop_nothing": "Couldn't read any local files from this drop",
    "file_missing": "That file could not be found on this computer",
    "n_items": ["{0} item", "{0} items"],
    "n_files": ["{0} file", "{0} files"],
    "n_outputs": ["{0} file created", "{0} files created"],
    "cat_image": "Image", "cat_video": "Video", "cat_audio": "Audio", "cat_pdf": "PDF", "cat_doc": "Document",
    "cat_text": "Text", "cat_subtitle": "Subtitles", "cat_archive": "Archive", "cat_folder": "Folder",
    "cat_other": "File", "cat_mixed": "Mixed files",
    "cap_same": "Same format", "cap_lossless": "Lossless", "cap_icon": "Icon", "cap_document": "Document",
    "cap_animation": "Animated", "cap_audio_only": "Audio only", "cap_merge": "Merge into one",
    "cap_universal": "Works everywhere", "cap_strong": "Stronger", "cap_video": "Video", "cap_pages": "Each page",
    "cap_text": "Text", "cap_extract": "Unpack", "cap_repack": "Repack",
    "t_merge": "Merge PDF", "t_split": "Split", "a_extract": "Extract",
    # the options page
    "o_compress": "Compression", "o_size": "Size", "o_rotate": "Rotate",
    "lv_none": "None", "lv_light": "Light", "lv_balanced": "Medium", "lv_strong": "Strong", "lv_extreme": "Max",
    "lvd_none": "Best quality · largest file",
    "lvd_light": "Very high quality · a little smaller",
    "lvd_balanced": "The best balance of quality and size",
    "lvd_strong": "Clearly smaller · a small loss of quality",
    "lvd_extreme": "Smallest possible · lower quality",
    "lvd_none_vid": "No re-encoding when possible, so it is instant",
    "size_orig": "Original",
    "rot_none": "None", "rot_right": "↻ Right", "rot_left": "↺ Left",
    "o_strip": "Remove metadata", "o_strip_sub": "Erases the GPS location and camera details",
    "o_strip_sub_av": "Erases tags and device / location details",
    "o_mono": "Mono audio", "o_mono_sub": "Smaller files, fine for speech",
    "o_normalize": "Normalize volume", "o_normalize_sub": "Makes loudness similar across files",
    "o_mute": "Mute", "o_mute_sub": "Drops the audio track from the video",
    "o_arc_level": "Compression level", "arc_fast": "Fast", "arc_normal": "Normal", "arc_max": "Smallest",
    "o_hidden": "Include hidden files", "o_hidden_sub": "Files whose name starts with a dot",
    "o_keep_root": "Keep the top folder inside the archive", "o_keep_root_sub": "When archiving a single folder",
    "btn_run": "Run", "btn_cancel": "Cancel", "btn_close": "Close",
    "o_nothing": "Pick a compression or a tool first, otherwise nothing would change.",
    "o_save_next": "The result is saved next to the original file", "o_save_to": "The result is saved in: {0}",
    "o_working": "Working…", "o_working_n": "Working… {0} of {1}",
    "show_in_folder": "Show in folder",
    # the status window
    "st_how_title": "How to use it",
    "st_how_1": "Drag a file from your file manager.",
    "st_how_2": "While dragging, press Shift: the wheel opens under the pointer.",
    "st_how_3": "Drop the file on the format you want.",
    "st_how_4": "A small page appears: pick compression and tools if you like, then press “Run”. The result is saved next to the original.",
    "st_state_title": "Your system",
    "st_mode_x11": "X11 session: dragging with Shift works directly, no add-on needed.",
    "st_mode_gnome_ok": "GNOME: the helper extension is running, so dragging with Shift is ready.",
    "st_mode_gnome_missing": "GNOME: the helper extension is not enabled yet. Run install.sh, then log out and back in once.",
    "st_mode_gnome_old": "GNOME: the helper extension is from the previous version. Log out and back in once to finish the update.",
    "st_mode_other": "This desktop does not let the app catch drags automatically yet. Try the demo button, or run: rumman FILE",
    "st_copy_report": "Copy diagnostics",
    "st_copied": "Report copied. Paste it in the chat.",
    "st_demo": "Try a demo image",
    "st_stop": "Stop the service",
    "st_stage": "Preview — stage 4",
    "st_ask": "Options page after a drop",
    "st_ask_sub": "Choose compression and tools before running. When off, a drop converts at once with the default choices.",
    "st_lang": "Language", "lang_auto": "Automatic (system language)",
    # the edge strip (desktops that can show a strip on the screen edge: KDE, Sway, Hyprland, ...)
    "st_how_2_edge": "Drag it to the thin strip on the edge of the screen and hold it there for a moment: the wheel opens.",
    "st_mode_edge": "Edge strip is ready. Drag a file to the thin strip on the screen edge ({0}) and hold it there for a moment.",
    "st_mode_edge_missing": "This desktop can show the edge strip, but the small library “gtk4-layer-shell” is missing. Install it with: {0} — then open Rumman again.",
    "st_mode_edge_missing_generic": "This desktop can show the edge strip, but the small library “gtk4-layer-shell” is missing. Install the package “gtk4-layer-shell” from your distribution, then open Rumman again.",
    "st_mode_edge_nosupport": "This desktop does not offer the layer-shell protocol, so the edge strip cannot be shown. Try the demo button, or run: rumman FILE",
    "st_edge": "Edge strip position",
    "st_edge_sub": "Which edge of the screen holds the thin strip.",
    "edge_right": "Right",
    "edge_left": "Left",
    "edge_top": "Top",
    "edge_bottom": "Bottom",
    # notifications
    "toast_done_one": "Done: {0}",
    "toast_done_n": ["Done: {0} file", "Done: {0} files"],
    "toast_failed_n": "Failed: {0}",
    # engine messages
    "err_tool_missing": "“{0}” isn't installed on this computer",
    "err_not_jpeg": "This isn't a valid JPEG file",
    "err_not_png": "This isn't a valid PNG file",
    "err_decode": "Couldn't read “{0}” (it may be damaged or in an unsupported format)",
    "err_no_encoder": "This computer can't write {0} (a required encoder is missing)",
    "err_no_output": "The program didn't produce any file",
    "err_no_gain": "Not worth compressing: the result wasn't smaller than the original, so it was left alone",
    "err_no_video": "This file has no video",
    "err_password": "The archive is password-protected (not supported)",
    "err_empty": "Nothing to process",
    "err_bad_archive": "The archive is damaged or in an unsupported format",
    "archive_default_name": "Archive",
    # stage 4: settings page, results, extra tools
    'st_settings': 'Settings…',
    'se_title': 'Rumman settings',
    'se_h_out': 'Results',
    'se_where': 'Save results',
    'se_where_source': 'Next to the original',
    'se_where_custom': 'In a folder I choose',
    'se_choose': 'Choose…',
    'se_no_folder': 'No folder chosen',
    'se_choose_title': 'Choose a folder for the results',
    'se_conflict': 'If the name already exists',
    'se_c_rename': 'Keep both',
    'se_c_replace': 'Replace the old one',
    'se_delete': 'Move the original to the trash',
    'se_delete_sub': 'Only after the result is safely written. Nothing is deleted for good.',
    'se_h_lvl': 'Compression: where the options page starts',
    'se_lvl_img': 'Images',
    'se_lvl_aud': 'Audio',
    'se_lvl_vid': 'Video',
    'se_lvl_pdf': 'PDF',
    'se_lvl_auto': 'Automatic',
    'se_h_tools': 'Tools: starting choices',
    'o_trim': 'Trim silence at the ends',
    'o_trim_sub': 'Cuts the silent parts at the start and the end',
    'se_h_after': 'When it finishes',
    'se_notify': 'Show a notification',
    'se_open': 'Open the result',
    'o_saved': 'Saved {0} ({1}%)',
    'o_mixed': '{0} done · {1} failed',
    'btn_undo': 'Undo',
    'o_undone': 'The result was moved to the trash',
    'o_orig_trashed': 'the original is in the trash',
    't_join': 'Merge',
    'cap_frame': 'Still frame',
    't_rotate': 'Rotate PDF',
    'o_w_source': 'Next to original',
    'o_w_folder': 'A folder',
    'o_c_keep': 'Keep both',
    'o_c_replace': 'Replace',
    'o_lbl_where': 'Save in',
    'o_lbl_exists': 'If it exists',
    'o_this_run': 'These choices apply to this run only. The permanent ones are in Settings.',
    'o_sum_trash': 'original to trash',
    'o_sum_replace': 'replaces same names',
    'o_sec_out': 'Result',
    'o_where_next_sub': 'Next to each original file',
    'o_c_keep_sub': 'A number is added to the new name',
    'o_c_replace_sub': "The new file takes the old one's place",
    'o_open_sub': 'Opens it in the default app when done',
    'o_notify_sub': 'For long jobs, or if you close this page',
    'o_saved_note': 'Your choices are remembered for next time (except size and rotation).',
    'pw_title': 'Rumman could not open its window',
    'pw_text': 'Something unexpected went wrong while opening. Press the copy button below and paste the report in the chat, so it can be fixed.',
    'pw_log': 'The log is saved in: {0}',
}


# ---- Türkçe --------------------------------------------------------------------------------------------------------
STRINGS["tr"] = {
    "app_name": "Rumman",
    "tagline": "Sürükle. Bırak. Bitti.",
    # the wheel
    "wheel_loading": "Okunuyor…",
    "wheel_no_files": "Burada dosya yok",
    "wheel_nothing": "Bu tür için seçenek yok",
    "wheel_busy": "Çalışıyor…",
    "wheel_failed": "Dönüştürülemedi",
    "wheel_nogain": "Kazanç yok",
    "wheel_cancelled": "İptal edildi",
    "no_options": "Bu dosya türü için seçenek yok",
    "no_options_install": "Şu an seçenek yok — etkinleştirmek için {0} kurun",
    "drop_nothing": "Bu bırakmadan yerel dosya okunamadı",
    "file_missing": "Bu dosya bilgisayarda bulunamadı",
    "n_items": ["{0} öğe", "{0} öğe"],
    "n_files": ["{0} dosya", "{0} dosya"],
    "n_outputs": ["{0} dosya oluşturuldu", "{0} dosya oluşturuldu"],
    "cat_image": "Resim", "cat_video": "Video", "cat_audio": "Ses", "cat_pdf": "PDF", "cat_doc": "Belge",
    "cat_text": "Metin", "cat_subtitle": "Altyazı", "cat_archive": "Arşiv", "cat_folder": "Klasör", "cat_other": "Dosya",
    "cat_mixed": "Karışık dosyalar",
    "cap_same": "Aynı biçim", "cap_lossless": "Kayıpsız", "cap_icon": "Simge", "cap_document": "Belge",
    "cap_animation": "Hareketli", "cap_audio_only": "Yalnızca ses", "cap_merge": "Tek dosyada birleştir",
    "cap_universal": "Her yerde çalışır", "cap_strong": "Daha güçlü", "cap_video": "Video", "cap_pages": "Her sayfa",
    "cap_text": "Metin", "cap_extract": "Çıkar", "cap_repack": "Yeniden paketle",
    "t_merge": "PDF birleştir", "t_split": "Böl", "a_extract": "Arşivden çıkar",
    # the options page
    "o_compress": "Sıkıştırma", "o_size": "Boyut", "o_rotate": "Döndür",
    "lv_none": "Yok", "lv_light": "Hafif", "lv_balanced": "Orta", "lv_strong": "Güçlü", "lv_extreme": "Azami",
    "lvd_none": "En yüksek kalite · en büyük dosya",
    "lvd_light": "Çok yüksek kalite · biraz daha küçük",
    "lvd_balanced": "Kalite ve boyut arasında en iyi denge",
    "lvd_strong": "Belirgin şekilde küçük · hafif kalite kaybı",
    "lvd_extreme": "Mümkün olan en küçük boyut · daha düşük kalite",
    "lvd_none_vid": "Mümkünse yeniden kodlamadan, yani anında",
    "size_orig": "Orijinal",
    "rot_none": "Yok", "rot_right": "↻ Sağa", "rot_left": "↺ Sola",
    "o_strip": "Meta verileri kaldır", "o_strip_sub": "GPS konumunu ve kamera bilgilerini siler",
    "o_strip_sub_av": "Etiketleri ve cihaz / konum bilgilerini siler",
    "o_mono": "Mono ses", "o_mono_sub": "Daha küçük dosya, konuşma için yeterli",
    "o_normalize": "Ses seviyesini eşitle", "o_normalize_sub": "Dosyalar arasında ses yüksekliğini yakınlaştırır",
    "o_mute": "Sesi kapat", "o_mute_sub": "Videodaki ses kanalını siler",
    "o_arc_level": "Sıkıştırma düzeyi", "arc_fast": "Hızlı", "arc_normal": "Normal", "arc_max": "En küçük",
    "o_hidden": "Gizli dosyaları ekle", "o_hidden_sub": "Adı nokta ile başlayan dosyalar",
    "o_keep_root": "Üst klasörü arşivde tut", "o_keep_root_sub": "Tek bir klasör arşivlenirken",
    "btn_run": "Çalıştır", "btn_cancel": "İptal", "btn_close": "Kapat",
    "o_nothing": "Önce bir sıkıştırma ya da araç seçin, yoksa hiçbir şey değişmez.",
    "o_save_next": "Sonuç özgün dosyanın yanına kaydedilir", "o_save_to": "Sonuç şuraya kaydedilir: {0}",
    "o_working": "Çalışıyor…", "o_working_n": "Çalışıyor… {0}/{1}",
    "show_in_folder": "Klasörde göster",
    # the status window
    "st_how_title": "Nasıl kullanılır",
    "st_how_1": "Dosya yöneticinizden bir dosya sürükleyin.",
    "st_how_2": "Sürüklerken Shift'e basın: tekerlek imlecin altında açılır.",
    "st_how_3": "Dosyayı istediğiniz biçimin üzerine bırakın.",
    "st_how_4": "Küçük bir sayfa açılır: isterseniz sıkıştırma ve araçları seçin, sonra «Çalıştır»a basın. Sonuç özgün dosyanın yanına kaydedilir.",
    "st_state_title": "Sisteminiz",
    "st_mode_x11": "X11 oturumu: Shift ile sürükleme doğrudan çalışır, eklenti gerekmez.",
    "st_mode_gnome_ok": "GNOME: yardımcı eklenti çalışıyor, Shift ile sürükleme hazır.",
    "st_mode_gnome_missing": "GNOME: yardımcı eklenti henüz etkin değil. install.sh çalıştırın, sonra bir kez oturumu kapatıp açın.",
    "st_mode_gnome_old": "GNOME: yardımcı eklenti önceki sürümden. Güncellemeyi tamamlamak için bir kez oturumu kapatıp açın.",
    "st_mode_other": "Bu masaüstü uygulamanın sürüklemeleri kendiliğinden yakalamasına henüz izin vermiyor. Demo düğmesini deneyin ya da şunu çalıştırın: rumman DOSYA",
    "st_copy_report": "Raporu kopyala",
    "st_copied": "Rapor kopyalandı. Sohbete yapıştırın.",
    "st_demo": "Örnek resimle dene",
    "st_stop": "Hizmeti durdur",
    "st_stage": "Deneme sürümü — 4. aşama",
    "st_ask": "Bırakınca seçenekler sayfası",
    "st_ask_sub": "Çalıştırmadan önce sıkıştırma ve araçları seçin. Kapalıyken dönüştürme varsayılan seçimlerle hemen yapılır.",
    "st_lang": "Dil", "lang_auto": "Otomatik (sistem dili)",
    # the edge strip (desktops that can show a strip on the screen edge: KDE, Sway, Hyprland, ...)
    "st_how_2_edge": "Dosyayı ekranın kenarındaki ince şeride sürükleyin ve orada kısa bir an bekletin: tekerlek açılır.",
    "st_mode_edge": "Kenar şeridi hazır. Bir dosyayı ekranın kenarındaki ince şeride ({0}) sürükleyin ve orada kısa bir an bekletin.",
    "st_mode_edge_missing": "Bu masaüstü kenar şeridini gösterebilir, ancak “gtk4-layer-shell” adlı küçük kitaplık kurulu değil. Şu komutla kurun: {0} — sonra Rumman'ı yeniden açın.",
    "st_mode_edge_missing_generic": "Bu masaüstü kenar şeridini gösterebilir, ancak “gtk4-layer-shell” adlı küçük kitaplık kurulu değil. Dağıtımınızdan “gtk4-layer-shell” paketini kurun, sonra Rumman'ı yeniden açın.",
    "st_mode_edge_nosupport": "Bu masaüstü layer-shell protokolünü sunmuyor, bu yüzden kenar şeridi gösterilemez. Demo düğmesini deneyin ya da şunu çalıştırın: rumman DOSYA",
    "st_edge": "Kenar şeridinin yeri",
    "st_edge_sub": "İnce şeridin ekranın hangi kenarında duracağı.",
    "edge_right": "Sağ",
    "edge_left": "Sol",
    "edge_top": "Üst",
    "edge_bottom": "Alt",
    # notifications
    "toast_done_one": "Bitti: {0}",
    "toast_done_n": ["Bitti: {0} dosya", "Bitti: {0} dosya"],
    "toast_failed_n": "Başarısız: {0}",
    # engine messages
    "err_tool_missing": "“{0}” bu bilgisayarda kurulu değil",
    "err_not_jpeg": "Bu geçerli bir JPEG dosyası değil",
    "err_not_png": "Bu geçerli bir PNG dosyası değil",
    "err_decode": "“{0}” okunamadı (bozuk ya da desteklenmeyen bir biçimde olabilir)",
    "err_no_encoder": "Bu bilgisayar {0} yazamıyor (gerekli kodlayıcı eksik)",
    "err_no_output": "Program hiçbir dosya üretmedi",
    "err_no_gain": "Sıkıştırmaya değmez: sonuç özgününden küçük olmadı, bu yüzden dosyaya dokunulmadı",
    "err_no_video": "Bu dosyada video yok",
    "err_password": "Arşiv parola korumalı (desteklenmiyor)",
    "err_empty": "İşlenecek bir şey yok",
    "err_bad_archive": "Arşiv bozuk ya da desteklenmeyen bir biçimde",
    "archive_default_name": "Arşiv",
    # stage 4: settings page, results, extra tools
    'st_settings': 'Ayarlar…',
    'se_title': 'Rumman ayarları',
    'se_h_out': 'Sonuçlar',
    'se_where': 'Sonuçların yeri',
    'se_where_source': 'Orijinalin yanına',
    'se_where_custom': 'Seçtiğim klasöre',
    'se_choose': 'Seç…',
    'se_no_folder': 'Klasör seçilmedi',
    'se_choose_title': 'Sonuçlar için bir klasör seç',
    'se_conflict': 'Aynı adlı dosya varsa',
    'se_c_rename': 'İkisini de tut',
    'se_c_replace': 'Eskisinin yerine yaz',
    'se_delete': 'Orijinali çöpe taşı',
    'se_delete_sub': 'Yalnızca sonuç güvenle yazıldıktan sonra. Hiçbir şey kalıcı olarak silinmez.',
    'se_h_lvl': 'Sıkıştırma: seçenek sayfası hangi düzeyde başlasın',
    'se_lvl_img': 'Resimler',
    'se_lvl_aud': 'Ses',
    'se_lvl_vid': 'Video',
    'se_lvl_pdf': 'PDF',
    'se_lvl_auto': 'Otomatik',
    'se_h_tools': 'Araçlar: başlangıç seçimleri',
    'o_trim': 'Baştaki ve sondaki sessizliği kes',
    'o_trim_sub': 'Başlangıç ve sondaki sessiz kısımları siler',
    'se_h_after': 'Bittiğinde',
    'se_notify': 'Bildirim göster',
    'se_open': 'Sonucu aç',
    'o_saved': '{0} tasarruf ({1}%)',
    'o_mixed': '{0} tamam · {1} başarısız',
    'btn_undo': 'Geri al',
    'o_undone': 'Sonuç çöpe taşındı',
    'o_orig_trashed': 'orijinal çöpte',
    't_join': 'Birleştir',
    'cap_frame': 'Kare',
    't_rotate': 'PDF döndür',
    'o_w_source': 'Özgün yanında',
    'o_w_folder': 'Klasör',
    'o_c_keep': 'İkisini de tut',
    'o_c_replace': 'Değiştir',
    'o_lbl_where': 'Kaydet',
    'o_lbl_exists': 'Aynı ad varsa',
    'o_this_run': "Bu seçimler yalnızca bu işlem içindir. Kalıcı olanlar Ayarlar'dadır.",
    'o_sum_trash': 'özgün çöpe',
    'o_sum_replace': 'aynı adlıların yerine yazar',
    'o_sec_out': 'Sonuç',
    'o_where_next_sub': 'Her özgün dosyanın yanında',
    'o_c_keep_sub': 'Yeni ada bir sayı eklenir',
    'o_c_replace_sub': 'Yeni dosya eskisinin yerine geçer',
    'o_open_sub': 'Bitince varsayılan uygulamada açar',
    'o_notify_sub': 'Uzun işler için ya da bu sayfayı kapatırsanız',
    'o_saved_note': 'Seçimleriniz bir sonraki sefer için hatırlanır (boyut ve döndürme hariç).',
    'pw_title': 'Rumman penceresi açılamadı',
    'pw_text': 'Açılırken beklenmedik bir hata oldu. Aşağıdaki kopyala düğmesine basıp raporu sohbete yapıştırın, böylece düzeltilebilir.',
    'pw_log': 'Kayıt şurada: {0}',
}


# ---- Español -------------------------------------------------------------------------------------------------------
STRINGS["es"] = {
    "app_name": "Rumman",
    "tagline": "Arrastra. Suelta. Listo.",
    # the wheel
    "wheel_loading": "Leyendo…",
    "wheel_no_files": "No hay archivos aquí",
    "wheel_nothing": "Sin opciones para este tipo",
    "wheel_busy": "Trabajando…",
    "wheel_failed": "No se pudo convertir",
    "wheel_nogain": "Nada que ganar",
    "wheel_cancelled": "Cancelado",
    "no_options": "No hay opciones para este tipo de archivo",
    "no_options_install": "No hay opciones ahora — instala {0} para activarlas",
    "drop_nothing": "No pude leer archivos locales de este arrastre",
    "file_missing": "No se encontró ese archivo en este equipo",
    "n_items": ["{0} elemento", "{0} elementos"],
    "n_files": ["{0} archivo", "{0} archivos"],
    "n_outputs": ["{0} archivo creado", "{0} archivos creados"],
    "cat_image": "Imagen", "cat_video": "Vídeo", "cat_audio": "Audio", "cat_pdf": "PDF", "cat_doc": "Documento",
    "cat_text": "Texto", "cat_subtitle": "Subtítulos", "cat_archive": "Archivo comprimido", "cat_folder": "Carpeta",
    "cat_other": "Archivo", "cat_mixed": "Archivos variados",
    "cap_same": "Mismo formato", "cap_lossless": "Sin pérdida", "cap_icon": "Icono", "cap_document": "Documento",
    "cap_animation": "Animado", "cap_audio_only": "Solo audio", "cap_merge": "Unir en uno",
    "cap_universal": "Funciona en todas partes", "cap_strong": "Más compresión", "cap_video": "Vídeo",
    "cap_pages": "Cada página", "cap_text": "Texto", "cap_extract": "Descomprimir", "cap_repack": "Reempaquetar",
    "t_merge": "Unir PDF", "t_split": "Dividir", "a_extract": "Descomprimir",
    # the options page
    "o_compress": "Compresión", "o_size": "Tamaño", "o_rotate": "Giro",
    "lv_none": "Ninguna", "lv_light": "Suave", "lv_balanced": "Media", "lv_strong": "Fuerte", "lv_extreme": "Máxima",
    "lvd_none": "La mejor calidad · el archivo más grande",
    "lvd_light": "Calidad muy alta · algo más pequeño",
    "lvd_balanced": "El mejor equilibrio entre calidad y tamaño",
    "lvd_strong": "Mucho más pequeño · pequeña pérdida de calidad",
    "lvd_extreme": "El tamaño más pequeño posible · menos calidad",
    "lvd_none_vid": "Sin recodificar si es posible, así que es instantáneo",
    "size_orig": "Original",
    "rot_none": "Ninguno", "rot_right": "↻ Der.", "rot_left": "↺ Izq.",
    "o_strip": "Quitar metadatos", "o_strip_sub": "Borra la ubicación GPS y los datos de la cámara",
    "o_strip_sub_av": "Borra etiquetas y datos del dispositivo y de la ubicación",
    "o_mono": "Audio mono", "o_mono_sub": "Archivos más pequeños, suficiente para la voz",
    "o_normalize": "Normalizar el volumen", "o_normalize_sub": "Iguala el volumen entre archivos",
    "o_mute": "Silenciar", "o_mute_sub": "Elimina la pista de audio del vídeo",
    "o_arc_level": "Nivel de compresión", "arc_fast": "Rápido", "arc_normal": "Normal", "arc_max": "Máximo",
    "o_hidden": "Incluir archivos ocultos", "o_hidden_sub": "Los que empiezan por un punto",
    "o_keep_root": "Conservar la carpeta principal dentro del archivo", "o_keep_root_sub": "Al comprimir una sola carpeta",
    "btn_run": "Ejecutar", "btn_cancel": "Cancelar", "btn_close": "Cerrar",
    "o_nothing": "Elige primero una compresión o una herramienta; si no, nada cambiará.",
    "o_save_next": "El resultado se guarda junto al archivo original", "o_save_to": "El resultado se guarda en: {0}",
    "o_working": "Trabajando…", "o_working_n": "Trabajando… {0} de {1}",
    "show_in_folder": "Mostrar en la carpeta",
    # the status window
    "st_how_title": "Cómo se usa",
    "st_how_1": "Arrastra un archivo desde tu gestor de archivos.",
    "st_how_2": "Mientras arrastras, pulsa Mayús: la rueda se abre bajo el puntero.",
    "st_how_3": "Suelta el archivo sobre el formato que quieras.",
    "st_how_4": "Aparece una página pequeña: elige compresión y herramientas si quieres y pulsa «Ejecutar». El resultado se guarda junto al original.",
    "st_state_title": "Tu sistema",
    "st_mode_x11": "Sesión X11: arrastrar con Mayús funciona directamente, sin extensiones.",
    "st_mode_gnome_ok": "GNOME: la extensión auxiliar está activa, así que arrastrar con Mayús está listo.",
    "st_mode_gnome_missing": "GNOME: la extensión auxiliar aún no está activada. Ejecuta install.sh y cierra e inicia sesión una vez.",
    "st_mode_gnome_old": "GNOME: la extensión auxiliar es de la versión anterior. Cierra e inicia sesión una vez para terminar la actualización.",
    "st_mode_other": "Este escritorio todavía no deja que la aplicación detecte los arrastres automáticamente. Prueba el botón de demostración o ejecuta: rumman ARCHIVO",
    "st_copy_report": "Copiar diagnóstico",
    "st_copied": "Informe copiado. Pégalo en el chat.",
    "st_demo": "Imagen de ejemplo",
    "st_stop": "Detener el servicio",
    "st_stage": "Versión de prueba — fase 4",
    "st_ask": "Página de opciones tras soltar",
    "st_ask_sub": "Elige compresión y herramientas antes de ejecutar. Si se desactiva, la conversión se hace al instante con los valores por defecto.",
    "st_lang": "Idioma", "lang_auto": "Automático (idioma del sistema)",
    # the edge strip (desktops that can show a strip on the screen edge: KDE, Sway, Hyprland, ...)
    "st_how_2_edge": "Arrástralo hasta la franja fina del borde de la pantalla y mantenlo ahí un momento: la rueda se abre.",
    "st_mode_edge": "La franja del borde está lista. Arrastra un archivo hasta la franja fina del borde de la pantalla ({0}) y mantenlo ahí un momento.",
    "st_mode_edge_missing": "Este escritorio puede mostrar la franja del borde, pero falta la pequeña biblioteca «gtk4-layer-shell». Instálala con: {0} — y vuelve a abrir Rumman.",
    "st_mode_edge_missing_generic": "Este escritorio puede mostrar la franja del borde, pero falta la pequeña biblioteca «gtk4-layer-shell». Instala el paquete «gtk4-layer-shell» de tu distribución y vuelve a abrir Rumman.",
    "st_mode_edge_nosupport": "Este escritorio no ofrece el protocolo layer-shell, así que no se puede mostrar la franja del borde. Prueba el botón de demostración o ejecuta: rumman ARCHIVO",
    "st_edge": "Posición de la franja",
    "st_edge_sub": "En qué borde de la pantalla se coloca la franja fina.",
    "edge_right": "Derecho",
    "edge_left": "Izquierdo",
    "edge_top": "Superior",
    "edge_bottom": "Inferior",
    # notifications
    "toast_done_one": "Listo: {0}",
    "toast_done_n": ["Listo: {0} archivo", "Listo: {0} archivos"],
    "toast_failed_n": "Error: {0}",
    # engine messages
    "err_tool_missing": "«{0}» no está instalado en este equipo",
    "err_not_jpeg": "Este no es un archivo JPEG válido",
    "err_not_png": "Este no es un archivo PNG válido",
    "err_decode": "No se pudo leer «{0}» (puede estar dañado o en un formato no admitido)",
    "err_no_encoder": "Este equipo no puede escribir {0} (falta un codificador)",
    "err_no_output": "El programa no produjo ningún archivo",
    "err_no_gain": "No compensa comprimir: el resultado no fue más pequeño que el original, así que se dejó tal cual",
    "err_no_video": "Este archivo no contiene vídeo",
    "err_password": "El archivo está protegido con contraseña (no admitido)",
    "err_empty": "No hay nada que procesar",
    "err_bad_archive": "El archivo está dañado o en un formato no admitido",
    "archive_default_name": "Archivo",
    # stage 4: settings page, results, extra tools
    'st_settings': 'Ajustes…',
    'se_title': 'Ajustes de Rumman',
    'se_h_out': 'Resultados',
    'se_where': 'Dónde guardar',
    'se_where_source': 'Junto al original',
    'se_where_custom': 'En una carpeta que elija',
    'se_choose': 'Elegir…',
    'se_no_folder': 'Ninguna carpeta elegida',
    'se_choose_title': 'Elige la carpeta de los resultados',
    'se_conflict': 'Si el nombre ya existe',
    'se_c_rename': 'Conservar ambos',
    'se_c_replace': 'Reemplazar el anterior',
    'se_delete': 'Mover el original a la papelera',
    'se_delete_sub': 'Solo cuando el resultado ya está guardado. Nada se borra para siempre.',
    'se_h_lvl': 'Compresión: nivel inicial de la página de opciones',
    'se_lvl_img': 'Imágenes',
    'se_lvl_aud': 'Audio',
    'se_lvl_vid': 'Vídeo',
    'se_lvl_pdf': 'PDF',
    'se_lvl_auto': 'Automático',
    'se_h_tools': 'Herramientas: opciones iniciales',
    'o_trim': 'Recortar el silencio de los extremos',
    'o_trim_sub': 'Quita las partes mudas del principio y del final',
    'se_h_after': 'Al terminar',
    'se_notify': 'Mostrar una notificación',
    'se_open': 'Abrir el resultado',
    'o_saved': 'Ahorro de {0} ({1} %)',
    'o_mixed': '{0} listos · {1} con error',
    'btn_undo': 'Deshacer',
    'o_undone': 'El resultado se movió a la papelera',
    'o_orig_trashed': 'el original está en la papelera',
    't_join': 'Unir',
    'cap_frame': 'Fotograma',
    't_rotate': 'Girar PDF',
    'o_w_source': 'Junto al original',
    'o_w_folder': 'Una carpeta',
    'o_c_keep': 'Conservar ambos',
    'o_c_replace': 'Reemplazar',
    'o_lbl_where': 'Guardar en',
    'o_lbl_exists': 'Si existe',
    'o_this_run': 'Estas opciones valen solo para esta vez. Las permanentes están en Ajustes.',
    'o_sum_trash': 'original a la papelera',
    'o_sum_replace': 'reemplaza los del mismo nombre',
    'o_sec_out': 'Resultado',
    'o_where_next_sub': 'Junto a cada archivo original',
    'o_c_keep_sub': 'Se añade un número al nombre nuevo',
    'o_c_replace_sub': 'El nuevo ocupa el lugar del anterior',
    'o_open_sub': 'Lo abre con la aplicación predeterminada',
    'o_notify_sub': 'Para trabajos largos o si cierras esta página',
    'o_saved_note': 'Tus elecciones se recuerdan la próxima vez (salvo tamaño y giro).',
    'pw_title': 'Rumman no pudo abrir su ventana',
    'pw_text': 'Algo inesperado falló al abrir. Pulsa el botón de copiar de abajo y pega el informe en el chat para poder arreglarlo.',
    'pw_log': 'El registro está en: {0}',
}


# ---- Русский -------------------------------------------------------------------------------------------------------
STRINGS["ru"] = {
    "app_name": "Rumman",
    "tagline": "Перетащи. Брось. Готово.",
    # the wheel
    "wheel_loading": "Чтение…",
    "wheel_no_files": "Здесь нет файлов",
    "wheel_nothing": "Для этого типа нет вариантов",
    "wheel_busy": "Работаю…",
    "wheel_failed": "Не удалось преобразовать",
    "wheel_nogain": "Нет выгоды",
    "wheel_cancelled": "Отменено",
    "no_options": "Для этого типа файлов нет вариантов",
    "no_options_install": "Сейчас вариантов нет — установите {0}, чтобы включить их",
    "drop_nothing": "Не удалось получить локальные файлы из этого перетаскивания",
    "file_missing": "Этот файл не найден на компьютере",
    "n_items": ["{0} элемент", "{0} элемента", "{0} элементов"],
    "n_files": ["{0} файл", "{0} файла", "{0} файлов"],
    "n_outputs": ["Создан {0} файл", "Создано {0} файла", "Создано {0} файлов"],
    "cat_image": "Изображение", "cat_video": "Видео", "cat_audio": "Аудио", "cat_pdf": "PDF", "cat_doc": "Документ",
    "cat_text": "Текст", "cat_subtitle": "Субтитры", "cat_archive": "Архив", "cat_folder": "Папка", "cat_other": "Файл",
    "cat_mixed": "Разные файлы",
    "cap_same": "Тот же формат", "cap_lossless": "Без потерь", "cap_icon": "Значок", "cap_document": "Документ",
    "cap_animation": "Анимация", "cap_audio_only": "Только звук", "cap_merge": "Объединить в один",
    "cap_universal": "Работает везде", "cap_strong": "Сильнее сжатие", "cap_video": "Видео", "cap_pages": "Каждая страница",
    "cap_text": "Текст", "cap_extract": "Распаковать", "cap_repack": "Перепаковать",
    "t_merge": "Объединить PDF", "t_split": "Разделить", "a_extract": "Распаковать",
    # the options page
    "o_compress": "Сжатие", "o_size": "Размер", "o_rotate": "Поворот",
    "lv_none": "Нет", "lv_light": "Слабое", "lv_balanced": "Среднее", "lv_strong": "Сильное", "lv_extreme": "Макс.",
    "lvd_none": "Лучшее качество · самый большой файл",
    "lvd_light": "Очень высокое качество · чуть меньше",
    "lvd_balanced": "Лучший баланс качества и размера",
    "lvd_strong": "Заметно меньше · небольшая потеря качества",
    "lvd_extreme": "Наименьший размер · качество ниже",
    "lvd_none_vid": "Без перекодирования, если возможно, — мгновенно",
    "size_orig": "Оригинал",
    "rot_none": "Нет", "rot_right": "↻ Вправо", "rot_left": "↺ Влево",
    "o_strip": "Удалить метаданные", "o_strip_sub": "Стирает координаты GPS и данные камеры",
    "o_strip_sub_av": "Стирает теги, данные устройства и места съёмки",
    "o_mono": "Моно-звук", "o_mono_sub": "Файлы меньше, подходит для речи",
    "o_normalize": "Выровнять громкость", "o_normalize_sub": "Сближает громкость разных файлов",
    "o_mute": "Отключить звук", "o_mute_sub": "Удаляет звуковую дорожку из видео",
    "o_arc_level": "Уровень сжатия", "arc_fast": "Быстро", "arc_normal": "Обычно", "arc_max": "Максимум",
    "o_hidden": "Включить скрытые файлы", "o_hidden_sub": "Файлы, имя которых начинается с точки",
    "o_keep_root": "Сохранить корневую папку в архиве", "o_keep_root_sub": "При архивации одной папки",
    "btn_run": "Выполнить", "btn_cancel": "Отмена", "btn_close": "Закрыть",
    "o_nothing": "Сначала выберите сжатие или инструмент, иначе ничего не изменится.",
    "o_save_next": "Результат сохраняется рядом с исходным файлом", "o_save_to": "Результат сохраняется в: {0}",
    "o_working": "Работаю…", "o_working_n": "Работаю… {0} из {1}",
    "show_in_folder": "Показать в папке",
    # the status window
    "st_how_title": "Как пользоваться",
    "st_how_1": "Перетащите файл из файлового менеджера.",
    "st_how_2": "Во время перетаскивания нажмите Shift — колесо появится под указателем.",
    "st_how_3": "Бросьте файл на нужный формат.",
    "st_how_4": "Появится небольшая страница: при желании выберите сжатие и инструменты и нажмите «Выполнить». Результат сохраняется рядом с оригиналом.",
    "st_state_title": "Ваша система",
    "st_mode_x11": "Сеанс X11: перетаскивание с Shift работает сразу, расширения не нужны.",
    "st_mode_gnome_ok": "GNOME: вспомогательное расширение работает, перетаскивание с Shift готово.",
    "st_mode_gnome_missing": "GNOME: вспомогательное расширение ещё не включено. Запустите install.sh и один раз выйдите из сеанса и войдите снова.",
    "st_mode_gnome_old": "GNOME: вспомогательное расширение от прежней версии. Один раз выйдите из сеанса и войдите снова, чтобы завершить обновление.",
    "st_mode_other": "Эта среда пока не позволяет программе перехватывать перетаскивание автоматически. Попробуйте кнопку демонстрации или команду: rumman ФАЙЛ",
    "st_copy_report": "Копировать отчёт",
    "st_copied": "Отчёт скопирован. Вставьте его в чат.",
    "st_demo": "Демо-картинка",
    "st_stop": "Остановить службу",
    "st_stage": "Пробная версия — этап 4",
    "st_ask": "Страница выбора после броска",
    "st_ask_sub": "Выберите сжатие и инструменты перед запуском. Если выключить, преобразование выполняется сразу с настройками по умолчанию.",
    "st_lang": "Язык", "lang_auto": "Автоматически (язык системы)",
    # the edge strip (desktops that can show a strip on the screen edge: KDE, Sway, Hyprland, ...)
    "st_how_2_edge": "Перетащите его на тонкую полосу у края экрана и задержите там на мгновение — колесо откроется.",
    "st_mode_edge": "Полоса у края готова. Перетащите файл на тонкую полосу у края экрана ({0}) и задержите там на мгновение.",
    "st_mode_edge_missing": "Эта среда может показывать полосу у края, но не установлена маленькая библиотека «gtk4-layer-shell». Установите её командой: {0} — и снова откройте Rumman.",
    "st_mode_edge_missing_generic": "Эта среда может показывать полосу у края, но не установлена маленькая библиотека «gtk4-layer-shell». Установите пакет «gtk4-layer-shell» из репозиториев вашего дистрибутива и снова откройте Rumman.",
    "st_mode_edge_nosupport": "Эта среда не поддерживает протокол layer-shell, поэтому полосу у края показать нельзя. Попробуйте кнопку демонстрации или команду: rumman ФАЙЛ",
    "st_edge": "Положение полосы",
    "st_edge_sub": "У какого края экрана находится тонкая полоса.",
    "edge_right": "Правый",
    "edge_left": "Левый",
    "edge_top": "Верхний",
    "edge_bottom": "Нижний",
    # notifications
    "toast_done_one": "Готово: {0}",
    "toast_done_n": ["Готово: {0} файл", "Готово: {0} файла", "Готово: {0} файлов"],
    "toast_failed_n": "Ошибка: {0}",
    # engine messages
    "err_tool_missing": "«{0}» не установлена на этом компьютере",
    "err_not_jpeg": "Это не допустимый файл JPEG",
    "err_not_png": "Это не допустимый файл PNG",
    "err_decode": "Не удалось прочитать «{0}» (файл может быть повреждён или иметь неподдерживаемый формат)",
    "err_no_encoder": "Этот компьютер не может записывать {0} (нет нужного кодировщика)",
    "err_no_output": "Программа не создала ни одного файла",
    "err_no_gain": "Сжимать нет смысла: результат не стал меньше оригинала, поэтому файл оставлен как есть",
    "err_no_video": "В этом файле нет видео",
    "err_password": "Архив защищён паролем (не поддерживается)",
    "err_empty": "Обрабатывать нечего",
    "err_bad_archive": "Архив повреждён или имеет неподдерживаемый формат",
    "archive_default_name": "Архив",
    # stage 4: settings page, results, extra tools
    'st_settings': 'Настройки…',
    'se_title': 'Настройки Rumman',
    'se_h_out': 'Результаты',
    'se_where': 'Куда сохранять',
    'se_where_source': 'Рядом с оригиналом',
    'se_where_custom': 'В выбранную папку',
    'se_choose': 'Выбрать…',
    'se_no_folder': 'Папка не выбрана',
    'se_choose_title': 'Выберите папку для результатов',
    'se_conflict': 'Если имя уже занято',
    'se_c_rename': 'Оставить оба',
    'se_c_replace': 'Заменить старый',
    'se_delete': 'Переносить оригинал в корзину',
    'se_delete_sub': 'Только после надёжной записи результата. Ничего не удаляется навсегда.',
    'se_h_lvl': 'Сжатие: уровень по умолчанию на странице параметров',
    'se_lvl_img': 'Изображения',
    'se_lvl_aud': 'Аудио',
    'se_lvl_vid': 'Видео',
    'se_lvl_pdf': 'PDF',
    'se_lvl_auto': 'Авто',
    'se_h_tools': 'Инструменты: значения по умолчанию',
    'o_trim': 'Обрезать тишину по краям',
    'o_trim_sub': 'Убирает тихие участки в начале и в конце',
    'se_h_after': 'По завершении',
    'se_notify': 'Показывать уведомление',
    'se_open': 'Открывать результат',
    'o_saved': 'Экономия {0} ({1}%)',
    'o_mixed': 'готово {0} · ошибок {1}',
    'btn_undo': 'Отменить',
    'o_undone': 'Результат перенесён в корзину',
    'o_orig_trashed': 'оригинал в корзине',
    't_join': 'Склеить',
    'cap_frame': 'Кадр',
    't_rotate': 'Повернуть PDF',
    'o_w_source': 'Рядом',
    'o_w_folder': 'Папка',
    'o_c_keep': 'Оставить оба',
    'o_c_replace': 'Заменить',
    'o_lbl_where': 'Куда',
    'o_lbl_exists': 'Если есть',
    'o_this_run': 'Эти параметры действуют только в этот раз. Постоянные — в настройках.',
    'o_sum_trash': 'оригинал в корзину',
    'o_sum_replace': 'заменяет файлы с тем же именем',
    'o_sec_out': 'Результат',
    'o_where_next_sub': 'Рядом с каждым исходным файлом',
    'o_c_keep_sub': 'К новому имени добавляется номер',
    'o_c_replace_sub': 'Новый файл заменит старый',
    'o_open_sub': 'Откроет в приложении по умолчанию',
    'o_notify_sub': 'Для долгих задач или если закрыть страницу',
    'o_saved_note': 'Выбор запоминается на следующий раз (кроме размера и поворота).',
    'pw_title': 'Rumman не смог открыть окно',
    'pw_text': 'При открытии произошла непредвиденная ошибка. Нажмите кнопку копирования ниже и вставьте отчёт в чат, чтобы это исправили.',
    'pw_log': 'Журнал сохранён здесь: {0}',
}
# ---------------------------------------------------------------------------------------------------------------------
# What the program says about itself to the desktop: the name, the one-line summary and the long description in the five
# languages. They go into the launcher entry (the app grid, search) and into the AppStream file (the software centre).
# ---------------------------------------------------------------------------------------------------------------------
META = {
    "en": {
        "name": "Rumman",
        "generic": "File converter and compressor",
        "summary": "Convert and compress files by dropping them",
        "intro": "Rumman is a small file converter and compressor for the Linux desktop. Take any file, start dragging it, and a "
                 "wheel opens right under the pointer: drop the file on the action you want and it is done. Nothing to open, "
                 "no menus to search.",
        "intro2": "It works with images, audio, video, archives and PDF files, shows the progress of every file, lets you stop "
                  "one file on its own, tells you how much space you saved and can take a result back with one click.",
        "points": ["Images: convert between JPG, PNG, WebP, AVIF and more; shrink, resize and clean metadata",
                   "Audio and video: convert, extract the sound, merge audio files, trim silence, take a still frame",
                   "Archives and PDF: pack, unpack, merge, split, rotate and compress",
                   "A settings page: where results go, what happens when a name exists, default quality, original to the trash",
                   "Five languages: Arabic, Turkish, Spanish, Russian and English"],
        "keywords": ["convert", "compress", "image", "video", "audio", "pdf", "zip"],
    },
    "ar": {
        "name": "رُمّان",
        "generic": "محوّل ومضغّط ملفات",
        "summary": "حوّل الملفات واضغطها بمجرد إسقاطها",
        "intro": "رُمّان محوّل ومضغّط ملفات صغير لسطح مكتب لينكس. أمسك أي ملف وابدأ بسحبه فتنفتح عجلة تحت المؤشر مباشرة: "
                 "أسقط الملف على ما تريد فيتم الأمر. لا برنامج تفتحه ولا قوائم تبحث فيها.",
        "intro2": "يعمل مع الصور والصوت والفيديو والأرشيف وملفات PDF، ويُظهر تقدّم كل ملف، ويتيح إيقاف ملف واحد وحده، ويخبرك "
                  "بالمساحة التي وفّرتها، ويمكنك التراجع عن النتيجة بنقرة واحدة.",
        "points": ["الصور: تحويل بين JPG وPNG وWebP وAVIF وغيرها، وتصغير وتغيير المقاس وإزالة البيانات الوصفية",
                   "الصوت والفيديو: تحويل واستخراج الصوت ودمج ملفات صوتية وقص الصمت والتقاط لقطة ثابتة",
                   "الأرشيف وPDF: ضغط وفكّ ودمج وتقسيم وتدوير وتصغير",
                   "صفحة إعدادات: مكان الحفظ، وما يحدث إن وُجد الاسم، والجودة الافتراضية، ونقل الأصل إلى سلة المحذوفات",
                   "خمس لغات: العربية والتركية والإسبانية والروسية والإنجليزية"],
        "keywords": ["تحويل", "ضغط", "صور", "فيديو", "صوت", "pdf"],
    },
    "tr": {
        "name": "Rumman",
        "generic": "Dosya dönüştürücü ve sıkıştırıcı",
        "summary": "Dosyaları bırakarak dönüştür ve sıkıştır",
        "intro": "Rumman, Linux masaüstü için küçük bir dosya dönüştürücü ve sıkıştırıcıdır. Bir dosyayı sürüklemeye başlayın, "
                 "işaretçinin hemen altında bir çark açılır: dosyayı istediğiniz işlemin üzerine bırakın, iş biter. Açılacak "
                 "bir program, aranacak bir menü yok.",
        "intro2": "Görüntü, ses, video, arşiv ve PDF dosyalarıyla çalışır, her dosyanın ilerlemesini gösterir, tek bir dosyayı "
                  "ayrıca durdurmanıza izin verir, ne kadar yer kazandığınızı söyler ve sonucu tek tıkla geri alabilirsiniz.",
        "points": ["Görüntüler: JPG, PNG, WebP, AVIF ve diğerleri arasında dönüştürme; küçültme, yeniden boyutlandırma, üstveri temizleme",
                   "Ses ve video: dönüştürme, sesi ayırma, ses dosyalarını birleştirme, sessizliği kırpma, sabit kare alma",
                   "Arşiv ve PDF: paketleme, açma, birleştirme, bölme, döndürme ve sıkıştırma",
                   "Ayar sayfası: sonuçların yeri, aynı ad varsa ne olacağı, varsayılan kalite, orijinali çöpe taşıma",
                   "Beş dil: Arapça, Türkçe, İspanyolca, Rusça ve İngilizce"],
        "keywords": ["dönüştür", "sıkıştır", "görüntü", "video", "ses", "pdf"],
    },
    "es": {
        "name": "Rumman",
        "generic": "Conversor y compresor de archivos",
        "summary": "Convierte y comprime archivos soltándolos",
        "intro": "Rumman es un pequeño conversor y compresor de archivos para el escritorio Linux. Empieza a arrastrar un "
                 "archivo y se abre una rueda justo bajo el puntero: suéltalo sobre la acción que quieras y listo. Sin abrir "
                 "programas ni buscar en menús.",
        "intro2": "Funciona con imágenes, audio, vídeo, archivos comprimidos y PDF, muestra el progreso de cada archivo, permite "
                  "detener uno solo, te dice cuánto espacio has ahorrado y puedes deshacer el resultado con un clic.",
        "points": ["Imágenes: conversión entre JPG, PNG, WebP, AVIF y más; reducir, cambiar tamaño y limpiar metadatos",
                   "Audio y vídeo: convertir, extraer el sonido, unir audios, recortar silencios, sacar un fotograma",
                   "Archivos y PDF: empaquetar, extraer, unir, dividir, girar y comprimir",
                   "Página de ajustes: dónde van los resultados, qué pasa si el nombre ya existe, calidad por defecto, original a la papelera",
                   "Cinco idiomas: árabe, turco, español, ruso e inglés"],
        "keywords": ["convertir", "comprimir", "imagen", "vídeo", "audio", "pdf"],
    },
    "ru": {
        "name": "Rumman",
        "generic": "Конвертер и архиватор файлов",
        "summary": "Конвертируйте и сжимайте файлы перетаскиванием",
        "intro": "Rumman — небольшой конвертер и архиватор файлов для рабочего стола Linux. Начните перетаскивать файл, и "
                 "прямо под указателем откроется колесо: бросьте файл на нужное действие — и всё готово. Не нужно открывать "
                 "программу и искать в меню.",
        "intro2": "Работает с изображениями, звуком, видео, архивами и PDF, показывает ход обработки каждого файла, позволяет "
                  "остановить один файл отдельно, сообщает, сколько места вы сэкономили, а результат можно вернуть одним щелчком.",
        "points": ["Изображения: преобразование между JPG, PNG, WebP, AVIF и другими; уменьшение, изменение размера, очистка метаданных",
                   "Звук и видео: конвертация, извлечение звука, объединение аудио, обрезка тишины, снимок кадра",
                   "Архивы и PDF: упаковка, распаковка, объединение, разделение, поворот и сжатие",
                   "Страница настроек: куда сохранять, что делать при совпадении имени, качество по умолчанию, оригинал в корзину",
                   "Пять языков: арабский, турецкий, испанский, русский и английский"],
        "keywords": ["конвертер", "сжатие", "изображение", "видео", "звук", "pdf"],
    },
}
META_ORDER = ["en", "ar", "tr", "es", "ru"]


# ============================================================================
#  [2] Utilities: logging, settings, paths, external tools, Job, subprocess
# ============================================================================
DEBUG = bool(os.environ.get("RUMMAN_DEBUG"))


def dbg(*a):
    if DEBUG:
        print("[rumman]", *a, file=sys.stderr, flush=True)


def log_error(text):
    """Append an error to ~/.cache/rumman/log.txt (useful when launched from a menu)."""
    try:
        d = os.path.join(GLib.get_user_cache_dir(), "rumman")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "log.txt")
        if os.path.exists(p) and os.path.getsize(p) > 512 * 1024:
            os.replace(p, p + ".old")
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + str(text).rstrip() + "\n")
    except Exception:
        pass


class Cancelled(Exception):
    """Raised inside a worker when the user cancels the job."""


class ConvError(Exception):
    """An error whose message is meant to be shown to the user."""


class ToolMissing(ConvError):
    def __init__(self, tool):
        super().__init__(tr("err_tool_missing", tool))
        self.tool = tool


# ---------------------------------------------------------------- settings --
DEFAULT_SETTINGS = {
    "language": "auto",      # auto | ar | tr | es | ru | en
    "ask_options": True,     # show the options page after a drop (off: the drop runs at once with the default choices)
    "archive_level": 6,      # 1..9   (the starting point of the options page)
    "include_hidden": True,
    "keep_root": True,
    "mono": False,
    "normalize": False,
    "mute": False,
    "strip_meta": False,
    "out_mode": "source",    # source | custom
    "out_dir": "",
    "notify": True,
    "open_after": False,
    "conflict": "rename",    # rename | replace: what to do when a file of the same name is already there
    "delete_original": False,   # move the original to the trash once its result is safely written
    "lvl_img": "auto",       # auto | none | light | balanced | strong | extreme: the compression level the options page starts at
    "lvl_aud": "auto",
    "lvl_vid": "auto",
    "lvl_pdf": "auto",
    "trim_silence": False,   # audio: cut the silence at both ends
    "edge": "right",         # right | left | top | bottom: the edge that holds the strip (desktops without Shift-drag)
}


class Settings:
    def __init__(self):
        self.path = os.path.join(GLib.get_user_config_dir(), "rumman", "settings.json")
        self.data = dict(DEFAULT_SETTINGS)
        self._save_id = 0
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            for k, dv in DEFAULT_SETTINGS.items():
                if k in obj and isinstance(obj[k], type(dv)) and not (isinstance(dv, int) and isinstance(obj[k], bool) and not isinstance(dv, bool)):
                    self.data[k] = obj[k]
        except Exception:
            pass

    def get(self, key):
        return self.data.get(key, DEFAULT_SETTINGS[key])

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(350, self._flush)

    def _flush(self):
        self._save_id = 0
        self.save()
        return False

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            dbg("settings save failed", e)

    def snapshot(self):
        return dict(self.data)


SETTINGS = None  # created in main()


# -------------------------------------------------------------------- paths --
COMPOUND_EXTS = (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tar.lz")


def split_name(path):
    """('photo', 'jpg') — understands .tar.gz style double extensions. Folders get ''."""
    base = os.path.basename(str(path).rstrip("/")) or str(path)
    low = base.lower()
    for c in COMPOUND_EXTS:
        if low.endswith(c) and len(base) > len(c):
            return base[:-len(c)], c[1:]
    if os.path.isdir(path):
        return base, ""
    stem, ext = os.path.splitext(base)
    if ext and stem:
        return stem, ext[1:].lower()
    return base, ""


def unique_path(directory, stem, ext=""):
    ext = ("." + ext.lstrip(".")) if ext else ""
    cand = os.path.join(directory, stem + ext)
    n = 1
    while os.path.lexists(cand):
        n += 1
        cand = os.path.join(directory, "%s (%d)%s" % (stem, n, ext))
    return cand


def writable_dir(d):
    return bool(d) and os.path.isdir(d) and os.access(d, os.W_OK | os.X_OK)


def fallback_dir():
    for kind in (GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_DOCUMENTS):
        p = GLib.get_user_special_dir(kind)
        if p and writable_dir(p):
            return p
    return os.path.expanduser("~")


def out_dir_for(src, opts):
    """Where the result of converting `src` goes: next to the original, or the chosen folder."""
    if opts.get("out_mode") == "custom" and writable_dir(opts.get("out_dir", "")):
        return opts["out_dir"]
    d = os.path.dirname(os.path.abspath(src))
    return d if writable_dir(d) else fallback_dir()


def trash_path(path):
    """Move a file or folder to the trash (never deletes for good). True when it worked."""
    try:
        Gio.File.new_for_path(path).trash(None)
        return True
    except Exception as e:
        dbg("trash failed", path, e)
        return False


def run_summary(jobs):
    """What a finished drop came to: results, failures, the space saved, whether "undo" would be honest."""
    done = [j for j in jobs if j.state == "done"]
    errors = [j for j in jobs if j.state == "error"]
    outs = [o for j in done for o in j.outputs]
    in_b = sum(j.in_bytes for j in done)
    out_b = sum(j.out_bytes for j in done)
    saved = in_b - out_b
    pct = int(round(100.0 * saved / in_b)) if in_b > 0 else 0
    trashed = [f for j in done for f in j.trashed]
    saved_txt = tr("o_saved", human_size(saved), pct) if (saved >= 10 * 1024 and pct >= 1) else ""
    long_run = any(j.t_end and j.t_end - j.t_start >= 8.0 for j in jobs)
    return {"done": done, "errors": errors, "outs": outs, "saved_txt": saved_txt, "trashed": trashed, "long": long_run,
            # undo removes the results; it cannot bring back an original that went to the trash or a file that was replaced
            "can_undo": bool(outs) and not trashed and all(j.opts.get("conflict") != "replace" for j in done)}


def human_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    if n < 1024:
        return "%d B" % n
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return ("%.1f %s" % (n, unit)) if n < 100 else ("%d %s" % (round(n), unit))
    return "?"


def path_size(path):
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def common_base(paths):
    paths = [os.path.abspath(p) for p in paths]
    if len(paths) == 1:
        return os.path.dirname(paths[0])
    try:
        return os.path.commonpath([os.path.dirname(p) for p in paths])
    except ValueError:
        return "/"


# -------------------------------------------------------------------- tools --
class Tools:
    """Finds external programs once (ffmpeg, 7z, ...) and knows what they can do."""
    _found = {}
    _encoders = None
    _magick_formats = None
    _lock = threading.Lock()

    @classmethod
    def find(cls, *names):
        if names not in cls._found:
            cls._found[names] = next((p for p in (shutil.which(n) for n in names) if p), None)
        return cls._found[names]

    @classmethod
    def ffmpeg(cls):
        return cls.find("ffmpeg")

    @classmethod
    def ffprobe(cls):
        return cls.find("ffprobe")

    @classmethod
    def magick(cls):
        return cls.find("magick", "convert")

    @classmethod
    def gs(cls):
        return cls.find("gs")

    @classmethod
    def sevenzip(cls):
        return cls.find("7zz", "7z", "7za", "7zr")

    @classmethod
    def soffice(cls):
        return cls.find("soffice", "libreoffice")

    @classmethod
    def pandoc(cls):
        return cls.find("pandoc")

    @classmethod
    def ffmpeg_encoders(cls):
        with cls._lock:
            if cls._encoders is None:
                names = set()
                ff = cls.ffmpeg()
                if ff:
                    try:
                        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True,
                                             text=True, timeout=20).stdout
                        for line in out.splitlines():
                            m = re.match(r"^\s([VAS])[.\w]{5}\s+(\S+)", line)
                            if m:
                                names.add(m.group(2))
                    except Exception:
                        pass
                cls._encoders = names
            return cls._encoders

    @classmethod
    def has_encoder(cls, name):
        return name in cls.ffmpeg_encoders()

    @classmethod
    def magick_formats(cls):
        with cls._lock:
            if cls._magick_formats is None:
                fm = {}
                im = cls.magick()
                if im:
                    try:
                        out = subprocess.run([im, "-list", "format"], capture_output=True, text=True,
                                             timeout=30).stdout
                        for line in out.splitlines():
                            m = re.match(r"^\s*([A-Za-z0-9_-]+)\*?\s+\S+\s+([r-][w-][+-])\s", line)
                            if m:
                                fm[m.group(1).upper()] = m.group(2)
                    except Exception:
                        pass
                cls._magick_formats = fm
            return cls._magick_formats

    @classmethod
    def magick_can(cls, fmt, write=False):
        mode = cls.magick_formats().get(fmt.upper())
        if not mode:
            return False
        return mode[1] == "w" if write else mode[0] == "r"

    @classmethod
    def pixbuf_writable(cls):
        return {f.get_name() for f in GdkPixbuf.Pixbuf.get_formats() if f.is_writable()}

    @classmethod
    def warm_up(cls):
        """Called in a background thread at start-up so that dragging never waits for these probes."""
        try:
            cls.ffmpeg_encoders()
            cls.magick_formats()
        except Exception:
            pass


# ---------------------------------------------------------------- Job object --
PROGRESS_RE = re.compile(r"^(frame|fps|stream_\d+_\d+_q|bitrate|total_size|out_time_us|out_time_ms|out_time|"
                         r"dup_frames|drop_frames|speed|progress)=")


# ---------------------------------------------- leftovers of a killed run --
TEMP_NAME_RE = re.compile(r"^\.rumman-[0-9a-f]{8}(\..{1,12})?$")


def _journal_path():
    return os.path.join(GLib.get_user_cache_dir(), "rumman", "temp-files.txt")


def journal_temp(path):
    """Write a temporary file's name down, so that a crash or a kill can't leave it behind for good."""
    if "\n" in path:
        return
    try:
        jp = _journal_path()
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        if os.path.getsize(jp) > 512 * 1024:                     # a very long session: keep only what still exists
            with open(jp, "r", encoding="utf-8", errors="replace") as f:
                keep = sorted({l.rstrip("\n") for l in f if os.path.lexists(l.rstrip("\n"))})
            with open(jp, "w", encoding="utf-8") as f:
                f.write("".join(k + "\n" for k in keep))
    except OSError:
        pass
    try:
        with open(_journal_path(), "a", encoding="utf-8") as f:
            f.write(path + "\n")
    except OSError:
        pass


def sweep_stale_temps():
    """At start-up: delete the hidden temporary files an earlier run left behind because it was killed."""
    jp = _journal_path()
    old = jp + ".old"
    try:
        os.replace(jp, old)                                       # new jobs write to a fresh journal meanwhile
        with open(old, "r", encoding="utf-8", errors="replace") as f:
            lines = [l.rstrip("\n") for l in f]
    except OSError:
        return 0
    n = 0
    for p in sorted(set(lines)):
        if not p or not os.path.isabs(p) or not TEMP_NAME_RE.match(os.path.basename(p)):
            continue                                              # never touch anything that is not ours
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
                n += 1
            elif os.path.lexists(p):
                os.remove(p)
                n += 1
        except OSError:
            pass
    try:
        os.remove(old)
    except OSError:
        pass
    dbg("swept stale temp files:", n)
    return n


class Job:
    """One unit of work shown as a row in the list (one file, or one archive of many files)."""
    _counter = itertools.count(1)

    def __init__(self, files, action, opts):
        self.id = next(Job._counter)
        self.files = list(files)
        self.action = action
        self.opts = dict(opts)
        self.state = "queued"          # queued | running | done | error | cancelled
        self.progress = 0.0            # 0..1, or None = unknown
        self.error = ""
        self.outputs = []
        self.in_bytes = 0
        self.out_bytes = 0
        self.cancelled = False
        self.proc = None
        self.on_update = None
        self._tmp = []
        self._last_emit = 0.0
        self.t_start = 0.0
        self.t_end = 0.0
        self.trashed = []              # originals that were moved to the trash after the result was written

    # --- control
    def cancel(self):
        self.cancelled = True
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass

    def check(self):
        if self.cancelled:
            raise Cancelled()

    # --- progress
    def report(self, frac, force=False):
        now = time.monotonic()
        self.progress = None if frac is None else max(0.0, min(1.0, frac))
        if not force and now - self._last_emit < 0.12:
            return
        self._last_emit = now
        self.emit()

    def emit(self):
        cb = self.on_update
        if cb:
            GLib.idle_add(cb, self)

    # --- temp files: we always write to a hidden temp name first, then rename
    def new_tmp(self, directory, ext=""):
        name = ".rumman-%s%s" % (secrets.token_hex(4), ("." + ext.lstrip(".")) if ext else "")
        p = os.path.join(directory, name)
        self._tmp.append(p)
        journal_temp(p)
        return p

    def target(self, directory, stem, ext=""):
        """The path a result will get: a free name, or (when asked to) the name of the file that is already there.
        Never an input of this job, a folder, or a link: those are not replaced."""
        free = unique_path(directory, stem, ext)
        if self.opts.get("conflict") != "replace":
            return free
        want = os.path.join(directory, stem + (("." + ext.lstrip(".")) if ext else ""))
        if want == free or not os.path.isfile(want) or os.path.islink(want):
            return free
        real = os.path.realpath(want)
        if any(os.path.realpath(f) == real for f in self.files):
            return free
        return want

    def commit(self, tmp, directory, stem, ext=""):
        final = self.target(directory, stem, ext)
        os.replace(tmp, final)
        if tmp in self._tmp:
            self._tmp.remove(tmp)
        return final

    def cleanup(self):
        for p in list(self._tmp):
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.lexists(p):
                    os.remove(p)
            except OSError:
                pass
        self._tmp = []


# ------------------------------------------------------------- subprocesses --
def run_cmd(job, argv, cwd=None, on_line=None, env=None, ok_codes=(0,)):
    """Run a program, stream its output, support cancel. Raises ConvError on failure."""
    job.check()
    dbg("run:", " ".join(str(a) for a in argv))
    try:
        p = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, cwd=cwd, env=env, text=True, errors="replace",
                             bufsize=1, start_new_session=True)
    except FileNotFoundError:
        raise ToolMissing(os.path.basename(str(argv[0])))
    job.proc = p
    if job.cancelled:
        job.cancel()
    tail = collections.deque(maxlen=30)
    try:
        for line in p.stdout:
            line = re.sub(r"[\x00-\x08\x0b-\x1f]", "", line.rstrip("\r\n"))
            if on_line is not None and on_line(line):
                continue
            if line.strip():
                tail.append(line)
        rc = p.wait()
    finally:
        job.proc = None
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
    job.check()
    if rc not in ok_codes:
        msg = "\n".join(tail)[-700:] or ("exit code %d" % rc)
        raise ConvError(msg)


def probe(path):
    """{'duration': float|None, 'streams': [{'codec_type','codec_name','width','height'}]}"""
    res = {"duration": None, "streams": []}
    ff = Tools.ffprobe()
    if not ff:
        return res
    try:
        out = subprocess.run([ff, "-v", "error", "-show_entries",
                              "stream=codec_type,codec_name,width,height,channels:format=duration", "-of", "json", path],
                             capture_output=True, text=True, timeout=40).stdout
        obj = json.loads(out or "{}")
        res["streams"] = obj.get("streams", [])
        d = obj.get("format", {}).get("duration")
        if d not in (None, "N/A"):
            res["duration"] = float(d)
    except Exception:
        pass
    return res


def run_ffmpeg(job, args, duration=None):
    ff = Tools.ffmpeg()
    if not ff:
        raise ToolMissing("ffmpeg")
    argv = [ff, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"] + list(args)

    def on_line(line):
        if not PROGRESS_RE.match(line):
            return False
        if line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                us = int(line.split("=", 1)[1])
                if duration and duration > 0:
                    job.report(min(0.99, us / 1e6 / duration))
            except ValueError:
                pass
        return True

    run_cmd(job, argv, on_line=on_line)


# ============================================================================
#  [3] Images  (GdkPixbuf in-process first; ImageMagick / ffmpeg as fallbacks)
# ============================================================================
IMG_EXTS = {"png", "jpg", "jpeg", "jpe", "webp", "bmp", "tif", "tiff", "gif", "ico", "svg", "svgz", "heic",
            "heif", "avif", "jxl", "tga", "pnm", "ppm", "pgm", "jp2", "icns", "qoi"}
JPEG_EXTS = {"jpg", "jpeg", "jpe"}
SCALE_FACTORS = (1.0, 0.75, 0.5, 0.25)


class NoGain(ConvError):
    """Compression produced a file that is not smaller than the original."""


class DecodeError(ConvError):
    pass


# ------------------------------------------------------ JPEG / PNG metadata --
def _find_exif_segment(data):
    """(offset, total_length) of the Exif APP1 segment, or None."""
    if data[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xDA, 0xD9):
            break
        if m == 0x01 or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if seglen < 2:
            break
        if m == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            return i, 2 + seglen
        i += 2 + seglen
    return None


def _exif_orientation_entry(buf, tiff_start):
    """Absolute index (inside buf) of the 2-byte Orientation value, and byte order."""
    t = buf[tiff_start:]
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return None, None
    bo = "little" if t[:2] == b"II" else "big"
    ifd = int.from_bytes(t[4:8], bo)
    if ifd + 2 > len(t):
        return None, None
    for k in range(int.from_bytes(t[ifd:ifd + 2], bo)):
        e = ifd + 2 + k * 12
        if e + 12 > len(t):
            break
        if int.from_bytes(t[e:e + 2], bo) == 0x0112:
            return tiff_start + e + 8, bo
    return None, None


def exif_orientation(data):
    seg = _find_exif_segment(data)
    if not seg:
        return None
    off, ln = seg
    buf = data[off:off + ln]
    idx, bo = _exif_orientation_entry(buf, 10)
    if idx is None:
        return None
    return int.from_bytes(buf[idx:idx + 2], bo)


def exif_segment_normalized(data):
    """The original Exif block with Orientation reset to 1 (the pixels are already rotated)."""
    seg = _find_exif_segment(data)
    if not seg:
        return None
    off, ln = seg
    buf = bytearray(data[off:off + ln])
    idx, bo = _exif_orientation_entry(bytes(buf), 10)
    if idx is not None:
        buf[idx:idx + 2] = (1).to_bytes(2, bo)
    return bytes(buf)


def jpeg_strip_metadata(data):
    """Lossless: drops Exif/XMP/IPTC/comments, keeps JFIF, ICC profile and Adobe marker."""
    if data[:2] != b"\xff\xd8":
        raise DecodeError(tr("err_not_jpeg"))
    out = bytearray(b"\xff\xd8")
    i, n = 2, len(data)
    while i + 2 <= n:
        if data[i] != 0xFF:
            out += data[i:]
            break
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD9, 0xDA):
            out += data[i:]
            break
        if m == 0x01 or 0xD0 <= m <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if seglen < 2:
            out += data[i:]
            break
        drop = m == 0xE1 or 0xE3 <= m <= 0xED or m == 0xEF or m == 0xFE
        if not drop:
            out += data[i:i + 2 + seglen]
        i += 2 + seglen
    return bytes(out)


def jpeg_insert_exif(jpeg, exif_seg):
    """Insert an Exif APP1 block right after SOI/JFIF."""
    if not exif_seg or jpeg[:2] != b"\xff\xd8":
        return jpeg
    pos = 2
    if jpeg[2:4] == b"\xff\xe0":
        pos = 4 + int.from_bytes(jpeg[4:6], "big")
    return jpeg[:pos] + exif_seg + jpeg[pos:]


def png_strip_metadata(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DecodeError(tr("err_not_png"))
    out = bytearray(data[:8])
    i = 8
    while i + 8 <= len(data):
        ln = int.from_bytes(data[i:i + 4], "big")
        typ = data[i + 4:i + 8]
        end = i + 12 + ln
        if typ not in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"):
            out += data[i:end]
        i = end
        if typ == b"IEND":
            break
    return bytes(out)


# ------------------------------------------------------------ pixbuf helpers --
def img_load(path):
    """Decode with GdkPixbuf and apply the EXIF rotation (phone photos!)."""
    ext = split_name(path)[1]
    try:
        if ext in ("svg", "svgz"):
            _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
            w, h = (w or 512), (h or 512)
            k = min(4.0, 1024.0 / max(w, h)) if max(w, h) < 1024 else 1.0
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, int(w * k), int(h * k), True)
        else:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error as e:
        raise DecodeError(str(e.message if hasattr(e, "message") else e))
    if pb is None:
        raise DecodeError("decode failed")
    return pb.apply_embedded_orientation() or pb


def load_any(job, src, directory):
    """Decode natively, otherwise via ImageMagick / ffmpeg into a temporary PNG."""
    try:
        return img_load(src)
    except DecodeError:
        pass
    tmp = job.new_tmp(directory, "png")
    ok = False
    if Tools.magick():
        try:
            run_cmd(job, [Tools.magick(), src + "[0]", "-auto-orient", tmp])
            ok = os.path.exists(tmp)
        except ConvError:
            ok = False
    if not ok and Tools.ffmpeg():
        try:
            run_ffmpeg(job, ["-i", src, "-frames:v", "1", tmp])
            ok = os.path.exists(tmp)
        except ConvError:
            ok = False
    if not ok:
        raise ConvError(tr("err_decode", os.path.basename(src)))
    return img_load(tmp)


def flatten_alpha(pb):
    """Put transparent pixels on white (JPEG/BMP have no transparency)."""
    if not pb.get_has_alpha():
        return pb
    w, h = pb.get_width(), pb.get_height()
    bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    bg.fill(0xFFFFFFFF)
    pb.composite(bg, 0, 0, w, h, 0, 0, 1.0, 1.0, GdkPixbuf.InterpType.NEAREST, 255)
    return bg


def scale_pixbuf(pb, factor=1.0, max_dim=None):
    w, h = pb.get_width(), pb.get_height()
    k = factor
    if max_dim and max(w, h) * k > max_dim:
        k = max_dim / float(max(w, h))
    if k >= 0.999:
        return pb
    return pb.scale_simple(max(1, int(round(w * k))), max(1, int(round(h * k))), GdkPixbuf.InterpType.HYPER)


def rotate_pixbuf(pb, degrees):
    """Rotate clockwise by 90 / 180 / 270 degrees (0 leaves the picture alone)."""
    d = int(degrees or 0) % 360
    rot = {90: GdkPixbuf.PixbufRotation.CLOCKWISE, 180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
           270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(d)
    return pb.rotate_simple(rot) if rot is not None else pb


def write_ico(pb, dst):
    """Real multi-size .ico (PNG-compressed entries), square-padded, never stretched."""
    w, h = pb.get_width(), pb.get_height()
    side = max(w, h)
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    if w != h:
        sq = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
        sq.fill(0x00000000)
        pb.copy_area(0, 0, w, h, sq, (side - w) // 2, (side - h) // 2)
        pb = sq
    sizes = [s for s in (16, 32, 48, 64, 128, 256) if s <= side] or [side]
    if side > 256:
        pb = pb.scale_simple(256, 256, GdkPixbuf.InterpType.HYPER)
        side = 256
    entries, blobs = b"", []
    for s in sizes:
        p = pb if s == side else pb.scale_simple(s, s, GdkPixbuf.InterpType.HYPER)
        ok, buf = p.save_to_bufferv("png", [], [])
        if not ok:
            raise ConvError("ico encode failed")
        blobs.append((s, bytes(buf)))
    offset = 6 + 16 * len(blobs)
    data = b""
    for s, buf in blobs:
        wb = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", wb, wb, 0, 0, 1, 32, len(buf), offset + len(data))
        data += buf
    with open(dst, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(blobs)) + entries + data)


def pdf_from_images(job, paths, dst, quality=85, scale=1.0, rotate=0, max_dim=None):
    """One page per image. JPEG photos are embedded as-is (no size explosion) unless they must be rotated / shrunk."""
    surface = None
    n = len(paths)
    try:
        for i, p in enumerate(paths):
            job.check()
            pb = load_any(job, p, os.path.dirname(dst))
            ext = split_name(p)[1]
            raw = None
            if ext in JPEG_EXTS and scale >= 0.999 and not rotate and not max_dim:
                try:
                    with open(p, "rb") as f:
                        data = f.read()
                    if exif_orientation(data) in (None, 1):
                        raw = data
                except OSError:
                    raw = None
            pb = scale_pixbuf(rotate_pixbuf(pb, rotate), scale, max_dim)
            w, h = pb.get_width(), pb.get_height()
            if raw is None and not pb.get_has_alpha():
                ok, buf = pb.save_to_bufferv("jpeg", ["quality"], [str(max(35, quality))])
                raw = bytes(buf) if ok else None
            k = min(1.0, 14000.0 / max(w * 0.75, h * 0.75))
            pw, ph = w * 0.75 * k, h * 0.75 * k
            if surface is None:
                surface = cairo.PDFSurface(dst, pw, ph)
                try:
                    surface.set_metadata(cairo.PDF_METADATA_CREATOR, "Rumman")
                except Exception:
                    pass
            else:
                surface.set_size(pw, ph)
            img = cairo.ImageSurface(cairo.FORMAT_ARGB32 if pb.get_has_alpha() else cairo.FORMAT_RGB24, w, h)
            c = cairo.Context(img)
            Gdk.cairo_set_source_pixbuf(c, pb, 0, 0)
            c.paint()
            img.flush()
            if raw is not None and not pb.get_has_alpha():
                try:
                    img.set_mime_data("image/jpeg", raw)
                except Exception:
                    pass
            cr = cairo.Context(surface)
            cr.scale(0.75 * k, 0.75 * k)
            cr.set_source_surface(img, 0, 0)
            cr.paint()
            surface.show_page()
            job.report((i + 1) / float(n))
    finally:
        if surface is not None:
            surface.finish()


# ------------------------------------------------------------- encoders ------
def _encode_external(job, png_tmp, tmp, fmt, quality):
    """WEBP / GIF / AVIF / HEIC from a clean temporary PNG, using ImageMagick or ffmpeg."""
    im = Tools.magick()
    if fmt == "WEBP":
        # the encoders that really follow the quality come first: some ImageMagick builds ignore -quality for WEBP
        cw = Tools.find("cwebp")
        if cw:
            run_cmd(job, [cw, "-quiet", "-q", str(quality), "-m", "4", png_tmp, "-o", tmp])
            return
        if Tools.has_encoder("libwebp"):
            run_ffmpeg(job, ["-i", png_tmp, "-c:v", "libwebp", "-quality", str(quality), "-compression_level", "4", tmp])
            return
    if im and Tools.magick_can(fmt, write=True):
        run_cmd(job, [im, png_tmp, "-quality", str(quality), tmp])
        return
    if fmt == "WEBP" and Tools.has_encoder("libwebp"):
        run_ffmpeg(job, ["-i", png_tmp, "-c:v", "libwebp", "-quality", str(quality), "-compression_level", "4", tmp])
        return
    if fmt == "GIF" and Tools.ffmpeg():
        run_ffmpeg(job, ["-i", png_tmp, tmp])
        return
    raise ConvError(tr("err_no_encoder", fmt))


def image_format_available(fmt):
    wr = Tools.pixbuf_writable()
    f = fmt.upper()
    if f == "JPG":
        return "jpeg" in wr
    if f in ("PNG", "BMP", "TIFF"):
        return f.lower() in wr
    if f in ("ICO", "PDF"):
        return "png" in wr or f == "PDF"
    if f == "WEBP":
        return "webp" in wr or Tools.magick_can("WEBP", True) or Tools.has_encoder("libwebp")
    if f == "GIF":
        return Tools.magick_can("GIF", True) or bool(Tools.ffmpeg())
    if f in ("AVIF", "HEIC"):
        return Tools.magick_can(f, True)
    return False


def _pngquant(job, tmp, odir, colors):
    """Optional lossy palette step for PNG (only when pngquant is installed); keeps the result only if it is smaller."""
    q = Tools.find("pngquant")
    if not q or not colors:
        return
    out2 = job.new_tmp(odir, "png")
    try:
        run_cmd(job, [q, "--quality=%d-%d" % (max(0, colors - 25), colors), "--force", "--output", out2, tmp])
        if os.path.exists(out2) and os.path.getsize(out2) < os.path.getsize(tmp):
            os.replace(out2, tmp)
    except ConvError:
        pass


def image_convert(job, src, fmt, quality=85, scale=1.0, strip=False, png_level=6, suffix="", max_dim=None,
                  rotate=0, png_colors=None, gain_check=False):
    """Convert / resize / rotate one image in one go. Returns the output path.
    gain_check: the only thing asked was "make it smaller" - then a result that is not smaller raises NoGain."""
    fmt = fmt.upper()
    ext = {"JPEG": "jpg"}.get(fmt, fmt.lower())
    odir = out_dir_for(src, job.opts)
    stem, sext = split_name(src)
    tmp = job.new_tmp(odir, ext)
    job.report(None)
    if fmt == "PDF":
        pdf_from_images(job, [src], tmp, quality, scale, rotate, max_dim)
        return job.commit(tmp, odir, stem + suffix, ext)
    pb = scale_pixbuf(rotate_pixbuf(load_any(job, src, odir), rotate), scale, max_dim)
    job.check()
    wr = Tools.pixbuf_writable()
    if fmt == "JPG" and "jpeg" in wr:
        flatten_alpha(pb).savev(tmp, "jpeg", ["quality"], [str(quality)])
        if not strip and sext in JPEG_EXTS:
            try:
                with open(src, "rb") as f:
                    seg = exif_segment_normalized(f.read())
                if seg:
                    with open(tmp, "rb") as f:
                        data = f.read()
                    with open(tmp, "wb") as f:
                        f.write(jpeg_insert_exif(data, seg))
            except OSError:
                pass
    elif fmt == "PNG" and "png" in wr:
        pb.savev(tmp, "png", ["compression"], [str(png_level)])
        _pngquant(job, tmp, odir, png_colors)
    elif fmt == "BMP" and "bmp" in wr:
        flatten_alpha(pb).savev(tmp, "bmp", [], [])
    elif fmt == "TIFF" and "tiff" in wr:
        pb.savev(tmp, "tiff", ["compression"], ["5"])
    elif fmt == "ICO":
        write_ico(pb, tmp)
    elif fmt == "WEBP" and "webp" in wr:
        pb.savev(tmp, "webp", ["quality"], [str(quality)])
    else:
        mid = job.new_tmp(odir, "png")
        pb.savev(mid, "png", ["compression"], ["1"])
        _encode_external(job, mid, tmp, fmt, quality)
    job.check()
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        raise ConvError(tr("err_no_output"))
    if gain_check and os.path.getsize(tmp) >= os.path.getsize(src):
        raise NoGain(tr("err_no_gain"))
    return job.commit(tmp, odir, stem + suffix, ext)


def image_compress(job, src, quality, max_dim=None, png_colors=None):
    """Same-format size reduction (JPEG/WEBP re-encode, PNG optimise). Raises NoGain if no gain."""
    stem, sext = split_name(src)
    in_size = os.path.getsize(src)
    odir = out_dir_for(src, job.opts)
    if sext in JPEG_EXTS:
        fmt, ext = "JPG", "jpg"
    elif sext == "png":
        fmt, ext = "PNG", "png"
    elif sext == "webp":
        fmt, ext = "WEBP", "webp"
    else:
        fmt, ext = "JPG", "jpg"
    job.report(None)
    pb = scale_pixbuf(load_any(job, src, odir), 1.0, max_dim)
    tmp = job.new_tmp(odir, ext)
    wr = Tools.pixbuf_writable()
    if fmt == "JPG":
        flatten_alpha(pb).savev(tmp, "jpeg", ["quality"], [str(quality)])
        if not job.opts.get("strip_meta"):
            try:
                with open(src, "rb") as f:
                    seg = exif_segment_normalized(f.read())
                if seg:
                    with open(tmp, "rb") as f:
                        data = f.read()
                    with open(tmp, "wb") as f:
                        f.write(jpeg_insert_exif(data, seg))
            except OSError:
                pass
    elif fmt == "PNG":
        pb.savev(tmp, "png", ["compression"], ["9"])
        _pngquant(job, tmp, odir, png_colors)
    else:
        if "webp" in wr:
            pb.savev(tmp, "webp", ["quality"], [str(quality)])
        else:
            mid = job.new_tmp(odir, "png")
            pb.savev(mid, "png", ["compression"], ["1"])
            _encode_external(job, mid, tmp, "WEBP", quality)
    job.check()
    if not os.path.exists(tmp):
        raise ConvError(tr("err_no_output"))
    if os.path.getsize(tmp) >= in_size:
        raise NoGain(tr("err_no_gain"))
    return job.commit(tmp, odir, stem + "_compressed", ext)


def image_strip_meta(job, src, suffix="_clean"):
    """Remove camera / GPS / software info. Lossless for JPEG and PNG."""
    stem, sext = split_name(src)
    odir = out_dir_for(src, job.opts)
    job.report(None)
    ext = "jpg" if sext in JPEG_EXTS else sext
    tmp = job.new_tmp(odir, ext)
    if sext in JPEG_EXTS:
        with open(src, "rb") as f:
            data = f.read()
        if exif_orientation(data) in (None, 1):
            with open(tmp, "wb") as f:
                f.write(jpeg_strip_metadata(data))
        else:  # rotated by EXIF: bake the rotation into the pixels first
            flatten_alpha(img_load(src)).savev(tmp, "jpeg", ["quality"], ["95"])
    elif sext == "png":
        with open(src, "rb") as f:
            data = f.read()
        with open(tmp, "wb") as f:
            f.write(png_strip_metadata(data))
    else:
        im = Tools.magick()
        if im:
            run_cmd(job, [im, src + "[0]", "-auto-orient", "-strip", tmp])
        else:
            pb = load_any(job, src, odir)
            fmt = {"jpg": "jpeg"}.get(ext, ext)
            if fmt not in Tools.pixbuf_writable():
                raise ConvError(tr("err_no_encoder", ext.upper()))
            pb.savev(tmp, fmt, [], [])
    return job.commit(tmp, odir, stem + suffix, ext)


def image_rotate(job, src, clockwise=True):
    stem, sext = split_name(src)
    odir = out_dir_for(src, job.opts)
    job.report(None)
    pb = load_any(job, src, odir)
    rot = GdkPixbuf.PixbufRotation.CLOCKWISE if clockwise else GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE
    pb = pb.rotate_simple(rot)
    if sext in JPEG_EXTS:
        fmt = "JPG"
    elif sext == "bmp":
        fmt = "BMP"
    elif sext in ("tif", "tiff"):
        fmt = "TIFF"
    elif sext == "webp":
        fmt = "WEBP"
    else:
        fmt = "PNG"
    ext = fmt.lower()
    tmp = job.new_tmp(odir, ext)
    wr = Tools.pixbuf_writable()
    if fmt == "JPG":
        flatten_alpha(pb).savev(tmp, "jpeg", ["quality"], ["93"])
    elif fmt in ("BMP", "TIFF") and fmt.lower() in wr:
        pb.savev(tmp, fmt.lower(), [], [])
    elif fmt == "WEBP":
        mid = job.new_tmp(odir, "png")
        pb.savev(mid, "png", ["compression"], ["1"])
        _encode_external(job, mid, tmp, "WEBP", 90)
    else:
        pb.savev(tmp, "png", ["compression"], ["6"])
        ext = "png"
    return job.commit(tmp, odir, stem + ("_right" if clockwise else "_left"), ext)


# ============================================================================
#  [4] Audio & video  (ffmpeg)
# ============================================================================
AUDIO_EXTS = {"mp3", "wav", "flac", "ogg", "oga", "m4a", "aac", "opus", "wma", "aiff", "aif", "amr", "mka", "ac3"}
VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "avi", "flv", "wmv", "m4v", "mpg", "mpeg", "3gp", "mts", "m2ts", "ogv"}
VIDEO_HEIGHTS = (None, 1080, 720, 480)

AUDIO_FMT_EXT = {"MP3": "mp3", "M4A": "m4a", "OGG": "ogg", "OPUS": "opus", "FLAC": "flac", "WAV": "wav"}
AUDIO_ENCODER = {"MP3": "libmp3lame", "M4A": "aac", "OGG": "libvorbis", "OPUS": "libopus", "FLAC": "flac", "WAV": "pcm_s16le"}

# which codecs can be copied (no re-encode) into which container
_REMUX = {
    "MP4": ({"h264", "hevc", "mpeg4", "av1"}, {"aac", "mp3", "ac3", "eac3", "alac"}),
    "MOV": ({"h264", "hevc", "mpeg4", "prores"}, {"aac", "mp3", "ac3", "alac", "pcm_s16le"}),
    "MKV": (None, None),
    "WEBM": ({"vp8", "vp9", "av1"}, {"vorbis", "opus"}),
}


def audio_format_available(fmt):
    return bool(Tools.ffmpeg()) and Tools.has_encoder(AUDIO_ENCODER[fmt])


def kbps_from_quality(q):
    k = 64 + (max(10, min(100, q)) - 10) / 90.0 * 256
    return int(round(k / 16.0) * 16)


def audio_fmt_for_ext(ext):
    return {"mp3": "MP3", "m4a": "M4A", "aac": "M4A", "ogg": "OGG", "oga": "OGG", "opus": "OPUS",
            "flac": "FLAC", "wav": "WAV"}.get(ext, "MP3")


def audio_codec_args(fmt, quality=85, kbps=None, mono=False, normalize=False, strip=False):
    k = kbps or kbps_from_quality(quality)
    a = []
    if mono:
        a += ["-ac", "1"]
    if normalize:
        a += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100"]
    if fmt == "MP3":
        a += ["-c:a", "libmp3lame", "-b:a", "%dk" % k]
    elif fmt == "M4A":
        a += ["-c:a", "aac", "-b:a", "%dk" % k, "-movflags", "+faststart"]
    elif fmt == "OGG":
        a += ["-c:a", "libvorbis", "-q:a", str(int(round(2 + (max(10, min(100, quality)) - 10) / 90.0 * 6)))]
    elif fmt == "OPUS":
        a += ["-c:a", "libopus", "-b:a", "%dk" % min(k, 256)]
    elif fmt == "FLAC":
        a += ["-c:a", "flac", "-compression_level", "8"]
    elif fmt == "WAV":
        a += ["-c:a", "pcm_s16le"]
    if strip:
        a += ["-map_metadata", "-1"]
    return a


_SIL_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


def silence_bounds(job, src, dur, threshold="-50dB", min_len=0.3):
    """(start, end) in seconds of the part to keep when the silence at both ends is cut. (0, dur) when there is none
    (or when the whole file is silent: then nothing is cut)."""
    starts, ends = [], []

    def on_line(line):
        m = _SIL_START.search(line)
        if m:
            starts.append(float(m.group(1)))
        m = _SIL_END.search(line)
        if m:
            ends.append(float(m.group(1)))
        return False
    ff = Tools.ffmpeg()
    if not ff or not dur:
        return 0.0, dur
    run_cmd(job, [ff, "-hide_banner", "-nostdin", "-i", src, "-vn", "-af", "silencedetect=noise=%s:d=%s" % (threshold, min_len),
                  "-f", "null", "-"], on_line=on_line)
    start, end = 0.0, dur
    if starts and starts[0] <= 0.05 and ends:
        start = max(0.0, ends[0] - 0.05)                       # keep a breath of 50 ms
    if starts and (len(starts) > len(ends) or (ends and ends[-1] >= dur - 0.05)):
        end = min(dur, starts[-1] + 0.05)
    if end - start < 0.2:
        return 0.0, dur
    return start, end


def audio_convert(job, src, fmt, quality=85, kbps=None, mono=False, normalize=False, strip=False, suffix="", trim=False):
    """Audio (or the sound track of a video) -> MP3/M4A/OGG/OPUS/FLAC/WAV."""
    fmt = fmt.upper()
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    ext = AUDIO_FMT_EXT[fmt]
    tmp = job.new_tmp(odir, ext)
    dur = probe(src)["duration"]
    job.report(0.0 if dur else None)
    cut = []
    if trim and dur:
        s, e = silence_bounds(job, src, dur)
        if s > 0 or e < dur:
            cut = ["-ss", "%.3f" % s] if s > 0 else []
            dur = e - s
            args_t = ["-t", "%.3f" % dur]
        else:
            args_t = []
    else:
        args_t = []
    args = cut + ["-i", src] + args_t + ["-vn", "-map", "0:a:0"] + audio_codec_args(fmt, quality, kbps, mono, normalize, strip) + [tmp]
    run_ffmpeg(job, args, dur)
    return job.commit(tmp, odir, stem + suffix, ext)


def audio_merge(job, files, fmt="MP3", quality=85, kbps=None, mono=False, normalize=False, strip=False):
    """Several audio files, one after the other, into one file (in the order given). Different sample rates / channel
    layouts are brought to one first."""
    fmt = fmt.upper()
    odir = out_dir_for(files[0], job.opts)
    stem = archive_name_for(files) if len(files) > 1 else split_name(files[0])[0]
    ext = AUDIO_FMT_EXT[fmt]
    tmp = job.new_tmp(odir, ext)
    total = 0.0
    args = []
    for f in files:
        job.check()
        args += ["-i", f]
        total += probe(f)["duration"] or 0.0
    n = len(files)
    chans = "mono" if mono else "stereo"
    parts = ["[%d:a:0]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=%s[a%d]" % (i, chans, i) for i in range(n)]
    graph = ";".join(parts) + ";" + "".join("[a%d]" % i for i in range(n)) + "concat=n=%d:v=0:a=1[m]" % n
    last = "m"
    if normalize:
        graph += ";[m]loudnorm=I=-16:TP=-1.5:LRA=11[o]"
        last = "o"
    codec = audio_codec_args(fmt, quality, kbps, False, False, strip)
    run_ffmpeg(job, args + ["-filter_complex", graph, "-map", "[%s]" % last, "-vn"] + codec + [tmp], total or None)
    return job.commit(tmp, odir, stem, ext)


def video_frame(job, src):
    """One still picture (JPG) from the middle of a video."""
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    info = probe(src)
    if not _stream(info, "video"):
        raise ConvError(tr("err_no_video"))
    dur = info["duration"] or 0.0
    tmp = job.new_tmp(odir, "jpg")
    job.report(0.0)
    run_ffmpeg(job, ["-ss", "%.3f" % (dur / 2.0), "-i", src, "-frames:v", "1", "-q:v", "2", "-an", tmp], None)
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:        # a very short clip: take the first picture instead
        run_ffmpeg(job, ["-i", src, "-frames:v", "1", "-q:v", "2", "-an", tmp], None)
    return job.commit(tmp, odir, stem + "_frame", "jpg")


def audio_compress(job, src, kbps, mono=False):
    stem, sext = split_name(src)
    fmt = audio_fmt_for_ext(sext) if sext in ("mp3", "m4a", "aac", "ogg", "oga", "opus") else "MP3"
    in_size = os.path.getsize(src)
    out = audio_convert(job, src, fmt, kbps=kbps, mono=mono, suffix="_compressed")
    if os.path.getsize(out) >= in_size:
        os.remove(out)
        raise NoGain(tr("err_no_gain"))
    return out


def audio_retag(job, src, mode):
    """Tools: 'strip' (lossless), 'normalize', 'mono'."""
    stem, sext = split_name(src)
    odir = out_dir_for(src, job.opts)
    dur = probe(src)["duration"]
    if mode == "strip":
        tmp = job.new_tmp(odir, sext)
        run_ffmpeg(job, ["-i", src, "-map", "0:a", "-c", "copy", "-map_metadata", "-1", tmp], dur)
        return job.commit(tmp, odir, stem + "_clean", sext)
    fmt = audio_fmt_for_ext(sext)
    suffix = "_normalized" if mode == "normalize" else "_mono"
    return audio_convert(job, src, fmt, quality=90, mono=(mode == "mono"), normalize=(mode == "normalize"), suffix=suffix)


# ------------------------------------------------------------------- video --
def _stream(info, kind):
    for s in info["streams"]:
        if s.get("codec_type") == kind:
            return s
    return None


def can_remux(fmt, vcodec, acodec):
    v_ok, a_ok = _REMUX.get(fmt, (set(), set()))
    if not vcodec:
        return False
    if v_ok is not None and vcodec not in v_ok:
        return False
    if acodec and a_ok is not None and acodec not in a_ok:
        return False
    return True


def crf_from_quality(q):
    return int(round(18 + (100 - max(10, min(100, q))) * 0.19))


def _h264_args(crf, preset):
    if Tools.has_encoder("libx264"):
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if Tools.has_encoder("libopenh264"):
        return ["-c:v", "libopenh264", "-b:v", "%dk" % max(500, int(9000 - crf * 200)), "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", str(max(2, min(20, int(round((crf - 10) / 1.4))))), "-pix_fmt", "yuv420p"]


def _rotate_filter(deg):
    return {90: "transpose=1", 270: "transpose=2", 180: "transpose=1,transpose=1"}.get(int(deg or 0) % 360, "")


def video_convert(job, src, fmt, quality=85, scale_idx=0, mute=False, strip=False, preset="veryfast",
                  crf=None, max_h=None, suffix="", abr=160, rotate=0, copy_ok=False):
    """Video -> MP4/MKV/MOV/WEBM/GIF. Instant (no re-encode) when only the container changes (copy_ok) and nothing
    else (resize, rotation) is asked for."""
    fmt = fmt.upper()
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    ext = fmt.lower()
    info = probe(src)
    dur = info["duration"]
    vs, au = _stream(info, "video"), _stream(info, "audio")
    if not vs:
        raise ConvError(tr("err_no_video"))
    rot = _rotate_filter(rotate)
    height = vs.get("height") or 0
    if rot and int(rotate) % 180 == 90:
        height = vs.get("width") or 0                       # a quarter turn swaps width and height
    target_h = max_h if max_h else VIDEO_HEIGHTS[scale_idx if 0 <= scale_idx < 4 else 0]
    needs_scale = bool(target_h and height and height > target_h)
    if au and au.get("channels") == 1:
        abr = min(abr, 96)
    tmp = job.new_tmp(odir, ext)
    job.report(0.0 if dur else None)

    if fmt == "GIF":
        vf = ((rot + ",") if rot else "") + ("fps=12,scale='min(480,iw)':-2:flags=lanczos,split[s0][s1];"
                                              "[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5")
        run_ffmpeg(job, ["-i", src, "-an", "-sn", "-vf", vf, "-loop", "0", tmp], dur)
        return job.commit(tmp, odir, stem + suffix, ext)

    args = ["-i", src, "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]
    if strip:
        args += ["-map_metadata", "-1", "-map_chapters", "-1"]
    can_copy = ((copy_ok or (quality >= 100 and crf is None)) and not needs_scale and not rot and
                can_remux(fmt, vs.get("codec_name"), au.get("codec_name") if au else None))
    if can_copy:
        args += ["-c", "copy"]
        if mute:
            args += ["-an"]
    else:
        vf = "scale=-2:%d" % target_h if needs_scale else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        if rot:
            vf = rot + "," + vf
        c = crf if crf is not None else crf_from_quality(quality)
        if fmt == "WEBM":
            args += ["-vf", vf, "-c:v", "libvpx-vp9", "-crf", str(min(63, c + 8)), "-b:v", "0",
                     "-row-mt", "1", "-cpu-used", "4", "-deadline", "good"]
            args += ["-an"] if mute else ["-c:a", "libopus", "-b:a", "%dk" % min(abr, 128)]
        else:
            args += ["-vf", vf] + _h264_args(c, preset)
            args += ["-an"] if mute else ["-c:a", "aac", "-b:a", "%dk" % abr]
    if fmt in ("MP4", "MOV"):
        args += ["-movflags", "+faststart"]
    run_ffmpeg(job, args + [tmp], dur)
    return job.commit(tmp, odir, stem + suffix, ext)


def video_compress(job, src, crf, max_h=None):
    stem, sext = split_name(src)
    fmt = {"mp4": "MP4", "m4v": "MP4", "mkv": "MKV", "mov": "MOV", "webm": "WEBM"}.get(sext, "MP4")
    in_size = os.path.getsize(src)
    abr = 128 if crf <= 25 else 112 if crf <= 28 else 96 if crf <= 30 else 64
    out = video_convert(job, src, fmt, crf=crf, max_h=max_h, preset="medium", mute=False,
                        strip=False, suffix="_compressed", abr=abr)
    if os.path.getsize(out) >= in_size:
        os.remove(out)
        raise NoGain(tr("err_no_gain"))
    return out


def video_tool(job, src, mode):
    """Tools: strip | mute | right | left | audio."""
    stem, sext = split_name(src)
    odir = out_dir_for(src, job.opts)
    info = probe(src)
    dur = info["duration"]
    if mode == "audio":
        ac = (_stream(info, "audio") or {}).get("codec_name")
        mp = {"aac": "m4a", "mp3": "mp3", "opus": "opus", "vorbis": "ogg", "flac": "flac"}
        if ac in mp:
            tmp = job.new_tmp(odir, mp[ac])
            run_ffmpeg(job, ["-i", src, "-vn", "-map", "0:a:0", "-c:a", "copy", tmp], dur)
            return job.commit(tmp, odir, stem, mp[ac])
        return audio_convert(job, src, "MP3", kbps=192)
    keep = sext if sext in ("mp4", "mkv", "mov", "webm") else "mp4"
    tmp = job.new_tmp(odir, keep)
    job.report(0.0 if dur else None)
    if mode == "strip":
        run_ffmpeg(job, ["-i", src, "-map", "0:v", "-map", "0:a?", "-map", "0:s?", "-c", "copy",
                         "-map_metadata", "-1", "-map_chapters", "-1", tmp], dur)
        suffix = "_clean"
    elif mode == "mute":
        run_ffmpeg(job, ["-i", src, "-map", "0:v", "-c:v", "copy", "-an", tmp], dur)
        suffix = "_mute"
    else:
        tr_ = "transpose=1" if mode == "right" else "transpose=2"
        args = ["-i", src, "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn", "-vf", tr_]
        args += _h264_args(20, "veryfast") if keep != "webm" else ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0"]
        args += ["-c:a", "copy"] if keep == "mkv" else (["-c:a", "libopus"] if keep == "webm" else ["-c:a", "aac", "-b:a", "160k"])
        run_ffmpeg(job, args + [tmp], dur)
        suffix = "_right" if mode == "right" else "_left"
    return job.commit(tmp, odir, stem + suffix, keep)


def gif_to_mp4(job, src):
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    tmp = job.new_tmp(odir, "mp4")
    job.report(None)
    run_ffmpeg(job, ["-i", src, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"] + _h264_args(23, "medium")
               + ["-an", "-movflags", "+faststart", tmp], probe(src)["duration"])
    return job.commit(tmp, odir, stem, "mp4")


# ============================================================================
#  [5] Archives: create (ZIP / TAR.GZ / TAR.XZ / 7Z), extract safely, repack
# ============================================================================
ARCHIVE_EXTS = {"zip", "tar", "gz", "tgz", "xz", "txz", "bz2", "tbz2", "7z", "rar", "zst", "tzst", "cbz", "jar"}
ARCHIVE_FMT_EXT = {"ZIP": "zip", "TAR.GZ": "tar.gz", "TAR.XZ": "tar.xz", "7Z": "7z"}


def is_archive_ext(ext):
    return ext in ARCHIVE_EXTS or ext.startswith("tar.")


def archive_format_available(fmt):
    return fmt != "7Z" or bool(Tools.sevenzip())


def archive_name_for(sources):
    if len(sources) == 1:
        return split_name(sources[0])[0]
    base = os.path.basename(common_base(sources).rstrip("/"))
    return base or tr("archive_default_name")


def _arc(*parts):
    return "/".join(p for p in parts if p and p != ".")


def gather_entries(sources, keep_root=True, include_hidden=True, exclude=()):
    """[(abs_path, archive_name, is_dir, size)] — hidden files are skipped only INSIDE dropped folders."""
    sources = [os.path.abspath(s) for s in sources]
    base = common_base(sources)
    single_dir = len(sources) == 1 and os.path.isdir(sources[0]) and not os.path.islink(sources[0])
    exclude = set(os.path.abspath(e) for e in exclude)
    entries, total = [], 0
    for s in sources:
        if os.path.isdir(s) and not os.path.islink(s):
            if single_dir and not keep_root:
                root_arc = ""
            else:
                root_arc = os.path.relpath(s, base).replace(os.sep, "/")
                entries.append((s, root_arc, True, 0))
            for dp, dn, fn in os.walk(s):
                dn.sort()
                fn.sort()
                if not include_hidden:
                    dn[:] = [d for d in dn if not d.startswith(".")]
                dn[:] = [d for d in dn if not os.path.islink(os.path.join(dp, d))]
                here = _arc(root_arc, os.path.relpath(dp, s).replace(os.sep, "/"))
                for d in dn:
                    entries.append((os.path.join(dp, d), _arc(here, d), True, 0))
                for f in fn:
                    if not include_hidden and f.startswith("."):
                        continue
                    fp = os.path.join(dp, f)
                    if os.path.abspath(fp) in exclude or not (os.path.isfile(fp) or os.path.islink(fp)):
                        continue
                    try:
                        sz = os.path.getsize(fp) if os.path.exists(fp) else 0
                    except OSError:
                        sz = 0
                    entries.append((fp, _arc(here, f), False, sz))
                    total += sz
        elif os.path.exists(s) and os.path.abspath(s) not in exclude:
            sz = os.path.getsize(s)
            entries.append((s, os.path.relpath(s, base).replace(os.sep, "/"), False, sz))
            total += sz
    return entries, total


class _ProgressReader:
    def __init__(self, f, job, state, total):
        self.f, self.job, self.state, self.total = f, job, state, max(1, total)

    def read(self, n=-1):
        data = self.f.read(n)
        self.state[0] += len(data)
        self.job.check()
        self.job.report(self.state[0] / self.total)
        return data


def _write_zip(job, tmp, entries, total, level):
    done = 0
    total = max(1, total)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True) as zf:
        for path, arc, is_dir, size in entries:
            job.check()
            zi = zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False)
            if is_dir:
                zf.writestr(zi, b"")
                continue
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as src, zf.open(zi, "w", force_zip64=(size > 0x7FFFFFFF)) as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
                    done += len(chunk)
                    job.check()
                    job.report(done / total)


def _write_tar(job, tmp, entries, total, comp, level):
    state = [0]
    if comp == "gz":
        tf = tarfile.open(tmp, "w:gz", compresslevel=level)
    else:
        tf = tarfile.open(tmp, "w:xz", preset=min(level, 7))
    with tf:
        for path, arc, is_dir, size in entries:
            job.check()
            ti = tf.gettarinfo(path, arc)
            if ti is None:
                continue
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if ti.isreg():
                with open(path, "rb") as f:
                    tf.addfile(ti, _ProgressReader(f, job, state, total))
            else:
                tf.addfile(ti)


def _percent_line(job):
    def on_line(line):
        m = re.search(r"(\d{1,3})%", line)
        if m:
            job.report(min(0.99, int(m.group(1)) / 100.0))
            return True
        return False
    return on_line


def _write_7z(job, tmp, sources, keep_root, include_hidden, level):
    sz = Tools.sevenzip()
    if not sz:
        raise ToolMissing("7z")
    sources = [os.path.abspath(s) for s in sources]
    single_dir = len(sources) == 1 and os.path.isdir(sources[0])
    if single_dir and not keep_root:
        cwd = sources[0]
        items = sorted(os.listdir(cwd))
        if not include_hidden:
            items = [i for i in items if not i.startswith(".")]
    else:
        cwd = common_base(sources)
        items = [os.path.relpath(s, cwd) for s in sources]
    if not items:
        raise ConvError(tr("err_empty"))
    argv = [sz, "a", "-t7z", "-bso0", "-bsp1", "-y", "-mx=%d" % level]
    if not include_hidden and not any(os.path.basename(i).startswith(".") for i in items):
        argv.append("-xr!.*")
    run_cmd(job, argv + [tmp, "--"] + items, cwd=cwd, on_line=_percent_line(job), ok_codes=(0, 1))


def archive_create(job, sources, fmt, level=6, keep_root=True, include_hidden=True):
    fmt = fmt.upper()
    ext = ARCHIVE_FMT_EXT[fmt]
    sources = [os.path.abspath(s) for s in sources]
    odir = out_dir_for(sources[0], job.opts)
    stem = archive_name_for(sources)
    tmp = job.new_tmp(odir, ext)
    level = max(1, min(9, int(level)))
    job.report(0.0)
    if fmt == "7Z":
        _write_7z(job, tmp, sources, keep_root, include_hidden, level)
    else:
        entries, total = gather_entries(sources, keep_root, include_hidden, exclude=[tmp])
        if not entries:
            raise ConvError(tr("err_empty"))
        if fmt == "ZIP":
            _write_zip(job, tmp, entries, total, level)
        else:
            _write_tar(job, tmp, entries, total, "gz" if fmt == "TAR.GZ" else "xz", level)
    job.check()
    return job.commit(tmp, odir, stem, ext)


# --------------------------------------------------------------- extraction --
def _safe_dest(root, name):
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    dest = os.path.join(root, *parts)
    rroot = os.path.realpath(root)
    if os.path.commonpath([rroot, os.path.realpath(dest)]) != rroot:
        return None
    return dest


def _zip_name(zi):
    """Windows zips often store Arabic names as cp1256 without the UTF-8 flag -> mojibake in Python."""
    name = zi.filename
    if not (zi.flag_bits & 0x800):
        try:
            raw = name.encode("cp437")
        except UnicodeEncodeError:
            return name
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        if LANG == "ar":
            try:
                return raw.decode("cp1256")
            except UnicodeDecodeError:
                pass
    if zi.create_system == 0 and "\\" in name and "/" not in name:
        name = name.replace("\\", "/")
    return name


def _extract_zip(job, src, work):
    with zipfile.ZipFile(src) as zf:
        infos = zf.infolist()
        total = max(1, sum(i.file_size for i in infos))
        done = 0
        for zi in infos:
            job.check()
            if zi.flag_bits & 0x1:
                raise ConvError(tr("err_password"))
            name = _zip_name(zi)
            dest = _safe_dest(work, name)
            if dest is None:
                continue
            if zi.is_dir() or name.endswith("/"):
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(zi) as fi, open(dest, "wb") as fo:
                while True:
                    chunk = fi.read(1 << 20)
                    if not chunk:
                        break
                    fo.write(chunk)
                    done += len(chunk)
                    job.check()
                    job.report(done / total)
            mode = (zi.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(dest, mode | 0o600)
                except OSError:
                    pass
            try:
                ts = time.mktime(zi.date_time + (0, 0, -1))
                os.utime(dest, (ts, ts))
            except (OverflowError, ValueError, OSError):
                pass


def _extract_tar(job, src, work):
    size = max(1, os.path.getsize(src))
    with open(src, "rb") as f:
        with tarfile.open(fileobj=f, mode="r|*") as tf:
            for m in tf:
                job.check()
                try:
                    try:
                        tf.extract(m, work, filter="data")
                    except TypeError:  # Python without extraction filters: do our own check
                        if _safe_dest(work, m.name) is None or m.issym() or m.islnk() or m.isdev():
                            continue
                        tf.extract(m, work)
                except (tarfile.FilterError, tarfile.ExtractError, OSError) as e:
                    dbg("skipped tar member", m.name, e)
                job.report(min(0.99, f.tell() / size))


def _extract_single_compressed(job, src, work, ext, stem):
    opener = {"gz": gzip.open, "xz": lzma.open, "bz2": bz2.open}[ext]
    size = max(1, os.path.getsize(src))
    with open(src, "rb") as raw, opener(raw, "rb") as fi, open(os.path.join(work, stem or "file"), "wb") as fo:
        while True:
            chunk = fi.read(1 << 20)
            if not chunk:
                break
            fo.write(chunk)
            job.check()
            job.report(min(0.99, raw.tell() / size))


def _clean_7z_error(msg):
    low = msg.lower()
    if "password" in low:
        return tr("err_password")
    keep = [l.strip() for l in msg.splitlines() if re.search(r"error|can ?not|unsupported|not archive|wrong", l, re.I)]
    if any(re.search(r"not archive|can ?not open|unsupported|headers error", l, re.I) for l in keep):
        return tr("err_bad_archive")
    return " / ".join(keep[-2:]) if keep else tr("err_bad_archive")


def _extract_external(job, src, work):
    sz = Tools.sevenzip()
    if sz:
        try:
            run_cmd(job, [sz, "x", "-y", "-bso0", "-bsp1", "-o" + work, src], on_line=_percent_line(job),
                    ok_codes=(0, 1))
            return
        except ConvError as e:
            if not (Tools.find("unrar") or Tools.find("bsdtar")):
                raise ConvError(_clean_7z_error(str(e)))
    if Tools.find("unrar") and src.lower().endswith(".rar"):
        run_cmd(job, [Tools.find("unrar"), "x", "-y", "-idq", src, work + os.sep])
        return
    if Tools.find("bsdtar"):
        run_cmd(job, [Tools.find("bsdtar"), "-xf", src, "-C", work])
        return
    raise ToolMissing("7z")


def _extract_into(job, src, work):
    stem, ext = split_name(src)
    if ext in ("zip", "cbz", "jar") and zipfile.is_zipfile(src):
        try:
            _extract_zip(job, src, work)
            return
        except NotImplementedError:  # exotic compression method
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
    elif ext in ("tar", "tgz", "txz", "tbz2") or ext.startswith("tar.") and ext not in ("tar.zst", "tar.lz"):
        _extract_tar(job, src, work)
        return
    elif ext in ("gz", "xz", "bz2"):
        _extract_single_compressed(job, src, work, ext, stem)
        return
    _extract_external(job, src, work)


def archive_extract(job, src):
    odir = out_dir_for(src, job.opts)
    stem, _ext = split_name(src)
    work = job.new_tmp(odir, "")
    os.makedirs(work)
    job.report(0.0)
    _extract_into(job, src, work)
    job.check()
    names = os.listdir(work)
    if not names:
        raise ConvError(tr("err_empty"))
    if len(names) == 1 and os.path.isdir(os.path.join(work, names[0])) and not os.path.islink(os.path.join(work, names[0])):
        final = unique_path(odir, names[0], "")
        os.replace(os.path.join(work, names[0]), final)
        shutil.rmtree(work, ignore_errors=True)
        job._tmp = [p for p in job._tmp if p != work]
        return final
    return job.commit(work, odir, stem, "")


def archive_repack(job, src, fmt, level=6):
    """Convert one archive type into another (extract to a hidden temp folder, then pack)."""
    odir = out_dir_for(src, job.opts)
    stem, _ext = split_name(src)
    work = job.new_tmp(odir, "")
    os.makedirs(work)
    job.report(0.0)
    _extract_into(job, src, work)
    job.check()
    if not os.listdir(work):
        raise ConvError(tr("err_empty"))
    ext = ARCHIVE_FMT_EXT[fmt.upper()]
    tmp = job.new_tmp(odir, ext)
    level = max(1, min(9, int(level)))
    if fmt.upper() == "7Z":
        _write_7z(job, tmp, [work], False, True, level)
    else:
        entries, total = gather_entries([work], keep_root=False, include_hidden=True, exclude=[tmp])
        if fmt.upper() == "ZIP":
            _write_zip(job, tmp, entries, total, level)
        else:
            _write_tar(job, tmp, entries, total, "gz" if fmt.upper() == "TAR.GZ" else "xz", level)
    job.check()
    return job.commit(tmp, odir, stem, ext)


# ============================================================================
#  [6] PDF, documents, text, subtitles
# ============================================================================
PDF_EXTS = {"pdf"}
WRITER_EXTS = {"doc", "docx", "odt", "rtf", "html", "htm", "epub"}
CALC_EXTS = {"xls", "xlsx", "ods"}
IMPRESS_EXTS = {"ppt", "pptx", "odp"}
TEXT_EXTS = {"txt", "md", "markdown", "rst", "log"}
SUB_EXTS = {"srt", "vtt"}
DOC_EXTS = WRITER_EXTS | CALC_EXTS | IMPRESS_EXTS

_OFFICE_FILTER = {"PDF": "pdf", "DOCX": "docx", "ODT": "odt", "TXT": "txt:Text (encoded):UTF8", "HTML": "html",
                  "RTF": "rtf", "XLSX": "xlsx", "ODS": "ods", "CSV": "csv", "PPTX": "pptx", "ODP": "odp"}


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig",) + (("cp1256",) if LANG == "ar" else ()) + ("cp1252",):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def doc_backend(sext, fmt):
    pd, lo = Tools.pandoc(), Tools.soffice()
    if sext in TEXT_EXTS and fmt == "PDF":
        return "builtin"
    if sext in ("md", "markdown", "rst") and pd and fmt in ("DOCX", "ODT", "HTML"):
        return "pandoc"
    if lo and fmt in _OFFICE_FILTER:
        return "office"
    if pd and sext in ("docx", "odt", "html", "htm", "epub", "txt", "log") and fmt in ("TXT", "MD", "HTML", "DOCX", "ODT"):
        return "pandoc"
    return None


def doc_target_formats(sext):
    if sext in CALC_EXTS:
        cands = ["PDF", "XLSX", "ODS", "CSV"]
    elif sext in IMPRESS_EXTS:
        cands = ["PDF", "PPTX", "ODP"]
    elif sext in TEXT_EXTS:
        cands = ["PDF", "DOCX", "ODT", "HTML"]
    else:
        cands = ["PDF", "DOCX", "ODT", "TXT", "HTML", "MD"]
    same = {"htm": "HTML", "markdown": "MD"}.get(sext, sext.upper())
    return [f for f in cands if f != same and doc_backend(sext, f)]


def office_convert(job, src, fmt):
    so = Tools.soffice()
    if not so:
        raise ToolMissing("LibreOffice")
    odir = out_dir_for(src, job.opts)
    stem, sext = split_name(src)
    ext = fmt.lower()
    work = job.new_tmp(odir, "")
    os.makedirs(work)
    prof = tempfile.mkdtemp(prefix="rumman-lo-")
    job.report(None)
    try:
        argv = [so, "-env:UserInstallation=file://" + prof, "--headless", "--norestore"]
        if sext in ("html", "htm"):
            argv.append("--infilter=HTML (StarWriter)")
        argv += ["--convert-to", _OFFICE_FILTER[fmt], "--outdir", work, src]
        run_cmd(job, argv)
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    made = [f for f in os.listdir(work) if not f.startswith(".")]
    if not made:
        raise ConvError(tr("err_no_output"))
    final = job.target(odir, stem, ext)
    os.replace(os.path.join(work, made[0]), final)
    return final


def pandoc_convert(job, src, fmt):
    pd = Tools.pandoc()
    if not pd:
        raise ToolMissing("pandoc")
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    ext = fmt.lower()
    to = {"txt": "plain", "md": "markdown", "html": "html5", "docx": "docx", "odt": "odt"}[ext]
    tmp = job.new_tmp(odir, ext)
    argv = [pd, src, "-t", to, "-o", tmp]
    if ext == "html":
        argv += ["--standalone", "--metadata", "pagetitle=" + stem]
    job.report(None)
    run_cmd(job, argv)
    return job.commit(tmp, odir, stem, ext)


def text_to_pdf(job, src, dst):
    """Plain text -> paginated A4 PDF with proper Arabic / mixed-direction layout (Pango)."""
    text = read_text(src).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    pw, ph, margin = 595.28, 841.89, 56.0
    surface = cairo.PDFSurface(dst, pw, ph)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.08, 0.08, 0.08)
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription("Sans 11"))
    layout.set_width(int((pw - 2 * margin) * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    limit = ph - margin
    y = margin
    paragraphs = text.split("\n")
    n = max(1, len(paragraphs))
    for pi, para in enumerate(paragraphs):
        job.check()
        layout.set_text(para if para else " ", -1)
        it = layout.get_iter()
        while True:
            line = it.get_line_readonly()
            _ink, logical = it.get_line_extents()
            base = it.get_baseline() / Pango.SCALE
            top = y + logical.y / Pango.SCALE
            if top + logical.height / Pango.SCALE > limit:
                surface.show_page()
                y = margin - logical.y / Pango.SCALE
            cr.move_to(margin + logical.x / Pango.SCALE, y + base)
            PangoCairo.show_layout_line(cr, line)
            if not it.next_line():
                break
        _a, extents = layout.get_extents()
        y += extents.height / Pango.SCALE + 3
        if pi % 200 == 0:
            job.report(pi / float(n))
    surface.finish()


def text_convert(job, src, fmt):
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    tmp = job.new_tmp(odir, "pdf")
    job.report(None)
    text_to_pdf(job, src, tmp)
    return job.commit(tmp, odir, stem, "pdf")


def doc_convert(job, src, fmt):
    fmt = fmt.upper()
    _stem, sext = split_name(src)
    be = doc_backend(sext, fmt)
    if be == "builtin":
        return text_convert(job, src, fmt)
    if be == "pandoc":
        return pandoc_convert(job, src, fmt)
    if be == "office":
        return office_convert(job, src, fmt)
    raise ConvError(tr("err_no_encoder", fmt))


# --------------------------------------------------------------- subtitles --
_TS = re.compile(r"(\d{1,2}):(\d\d):(\d\d)[,.](\d{1,3})")
_TS_SHORT = re.compile(r"(?<!\d:)\b(\d\d):(\d\d)[.,](\d{3})\b")


def _norm_ts(s, sep):
    def fix(m):
        return "%02d:%s:%s%s%s" % (int(m.group(1)), m.group(2), m.group(3), sep, m.group(4).ljust(3, "0"))
    s = _TS.sub(fix, s)
    return s


def subs_convert(job, src, fmt):
    fmt = fmt.upper()
    raw = read_text(src).replace("\r\n", "\n").replace("\r", "\n")
    _stem0, sext = split_name(src)
    blocks = [b for b in re.split(r"\n\s*\n", raw.strip("﻿\n ")) if b.strip()]
    cues = []
    for b in blocks:
        lines = b.split("\n")
        if lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, _, rest = lines[ti].partition("-->")
        end = rest.strip().split(" ")[0]
        a = a.strip()
        if a.count(":") == 1:
            a = "00:" + a
        if end.count(":") == 1:
            end = "00:" + end
        cues.append((a, end, "\n".join(lines[ti + 1:])))
    if not cues:
        raise ConvError(tr("err_decode", os.path.basename(src)))
    out = []
    if fmt == "VTT":
        out.append("WEBVTT\n")
        for a, e, t in cues:
            out.append("%s --> %s\n%s\n" % (_norm_ts(a, "."), _norm_ts(e, "."), t))
    elif fmt == "SRT":
        for i, (a, e, t) in enumerate(cues, 1):
            out.append("%d\n%s --> %s\n%s\n" % (i, _norm_ts(a, ","), _norm_ts(e, ","), re.sub(r"<[^>]+>", "", t)))
    else:
        for _a, _e, t in cues:
            out.append(re.sub(r"<[^>]+>|\{[^}]*\}", "", t))
    odir = out_dir_for(src, job.opts)
    stem, _s = split_name(src)
    ext = fmt.lower()
    tmp = job.new_tmp(odir, ext)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return job.commit(tmp, odir, stem, ext)


# --------------------------------------------------------------------- PDF --
def pdf_to_images(job, src, fmt, quality=85):
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    ext = "png" if fmt.upper() == "PNG" else "jpg"
    work = job.new_tmp(odir, "")
    os.makedirs(work)
    job.report(None)
    pp = Tools.find("pdftoppm")
    if pp:
        argv = [pp, "-r", "150", "-png" if ext == "png" else "-jpeg"]
        if ext == "jpg":
            argv += ["-jpegopt", "quality=%d" % quality]
        run_cmd(job, argv + [src, os.path.join(work, "page")])
    elif Tools.gs():
        dev = "png16m" if ext == "png" else "jpeg"
        run_cmd(job, [Tools.gs(), "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=" + dev, "-r150",
                      "-dJPEGQ=%d" % quality, "-sOutputFile=" + os.path.join(work, "page-%03d." + ext), src])
    else:
        raise ToolMissing("poppler-utils (pdftoppm)")
    files = sorted(f for f in os.listdir(work) if not f.startswith("."))
    if not files:
        raise ConvError(tr("err_no_output"))
    if len(files) == 1:
        final = job.target(odir, stem, ext)
        os.replace(os.path.join(work, files[0]), final)
        shutil.rmtree(work, ignore_errors=True)
        return final
    return job.commit(work, odir, stem, "")


def pdf_to_text(job, src):
    pt = Tools.find("pdftotext")
    if not pt:
        raise ToolMissing("poppler-utils (pdftotext)")
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    tmp = job.new_tmp(odir, "txt")
    job.report(None)
    run_cmd(job, [pt, "-enc", "UTF-8", src, tmp])
    return job.commit(tmp, odir, stem, "txt")


_GS_LEVELS = {
    "printer": ["-dPDFSETTINGS=/printer"],
    "ebook": ["-dPDFSETTINGS=/ebook"],
    "screen": ["-dPDFSETTINGS=/screen"],
    "extreme": ["-dPDFSETTINGS=/screen", "-dColorImageResolution=60", "-dGrayImageResolution=60",
                "-dMonoImageResolution=120", "-dJPEGQ=40"],
}


def pdf_compress(job, src, level):
    gs = Tools.gs()
    if not gs:
        raise ToolMissing("Ghostscript (gs)")
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    tmp = job.new_tmp(odir, "pdf")
    total = [0]
    job.report(None)

    def on_line(line):
        m = re.search(r"through (\d+)", line)
        if m:
            total[0] = int(m.group(1))
        m = re.match(r"\s*Page (\d+)", line)
        if m and total[0]:
            job.report(min(0.99, int(m.group(1)) / float(total[0])))
            return True
        return bool(m)

    argv = [gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5"]
    argv += _GS_LEVELS[level] + ["-sOutputFile=" + tmp, src]
    run_cmd(job, argv, on_line=on_line)
    if not os.path.exists(tmp) or os.path.getsize(tmp) >= os.path.getsize(src):
        raise NoGain(tr("err_no_gain"))
    return job.commit(tmp, odir, stem + "_compressed", "pdf")


def pdf_merge(job):
    files = [os.path.abspath(f) for f in job.files]
    odir = out_dir_for(files[0], job.opts)
    stem, _sext = split_name(files[0])
    tmp = job.new_tmp(odir, "pdf")
    job.report(None)
    if Tools.find("pdfunite"):
        run_cmd(job, [Tools.find("pdfunite")] + files + [tmp])
    elif Tools.find("qpdf"):
        run_cmd(job, [Tools.find("qpdf"), "--empty", "--pages"] + files + ["--", tmp])
    else:
        raise ToolMissing("poppler-utils (pdfunite)")
    return job.commit(tmp, odir, stem + "_merged", "pdf")


def pdf_rotate(job, src, degrees):
    """All pages turned by 90 / 180 / 270 degrees clockwise: a new file, the original is not touched."""
    degrees = degrees if degrees in (90, 180, 270) else 90
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    tmp = job.new_tmp(odir, "pdf")
    job.report(None)
    if Tools.find("qpdf"):
        run_cmd(job, [Tools.find("qpdf"), "--warning-exit-0", "--rotate=+%d" % degrees, src, tmp])
    elif Tools.find("pdftk"):
        run_cmd(job, [Tools.find("pdftk"), src, "cat", "1-end" + {90: "east", 180: "south", 270: "west"}[degrees], "output", tmp])
    else:
        raise ToolMissing("qpdf")
    if not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
        raise ConvError(tr("err_no_output"))
    return job.commit(tmp, odir, stem + "_rotated", "pdf")


def pdf_split(job, src):
    odir = out_dir_for(src, job.opts)
    stem, _sext = split_name(src)
    work = job.new_tmp(odir, "")
    os.makedirs(work)
    job.report(None)
    if Tools.find("pdfseparate"):
        run_cmd(job, [Tools.find("pdfseparate"), src, os.path.join(work, "page-%d.pdf")])
    elif Tools.find("qpdf"):
        run_cmd(job, [Tools.find("qpdf"), src, "--split-pages", os.path.join(work, "page.pdf")])
    else:
        raise ToolMissing("poppler-utils (pdfseparate)")
    if not os.listdir(work):
        raise ConvError(tr("err_no_output"))
    return job.commit(work, odir, stem, "")


# ============================================================================
#  [7] Planning: the targets on the wheel, the controls of the options page, the pipelines, the queue
#      The wheel only asks "to what?". Everything else (how hard to compress, size, rotation, tools) is decided on the
#      options page and travels with the job as plain values in job.opts, so one run does it all.
# ============================================================================
IMAGE_TARGET_ORDER = ["JPG", "PNG", "WEBP", "PDF", "ICO", "AVIF", "BMP", "TIFF", "GIF", "HEIC"]
IMAGE_SAME_OK = ("JPG", "PNG", "WEBP", "BMP", "TIFF")          # formats that can be "kept": compressed / edited in place
AUDIO_FORMATS = ("MP3", "M4A", "OGG", "OPUS", "FLAC", "WAV")
AUDIO_LOSSY = ("MP3", "M4A", "OGG", "OPUS")
VIDEO_SAME = {"mp4": "MP4", "mkv": "MKV", "mov": "MOV", "webm": "WEBM"}
MAX_SLICES = 8

# One compression scale for every kind of file: how hard to squeeze.
LEVELS = ("none", "light", "balanced", "strong", "extreme")
IMG_Q = {"none": 95, "light": 85, "balanced": 72, "strong": 55, "extreme": 38}
IMG_MAXDIM = {"none": None, "light": None, "balanced": None, "strong": 2560, "extreme": 1600}
PNG_COLORS = {"none": None, "light": None, "balanced": 90, "strong": 75, "extreme": 55}
AUD_KBPS = {"none": 320, "light": 192, "balanced": 128, "strong": 96, "extreme": 64}
VID_CRF = {"none": 20, "light": 25, "balanced": 28, "strong": 30, "extreme": 33}
VID_MAXH = {"none": None, "light": None, "balanced": None, "strong": 720, "extreme": 480}
PDF_GS = {"none": "printer", "light": "printer", "balanced": "ebook", "strong": "screen", "extreme": "extreme"}
PDF_DPI = {"printer": 300, "ebook": 150, "screen": 72, "extreme": 60}
ARCHIVE_LEVELS = (3, 6, 9)


class Action:
    """One slice of the wheel = one target. `fn(job)` does the work and reads every choice from job.opts."""
    def __init__(self, key, label, caption, fn, batch=False, title=None, cat="", fmt="", same=False, op=""):
        self.key = key
        self.label = label          # big text on the slice
        self.caption = caption      # small text under it
        self.fn = fn
        self.batch = batch          # True: all dropped files are handled by ONE job (archive / merge)
        self.title = title or label # shown in the job list
        self.cat = cat              # kind of the files: image | audio | video | pdf | ...
        self.fmt = fmt              # target format ("JPG", "ZIP", ...) or ""
        self.same = same            # True: keep the format (compress / edit without converting)
        self.op = op                # what it does: img | img-pdf | aud | vid | vid-aud | vid-gif | pdf-img | pdf-same | arc ...


class Opt:
    """One control of the options page.  kind: level (compression chips + a hint line) | chips | switch."""
    def __init__(self, key, kind, label, choices=(), default=None, sub=""):
        self.key = key              # name of the value in job.opts
        self.kind = kind
        self.label = label          # already translated
        self.choices = list(choices)  # [(value, translated text)]
        self.default = default
        self.sub = sub              # small explanation under a switch (translated)


def categorize(path):
    if os.path.isdir(path):
        return "folder"
    _stem, ext = split_name(path)
    if is_archive_ext(ext):
        return "archive"
    if ext in IMG_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in SUB_EXTS:
        return "subtitle"
    if ext in TEXT_EXTS:
        return "text"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def files_category(files):
    cats = {categorize(f) for f in files}
    return cats.pop() if len(cats) == 1 else "mixed"


def describe_files(files):
    """(title, subtitle) for the centre of the wheel."""
    n = len(files)
    if n == 1:
        f = files[0]
        cat = categorize(f)
        try:
            if os.path.isdir(f):
                extra = tr("n_items", len(os.listdir(f)))
            else:
                extra = human_size(os.path.getsize(f))
        except OSError:
            extra = ""
        return os.path.basename(f), (tr("cat_" + cat) + ((" · " + ltr(extra)) if extra else ""))
    cat = files_category(files)
    return tr("n_files", n), tr("cat_" + cat)


def _norm_img_fmt(ext):
    return {"jpg": "JPG", "jpeg": "JPG", "jpe": "JPG", "tif": "TIFF", "tiff": "TIFF", "heif": "HEIC"}.get(ext, ext.upper())


# ------------------------------------------------------- reading the choices --
def _lvl(o, same=False, default="light"):
    v = o.get("level")
    return v if v in LEVELS else ("balanced" if same else default)


def _scale(o):
    return SCALE_FACTORS[int(o.get("scale_idx", 0) or 0) % 4]


def _only_squeezing(o, lvl):
    """The only thing asked for is a smaller file: then "no gain" is a result worth telling about."""
    return (lvl != "none" and not o.get("rotate") and not o.get("scale_idx") and not o.get("strip_meta"))


def _suffix(same, lvl):
    if not same:
        return ""
    return "_compressed" if lvl != "none" else "_edited"


# ------------------------------------------------------------ job functions --
def _fn_image(fmt, same=False):
    def fn(job):
        o = job.opts
        src = job.files[0]
        lvl = _lvl(o, same)
        scale, rot = _scale(o), int(o.get("rotate", 0) or 0)
        if (same and fmt == "JPG" and lvl == "none" and o.get("strip_meta") and scale >= 0.999 and not rot
                and split_name(src)[1] in JPEG_EXTS):
            return [image_strip_meta(job, src, "_edited")]            # only "remove the camera / GPS data": lossless
        return [image_convert(job, src, fmt, quality=IMG_Q[lvl], scale=scale, strip=bool(o.get("strip_meta")),
                              suffix=_suffix(same, lvl), max_dim=IMG_MAXDIM[lvl], rotate=rot,
                              png_level=(9 if lvl != "none" else 6), png_colors=PNG_COLORS[lvl] if fmt == "PNG" else None,
                              gain_check=same and _only_squeezing(o, lvl))]
    return fn


def _fn_images_pdf(job):
    o = job.opts
    lvl = _lvl(o)
    odir = out_dir_for(job.files[0], o)
    stem = split_name(job.files[0])[0] if len(job.files) == 1 else archive_name_for(job.files)
    tmp = job.new_tmp(odir, "pdf")
    pdf_from_images(job, job.files, tmp, IMG_Q[lvl], _scale(o), int(o.get("rotate", 0) or 0), IMG_MAXDIM[lvl])
    return [job.commit(tmp, odir, stem, "pdf")]


def _fn_gif_mp4(job):
    return [gif_to_mp4(job, job.files[0])]


def _fn_audio(fmt, same=False):
    def fn(job):
        o = job.opts
        src = job.files[0]
        lvl = _lvl(o, same)
        lossy = fmt in AUDIO_LOSSY
        kb = AUD_KBPS[lvl] if lossy else None
        q = int(round(10 + (kb - 64) / 256.0 * 90)) if kb else 90
        squeeze = same and lossy and lvl != "none" and not o.get("mono") and not o.get("normalize") and not o.get("strip_meta")
        out = audio_convert(job, src, fmt, quality=q, kbps=kb, mono=bool(o.get("mono")), normalize=bool(o.get("normalize")),
                            strip=bool(o.get("strip_meta")), suffix=_suffix(same, lvl if lossy else "none"),
                            trim=bool(o.get("trim_silence")))
        squeeze = squeeze and not o.get("trim_silence")
        if squeeze and os.path.getsize(out) >= os.path.getsize(src):
            os.remove(out)
            raise NoGain(tr("err_no_gain"))
        return [out]
    return fn


def _fn_audio_merge(job):
    o = job.opts
    lvl = _lvl(o, False)
    kb = AUD_KBPS[lvl]
    q = int(round(10 + (kb - 64) / 256.0 * 90))
    return [audio_merge(job, job.files, "MP3", quality=q, kbps=kb, mono=bool(o.get("mono")), normalize=bool(o.get("normalize")),
                        strip=bool(o.get("strip_meta")))]


def _fn_video_frame(job):
    return [video_frame(job, job.files[0])]


def _fn_video(fmt, same=False):
    def fn(job):
        o = job.opts
        src = job.files[0]
        lvl = _lvl(o, same, default="none")                          # converting a video: "none" = just re-wrap when possible
        rot = int(o.get("rotate", 0) or 0)
        heights = [h for h in (VID_MAXH[lvl], VIDEO_HEIGHTS[int(o.get("scale_idx", 0) or 0) % 4]) if h]
        abr = 160 if lvl in ("none", "light") else 128 if lvl == "balanced" else 96 if lvl == "strong" else 64
        out = video_convert(job, src, fmt, crf=VID_CRF[lvl], max_h=min(heights) if heights else None,
                            mute=bool(o.get("mute")), strip=bool(o.get("strip_meta")), rotate=rot, copy_ok=(lvl == "none"),
                            preset="medium" if same else "veryfast", suffix=_suffix(same, lvl), abr=abr)
        if (same and _only_squeezing(o, lvl) and not o.get("mute") and not heights
                and os.path.getsize(out) >= os.path.getsize(src)):
            os.remove(out)
            raise NoGain(tr("err_no_gain"))
        return [out]
    return fn


def _fn_archive(fmt):
    def fn(job):
        o = job.opts
        return [archive_create(job, job.files, fmt, int(o.get("archive_level", 6)), o.get("keep_root", True),
                               o.get("include_hidden", True))]
    return fn


def _fn_extract(job):
    return [archive_extract(job, job.files[0])]


def _fn_repack(fmt):
    def fn(job):
        return [archive_repack(job, job.files[0], fmt, int(job.opts.get("archive_level", 6)))]
    return fn


def _fn_doc(fmt):
    def fn(job):
        return [doc_convert(job, job.files[0], fmt)]
    return fn


def _fn_subs(fmt):
    def fn(job):
        return [subs_convert(job, job.files[0], fmt)]
    return fn


def _fn_pdf_images(fmt):
    def fn(job):
        return [pdf_to_images(job, job.files[0], fmt, IMG_Q[_lvl(job.opts)])]
    return fn


def _fn_pdf_text(job):
    return [pdf_to_text(job, job.files[0])]


def _fn_pdf_same(job):
    return [pdf_compress(job, job.files[0], PDF_GS[_lvl(job.opts, True)])]


def _fn_pdf_merge(job):
    return [pdf_merge(job)]


def _fn_pdf_rotate(job):
    return [pdf_rotate(job, job.files[0], int(job.opts.get("rotate", 90) or 90))]


def _fn_pdf_split(job):
    return [pdf_split(job, job.files[0])]


# ------------------------------------------------------------ the wheel --
def _archive_actions(files):
    caps = {"ZIP": tr("cap_universal"), "7Z": tr("cap_strong"), "TAR.GZ": "Linux", "TAR.XZ": tr("cap_strong")}
    acts = []
    for fmt in ("ZIP", "7Z", "TAR.GZ", "TAR.XZ"):
        if archive_format_available(fmt):
            acts.append(Action("arc:" + fmt, fmt, caps[fmt], _fn_archive(fmt), batch=True, fmt=fmt, op="arc",
                               cat=files_category(files)))
    return acts


def _wheel_actions(files, cat):
    n = len(files)
    exts = {split_name(f)[1] for f in files}
    first_ext = split_name(files[0])[1]
    acts = []
    if cat == "image":
        src_fmts = {_norm_img_fmt(e) for e in exts}
        keep = next(iter(src_fmts)) if len(src_fmts) == 1 else None
        if "gif" in exts and Tools.ffmpeg():
            acts.append(Action("img:mp4", "MP4", tr("cap_video"), _fn_gif_mp4, cat=cat, fmt="MP4", op="gif-mp4"))
        for f in IMAGE_TARGET_ORDER:
            if not image_format_available(f):
                continue
            if keep == f:
                if f in IMAGE_SAME_OK:
                    acts.append(Action("img:same", f, tr("cap_same"), _fn_image(f, True), cat=cat, fmt=f, same=True, op="img"))
                continue
            if f == "PDF" and n > 1:
                acts.append(Action("img:pdf-merge", "PDF", tr("cap_merge"), _fn_images_pdf, batch=True, cat=cat, fmt="PDF",
                                   op="img-pdf"))
                continue
            cap = {"PNG": tr("cap_lossless"), "BMP": tr("cap_lossless"), "TIFF": tr("cap_lossless"),
                   "ICO": tr("cap_icon"), "PDF": tr("cap_document")}.get(f, "")
            acts.append(Action("img:" + f.lower(), f, cap, _fn_image(f), cat=cat, fmt=f, op="img"))
    elif cat == "audio":
        src_fmts = {audio_fmt_for_ext(e) for e in exts}
        keep = next(iter(src_fmts)) if len(src_fmts) == 1 else None
        for f in AUDIO_FORMATS:
            if not audio_format_available(f):
                continue
            if keep == f:
                acts.append(Action("aud:same", f, tr("cap_same"), _fn_audio(f, True), cat=cat, fmt=f, same=True, op="aud"))
                continue
            acts.append(Action("aud:" + f.lower(), f, tr("cap_lossless") if f in ("FLAC", "WAV") else "", _fn_audio(f),
                               cat=cat, fmt=f, op="aud"))
        if n > 1 and audio_format_available("MP3"):
            acts.append(Action("aud:merge", tr("t_join"), "MP3", _fn_audio_merge, batch=True, cat=cat, fmt="MP3", op="aud-merge"))
    elif cat == "video":
        if Tools.ffmpeg():
            webm_ok = Tools.has_encoder("libvpx-vp9") and Tools.has_encoder("libopus")
            for f, cap in (("MP4", "H.264"), ("MKV", "H.264"), ("WEBM", "VP9"), ("MOV", "H.264"), ("GIF", tr("cap_animation"))):
                if f == "WEBM" and not webm_ok:
                    continue
                if len(exts) == 1 and VIDEO_SAME.get(first_ext) == f:
                    acts.append(Action("vid:same", f, tr("cap_same"), _fn_video(f, True), cat=cat, fmt=f, same=True, op="vid"))
                    continue
                if f.lower() in exts:
                    continue
                acts.append(Action("vid:" + f.lower(), f, cap, _fn_video(f), cat=cat, fmt=f,
                                   op="vid-gif" if f == "GIF" else "vid"))
            for f in ("MP3", "M4A"):
                if audio_format_available(f):
                    acts.append(Action("vid:" + f.lower(), f, tr("cap_audio_only"), _fn_audio(f), cat=cat, fmt=f, op="vid-aud"))
            acts.append(Action("vid:frame", "JPG", tr("cap_frame"), _fn_video_frame, cat=cat, fmt="JPG", op="vid-frame"))
    elif cat == "pdf":
        if Tools.find("pdftoppm") or Tools.gs():
            acts.append(Action("pdf:png", "PNG", tr("cap_pages"), _fn_pdf_images("PNG"), cat=cat, fmt="PNG", op="pdf-img"))
            acts.append(Action("pdf:jpg", "JPG", tr("cap_pages"), _fn_pdf_images("JPG"), cat=cat, fmt="JPG", op="pdf-img"))
        if Tools.find("pdftotext"):
            acts.append(Action("pdf:txt", "TXT", tr("cap_text"), _fn_pdf_text, cat=cat, fmt="TXT", op="pdf-txt"))
        if n > 1 and (Tools.find("pdfunite") or Tools.find("qpdf")):
            acts.append(Action("pdf:merge", tr("t_merge"), "PDF", _fn_pdf_merge, batch=True, cat=cat, fmt="PDF", op="pdf-merge"))
        if n == 1 and (Tools.find("pdfseparate") or Tools.find("qpdf")):
            acts.append(Action("pdf:split", tr("t_split"), tr("cap_pages"), _fn_pdf_split, cat=cat, fmt="PDF", op="pdf-split"))
        if Tools.find("qpdf") or Tools.find("pdftk"):
            acts.append(Action("pdf:rotate", tr("t_rotate"), "PDF", _fn_pdf_rotate, cat=cat, fmt="PDF", op="pdf-rotate"))
        if Tools.gs():
            acts.append(Action("pdf:same", "PDF", tr("cap_same"), _fn_pdf_same, cat=cat, fmt="PDF", same=True, op="pdf-same"))
    elif cat in ("doc", "text"):
        for f in doc_target_formats(first_ext):
            acts.append(Action("doc:" + f.lower(), f, tr("cap_document") if f in ("PDF", "DOCX", "ODT") else "", _fn_doc(f),
                               cat=cat, fmt=f, op="doc"))
    elif cat == "subtitle":
        for f in ("SRT", "VTT", "TXT"):
            if f.lower() not in exts:
                acts.append(Action("sub:" + f.lower(), f, tr("cap_text") if f == "TXT" else "", _fn_subs(f), cat=cat, fmt=f,
                                   op="sub"))
    elif cat == "archive":
        acts.append(Action("arc:extract", tr("a_extract"), tr("cap_extract"), _fn_extract, cat=cat, op="extract"))
        norm_exts = {{"tgz": "tar.gz", "txz": "tar.xz"}.get(e, e) for e in exts}
        for f in ("ZIP", "7Z", "TAR.GZ", "TAR.XZ"):
            if archive_format_available(f) and ARCHIVE_FMT_EXT[f] not in norm_exts:
                acts.append(Action("arc:re:" + f, f, tr("cap_repack"), _fn_repack(f), cat=cat, fmt=f, op="arc-re"))
    else:  # folder / other / mixed
        acts = _archive_actions(files)
    extras = []
    if n > 1 and cat in ("image", "audio", "video", "pdf", "doc", "text"):      # several files: "put them all in one archive"
        extras = [a for a in _archive_actions(files) if a.label in ("ZIP", "7Z")][:1]
    return acts[:MAX_SLICES - len(extras)] + extras


def build_actions(files, opts=None):
    """The slices of the wheel for these files (nothing but targets: the rest is chosen on the options page)."""
    files = [f for f in files if os.path.lexists(f)]
    if not files:
        return []
    return _wheel_actions(files, files_category(files))


# ----------------------------------------------------------- options page --
_LEVEL_KEY = {"img": "lvl_img", "img-pdf": "lvl_img", "aud": "lvl_aud", "vid-aud": "lvl_aud", "aud-merge": "lvl_aud", "vid": "lvl_vid",
              "pdf-img": "lvl_pdf", "pdf-same": "lvl_pdf"}


def default_level(op, builtin, with_none=True):
    """The level the options page starts at: the one chosen in the settings, or the built-in one for this kind of work."""
    key = _LEVEL_KEY.get(op)
    v = SETTINGS.get(key) if (key and SETTINGS is not None) else "auto"
    if v in LEVELS and (with_none or v != "none"):
        return v
    return builtin


def _level_choices(with_none=True):
    return [(l, tr("lv_" + l)) for l in (LEVELS if with_none else LEVELS[1:])]


def _size_choices(video=False):
    if video:
        return [(0, tr("size_orig")), (1, "1080p"), (2, "720p"), (3, "480p")]
    return [(0, "100%"), (1, "75%"), (2, "50%"), (3, "25%")]


def _rotate_choices():
    return [(0, tr("rot_none")), (90, tr("rot_right")), (270, tr("rot_left")), (180, "180°")]


def option_specs(act, files):
    """The controls of the options page for this slice. An empty list means "nothing to choose": it runs at once."""
    base = SETTINGS.snapshot() if SETTINGS is not None else dict(DEFAULT_SETTINGS)
    op, fmt, same = act.op, act.fmt, act.same
    out = []
    if op in ("img", "img-pdf"):
        if fmt in ("JPG", "WEBP", "AVIF", "HEIC", "PNG", "PDF"):
            out.append(Opt("level", "level", tr("o_compress"), _level_choices(), default_level(op, "balanced" if same else "light")))
        if fmt != "ICO":
            out.append(Opt("scale_idx", "chips", tr("o_size"), _size_choices(), 0))
            out.append(Opt("rotate", "chips", tr("o_rotate"), _rotate_choices(), 0))
        if same and fmt == "JPG":
            out.append(Opt("strip_meta", "switch", tr("o_strip"), default=bool(base["strip_meta"]), sub=tr("o_strip_sub")))
    elif op in ("aud", "vid-aud"):
        if fmt in AUDIO_LOSSY:
            out.append(Opt("level", "level", tr("o_compress"), _level_choices(), default_level(op, "balanced" if same else "light")))
        out.append(Opt("mono", "switch", tr("o_mono"), default=bool(base["mono"]), sub=tr("o_mono_sub")))
        out.append(Opt("normalize", "switch", tr("o_normalize"), default=bool(base["normalize"]), sub=tr("o_normalize_sub")))
        if op == "aud":
            out.append(Opt("trim_silence", "switch", tr("o_trim"), default=bool(base["trim_silence"]), sub=tr("o_trim_sub")))
        out.append(Opt("strip_meta", "switch", tr("o_strip"), default=bool(base["strip_meta"]), sub=tr("o_strip_sub_av")))
    elif op == "aud-merge":
        out.append(Opt("level", "level", tr("o_compress"), _level_choices(), default_level("aud", "light")))
        out.append(Opt("mono", "switch", tr("o_mono"), default=bool(base["mono"]), sub=tr("o_mono_sub")))
        out.append(Opt("normalize", "switch", tr("o_normalize"), default=bool(base["normalize"]), sub=tr("o_normalize_sub")))
        out.append(Opt("strip_meta", "switch", tr("o_strip"), default=bool(base["strip_meta"]), sub=tr("o_strip_sub_av")))
    elif op == "vid":
        out.append(Opt("level", "level", tr("o_compress"), _level_choices(), default_level(op, "balanced" if same else "none")))
        out.append(Opt("scale_idx", "chips", tr("o_size"), _size_choices(True), 0))
        out.append(Opt("rotate", "chips", tr("o_rotate"), _rotate_choices(), 0))
        out.append(Opt("mute", "switch", tr("o_mute"), default=bool(base["mute"]), sub=tr("o_mute_sub")))
        out.append(Opt("strip_meta", "switch", tr("o_strip"), default=bool(base["strip_meta"]), sub=tr("o_strip_sub_av")))
    elif op == "pdf-img" and fmt == "JPG":
        out.append(Opt("level", "level", tr("o_compress"), _level_choices(), default_level(op, "light")))
    elif op == "pdf-same":
        out.append(Opt("level", "level", tr("o_compress"), _level_choices(False), default_level(op, "balanced", False)))
    elif op == "pdf-rotate":
        out.append(Opt("rotate", "chips", tr("o_rotate"), [(90, tr("rot_right")), (270, tr("rot_left")), (180, "180°")], 90))
    elif op in ("arc", "arc-re"):
        out.append(Opt("archive_level", "chips", tr("o_arc_level"),
                       [(3, tr("arc_fast")), (6, tr("arc_normal")), (9, tr("arc_max"))], int(base["archive_level"])
                       if int(base["archive_level"]) in ARCHIVE_LEVELS else 6))
        if op == "arc" and any(os.path.isdir(f) for f in files):
            out.append(Opt("include_hidden", "switch", tr("o_hidden"), default=bool(base["include_hidden"]),
                           sub=tr("o_hidden_sub")))
            if len(files) == 1:
                out.append(Opt("keep_root", "switch", tr("o_keep_root"), default=bool(base["keep_root"]),
                               sub=tr("o_keep_root_sub")))
    return out


def general_specs():
    """The choices every kind of file has: where the result goes, what happens when the name exists, the original, opening,
    the notification. They start from the settings and what is changed in the card is kept as the new setting."""
    base = SETTINGS.snapshot() if SETTINGS is not None else dict(DEFAULT_SETTINGS)
    custom = base.get("out_mode") == "custom" and writable_dir(base.get("out_dir", ""))
    return [Opt("out_mode", "chips", tr("o_lbl_where"), [("source", tr("o_w_source")), ("custom", tr("o_w_folder"))],
                "custom" if custom else "source"),
            Opt("conflict", "chips", tr("o_lbl_exists"), [("rename", tr("o_c_keep")), ("replace", tr("o_c_replace"))],
                "replace" if base.get("conflict") == "replace" else "rename"),
            Opt("delete_original", "switch", tr("se_delete"), default=bool(base.get("delete_original")), sub=tr("se_delete_sub")),
            Opt("open_after", "switch", tr("se_open"), default=bool(base.get("open_after")), sub=tr("o_open_sub")),
            Opt("notify", "switch", tr("se_notify"), default=bool(base.get("notify")), sub=tr("o_notify_sub"))]


def level_hint(act, lvl):
    """The short line under the compression chips: what this level means for this slice."""
    op, fmt = act.op, act.fmt
    desc = tr("lvd_none_vid") if (lvl == "none" and op == "vid") else tr("lvd_" + lvl)
    bits = []
    if op in ("img", "img-pdf"):
        if fmt != "PNG":
            bits.append("%d%%" % IMG_Q[lvl])
        if IMG_MAXDIM[lvl]:
            bits.append("≤ %dpx" % IMG_MAXDIM[lvl])
    elif op == "pdf-img":
        bits.append("%d%%" % IMG_Q[lvl])
    elif op in ("aud", "vid-aud", "aud-merge"):
        bits.append("%d kbps" % AUD_KBPS[lvl])
    elif op == "vid":
        if VID_MAXH[lvl]:
            bits.append("≤ %dp" % VID_MAXH[lvl])
    elif op == "pdf-same":
        bits.append("%d dpi" % PDF_DPI[PDF_GS[lvl]])
    return desc + ((" · " + ltr(" · ".join(bits))) if bits else "")


def nothing_to_do(act, values):
    """Keeping the format with no compression and no tool selected would only copy the file."""
    if not act.same:
        return False
    if act.op == "pdf-same":
        return False
    return (values.get("level", "balanced") == "none" and not values.get("scale_idx") and not values.get("rotate")
            and not values.get("strip_meta") and not values.get("mono") and not values.get("normalize")
            and not values.get("mute") and not values.get("trim_silence"))


# ------------------------------------------------------------------- queue --
class JobQueue:
    """One worker thread: conversions run one after another (ffmpeg already uses all cores)."""
    def __init__(self):
        self.q = queue.Queue()
        self.thread = None

    def submit(self, job):
        job.state = "queued"
        job.emit()
        self.q.put(job)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, name="rumman-worker", daemon=True)
            self.thread.start()

    def _loop(self):
        while True:
            job = self.q.get()
            try:
                self._run(job)
            except Exception:
                log_error(traceback.format_exc())

    def _run(self, job):
        if job.cancelled:
            job.state = "cancelled"
            job.emit()
            return
        job.state = "running"
        job.progress = None
        job.t_start = time.monotonic()
        job.in_bytes = sum(path_size(f) for f in job.files)
        job.emit()
        try:
            res = job.action.fn(job)
            outs = res if isinstance(res, (list, tuple)) else [res]
            job.outputs = [x for x in outs if x]
            job.out_bytes = sum(path_size(x) for x in job.outputs)
            if job.opts.get("delete_original"):
                self._trash_originals(job)
            job.state = "done"
            job.progress = 1.0
        except Cancelled:
            job.state = "cancelled"
        except NoGain as e:
            job.state = "nogain"
            job.error = str(e)
        except ConvError as e:
            job.state = "error"
            job.error = str(e)
            log_error("%s: %s -> %s" % (job.action.key, job.files, e))
        except Exception as e:
            job.state = "error"
            job.error = "%s: %s" % (type(e).__name__, e)
            log_error(traceback.format_exc())
        finally:
            job.t_end = time.monotonic()
            job.cleanup()
            job.emit()

    @staticmethod
    def _trash_originals(job):
        """"Delete the original": only after every result exists and is not empty, never an original that is also a result,
        and only into the trash."""
        outs = [os.path.realpath(o) for o in job.outputs]
        if not outs or any(not os.path.lexists(o) or path_size(o) <= 0 for o in outs):
            return
        for f in job.files:
            if os.path.realpath(f) in outs or not os.path.lexists(f):
                continue
            if trash_path(f):
                job.trashed.append(f)


# ============================================================================
#  [8] The wheel: drawing + hit-testing.  Two colours: silver and burgundy.
#      Pure visuals: the only timer is the short animation tick.
# ============================================================================
WHEEL_SIZE = 340            # logical px: the window is a square of this size and the wheel sits in its middle
WHEEL_R_IN = 56.0
WHEEL_R_OUT = 130.0
WHEEL_GAP = 5.0             # visible gap between two slices
WHEEL_STROKE = 6.0          # same-colour stroke that rounds the slice corners


def _hex(h):
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


WINE = _hex("#7A1F3F")          # burgundy
WINE_DEEP = _hex("#3F0F23")
WINE_LIGHT = _hex("#A3305A")
WINE_HI = _hex("#8B2748")
WINE_LO = _hex("#651833")
SILVER = _hex("#C9CDD3")
SILVER_HI = _hex("#E8EAEE")
SILVER_LO = _hex("#A4AAB3")
CREAM = _hex("#F5F6F8")         # the pale "pearl" next to silver (kept under its old name)
INK = _hex("#24151B")
MUTED = _hex("#5E6470")
ROSE = _hex("#EADFE5")
RED = _hex("#B3263A")
UI_FONT = "IBM Plex Sans Arabic, Noto Sans Arabic, Cantarell, sans-serif"


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class WheelCanvas(Gtk.DrawingArea):
    """The wheel itself.

    state: loading  - the window is open but we do not know the files yet (ghost slices)
           ready    - silver slices with the formats that fit the files; the slice under the pointer turns burgundy
           empty    - files are known but nothing can be offered for them
           busy     - the chosen slice is being worked on (progress ring)
           done / error - result shown in the centre for a moment
    """

    def __init__(self, owner=None):
        super().__init__()
        self.owner = owner
        self.set_content_width(WHEEL_SIZE)
        self.set_content_height(WHEEL_SIZE)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)
        self.state = "loading"
        self.actions = []
        self.title = ""
        self.subtitle = ""
        self.message = ""
        self.active = -1            # slice under the pointer
        self.chosen = -1            # slice being worked on
        self.progress = None        # 0..1, None = unknown
        self.opaque = False         # no compositing manager: paint our own backdrop
        self._appear = 1.0
        self._ready = 0.0
        self._spin = 0.0
        self._tick_id = 0
        self._t_last = None
        self._closing = False
        self._on_closed = None
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: self.hover(x, y))
        motion.connect("leave", lambda c: self.hover(-1, -1))
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_release)
        self.add_controller(click)

    # ------------------------------------------------------------ public API --
    def pop_in(self):
        self._appear = 0.0
        self._closing = False
        self._on_closed = None
        self._kick()

    def fade_out(self, done=None):
        """Quick fade, then call done() (used before the window is hidden)."""
        if self._appear <= 0.01:
            if done:
                done()
            return
        self._closing = True
        self._on_closed = done
        self._kick()

    def nav(self, d):
        n = len(self.actions)
        if not n or self.state != "ready":
            return
        if self.active < 0:
            self.active = 0 if d > 0 else n - 1
        else:
            self.active = (self.active + d) % n
        self.queue_draw()

    def active_action(self):
        if self.state == "ready" and 0 <= self.active < len(self.actions):
            return self.actions[self.active]
        return None

    def set_note(self, message):
        self.state = "note"
        self.message = message
        self.chosen = -1
        self.queue_draw()

    def set_loading(self):
        self.state = "loading"
        self.actions = []
        self.title = self.subtitle = self.message = ""
        self.active = self.chosen = -1
        self.progress = None
        self._ready = 0.0
        self._kick()
        self.queue_draw()

    def set_actions(self, actions, title, subtitle, message=""):
        self.actions = list(actions)
        self.title, self.subtitle, self.message = title, subtitle, message
        self.state = "ready" if self.actions else "empty"
        if self.active >= len(self.actions):
            self.active = -1
        self.chosen = -1
        self._kick()
        self.queue_draw()

    def set_busy(self, index, progress=None, label=""):
        self.state = "busy"
        self.chosen = index
        self.progress = progress
        self.message = label
        self._kick()
        self.queue_draw()

    def set_done(self, message):
        self.state = "done"
        self.message = message
        self.progress = 1.0
        self.queue_draw()

    def set_error(self, message):
        self.state = "error"
        self.message = message
        self.queue_draw()

    def hover(self, x, y):
        i = self.slice_at(x, y) if x >= 0 else -1
        if self.state != "ready":
            i = -1
        if i != self.active:
            self.active = i
            self.queue_draw()

    def slice_at(self, x, y):
        n = len(self.actions)
        if not n:
            return -1
        cx, cy = self._center()
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        if r < WHEEL_R_IN - 2 or r > WHEEL_R_OUT + 26:
            return -1
        a = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi)
        step = 2 * math.pi / n
        return int(((a + step / 2) % (2 * math.pi)) / step) % n

    def slice_center(self, i):
        """Centre of slice i in widget coordinates (used by tests and keyboard picking)."""
        n = max(len(self.actions), 1)
        cx, cy = self._center()
        mid = i * (2 * math.pi / n) - math.pi / 2
        rm = (WHEEL_R_IN + WHEEL_R_OUT) / 2.0
        return cx + math.cos(mid) * rm, cy + math.sin(mid) * rm

    # -------------------------------------------------------------- internals --
    def _center(self):
        return (self.get_width() or WHEEL_SIZE) / 2.0, (self.get_height() or WHEEL_SIZE) / 2.0

    def _on_release(self, gesture, n_press, x, y):
        if self.owner is None:
            return
        i = self.slice_at(x, y)
        if i >= 0 and self.state == "ready":
            cb = getattr(self.owner, "wheel_pick", None)
            if cb:
                cb(i)

    def _kick(self):
        if not self._tick_id:
            self._t_last = None
            self._tick_id = self.add_tick_callback(self._tick)

    def _tick(self, widget, clock):
        t = clock.get_frame_time() / 1e6
        dt = 0.016 if self._t_last is None else min(0.05, max(0.001, t - self._t_last))
        self._t_last = t
        animating = False
        if self._closing:
            self._appear = max(0.0, self._appear - dt / 0.12)
            if self._appear <= 0.0:
                self._closing = False
                cb, self._on_closed = self._on_closed, None
                self._tick_id = 0
                self._t_last = None
                self.queue_draw()
                if cb:
                    cb()
                return False
            self.queue_draw()
            return True
        if self._appear < 1.0:
            self._appear = min(1.0, self._appear + dt / 0.16)
            animating = True
        target = 1.0 if self.state in ("ready", "busy", "done", "error", "note") else 0.0
        if abs(self._ready - target) > 0.004:
            self._ready += (target - self._ready) * min(1.0, dt * 14.0)
            if abs(self._ready - target) <= 0.004:
                self._ready = target
            animating = True
        if self.state == "busy" and self.progress is None:
            self._spin = (self._spin + dt * 4.2) % (2 * math.pi)
            animating = True
        self.queue_draw()
        if not animating:
            self._tick_id = 0
            self._t_last = None
            return False
        return True

    # --------------------------------------------------------------- drawing --
    def _font(self, px, weight=Pango.Weight.NORMAL):
        fd = Pango.FontDescription()
        fd.set_family(UI_FONT)
        fd.set_absolute_size(px * Pango.SCALE)
        fd.set_weight(weight)
        return fd

    def _text(self, cr, s, x, y, px, rgb, alpha, weight=Pango.Weight.NORMAL, width=None, shrink=0.0,
              ellipsize=None, lines=1):
        """Centred text. `shrink` lets a long label get smaller (down to px*shrink) before it is cut."""
        if not s:
            return 0, 0

        def make(sz):
            layout = self.create_pango_layout(s)
            layout.set_font_description(self._font(sz, weight))
            layout.set_alignment(Pango.Alignment.CENTER)
            return layout
        layout = make(px)
        if width and shrink and lines == 1:
            nat = layout.get_pixel_size()[0]
            if nat > width:
                layout = make(max(px * shrink, px * width / float(nat)))
        if width:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_ellipsize(ellipsize if ellipsize is not None else Pango.EllipsizeMode.END)
            if lines > 1:
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                layout.set_height(-lines)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
        # with a layout width set, Pango centres the line inside that width, so the box (not the line) is centred on x
        cr.move_to(x - (width / 2.0 if width else tw / 2.0), y - th / 2.0)
        PangoCairo.show_layout(cr, layout)
        return tw, th

    def _fit(self, s, px, weight, width, min_scale, max_lines=2):
        """Layout for a slice label: shrink first, then wrap into up to `max_lines` lines, then cut with an ellipsis."""
        def make(sz, wrap):
            layout = self.create_pango_layout(s)
            layout.set_font_description(self._font(sz, weight))
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_width(int(width * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            if wrap:
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                layout.set_height(-max_lines)
            return layout
        probe = self.create_pango_layout(s)
        probe.set_font_description(self._font(px, weight))
        nat = probe.get_pixel_size()[0]
        if nat <= width:
            return make(px, False)
        sz = max(px * min_scale, px * width / float(nat))
        probe.set_font_description(self._font(sz, weight))
        if probe.get_pixel_size()[0] <= width:
            return make(sz, False)
        return make(max(px * min_scale, px * 0.8), True)       # too long even when small: two lines

    def _slice_text(self, cr, act, tx, ty, width, col, a):
        lab = self._fit(act.label, 15, Pango.Weight.SEMIBOLD, width, 0.72)
        lw, lh = lab.get_pixel_size()
        cap = None
        ch = 0
        if act.caption:
            cap = self._fit(act.caption, 10.5, Pango.Weight.NORMAL, width, 0.80, 2)
            ch = cap.get_pixel_size()[1]
        total = lh + (3 + ch if cap else 0)
        top = ty - total / 2.0
        cr.set_source_rgba(col[0], col[1], col[2], a)
        cr.move_to(tx - width / 2.0, top)
        PangoCairo.show_layout(cr, lab)
        if cap is not None:
            cr.set_source_rgba(col[0], col[1], col[2], 0.80 * a)
            cr.move_to(tx - width / 2.0, top + lh + 3)
            PangoCairo.show_layout(cr, cap)

    @staticmethod
    def _rounded(cr, x, y, w, h, r):
        cr.new_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    @staticmethod
    def _slice_path(cr, cx, cy, ri, ro, i, n):
        """Slice i: straight parallel edges, a constant gap between neighbours, rounded by the stroke."""
        if n == 1:
            cr.new_path()
            cr.arc(cx, cy, ro, 0, 2 * math.pi)
            cr.new_sub_path()
            cr.arc_negative(cx, cy, ri, 2 * math.pi, 0)
            return
        step = 2 * math.pi / n
        t0 = -math.pi / 2 + (i - 0.5) * step
        t1 = t0 + step
        g = WHEEL_GAP + WHEEL_STROKE

        def off(r):
            return math.asin(min(0.99, g / (2.0 * r)))
        cr.new_path()
        cr.arc(cx, cy, ro, t0 + off(ro), t1 - off(ro))
        cr.arc_negative(cx, cy, ri, t1 - off(ri), t0 + off(ri))
        cr.close_path()

    def _shadow(self, cr, cx, cy, ro, p):
        g = cairo.RadialGradient(cx, cy + 6, ro - 12, cx, cy + 6, ro + 28)
        g.add_color_stop_rgba(0.0, 0.10, 0.02, 0.05, 0.50 * p)
        g.add_color_stop_rgba(1.0, 0.10, 0.02, 0.05, 0.0)
        cr.set_source(g)
        cr.new_path()
        cr.arc(cx, cy + 6, ro + 28, 0, 2 * math.pi)
        cr.fill()

    @staticmethod
    def _silver(cx, cy, ro, a):
        """Brushed silver: one vertical gradient across the whole wheel, so neighbouring slices share the same sheen."""
        g = cairo.LinearGradient(cx, cy - ro, cx, cy + ro)
        g.add_color_stop_rgba(0.0, SILVER_HI[0], SILVER_HI[1], SILVER_HI[2], a)
        g.add_color_stop_rgba(0.5, SILVER[0], SILVER[1], SILVER[2], a)
        g.add_color_stop_rgba(1.0, SILVER_LO[0], SILVER_LO[1], SILVER_LO[2], a)
        return g

    @staticmethod
    def _burgundy(cx, cy, ro, a):
        g = cairo.LinearGradient(cx, cy - ro, cx, cy + ro)
        g.add_color_stop_rgba(0.0, WINE_HI[0], WINE_HI[1], WINE_HI[2], a)
        g.add_color_stop_rgba(1.0, WINE_LO[0], WINE_LO[1], WINE_LO[2], a)
        return g

    def _draw_slices(self, cr, cx, cy, n, alpha, ghost):
        ri, ro = WHEEL_R_IN + WHEEL_STROKE / 2, WHEEL_R_OUT - WHEEL_STROKE / 2
        busy = (self.state == "busy")
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        for i in range(n):
            is_act = (not ghost) and (i == self.active or (busy and i == self.chosen) or
                                      (self.state in ("done", "error") and i == self.chosen))
            dim = (busy or self.state in ("done", "error")) and not is_act
            a = alpha * (0.42 if dim else 1.0)
            rout = ro + (8 if is_act else 0)
            self._slice_path(cr, cx, cy, ri, rout, i, n)
            if is_act:
                for lw, al in ((20, 0.10), (13, 0.14), (7, 0.22)):
                    cr.set_source_rgba(WINE_LIGHT[0], WINE_LIGHT[1], WINE_LIGHT[2], al * alpha)
                    cr.set_line_width(lw)
                    cr.stroke_preserve()
                cr.set_source(self._burgundy(cx, cy, ro, alpha))
            else:
                cr.set_source(self._silver(cx, cy, ro, a * (0.55 if ghost else 1.0)))
            cr.fill_preserve()
            cr.set_line_width(WHEEL_STROKE)
            cr.stroke()
            if ghost:
                continue
            act = self.actions[i]
            mid = i * (2 * math.pi / n) - math.pi / 2
            rm = (WHEEL_R_IN + WHEEL_R_OUT) / 2.0 + (3 if is_act else 0)
            tx, ty = cx + math.cos(mid) * rm, cy + math.sin(mid) * rm
            col = CREAM if is_act else WINE_DEEP
            if n > 1:
                chord = 2 * rm * math.sin(math.pi / n) * 0.80
                ring = (WHEEL_R_OUT - WHEEL_R_IN) * 0.92 / max(abs(math.cos(mid)), 0.5)
                width = max(46.0, min(chord, ring))
            else:
                width = 110.0
            self._slice_text(cr, act, tx, ty, width, col, a)

    def _draw_empty_ring(self, cr, cx, cy, p):
        """Nothing to offer for these files: a calm, empty silver ring instead of slices."""
        rm = (WHEEL_R_IN + WHEEL_R_OUT) / 2.0
        cr.new_path()
        cr.arc(cx, cy, rm, 0, 2 * math.pi)
        cr.set_source(self._silver(cx, cy, WHEEL_R_OUT, 0.55 * p))
        cr.set_line_width(WHEEL_R_OUT - WHEEL_R_IN - 2 * WHEEL_GAP)
        cr.stroke()

    def _draw_hub(self, cr, cx, cy, p):
        hub_r = WHEEL_R_IN - 6
        cr.new_path()
        cr.arc(cx, cy, hub_r, 0, 2 * math.pi)
        cr.set_source_rgba(CREAM[0], CREAM[1], CREAM[2], p)
        cr.fill_preserve()
        lit = self.active >= 0 or self.state in ("busy", "done")
        if self.state == "error":
            cr.set_source_rgba(RED[0], RED[1], RED[2], 0.9 * p)
            cr.set_line_width(2.5)
        elif lit:
            cr.set_source_rgba(WINE[0], WINE[1], WINE[2], 0.95 * p)
            cr.set_line_width(2.5)
        else:
            cr.set_source_rgba(SILVER_LO[0], SILVER_LO[1], SILVER_LO[2], 0.85 * p)
            cr.set_line_width(1.5)
        cr.stroke()
        w = hub_r * 1.72
        st = self.state
        if st == "loading":
            self._text(cr, tr("wheel_loading"), cx, cy, 10.5, MUTED, 0.9 * p, width=w, shrink=0.8)
        elif st in ("ready", "empty"):
            up = 8 if st == "empty" else 0
            if self.title:
                self._text(cr, self.title, cx, cy - 7 - up, 12.5, WINE, p, Pango.Weight.SEMIBOLD, w,
                           ellipsize=Pango.EllipsizeMode.MIDDLE)
                self._text(cr, self.subtitle, cx, cy + 10 - up, 9.5, MUTED, 0.95 * p, width=w, shrink=0.8)
            if st == "empty":
                self._text(cr, self.message or tr("wheel_nothing"), cx, cy + 27, 8.5, MUTED, 0.95 * p, width=w,
                           shrink=0.9, lines=2)
        elif st == "busy":
            pct = "…" if self.progress is None else "%d%%" % int(round(self.progress * 100))
            self._text(cr, ltr(pct), cx, cy - 6, 19, WINE, p, Pango.Weight.SEMIBOLD, w)
            self._text(cr, self.message or tr("wheel_busy"), cx, cy + 14, 9.5, MUTED, 0.95 * p, width=w, shrink=0.8)
        elif st == "done":
            cr.set_source_rgba(WINE[0], WINE[1], WINE[2], p)
            cr.set_line_width(4.5)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.move_to(cx - 13, cy - 8)
            cr.line_to(cx - 4, cy + 1)
            cr.line_to(cx + 14, cy - 17)
            cr.stroke()
            self._text(cr, self.message, cx, cy + 16, 9.5, MUTED, 0.95 * p, width=w, shrink=0.8,
                       ellipsize=Pango.EllipsizeMode.MIDDLE)
        elif st == "error":
            self._text(cr, self.message or tr("wheel_failed"), cx, cy, 10, RED, p, Pango.Weight.SEMIBOLD, w,
                       shrink=0.85, lines=3)
        elif st == "note":
            self._text(cr, self.message, cx, cy, 10.5, MUTED, p, Pango.Weight.SEMIBOLD, w, shrink=0.85, lines=3)

    def _draw_progress(self, cr, cx, cy, p):
        r = WHEEL_R_OUT + 12
        cr.new_path()
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        if self.opaque:
            cr.set_source_rgba(SILVER_HI[0], SILVER_HI[1], SILVER_HI[2], 0.25 * p)
        else:
            cr.set_source_rgba(1, 1, 1, 0.65 * p)               # a pale halo, so the burgundy arc reads on any wallpaper
        cr.set_line_width(7)
        cr.stroke()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        arc = SILVER_HI if self.opaque else WINE
        cr.set_source_rgba(arc[0], arc[1], arc[2], 0.97 * p)
        cr.set_line_width(4)
        cr.new_path()
        if self.progress is None:
            a0 = -math.pi / 2 + self._spin
            cr.arc(cx, cy, r, a0, a0 + math.pi / 2)
        else:
            cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * max(0.02, self.progress))
        cr.stroke()

    def _draw(self, area, cr, w, h):
        p = ease_out(self._appear)
        if p <= 0.003:
            return
        cx, cy = w / 2.0, h / 2.0
        cr.save()
        sc = 0.86 + 0.14 * p
        cr.translate(cx, cy)
        cr.scale(sc, sc)
        cr.translate(-cx, -cy)
        if self.opaque:                                   # no compositor: our own backdrop instead of transparency
            self._rounded(cr, 6, 6, w - 12, h - 12, 36)
            cr.set_source_rgba(WINE_DEEP[0], WINE_DEEP[1], WINE_DEEP[2], p)
            cr.fill()
        else:
            self._shadow(cr, cx, cy, WHEEL_R_OUT, p)
        cr.new_path()
        cr.arc(cx, cy, WHEEL_R_OUT + 9, 0, 2 * math.pi)
        cr.set_source_rgba(SILVER_HI[0], SILVER_HI[1], SILVER_HI[2], 0.30 * p)
        cr.set_line_width(1)
        cr.stroke()
        real = self._ready
        if self.state == "empty":
            self._draw_empty_ring(cr, cx, cy, p)
        else:
            if real < 0.999:
                self._draw_slices(cr, cx, cy, 6 if not self.actions else len(self.actions), p * (1.0 - real), True)
            if real > 0.001 and self.actions:
                self._draw_slices(cr, cx, cy, len(self.actions), p * real, False)
        if self.state == "busy":
            self._draw_progress(cr, cx, cy, p)
        self._draw_hub(cr, cx, cy, p)
        cr.restore()


# ============================================================================
#  [9a] Look: stylesheet, logo, application icon
# ============================================================================
_PETALS = ((-90, False), (-30, True), (30, False), (90, False), (150, False), (210, False))   # (centre angle, pale?)


def _petal_svg(cx, cy, ri, ro, half):
    out = []
    for centre, pale in _PETALS:
        a0, a1 = math.radians(centre - half), math.radians(centre + half)
        p0 = (cx + ro * math.cos(a0), cy + ro * math.sin(a0))
        p1 = (cx + ro * math.cos(a1), cy + ro * math.sin(a1))
        q1 = (cx + ri * math.cos(a1), cy + ri * math.sin(a1))
        q0 = (cx + ri * math.cos(a0), cy + ri * math.sin(a0))
        col = "#F5F6F8" if pale else "#C9CDD3"
        out.append('<path d="M%.1f %.1f A%d %d 0 0 1 %.1f %.1f L%.1f %.1f A%d %d 0 0 0 %.1f %.1f Z" fill="%s" stroke="%s"/>'
                   % (p0[0], p0[1], ro, ro, p1[0], p1[1], q1[0], q1[1], ri, ri, q0[0], q0[1], col, col))
    return "\n  ".join(out)


ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
 <rect width="128" height="128" rx="30" fill="#7A1F3F"/>
 <g stroke-width="4" stroke-linejoin="round">
  %s
 </g>
 <circle cx="64" cy="64" r="9" fill="#F5F6F8"/>
 <path d="M64 59v10M59 64h10" stroke="#7A1F3F" stroke-width="3" stroke-linecap="round" fill="none"/>
</svg>
""" % _petal_svg(64, 64, 19, 41, 20)


def paint_logo(cr, tile=True):
    """The mark in a 128 x 128 box: burgundy tile (optional), six silver petals (one pale) around a centre with a plus."""
    if tile:
        r = 30
        cr.new_path()
        cr.arc(128 - r, r, r, -math.pi / 2, 0)
        cr.arc(128 - r, 128 - r, r, 0, math.pi / 2)
        cr.arc(r, 128 - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)
        cr.close_path()
        cr.set_source_rgb(*WINE)
        cr.fill()
    cr.set_line_width(4)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for centre, pale in _PETALS:
        a0, a1 = math.radians(centre - 20), math.radians(centre + 20)
        cr.new_path()
        cr.arc(64, 64, 41, a0, a1)
        cr.arc_negative(64, 64, 19, a1, a0)
        cr.close_path()
        cr.set_source_rgb(*(CREAM if pale else SILVER))
        cr.fill_preserve()
        cr.stroke()
    cr.new_path()
    cr.arc(64, 64, 9, 0, 2 * math.pi)
    cr.set_source_rgb(*CREAM)
    cr.fill()
    cr.set_source_rgb(*WINE)
    cr.set_line_width(3)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(64, 59)
    cr.line_to(64, 69)
    cr.move_to(59, 64)
    cr.line_to(69, 64)
    cr.stroke()


class LogoMark(Gtk.DrawingArea):
    """The Rumman mark drawn with cairo, so no SVG loader is needed."""

    def __init__(self, size=64, tile=True):
        super().__init__()
        self.size = size
        self.tile = tile
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, area, cr, w, h):
        k = min(w, h) / 128.0
        cr.save()
        cr.translate((w - 128 * k) / 2.0, (h - 128 * k) / 2.0)
        cr.scale(k, k)
        paint_logo(cr, self.tile)
        cr.restore()


class FixedWidth(Gtk.Widget):
    """Holds one child at a fixed width.

    A window that cannot be resized is given the smallest width at which its content fits into the height it got. With
    many wrapping labels GTK answers that question with a width far bigger than needed (a 600 px window came out 721 px
    wide under Wayland, in some languages only). Here the width is simply the wanted one, or the content's own minimum
    if that is bigger, so nothing is ever cut, and the height follows from it."""
    __gtype_name__ = "RummanFixedWidth"

    def __init__(self, width):
        super().__init__()
        self._width = width
        self._child = None

    def set_child(self, child):
        if self._child is not None:
            self._child.unparent()
        self._child = child
        if child is not None:
            child.set_parent(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _wanted(self):
        least = self._child.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        return max(self._width, least)

    def do_measure(self, orientation, for_size):
        if self._child is None or not self._child.should_layout():
            return (0, 0, -1, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            w = self._wanted()
            return (w, w, -1, -1)
        mn, nat, _a, _b = self._child.measure(Gtk.Orientation.VERTICAL, for_size if for_size >= 0 else self._wanted())
        return (mn, nat, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        if self._child is not None and self._child.should_layout():
            self._child.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


APP_CSS = """
/* the theme paints a green frame around anything a file is dragged over (":drop(active)"): none of ours wants it */
window.rumman-wheel *:drop(active), window.rumman-wheel:drop(active),
window.rumman-options *:drop(active), window.rumman-options:drop(active),
window.rumman-status *:drop(active), window.rumman-status:drop(active) {
    box-shadow: none; outline: none; outline-color: transparent; border-color: transparent; background-image: none;
}

window.rumman-wheel, window.rumman-wheel.background, window.rumman-wheel.csd, window.rumman-wheel.solid-csd {
    background: transparent; background-color: transparent; box-shadow: none; border: none; border-radius: 0;
}
window.rumman-wheel.opaque, window.rumman-wheel.opaque.background { background: #3F0F23; background-color: #3F0F23; }
/* the edge strip: a thin burgundy line on the screen edge; lighter while a file is held on it */
.rumman-stage { background-color: transparent; }
.rumman-stage.strip { background-color: rgba(122, 31, 63, 0.92); }
.rumman-stage.strip.hot { background-color: #C23A68; }
.rumman-stage.strip.edge-right { border-radius: 3px 0 0 3px; }
.rumman-stage.strip.edge-left { border-radius: 0 3px 3px 0; }
.rumman-stage.strip.edge-top { border-radius: 0 0 3px 3px; }
.rumman-stage.strip.edge-bottom { border-radius: 3px 3px 0 0; }

window.rumman-options, window.rumman-options.background, window.rumman-options.csd, window.rumman-options.solid-csd {
    background: transparent; background-color: transparent; box-shadow: none; border: none; border-radius: 0;
    font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", "Cantarell", sans-serif;
}
window.rumman-options.opaque, window.rumman-options.opaque.background { background: #3F0F23; background-color: #3F0F23; }
window.rumman-options .rcard { background: #F5F6F8; color: #24151B; border-radius: 22px; margin: 32px;
                               border: 1px solid #C9CDD3; box-shadow: 0 18px 48px rgba(63, 15, 35, 0.42); }
window.rumman-options.opaque .rcard { margin: 6px; border-radius: 16px; border: 2px solid #C9CDD3; box-shadow: none; }
/* the head: a picture of the file, its name, what is being done */
window.rumman-options .rhead { background-image: linear-gradient(135deg, #7A1F3F, #3F0F23); border-radius: 21px 21px 0 0;
                               padding: 16px 22px; }
window.rumman-options.opaque .rhead { border-radius: 13px 13px 0 0; }
window.rumman-options.compact .rhead { padding: 10px 22px; }
window.rumman-options .rthumb { background: rgba(255, 255, 255, 0.14); border: 1px solid rgba(201, 205, 211, 0.55); border-radius: 13px; }
window.rumman-options .rthumb picture, window.rumman-options .rthumb drawingarea { border-radius: 12px; }
window.rumman-options .rthumbt { color: #F5F6F8; font-size: 13px; font-weight: 700; line-height: 16px; }
window.rumman-options .rname { color: #F5F6F8; font-size: 18px; font-weight: 700; line-height: 24px; }
window.rumman-options .rsub { color: #C9CDD3; font-size: 13px; line-height: 18px; }
window.rumman-options .rbadge { background: #F5F6F8; color: #3F0F23; border-radius: 99px; padding: 5px 14px;
                                font-size: 13px; font-weight: 700; line-height: 18px; }
/* the body */
window.rumman-options .rbody { padding: 16px 24px 4px 24px; }
window.rumman-options.compact .rbody { padding: 10px 24px 2px 24px; }
window.rumman-options .rlabel { color: #3F0F23; font-size: 14px; font-weight: 700; line-height: 20px; }
window.rumman-options .rsec { color: #7A1F3F; font-size: 13px; font-weight: 700; line-height: 18px; }
window.rumman-options .rhint { color: #5E6470; font-size: 12px; line-height: 17px; }
window.rumman-options .rdesc { color: #5E6470; font-size: 12px; line-height: 17px; }
window.rumman-options .rsw { font-size: 13px; font-weight: 600; color: #24151B; line-height: 18px; }
window.rumman-options .rline { background: #DADDE2; min-height: 1px; }
/* the compression level: one bar split into buttons */
window.rumman-options .seg { background: #E4E6EA; border-radius: 13px; padding: 3px; }
window.rumman-options .seg button.segbtn { background: transparent; background-image: none; border: none; box-shadow: none;
                                           text-shadow: none; color: #3F0F23; font-weight: 600; font-size: 14px;
                                           min-height: 22px; padding: 6px 4px; border-radius: 10px; outline: none; line-height: 20px; }
window.rumman-options .seg button.segbtn:hover { background: #D5D8DE; }
window.rumman-options .seg button.segbtn:checked { background: #7A1F3F; color: #F5F6F8; }
/* the other choices: separate rounded buttons */
window.rumman-options .pills button.pill { background: transparent; background-image: none; border: 1px solid #C9CDD3; box-shadow: none;
                                           text-shadow: none; color: #24151B; font-weight: 600; font-size: 13px;
                                           min-height: 18px; padding: 5px 4px; border-radius: 99px; outline: none; line-height: 18px; }
window.rumman-options .pills button.pill:hover { background: #E4E6EA; }
window.rumman-options .pills button.pill:checked { background: #7A1F3F; border-color: #7A1F3F; color: #F5F6F8; }
/* a switch with its name and one line of explanation */
window.rumman-options .rtile { background: #FFFFFF; border: 1px solid #DADDE2; border-radius: 14px; padding: 8px 14px; }
window.rumman-options .rtile.short { padding: 8px 12px; }
window.rumman-options .rtile:hover { border-color: #B98CA0; }
window.rumman-options.compact .rtile { padding: 6px 12px; }
window.rumman-options switch { background: #C9CDD3; background-image: none; border: none; box-shadow: none; }
window.rumman-options switch:checked { background: #7A1F3F; }
window.rumman-options switch slider { background: #F5F6F8; background-image: none; border: none; box-shadow: none; }
/* the foot */
window.rumman-options .rfoot { padding: 10px 24px 18px 24px; }
window.rumman-options.compact .rfoot { padding: 6px 24px 12px 24px; }
window.rumman-options .rnote { color: #5E6470; font-size: 12px; line-height: 17px; }
window.rumman-options .rwarn { color: #B3263A; font-size: 12px; font-weight: 600; line-height: 17px; }
window.rumman-options button.rbtn { box-shadow: none; text-shadow: none; font-weight: 700; font-size: 14px; min-height: 22px;
                                    padding: 8px 18px; border-radius: 13px; line-height: 20px; }
window.rumman-options button.rbtn.big { font-size: 16px; min-height: 26px; padding: 11px 22px; border-radius: 14px; line-height: 22px; }
window.rumman-options.compact button.rbtn.big { min-height: 24px; padding: 8px 22px; }
window.rumman-options button.rbtn.small { padding: 2px 12px; min-height: 0; font-size: 12px; line-height: 17px; }
window.rumman-options button.rbtn.primary { background: #7A1F3F; background-image: none; color: #F5F6F8; border: 1.5px solid #7A1F3F; }
window.rumman-options button.rbtn.primary:hover { background: #8B2748; border-color: #8B2748; }
window.rumman-options button.rbtn.primary:disabled { background: #C9CDD3; border-color: #C9CDD3; color: #7C828D; }
window.rumman-options button.rbtn.ghost { background: transparent; background-image: none; color: #7A1F3F; border: 1.5px solid #C9CDD3; }
window.rumman-options button.rbtn.ghost:hover { background: #E4E6EA; }
window.rumman-options button.rclose { background: transparent; background-image: none; border: none; box-shadow: none;
                                      color: #C9CDD3; min-width: 28px; min-height: 28px; padding: 0; border-radius: 99px; }
window.rumman-options button.rclose:hover { background: rgba(255, 255, 255, 0.16); color: #FFFFFF; }
/* the progress and the result */
window.rumman-options .rprog { padding: 20px 24px 22px 24px; }
window.rumman-options progressbar trough { background: #DADDE2; border: none; min-height: 8px; border-radius: 99px; }
window.rumman-options progressbar progress { background: #7A1F3F; border: none; min-height: 8px; border-radius: 99px; }
window.rumman-options .rrow { font-size: 12px; color: #24151B; font-weight: 600; line-height: 17px; }
window.rumman-options .rdone { font-size: 12px; color: #2F7D5B; font-weight: 600; line-height: 17px; }
window.rumman-options .rrowerr { font-size: 12px; color: #B3263A; font-weight: 600; line-height: 17px; }
window.rumman-options .rstop { font-size: 12px; color: #7C828D; font-weight: 600; line-height: 17px; }
window.rumman-options label.rerror { font-weight: 600; }
window.rumman-options button.rx { background: transparent; background-image: none; border: none; box-shadow: none; color: #7C828D;
                                  min-width: 22px; min-height: 22px; padding: 0; border-radius: 99px; font-size: 12px; line-height: 17px; }
window.rumman-options button.rx:hover { background: #E4E6EA; color: #B3263A; }
window.rumman-options .rresult { font-size: 15px; font-weight: 700; color: #7A1F3F; line-height: 21px; }
window.rumman-options .rerror { font-size: 14px; font-weight: 600; color: #B3263A; line-height: 20px; }

window.rumman-status, window.rumman-status.background {
    background: #F5F6F8; color: #24151B;
    font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", "Cantarell", sans-serif;
}
window.rumman-status .head { background-image: linear-gradient(135deg, #7A1F3F, #3F0F23); padding: 24px 30px; }
window.rumman-status .name { font-family: "Reem Kufi", "IBM Plex Sans Arabic", "Noto Sans Arabic", sans-serif;
                             font-size: 42px; font-weight: 700; color: #F5F6F8; }
window.rumman-status .tag { color: #C9CDD3; font-size: 15px; font-weight: 600; }
window.rumman-status .body { padding: 22px 30px 6px 30px; }
window.rumman-status .h { color: #7A1F3F; font-size: 16px; font-weight: 700; }
window.rumman-status .stepn { background: #7A1F3F; color: #F5F6F8; border-radius: 99px;
                              min-width: 26px; min-height: 26px; font-weight: 700; font-size: 13px; }
window.rumman-status .stept { font-size: 14px; color: #24151B; }
window.rumman-status .card { background: #E4E6EA; border-radius: 14px; padding: 14px 16px; }
window.rumman-status .cardt { font-size: 14px; color: #24151B; }
window.rumman-status .dot { border-radius: 99px; min-width: 12px; min-height: 12px; }
window.rumman-status .dot.ok { background: #2F7D5B; }
window.rumman-status .dot.warn { background: #D9A85B; }
window.rumman-status .dot.bad { background: #B3263A; }
window.rumman-status .muted { color: #5E6470; font-size: 12px; }
window.rumman-status .toast { color: #7A1F3F; font-size: 13px; font-weight: 600; }
window.rumman-status .pwname { font-family: "Reem Kufi", "IBM Plex Sans Arabic", "Noto Sans Arabic", sans-serif;
                               font-size: 22px; font-weight: 700; color: #F5F6F8; line-height: 32px; }
window.rumman-status .pwerr { font-family: monospace; font-size: 12px; color: #3F0F23; line-height: 18px; }
window.rumman-status .setname { font-size: 14px; font-weight: 700; color: #24151B; }
window.rumman-status switch { background: #C9CDD3; background-image: none; border: none; box-shadow: none; }
window.rumman-status switch:checked { background: #7A1F3F; }
window.rumman-status switch slider { background: #F5F6F8; background-image: none; border: none; box-shadow: none; }
window.rumman-status dropdown > button { background: rgba(255, 255, 255, 0.14); background-image: none; color: #F5F6F8;
                                         border: 1px solid rgba(201, 205, 211, 0.55); box-shadow: none; border-radius: 10px;
                                         min-height: 20px; padding: 4px 10px; font-weight: 600; }
window.rumman-status dropdown > button:hover { background: rgba(255, 255, 255, 0.24); }
window.rumman-status button { box-shadow: none; text-shadow: none; font-weight: 600; font-size: 14px;
                              min-height: 22px; padding: 8px 16px; border-radius: 12px; }
window.rumman-status button.primary { background: #7A1F3F; background-image: none; color: #F5F6F8; border: 1.5px solid #7A1F3F; }
window.rumman-status button.primary:hover { background: #A3305A; border-color: #A3305A; }
window.rumman-status button.ghost { background: transparent; background-image: none; color: #7A1F3F; border: 1.5px solid #7A1F3F; }
window.rumman-status button.ghost:hover { background: #E4E6EA; }
window.rumman-status button.danger { background: transparent; background-image: none; color: #B3263A; border: 1.5px solid transparent; }
window.rumman-status button.danger:hover { background: #E4E6EA; }

window.rumman-settings, window.rumman-settings.background {
    background: #F5F6F8; color: #24151B;
    font-family: "IBM Plex Sans Arabic", "Noto Sans Arabic", "Cantarell", sans-serif;
}
window.rumman-settings .head { background-image: linear-gradient(135deg, #7A1F3F, #3F0F23); padding: 16px 26px; }
window.rumman-settings .stitle { color: #F5F6F8; font-size: 22px; font-weight: 700; }
window.rumman-settings .body { padding: 18px 26px 6px 26px; }
window.rumman-settings .card { background: #E4E6EA; border-radius: 14px; padding: 14px 16px; }
window.rumman-settings .h { color: #7A1F3F; font-size: 15px; font-weight: 700; }
window.rumman-settings .setname { font-size: 14px; font-weight: 700; color: #24151B; }
window.rumman-settings .muted { color: #5E6470; font-size: 12px; }
window.rumman-settings .pathl { color: #5E6470; font-size: 12px; }
window.rumman-settings switch { background: #C9CDD3; background-image: none; border: none; box-shadow: none; }
window.rumman-settings switch:checked { background: #7A1F3F; }
window.rumman-settings switch slider { background: #F5F6F8; background-image: none; border: none; box-shadow: none; }
window.rumman-settings dropdown > button { background: #F5F6F8; background-image: none; color: #24151B; border: 1px solid #C9CDD3;
                                           box-shadow: none; border-radius: 10px; min-height: 20px; padding: 4px 10px; font-weight: 600; }
window.rumman-settings dropdown > button:hover { background: #FFFFFF; }
window.rumman-settings button { box-shadow: none; text-shadow: none; font-weight: 600; font-size: 14px;
                                min-height: 22px; padding: 6px 14px; border-radius: 12px; }
window.rumman-settings button.primary { background: #7A1F3F; background-image: none; color: #F5F6F8; border: 1.5px solid #7A1F3F; }
window.rumman-settings button.primary:hover { background: #A3305A; border-color: #A3305A; }
window.rumman-settings button.ghost { background: transparent; background-image: none; color: #7A1F3F; border: 1.5px solid #7A1F3F; }
window.rumman-settings button.ghost:hover { background: #F5F6F8; }
"""


def load_css(provider, text):
    try:
        provider.load_from_string(text)              # GTK 4.12+
    except AttributeError:
        provider.load_from_data(text.encode("utf-8"), -1)


_CSS_DONE = False


def install_css():
    global _CSS_DONE
    if _CSS_DONE:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    prov = Gtk.CssProvider()
    load_css(prov, APP_CSS)
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_DONE = True


# ============================================================================
#  [9b] The wheel window: shows the wheel, receives the drop, runs the chosen action
# ============================================================================
WHEEL_TITLE = "rumman-wheel"       # the GNOME helper recognises the window by this title
TERMINAL = ("done", "error", "cancelled", "nogain")
PICK_IDLE_SECONDS = 20


def paths_from_value(value):
    """Local file paths out of whatever a drop / the clipboard gave us."""
    items = []
    try:
        if value is None:
            return []
        if isinstance(value, Gdk.FileList):
            items = list(value.get_files())
        elif isinstance(value, Gio.File):
            items = [value]
        elif isinstance(value, str):
            for line in value.replace("\r", "").split("\n"):
                line = line.strip().strip("'\"")
                if not line or line.startswith("#"):
                    continue
                if "://" in line:
                    items.append(Gio.File.new_for_uri(line))
                elif line.startswith("/"):
                    items.append(Gio.File.new_for_path(line))
    except Exception as e:
        dbg("paths_from_value", e)
    out = []
    for f in items:
        try:
            p = f.get_path()
        except Exception:
            p = None
        if p and os.path.lexists(p) and p not in out:
            out.append(p)
    return out


def missing_tool_hint(files):
    need = {"video": "ffmpeg", "audio": "ffmpeg", "pdf": "poppler / ghostscript", "doc": "LibreOffice / pandoc",
            "text": "pandoc / LibreOffice", "archive": "7-Zip", "image": "ImageMagick"}.get(files_category(files), "")
    return tr("no_options_install", need) if need else tr("no_options")


def display_is_composited():
    try:
        d = Gdk.Display.get_default()
        return bool(d.is_composited()) if d is not None else True
    except Exception:
        return True


class WheelWindow(Gtk.Window):
    """One instance lives for the whole run. It is shown when the helper asks for it, and hidden afterwards.

    kind:  idle     - hidden
           drag     - opened in the middle of a drag; waiting for the pointer to arrive and for the file list
           pick     - files are known and the drop (or "Open with") is over: click a slice
           running  - a slice without options was chosen; the jobs are being worked on (the others go to the options page)
    """

    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title(WHEEL_TITLE)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(WHEEL_SIZE, WHEEL_SIZE)
        self.set_hide_on_close(True)
        self.add_css_class("rumman-wheel")
        self.canvas = WheelCanvas(owner=self)
        self._attach_canvas()
        self.kind = "idle"
        self.files = []
        self.jobs = []
        self.dropped = False
        self.drag_inside = False
        self.last_xy = (-1.0, -1.0)
        self._gen = 0
        self._close_id = 0
        self._idle_id = 0
        self._noval_id = 0
        self._was_active = False
        self._setup_drop()
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        move = Gtk.EventControllerMotion()
        move.connect("motion", lambda c, x, y: self._arm_idle())
        self.add_controller(move)
        self.connect("notify::is-active", self._on_active)

    def _attach_canvas(self):
        """The canvas is the window's only child (the edge strip puts it into a container instead)."""
        self.set_child(self.canvas)

    def _drop_widget(self):
        """The widget that receives the drop."""
        return self.canvas

    # ---------------------------------------------------------------- opening --
    def is_open(self):
        return self.kind != "idle" and self.get_visible()

    def open_for_drag(self):
        """Shift was pressed in the middle of a drag: open the wheel now, fill it when the files are known."""
        self._reset("drag")
        self.canvas.set_loading()
        self._present()
        GLib.timeout_add(160, self._enter_check, self._gen, 0)

    def _enter_check(self, gen, n):
        """The wheel opened under a pointer that is not moving, so the drag source has not noticed the new window
        yet (it only looks again when the pointer moves). Push the pointer by a pixel until the wheel sees the drag."""
        if gen != self._gen or self.kind != "drag" or self.drag_inside or self.dropped or self._close_id:
            return False                             # (a pending close means the helper said the drag is over)
        self.app.backend.nudge_pointer()
        if n < 3:
            GLib.timeout_add(260, self._enter_check, gen, n + 1)
        return False

    def open_for_files(self, files):
        """`rumman FILE...` / "Open with" / the demo: the same wheel, but you click instead of dropping."""
        self._reset("pick")
        self.files = list(files)
        self.dropped = True
        self._show_files()
        self._present()
        self._arm_idle()

    def _reset(self, kind):
        self._cancel_close()
        self._cancel_idle()
        self._cancel_noval()
        self._gen += 1
        self.kind = kind
        self.files = []
        self.jobs = []
        self.dropped = False
        self.drag_inside = False
        self._was_active = False                    # "had the focus and lost it" is judged afresh for every opening

    def _present(self):
        opaque = not display_is_composited()
        self.canvas.opaque = opaque
        if opaque:
            self.add_css_class("opaque")
        else:
            self.remove_css_class("opaque")
        self.canvas.pop_in()
        be = self.app.backend
        try:
            be.before_show(self)
        except Exception:
            log_error(traceback.format_exc())
        self.present()
        try:
            be.after_show(self)
        except Exception:
            log_error(traceback.format_exc())

    # ---------------------------------------------------------------- closing --
    def drag_ended(self):
        """The helper says the drag is over. The drop event may still be on its way (it travels another road),
        so wait a moment before closing, and do nothing at all if the drop did reach us."""
        if self.kind == "drag" and not self.dropped:
            self.close_wheel(0.28)

    def close_wheel(self, delay=0.0):
        if DEBUG:
            dbg("close_wheel", delay, self.kind, "from", traceback.extract_stack(limit=2)[0].name)
        self._cancel_close()
        gen = self._gen

        def go():
            self._close_id = 0
            if gen == self._gen:
                self._hide_now()
            return False
        if delay <= 0:
            go()
        else:
            self._close_id = GLib.timeout_add(int(delay * 1000), go)

    def _hide_now(self):
        self._cancel_idle()
        self._cancel_noval()
        if self.get_mapped():
            self.canvas.fade_out(self._finish_hide)
        else:
            self._finish_hide()

    def _finish_hide(self):
        self.set_visible(False)
        self.kind = "idle"
        self.files = []
        self.jobs = []
        self.drag_inside = False
        self._was_active = False
        self.canvas.set_loading()
        try:
            self.app.backend.after_hide(self)
        except Exception:
            pass

    def _cancel_close(self):
        if self._close_id:
            GLib.source_remove(self._close_id)
            self._close_id = 0

    def _arm_idle(self):
        if self.kind != "pick":
            return
        self._cancel_idle()
        gen = self._gen

        def idle():
            self._idle_id = 0
            if gen == self._gen and self.kind == "pick":
                self._hide_now()
            return False
        self._idle_id = GLib.timeout_add_seconds(PICK_IDLE_SECONDS, idle)

    def _cancel_idle(self):
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0

    def _cancel_noval(self):
        if self._noval_id:
            GLib.source_remove(self._noval_id)
            self._noval_id = 0

    def _on_active(self, win, pspec):
        active = self.is_active()
        dbg("wheel active", active, "kind", self.kind)
        if self._was_active and not active and self.kind == "pick" and not self.drag_inside:
            self.close_wheel(0.15)                  # clicked somewhere else
        self._was_active = active

    # --------------------------------------------------------------- the files --
    def _show_files(self):
        if not self.files:
            self.canvas.set_loading()
            return
        acts = build_actions(self.files)
        title, sub = describe_files(self.files)
        dbg("wheel actions", [a.label for a in acts])
        self.canvas.set_actions(acts, title, sub, "" if acts else missing_tool_hint(self.files))

    def _take_value(self, value):
        files = paths_from_value(value)
        if files:
            self._cancel_noval()
            if files != self.files:
                self.files = files
                self._show_files()
            return True
        return False

    def _no_files_yet(self, gen):
        self._noval_id = 0
        if gen == self._gen and self.kind == "drag" and not self.files and not self.dropped:
            self.canvas.set_note(tr("wheel_no_files"))
            self.close_wheel(1.6)
        return False

    # ------------------------------------------------------------- a click on a slice --
    def wheel_pick(self, index):                    # called by the canvas when a slice is clicked
        if self.kind == "pick":
            self.pick(index)

    # ------------------------------------------------------------------- drop --
    def _setup_drop(self):
        dt = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)      # COPY only: the original is never touched
        dt.set_gtypes([Gdk.FileList, GObject.TYPE_STRING])
        dt.set_preload(True)
        dt.connect("enter", self._on_enter)
        dt.connect("motion", self._on_motion)
        dt.connect("leave", self._on_leave)
        dt.connect("drop", self._on_drop)
        dt.connect("notify::value", self._on_value)
        self._drop_widget().add_controller(dt)
        self._drop_widget().connect("state-flags-changed", self._no_drop_state)
        self.drop_target = dt

    @staticmethod
    def _no_drop_state(widget, _old):
        """The theme paints a frame (GNOME: a green one) around whatever a file is dragged over, through the ":drop(active)"
        state. The wheel has no use for that state, so it is taken off again the moment the toolkit sets it."""
        if widget.get_state_flags() & Gtk.StateFlags.DROP_ACTIVE:
            widget.unset_state_flags(Gtk.StateFlags.DROP_ACTIVE)

    def _on_enter(self, dt, x, y):
        dbg("drop enter", x, y)
        self._flags_logged = False
        self.drag_inside = True
        self.last_xy = (x, y)
        self._cancel_close()
        self._cancel_idle()
        self.app.event("wheel-enter")
        got = self._take_value(dt.get_value())
        if not got and not self.files and self.kind == "drag" and not self._noval_id:
            self._noval_id = GLib.timeout_add(1500, self._no_files_yet, self._gen)
        self.canvas.hover(x, y)
        return Gdk.DragAction.COPY

    def _on_motion(self, dt, x, y):
        self.last_xy = (x, y)
        if not getattr(self, "_flags_logged", True):
            self._flags_logged = True
            dbg("drop state", "drop-active" if self.canvas.get_state_flags() & Gtk.StateFlags.DROP_ACTIVE else "clear")
        self.canvas.hover(x, y)
        return Gdk.DragAction.COPY

    def _on_leave(self, dt):
        dbg("drop leave")
        self.drag_inside = False
        self.canvas.hover(-1, -1)
        self._arm_idle()

    def _on_value(self, dt, pspec):
        if self.drag_inside:
            self._take_value(dt.get_value())

    def _on_drop(self, dt, value, x, y):
        dbg("drop", x, y)
        self.app.event("wheel-drop")
        self.dropped = True
        self.drag_inside = False
        self._cancel_close()
        self._cancel_noval()
        files = paths_from_value(value) or list(self.files)
        if not files:
            self.canvas.set_error(tr("drop_nothing"))
            self.close_wheel(2.6)
            return False
        self.files = files
        self._show_files()
        i = self.canvas.slice_at(x, y)
        if 0 <= i < len(self.canvas.actions):
            GLib.idle_add(self._pick_later, i, self._gen)      # let GTK finish the drop before windows change
        else:                                       # dropped on the hub or in a gap: keep the files, click a slice
            self._to_pick()
        return True

    def _pick_later(self, index, gen):
        if gen == self._gen:
            self.pick(index)
        return False

    def _to_pick(self):
        self.kind = "pick"
        self.canvas.hover(-1, -1)
        self.canvas.queue_draw()
        self._arm_idle()

    # ------------------------------------------------------------------ running --
    def pick(self, index):
        """A slice was chosen (dropped on, or clicked). The options page opens in the wheel's place (every slice has at
        least the choices about the result); with the page switched off the slice runs at once."""
        acts = self.canvas.actions
        if not (0 <= index < len(acts)) or not self.files:
            return
        act = acts[index]
        files = list(self.files)
        self._cancel_idle()
        self._cancel_close()
        if SETTINGS.get("ask_options"):                  # every slice: where the result goes is chosen there too
            self.app.event("options %s" % act.key)
            self.kind = "idle"
            self._gen += 1
            self.app.show_options(act, files)            # first the page, then the wheel goes away: no empty moment
            self._finish_hide()
            return
        self.kind = "running"
        self.canvas.set_busy(index, None, "")
        self.jobs = self.app.run_action(act, files, self)
        self.app.event("run %s x%d" % (act.key, len(self.jobs)))

    def job_updated(self, job):
        if self.kind != "running" or job not in self.jobs:
            return
        n = len(self.jobs)
        finished = sum(1 for j in self.jobs if j.state in TERMINAL)
        if finished < n:
            cur = next((j for j in self.jobs if j.state == "running"), None)
            frac = None
            if cur is not None and cur.progress is not None:
                frac = (finished + cur.progress) / float(n)
            elif cur is None:
                frac = finished / float(n)
            label = ltr("%d/%d" % (finished + 1, n)) if n > 1 else tr("wheel_busy")
            self.canvas.set_busy(self.canvas.chosen, frac, label)
            return
        self.app.jobs_finished(self.jobs, self.is_open())
        errors = [j for j in self.jobs if j.state == "error"]
        done = [j for j in self.jobs if j.state == "done"]
        outs = [o for j in done for o in j.outputs]
        if errors:
            self.canvas.set_error(errors[0].error or tr("wheel_failed"))
            hold = 4.0
        elif done:
            if len(outs) == 1:
                msg = os.path.basename(outs[0])
            else:
                msg = tr("n_outputs", len(outs))
            self.canvas.set_done(msg)
            hold = 1.5
        elif any(j.state == "nogain" for j in self.jobs):
            self.canvas.set_note(tr("wheel_nogain"))
            hold = 2.2
        else:
            self.canvas.set_note(tr("wheel_cancelled"))
            hold = 1.0
        self.close_wheel(hold)

    def cancel(self):
        """Esc: stop whatever is running and close."""
        for j in self.jobs:
            if j.state in ("queued", "running"):
                j.cancel()
        self.close_wheel(0.0)

    # --------------------------------------------------------------------- keys --
    def _on_key(self, ctrl, keyval, keycode, state):
        K = Gdk
        if keyval == K.KEY_Escape:
            self.cancel()
            return True
        if self.kind != "pick":
            return False
        self._arm_idle()
        if keyval in (K.KEY_Return, K.KEY_KP_Enter, K.KEY_space):
            act = self.canvas.active_action()
            idx = self.canvas.actions.index(act) if act is not None else (0 if self.canvas.actions else -1)
            if idx >= 0:
                self.pick(idx)
        elif keyval in (K.KEY_Left, K.KEY_Up):
            self.canvas.nav(-1)
        elif keyval in (K.KEY_Right, K.KEY_Down):
            self.canvas.nav(1)
        elif K.KEY_1 <= keyval <= K.KEY_9:
            i = keyval - K.KEY_1
            if i < len(self.canvas.actions):
                self.pick(i)
        else:
            return False
        return True


# ============================================================================
#  [9c] Status window: how to use it, and what the app found on this system
# ============================================================================
def _label(text, css=None, wrap=True, xalign=0.0):
    lb = Gtk.Label(label=text)
    if css:
        lb.add_css_class(css)
    lb.set_wrap(wrap)
    if wrap:
        lb.set_max_width_chars(46)       # a long translation wraps instead of widening the whole window
    lb.set_xalign(xalign)
    lb.set_hexpand(True)
    lb.set_halign(Gtk.Align.FILL)
    return lb


def _button(text, css, cb):
    b = Gtk.Button()
    lb = Gtk.Label(label=text)
    lb.set_wrap(True)                      # a long translation goes onto two lines instead of widening the window
    lb.set_justify(Gtk.Justification.CENTER)
    lb.set_max_width_chars(22)
    b.set_child(lb)
    b.add_css_class(css)
    b.connect("clicked", lambda *a: cb())
    return b


STATUS_W = 600
LANGUAGE_NAMES = (("ar", "العربية"), ("tr", "Türkçe"), ("es", "Español"), ("ru", "Русский"), ("en", "English"))


class StatusWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title(tr("app_name"))
        self.set_resizable(False)
        self.set_default_size(STATUS_W, -1)
        self.add_css_class("rumman-status")
        self._toast_id = 0
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder = FixedWidth(STATUS_W)             # the width does not depend on how GTK shares the height out (see FixedWidth)
        holder.set_child(root)
        self.set_child(holder)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        head.add_css_class("head")
        head.append(LogoMark(76, tile=False))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.set_valign(Gtk.Align.CENTER)
        col.set_hexpand(True)
        col.append(_label(tr("app_name"), "name", wrap=False))
        col.append(_label(tr("tagline"), "tag", wrap=False))
        head.append(col)
        head.append(self._language_picker())
        root.append(head)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("body")
        root.append(body)
        body.append(_label(tr("st_how_title"), "h"))
        for i in range(1, 5):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            n = Gtk.Label(label=str(i))
            n.add_css_class("stepn")
            n.set_xalign(0.5)
            n.set_yalign(0.5)
            n.set_valign(Gtk.Align.START)
            row.append(n)
            edge_mode = app.backend is not None and app.backend.kind == "edge"
            row.append(_label(tr("st_how_2_edge" if (i == 2 and edge_mode) else "st_how_%d" % i), "stept"))
            body.append(row)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.set_margin_top(8)
        card.append(_label(tr("st_state_title"), "h"))
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.dot = Gtk.Box()
        self.dot.add_css_class("dot")
        self.dot.set_valign(Gtk.Align.START)
        self.dot.set_margin_top(5)
        line.append(self.dot)
        self.state_lbl = _label("", "cardt")
        line.append(self.state_lbl)
        card.append(line)
        body.append(card)

        ask = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        ask.set_margin_top(6)
        acol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        acol.set_hexpand(True)
        acol.append(_label(tr("st_ask"), "setname"))
        acol.append(_label(tr("st_ask_sub"), "muted"))
        ask.append(acol)
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(SETTINGS.get("ask_options")))
        sw.connect("notify::active", lambda s, p: SETTINGS.set("ask_options", s.get_active()))
        ask.append(sw)
        body.append(ask)

        if app.backend is not None and app.backend.kind == "edge":      # where the strip sits (only desktops that have one)
            edge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            edge_row.set_margin_top(6)
            ecol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            ecol.set_hexpand(True)
            ecol.append(_label(tr("st_edge"), "setname"))
            ecol.append(_label(tr("st_edge_sub"), "muted"))
            edge_row.append(ecol)
            self._edge_codes = list(EDGE_NAMES)
            dd = Gtk.DropDown.new_from_strings([tr("edge_" + c) for c in self._edge_codes])
            dd.set_valign(Gtk.Align.CENTER)
            dd.set_selected(self._edge_codes.index(current_edge()))
            dd.connect("notify::selected", self._on_edge)
            self.edge_dd = dd
            edge_row.append(dd)
            body.append(edge_row)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_margin_top(10)
        btns.append(_button(tr("st_demo"), "primary", lambda: app.show_demo()))
        btns.append(_button(tr("st_settings"), "ghost", lambda: app.show_settings()))
        btns.append(_button(tr("st_copy_report"), "ghost", lambda: app.copy_report(self)))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        btns.append(spacer)
        btns.append(_button(tr("st_stop"), "danger", lambda: app.quit_app()))
        body.append(btns)
        self.toast = _label("", "toast")
        self.toast.set_visible(False)
        body.append(self.toast)

        foot = _label("%s · %s %s" % (tr("st_stage"), APP_NAME, APP_VERSION), "muted")
        foot.set_margin_top(10)
        foot.set_margin_bottom(16)
        foot.set_margin_start(30)
        foot.set_margin_end(30)
        root.append(foot)
        self.refresh()

    def _language_picker(self):
        names = [tr("lang_auto")] + [n for _c, n in LANGUAGE_NAMES]
        self._codes = ["auto"] + [c for c, _n in LANGUAGE_NAMES]
        dd = Gtk.DropDown.new_from_strings(names)
        cur = SETTINGS.get("language")
        dd.set_selected(self._codes.index(cur) if cur in self._codes else 0)
        dd.set_valign(Gtk.Align.START)
        dd.set_tooltip_text(tr("st_lang"))
        dd.connect("notify::selected", self._on_language)
        self.lang_dd = dd
        return dd

    def _on_language(self, dd, pspec):
        code = self._codes[dd.get_selected()] if dd.get_selected() < len(self._codes) else "auto"
        if code == SETTINGS.get("language"):
            return
        SETTINGS.set("language", code)
        apply_language(code)
        GLib.idle_add(self.app.rebuild_status)               # every text of this window is built again in the new language

    def _on_edge(self, dd, pspec):
        i = dd.get_selected()
        code = self._edge_codes[i] if i < len(self._edge_codes) else "right"
        if code == SETTINGS.get("edge"):
            return
        SETTINGS.set("edge", code)
        if self.app.backend is not None and hasattr(self.app.backend, "apply_edge"):
            self.app.backend.apply_edge()
        self.refresh()

    def refresh(self):
        st = self.app.backend.status() if self.app.backend is not None else {"level": "warn", "text": ""}
        for c in ("ok", "warn", "bad"):
            self.dot.remove_css_class(c)
        self.dot.add_css_class(st.get("level", "warn"))
        self.state_lbl.set_label(st.get("text", ""))

    def show_toast(self, text):
        self.toast.set_label(text)
        self.toast.set_visible(True)
        if self._toast_id:
            GLib.source_remove(self._toast_id)

        def hide():
            self._toast_id = 0
            self.toast.set_visible(False)
            return False
        self._toast_id = GLib.timeout_add_seconds(3, hide)


# ============================================================================
#  [9c+] "Could not open" window: what a person sees instead of a click that does nothing
# ============================================================================
PROBLEM_W = 560


def short_home(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home + os.sep) else path


class ProblemWindow(Gtk.ApplicationWindow):
    """Says that something could not be opened, gives the reason in one line, and has a button that copies the whole
    diagnostic report (which already holds the log) so that it can be pasted in a chat.  Built from the simplest widgets
    on purpose: it is the last thing that has to work."""

    def __init__(self, app, what, detail):
        super().__init__(application=app)
        self.app = app
        self._items = []
        self._toast_id = 0
        self.set_title(tr("app_name"))
        self.set_resizable(False)
        self.set_default_size(PROBLEM_W, -1)
        self.add_css_class("rumman-status")
        holder = FixedWidth(PROBLEM_W)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.set_child(root)
        self.set_child(holder)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        head.add_css_class("head")
        head.append(LogoMark(56, tile=False))
        title = _label(tr("pw_title"), "pwname")
        title.set_valign(Gtk.Align.CENTER)
        head.append(title)
        root.append(head)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("body")
        root.append(body)
        body.append(_label(tr("pw_text"), "stept"))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        self.err = Gtk.Label(label="")
        self.err.add_css_class("pwerr")
        self.err.set_wrap(True)
        self.err.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.err.set_max_width_chars(60)
        self.err.set_lines(7)
        self.err.set_ellipsize(Pango.EllipsizeMode.END)
        self.err.set_selectable(True)
        self.err.set_xalign(0.0)
        self.err.set_hexpand(True)
        self.err.set_direction(Gtk.TextDirection.LTR)           # an error text is English, whatever the language of the window
        card.append(self.err)
        body.append(card)
        logp = os.path.join(GLib.get_user_cache_dir(), "rumman", "log.txt")
        body.append(_label(tr("pw_log", short_home(logp)), "muted"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.append(_button(tr("st_copy_report"), "primary", lambda: app.copy_report(self)))
        row.append(_button(tr("btn_close"), "ghost", self.close))
        body.append(row)
        self.toast = _label("", "toast")
        self.toast.set_visible(False)
        body.append(self.toast)
        pad = Gtk.Box()
        pad.set_size_request(-1, 14)
        body.append(pad)
        self.add_detail(what, detail)

    def add_detail(self, what, detail):
        lines = [ln.strip() for ln in str(detail).strip().splitlines() if ln.strip()]
        self._items.append("%s: %s" % (what, lines[-1] if lines else "?"))
        del self._items[:-3]                                     # the last three reasons are enough here; the log has them all
        self.err.set_label("\n".join(self._items))

    def show_toast(self, text):
        self.toast.set_label(text)
        self.toast.set_visible(True)
        if self._toast_id:
            GLib.source_remove(self._toast_id)

        def hide():
            self._toast_id = 0
            self.toast.set_visible(False)
            return False
        self._toast_id = GLib.timeout_add_seconds(3, hide)


# ============================================================================
#  [9d] The options page: it opens after a file is dropped on a slice. Everything that can be chosen for this kind of file
#       is in it, at once: compression and tools first, then where the result goes. One press on "Run" does everything for
#       all the files (batch), with the progress shown in the same card.
#
#       Sizing rule (this matters): a window that cannot be resized may be given the *smallest* size its content accepts
#       on some systems. So nothing in the card may shrink (no scrolling area, no collapsing part): the smallest height
#       is the natural height. If a screen is too small for the whole card, the card gets tighter (compact), and only when
#       that is not enough either, the middle part scrolls inside a fixed height.
# ============================================================================
OPTIONS_TITLE = "rumman-options"      # the GNOME helper recognises this window by its title too
CARD_W = 680                          # width of the card itself
CARD_MARGIN = 32                      # transparent margin around it (the shadow). 32 px is what the top bar of GNOME takes,
CARD_MARGIN_PLAIN = 6                 # so a card pushed against the top edge is not hidden by it.  No compositor: no shadow.
BODY_PAD = 24                         # side padding inside the card
GRID_GAP = 12
OPTIONS_IDLE_SECONDS = 180
# Choices that are kept for the next time as soon as they are made (the settings page shows the same values).
# Size and rotation are not: they belong to this one file, not to every file after it.
KEPT_CHOICES = ("out_mode", "conflict", "delete_original", "open_after", "notify", "strip_meta", "mono", "normalize",
                "trim_silence", "mute", "include_hidden", "keep_root", "archive_level")
TOOL_PILLS = ("pdf-merge", "pdf-split", "pdf-rotate", "aud-merge", "extract")      # these show their name, not "A -> B"


def image_dims(path):
    """(width, height) of an image, read from its header only (fast), or None."""
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        if info and info[0] is not None and info[1] > 0 and info[2] > 0:
            return (info[1], info[2])
    except Exception:
        pass
    return None


def card_texts(files):
    """(title, subtitle) for the head of the card: the name, and kind · size · dimensions."""
    title, sub = describe_files(files)
    if len(files) == 1 and categorize(files[0]) == "image":
        dims = image_dims(files[0])
        if dims:
            sub += " · " + ltr("%d×%d px" % dims)
    return title, sub


def thumbnail_for(path, size=50):
    """A picture of the image for the head of the card, as a drawing area of exactly size x size (cropped to fill it,
    turned the way the photo says), or None (no loader for the format, too big, damaged...)."""
    try:
        if categorize(path) != "image" or os.path.getsize(path) > 30 * 1024 * 1024:
            return None
        dims = image_dims(path)
        if not dims or dims[0] * dims[1] > 40_000_000:
            return None
        k = 2 * size                                          # sharp on screens that scale
        w, h = dims
        tw, th = (k, max(1, round(k * h / w))) if w >= h else (max(1, round(k * w / h)), k)
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, tw, th, False)
        pb = pb.apply_embedded_orientation() or pb
    except Exception as e:
        dbg("thumbnail failed", e)
        return None
    area = Gtk.DrawingArea()
    area.set_content_width(size)
    area.set_content_height(size)

    def draw(da, cr, width, height):
        pw, ph = pb.get_width(), pb.get_height()
        scale = max(width / pw, height / ph)
        cr.translate((width - pw * scale) / 2.0, (height - ph * scale) / 2.0)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
        cr.paint()
    area.set_draw_func(draw)
    return area


class DetachedRun:
    """The jobs of a page that was dismissed while they ran: nobody shows their progress any more, the result is announced."""
    def __init__(self, app, jobs):
        self.app = app
        self.jobs = list(jobs)
        self.reported = False

    def job_updated(self, job):
        if not self.reported and all(j.state in TERMINAL for j in self.jobs):
            self.reported = True
            self.app.jobs_finished(self.jobs, False)


class OptionsWindow(Gtk.Window):
    """One instance lives for the whole run (like the wheel window); it is rebuilt for every drop.

    kind:  idle     - hidden
           options  - the choices are shown, nothing has been started
           running  - "Run" was pressed; the jobs are being worked on (progress in the card)
           done     - everything finished; the result is shown for a moment
    """

    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title(OPTIONS_TITLE)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_hide_on_close(True)
        self._margin = CARD_MARGIN
        self.set_default_size(self.window_width(), -1)
        self.add_css_class("rumman-options")
        self.kind = "idle"
        self.act = None
        self.files = []
        self.jobs = []
        self.specs = []
        self.values = {}
        self._hint_labels = {}
        self._chip_btns = {}
        self._reserve = []
        self._descs = []
        self._grids = []
        self._scroll = None
        self._compact = False
        self._dialog_open = False
        self.gspecs = []
        self._gen = 0
        self._close_id = 0
        self._idle_id = 0
        self._pulse_id = 0
        self._outs = []
        self._unknown = True            # the progress of the running job is not known (the bar pulses)
        self._close_after = 0
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)      # Esc / Enter work even when a button has the focus
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        move = Gtk.EventControllerMotion()
        move.connect("motion", lambda c, x, y: self._on_motion())
        self.add_controller(move)

    def window_width(self):
        return CARD_W + 2 * self._margin

    # ---------------------------------------------------------------- opening --
    def is_open(self):
        return self.kind != "idle" and self.get_visible()

    def open_for(self, act, files):
        """Show the page for what was dropped on this slice."""
        self._cancel_close()
        self._cancel_idle()
        self._stop_pulse()
        self._gen += 1
        self.act = act
        self.files = list(files)
        self.jobs = []
        self._outs = []
        self.specs = option_specs(act, self.files)
        self.values = {s.key: s.default for s in self.specs}
        self.gspecs = general_specs()
        self.values.update({s.key: s.default for s in self.gspecs})
        self.values["out_dir"] = SETTINGS.get("out_dir") or ""
        opaque = not display_is_composited()
        self._margin = CARD_MARGIN_PLAIN if opaque else CARD_MARGIN
        if opaque:
            self.add_css_class("opaque")
        else:
            self.remove_css_class("opaque")
        self._reset_size()
        self._build()
        self.kind = "options"
        self._refresh_state()
        self._present()
        self._arm_idle()
        GLib.timeout_add(120, self._fit_height_gen, self._gen)      # a window that stayed tall from the card before shrinks to this one

    def _fit_height_gen(self, gen):
        if gen == self._gen and self.kind == "options":
            self._fit_height()
        return False

    def _reset_size(self):
        """GTK remembers the size a window last had and uses it as the default the next time it is shown. After the short
        progress view the next page (taller) would be cut off at the bottom. So: the height is whatever the content needs."""
        self.set_default_size(self.window_width(), -1)

    def _present(self):
        be = self.app.backend
        try:
            be.before_show(self)
        except Exception:
            log_error(traceback.format_exc())
        self.present()
        try:
            be.after_show(self)
        except Exception:
            log_error(traceback.format_exc())

    # ------------------------------------------------------------------ the card --
    def _build(self, compact=False):
        self._chip_btns = {}
        self._hint_labels = {}
        self._reserve = []
        self._descs = []
        self._grids = []
        self._scroll = None
        self._compact = False
        self.remove_css_class("compact")
        title, sub = card_texts(self.files)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("rcard")
        self._head = self._build_head(title, sub)
        root.append(self._head)
        self._body = self._build_body()
        root.append(self._body)
        self._foot = self._build_foot()
        root.append(self._foot)
        self._prog = self._build_progress()
        root.append(self._prog)
        self._root = root
        old = self.get_child()
        if isinstance(old, FixedWidth):
            old.set_child(None)                            # the card before is taken apart cleanly
        fixed = FixedWidth(self.window_width())            # the width is what we say: it never depends on the texts
        fixed.set_child(root)
        self.set_child(fixed)
        if compact:
            self._set_compact(True)                        # before anything is measured (see _fit_to_screen)
        self._apply_reserves()
        self._fit_to_screen()

    # ---- the head: a picture, the name, what is being done
    def _build_head(self, title, sub):
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.add_css_class("rhead")
        head.append(self._build_thumb())
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        col.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=title)
        name.add_css_class("rname")
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        col.append(name)
        subl = Gtk.Label(label=sub)
        subl.add_css_class("rsub")
        subl.set_xalign(0.0)
        subl.set_ellipsize(Pango.EllipsizeMode.END)
        col.append(subl)
        head.append(col)
        badge = Gtk.Label(label=self._pill_text())
        badge.add_css_class("rbadge")
        badge.set_valign(Gtk.Align.CENTER)
        head.append(badge)
        close = Gtk.Button(label="✕")
        close.add_css_class("rclose")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *a: self.cancel())
        head.append(close)
        return head

    def _build_thumb(self):
        box = Gtk.Box()
        box.add_css_class("rthumb")
        box.set_size_request(52, 52)
        box.set_hexpand(False)
        box.set_vexpand(False)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_overflow(Gtk.Overflow.HIDDEN)
        pic = thumbnail_for(self.files[0]) if len(self.files) == 1 else None
        if pic is not None:
            box.append(pic)
            return box
        if len(self.files) > 1:
            text = str(len(self.files))
        else:
            ext = split_name(self.files[0])[1].upper()
            text = ext[:4] if ext else tr("cat_" + categorize(self.files[0]))
        lab = Gtk.Label(label=text)
        lab.add_css_class("rthumbt")
        lab.set_halign(Gtk.Align.CENTER)
        lab.set_valign(Gtk.Align.CENTER)
        lab.set_hexpand(True)                   # inside the box only: the box itself does not grow (hexpand set above)
        lab.set_vexpand(True)
        box.append(lab)
        return box

    def _source_label(self):
        """'JPG' when everything dropped is a JPG; '' for a folder or a mix."""
        exts = {split_name(f)[1] for f in self.files if not os.path.isdir(f)}
        if len(exts) != 1 or any(os.path.isdir(f) for f in self.files):
            return ""
        return _norm_img_fmt(next(iter(exts))) if exts else ""

    def _pill_text(self):
        """What is being done, for the badge in the head: 'JPG → WEBP', 'JPG · same format', 'Rotate PDF'."""
        act = self.act
        if act.same:
            return "%s · %s" % (ltr(act.label), act.caption)
        if act.op in TOOL_PILLS or not act.fmt:
            return act.label
        src = self._source_label()
        return ltr("%s → %s" % (src, act.fmt) if src else "→ %s" % act.fmt)

    # ---- the body: what can be chosen for this kind of file, then where the result goes
    def _build_body(self):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("rbody")
        items = [self._spec_item(s) for s in self.specs]
        if items:
            body.append(self._grid(items))
            body.append(self._section(tr("o_sec_out")))
        out, switches = [], []
        for spec in self.gspecs:
            if spec.key == "out_mode":
                out.append((self._where_block(spec), "chips"))
            elif spec.key == "conflict":
                out.append((self._exists_block(spec), "chips"))
            else:
                switches.append(self._tile(spec, short=True))
        lower = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GRID_GAP)
        lower.append(self._grid(out))
        lower.append(self._row_of(switches))
        body.append(lower)
        return body

    def _spec_item(self, spec):
        if spec.kind == "level":
            return (self._level_block(spec), "level")
        if spec.kind == "chips":
            return (self._chips_block(spec), "chips")
        return (self._tile(spec), "tile")

    def _grid(self, items):
        """Two equal columns. A wide block (the compression level) takes a whole row; the rest pair up with their own
        kind (chips with chips, tiles with tiles); a lone one at the end of its kind takes the whole row."""
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(GRID_GAP)
        grid.set_row_spacing(GRID_GAP)
        self._grids.append(grid)
        row, i = 0, 0
        while i < len(items):
            widget, kind = items[i]
            nxt = items[i + 1] if i + 1 < len(items) else None
            if kind != "level" and nxt is not None and nxt[1] == kind:
                grid.attach(widget, 0, row, 1, 1)
                grid.attach(nxt[0], 1, row, 1, 1)
                i += 2
            else:
                grid.attach(widget, 0, row, 2, 1)
                i += 1
            row += 1
        return grid

    def _row_of(self, widgets):
        """Equal columns, one row (the three switches that every kind of file has)."""
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(10)
        for i, w in enumerate(widgets):
            grid.attach(w, i, 0, 1, 1)
        return grid

    def _section(self, text):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(2)
        lab = Gtk.Label(label=text)
        lab.add_css_class("rsec")
        box.append(lab)
        line = Gtk.Box()
        line.add_css_class("rline")
        line.set_hexpand(True)
        line.set_valign(Gtk.Align.CENTER)
        box.append(line)
        return box

    def _label(self, text, css="rlabel"):
        lab = Gtk.Label(label=text)
        lab.add_css_class(css)
        lab.set_xalign(0.0)
        lab.set_wrap(True)
        return lab

    def _level_block(self, spec):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self._label(spec.label))
        box.append(self._chips(spec, "seg"))
        hint = self._label(level_hint(self.act, self.values[spec.key]), "rhint")
        self._hint_labels[spec.key] = lambda v, h=hint: h.set_label(level_hint(self.act, v))
        self._reserve.append((hint, [level_hint(self.act, v) for v, _t in spec.choices], CARD_W - 2 * BODY_PAD))
        box.append(hint)
        return box

    def _chips_block(self, spec):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self._label(spec.label))
        box.append(self._chips(spec, "pill"))
        return box

    def _where_block(self, spec):
        """Where the result goes: next to the original, or in a folder (with its path and a button to change it)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self._label(spec.label))
        box.append(self._chips(spec, "pill"))
        self._folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._folder_row.set_size_request(-1, 26)               # the same height with or without the button
        self._folder_lbl = Gtk.Label()
        self._folder_lbl.add_css_class("rhint")
        self._folder_lbl.set_xalign(0.0)
        self._folder_lbl.set_hexpand(True)
        self._folder_lbl.set_valign(Gtk.Align.CENTER)
        self._folder_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._folder_row.append(self._folder_lbl)
        self._pick_btn = Gtk.Button(label=tr("se_choose"))
        self._pick_btn.add_css_class("rbtn")
        self._pick_btn.add_css_class("ghost")
        self._pick_btn.add_css_class("small")
        self._pick_btn.set_valign(Gtk.Align.CENTER)
        self._pick_btn.connect("clicked", lambda *a: self._choose_folder())
        self._folder_row.append(self._pick_btn)
        box.append(self._folder_row)
        self._update_where()
        return box

    def _exists_block(self, spec):
        """What to do when a file of that name is already there, with one line saying what each choice means."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(self._label(spec.label))
        box.append(self._chips(spec, "pill"))
        texts = {"rename": tr("o_c_keep_sub"), "replace": tr("o_c_replace_sub")}
        hint = self._label(texts[self.values["conflict"]], "rhint")
        self._hint_labels["conflict"] = lambda v, h=hint: h.set_label(texts.get(v, ""))
        self._reserve.append((hint, list(texts.values()), (CARD_W - 2 * BODY_PAD - GRID_GAP) // 2))
        box.append(hint)
        self._descs.append(hint)
        return box

    def _tile(self, spec, short=False):
        """A switch in a bordered tile; a click anywhere on the tile flips it. Long form: name and a line of explanation.
        Short form (the switches every kind of file has): the name only, the explanation is in the tooltip."""
        tile = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tile.add_css_class("rtile")
        if short:
            tile.add_css_class("short")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        col.set_valign(Gtk.Align.CENTER)
        col.append(self._label(spec.label, "rsw"))
        if spec.sub and not short:
            d = self._label(spec.sub, "rdesc")
            self._descs.append(d)
            col.append(d)
        if spec.sub and short:
            tile.set_tooltip_text(spec.sub)
        tile.append(col)
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(self.values[spec.key]))
        sw.connect("notify::active", lambda s, p, key=spec.key: self._set_value(key, s.get_active()))
        tile.append(sw)

        def flip(gesture, n, x, y):
            hit = tile.pick(x, y, Gtk.PickFlags.DEFAULT)
            if hit is not None and (hit is sw or hit.is_ancestor(sw)):
                return                                       # the switch handles its own click
            sw.set_active(not sw.get_active())
        click = Gtk.GestureClick()
        click.connect("released", flip)
        tile.add_controller(click)
        return tile

    def _chips(self, spec, style):
        """style: seg = one rounded bar split into buttons (the compression level);  pill = separate rounded buttons."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4 if style == "seg" else 6)
        box.add_css_class("seg" if style == "seg" else "pills")
        box.set_homogeneous(True)
        first = None
        for value, text in spec.choices:
            b = Gtk.ToggleButton(label=text)
            b.add_css_class("segbtn" if style == "seg" else "pill")
            child = b.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.set_active(value == self.values[spec.key])
            b.connect("toggled", lambda btn, key=spec.key, v=value: self._set_value(key, v) if btn.get_active() else None)
            self._chip_btns.setdefault(spec.key, {})[value] = b
            box.append(b)
        return box

    # ---- the foot: a line of explanation (or a warning), Run and Cancel
    def _build_foot(self):
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        foot.add_css_class("rfoot")
        line = Gtk.Box()
        line.add_css_class("rline")
        foot.append(line)
        self._note_texts = (tr("o_saved_note"), tr("o_nothing"))
        self._note = self._label(self._note_texts[0], "rnote")
        self._reserve.append((self._note, list(self._note_texts), CARD_W - 2 * BODY_PAD))
        foot.append(self._note)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._run_btn = Gtk.Button(label=tr("btn_run"))
        self._run_btn.add_css_class("rbtn")
        self._run_btn.add_css_class("primary")
        self._run_btn.add_css_class("big")
        self._run_btn.set_hexpand(True)
        self._run_btn.connect("clicked", lambda *a: self.run())
        btns.append(self._run_btn)
        cancel = Gtk.Button(label=tr("btn_cancel"))
        cancel.add_css_class("rbtn")
        cancel.add_css_class("ghost")
        cancel.add_css_class("big")
        cancel.connect("clicked", lambda *a: self.cancel())
        btns.append(cancel)
        foot.append(btns)
        return foot

    # ---- the progress / result view (replaces the body and the foot after "Run")
    def _build_progress(self):
        prog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        prog.add_css_class("rprog")
        self._status = Gtk.Label(label=tr("o_working"))
        self._status.add_css_class("rlabel")
        self._status.set_xalign(0.0)
        self._status.set_wrap(True)
        prog.append(self._status)
        self._bar = Gtk.ProgressBar()
        self._bar.set_hexpand(True)
        prog.append(self._bar)
        self._rows_scroll = Gtk.ScrolledWindow()               # one row per file when several files run together
        self._rows_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._rows_scroll.set_child(self._rows_box)
        self._rows_scroll.set_visible(False)
        prog.append(self._rows_scroll)
        self._row_widgets = {}
        self._prog_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._prog_btns.set_margin_top(4)
        self._reveal_btn = Gtk.Button(label=tr("show_in_folder"))
        self._reveal_btn.add_css_class("rbtn")
        self._reveal_btn.add_css_class("ghost")
        self._reveal_btn.connect("clicked", lambda *a: self._reveal())
        self._prog_btns.append(self._reveal_btn)
        self._undo_btn = Gtk.Button(label=tr("btn_undo"))
        self._undo_btn.add_css_class("rbtn")
        self._undo_btn.add_css_class("ghost")
        self._undo_btn.connect("clicked", lambda *a: self._undo())
        self._undo_btn.set_visible(False)
        self._prog_btns.append(self._undo_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self._prog_btns.append(spacer)
        self._stop_btn = Gtk.Button(label=tr("btn_cancel"))
        self._stop_btn.add_css_class("rbtn")
        self._stop_btn.add_css_class("ghost")
        self._stop_btn.connect("clicked", lambda *a: self.cancel())
        self._prog_btns.append(self._stop_btn)
        prog.append(self._prog_btns)
        prog.set_visible(False)
        return prog

    # ---- fitting the card into the screen, and keeping its height steady
    def _apply_reserves(self):
        """A line of explanation changes with the choice (one line or two). The room for the longest one is kept from the
        start, so the card never changes its height while someone is choosing. (Measured now, inside the window: the
        styles of the card only apply to widgets that belong to it.)"""
        for label, texts, width in self._reserve:
            shown = label.get_label()
            best = 0
            for t in texts:
                label.set_label(t)
                best = max(best, label.measure(Gtk.Orientation.VERTICAL, width)[1])
            label.set_label(shown)
            label.set_size_request(-1, best)

    BAR_ROOM = 56                                                # px: a big desktop bar (GNOME 32, Plasma 44) and a little more

    def _max_window_height(self):
        """The tallest window that still shows the whole card on the smallest screen.  A program cannot ask where the
        desktop's bar is, so room for a big one is kept: the window may start right below the bar, and then only its
        transparent margin (the shadow) is allowed to reach past the bottom of the screen - never the card itself, whose
        bottom holds the Run button."""
        try:
            mons = Gdk.Display.get_default().get_monitors()
            heights = [mons.get_item(i).get_geometry().height for i in range(mons.get_n_items())]
            return max(360, min(heights) - self.BAR_ROOM + self._margin)
        except Exception:
            return 700

    def _need_height(self):
        return self.measure(Gtk.Orientation.VERTICAL, self.window_width())[1]

    def _fit_to_screen(self):
        avail = self._max_window_height()
        need = self._need_height()
        if need <= avail:
            return
        if not self._compact:
            # Tighter, and the one-line explanations go away.  The card is built AGAIN that way instead of switching the
            # style of this one: a style that changes after a measure is only seen at the next frame, so the measure that
            # follows would still count the old paddings and the card would scroll although it fits.
            self._build(compact=True)
            return
        body_h = self._body.measure(Gtk.Orientation.VERTICAL, CARD_W)[1]
        h = max(180, body_h - (need - avail))
        scroll = Gtk.ScrolledWindow()                            # last resort: a middle part of one fixed height
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(True)                       # a bar over the content: it takes no width from it (a bar of its
                                                                 # own did, and in right-to-left text GTK even placed it outside)
        scroll.set_min_content_height(h)
        scroll.set_max_content_height(h)
        self._root.remove(self._body)
        scroll.set_child(self._body)
        self._root.insert_child_after(scroll, self._head)
        self._scroll = scroll
        self._body = scroll
        dbg("options card scrolls: needs %d, screen allows %d" % (need, avail))

    def _set_compact(self, on):
        self._compact = on
        if on:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        for g in self._grids:
            g.set_row_spacing(8 if on else GRID_GAP)
        for d in self._descs:
            d.set_visible(not on)
        if isinstance(self._body, Gtk.Box):
            self._body.set_spacing(8 if on else 14)
        self._foot.set_spacing(6 if on else 12)

    # ----------------------------------------------------- where the result goes --
    def _custom_dir(self):
        d = self.values.get("out_dir") or ""
        return d if self.values.get("out_mode") == "custom" and writable_dir(d) else ""

    def _update_where(self):
        custom = self.values.get("out_mode") == "custom"
        if custom:
            self._folder_lbl.set_label(self.values.get("out_dir") or tr("se_no_folder"))
            self._folder_lbl.set_tooltip_text(self.values.get("out_dir") or None)
        else:
            self._folder_lbl.set_label(tr("o_where_next_sub"))
            self._folder_lbl.set_tooltip_text(None)
        self._pick_btn.set_visible(custom)                         # the row keeps its height (set above) either way

    def _choose_folder(self):
        self._dialog_open = True
        self._cancel_idle()
        try:
            dlg = Gtk.FileDialog()
            dlg.set_title(tr("se_choose_title"))
            start = self.values.get("out_dir")
            if start and os.path.isdir(start):
                dlg.set_initial_folder(Gio.File.new_for_path(start))
            dlg.select_folder(self, None, self._folder_chosen)
        except Exception as e:
            dbg("folder dialog failed", e)
            self._folder_chosen(None, None)
        return False

    def _folder_chosen(self, dlg, res):
        self._dialog_open = False
        path = None
        try:
            if dlg is not None:
                f = dlg.select_folder_finish(res)
                path = f.get_path() if f is not None else None
        except GLib.Error:
            path = None
        if path and writable_dir(path):
            self.values["out_dir"] = path
            if SETTINGS is not None:
                SETTINGS.set("out_dir", path)
            self._set_chip("out_mode", "custom")
        elif not writable_dir(self.values.get("out_dir") or ""):
            self._set_chip("out_mode", "source")             # nothing usable chosen: back to "next to the original"
        self._update_where()
        self._arm_idle()

    def _set_chip(self, key, value):
        self.values[key] = value
        for v, b in self._chip_btns.get(key, {}).items():
            if b.get_active() != (v == value):
                b.set_active(v == value)
        if key == "out_mode":
            self._remember("out_mode", value)
            self._update_where()

    def _remember(self, key, value):
        """What is chosen here is what the settings page shows and what the next card starts with."""
        if SETTINGS is None:
            return
        if key == "out_mode" and value == "custom" and not writable_dir(self.values.get("out_dir") or ""):
            return                                           # no folder yet: kept once one has been chosen
        if key in KEPT_CHOICES:
            SETTINGS.set(key, value)
        elif key == "level":
            lk = _LEVEL_KEY.get(self.act.op) if self.act is not None else None
            if lk:
                SETTINGS.set(lk, value)

    def _set_value(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        if key == "out_mode" and value == "custom" and not writable_dir(self.values.get("out_dir") or ""):
            GLib.idle_add(self._choose_folder)                # no folder known yet: ask for one
        self._remember(key, value)
        if key == "out_mode":
            self._update_where()
        hint = self._hint_labels.get(key)
        if hint is not None:
            hint(value)
        self._refresh_state()
        self._arm_idle()

    def _refresh_state(self):
        idle = nothing_to_do(self.act, self.values) if self.act is not None else False
        self._run_btn.set_sensitive(not idle)
        self._note.set_label(self._note_texts[1 if idle else 0])
        if idle:
            self._note.add_css_class("rwarn")
        else:
            self._note.remove_css_class("rwarn")

    def _view(self, which):
        """options | progress: what the card shows below its header."""
        self._body.set_visible(which == "options")
        self._foot.set_visible(which == "options")
        self._prog.set_visible(which == "progress")

    # ---------------------------------------------------------------- running --
    def run(self):
        if self.kind != "options" or nothing_to_do(self.act, self.values):
            return
        self._cancel_idle()
        self.kind = "running"
        self._status.set_label(tr("o_working"))
        self._bar.set_fraction(0.0)
        self._status.remove_css_class("rresult")
        self._status.remove_css_class("rerror")
        self._status.add_css_class("rlabel")
        self._reveal_btn.set_visible(False)
        self._undo_btn.set_visible(False)
        self._stop_btn.set_label(tr("btn_cancel"))
        self._view("progress")
        self._start_pulse()
        self.jobs = self.app.run_action(self.act, list(self.files), self, dict(self.values))
        self._build_rows()
        self.app.event("run %s x%d %s" % (self.act.key, len(self.jobs), json.dumps(self.values, sort_keys=True)))
        GLib.idle_add(self._fit_height)                       # the progress view is much shorter than the page

    def _build_rows(self):
        """Several files: a row for each (name, its own bar, a cross that stops only that one)."""
        c = self._rows_box.get_first_child()
        while c is not None:
            nxt = c.get_next_sibling()
            self._rows_box.remove(c)
            c = nxt
        self._row_widgets = {}
        many = len(self.jobs) > 1
        self._rows_scroll.set_visible(many)
        if not many:
            return
        for j in self.jobs:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = tr("n_files", len(j.files)) if len(j.files) > 1 else os.path.basename(j.files[0].rstrip("/"))
            lb = Gtk.Label(label=name)
            lb.add_css_class("rrow")
            lb.set_xalign(0.0)
            lb.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            lb.set_max_width_chars(22)
            lb.set_width_chars(14)
            row.append(lb)
            bar = Gtk.ProgressBar()
            bar.set_hexpand(True)
            bar.set_valign(Gtk.Align.CENTER)
            row.append(bar)
            x = Gtk.Button(label="✕")
            x.add_css_class("rx")
            x.set_tooltip_text(tr("btn_cancel"))
            x.connect("clicked", lambda b, job=j: (job.cancel(), self._update_rows()))
            row.append(x)
            self._rows_box.append(row)
            self._row_widgets[j] = (lb, bar, x)
        # a list that may need to scroll still has one fixed height (its smallest is its natural one)
        rows = [self._rows_box.get_first_child()]
        rh = rows[0].measure(Gtk.Orientation.VERTICAL, -1)[1] if rows[0] is not None else 26
        h = min(len(self.jobs) * rh + (len(self.jobs) - 1) * 6, 150)
        self._rows_scroll.set_min_content_height(h)
        self._rows_scroll.set_max_content_height(h)

    def _update_rows(self):
        for j, (lb, bar, x) in self._row_widgets.items():
            if j.state == "done":
                bar.set_fraction(1.0)
            elif j.state == "running":
                if j.progress is None:
                    bar.pulse()
                else:
                    bar.set_fraction(j.progress)
            else:
                bar.set_fraction(0.0)
            live = j.state in ("queued", "running") and not j.cancelled      # a stopped file shows it at once, before the worker gets to it
            x.set_sensitive(live)
            for cls in ("rrowerr", "rdone", "rstop"):
                lb.remove_css_class(cls)
            state = "cancelled" if j.cancelled and j.state in ("queued", "running") else j.state
            lb.add_css_class({"error": "rrowerr", "done": "rdone", "cancelled": "rstop", "nogain": "rstop"}.get(state, "rrow"))
            if j.cancelled and j.state in ("queued", "running"):
                bar.set_fraction(0.0)

    def _fit_height(self):
        """The content changed its height (the progress view, a result text that needs a second line). A window that
        cannot be resized does not always follow by itself, so it is told its height again: whatever the content needs
        at the card's width."""
        try:
            w = self.window_width()
            need = self.measure(Gtk.Orientation.VERTICAL, w)[1]
            if need > 0 and need != self.get_height():
                self.set_resizable(True)
                self.set_default_size(w, need)
                self.queue_resize()
                GLib.idle_add(lambda: (self.set_resizable(False), False)[1])
        except Exception as e:
            dbg("fit height failed", e)
        return False

    def _undo(self):
        n = self.app.undo_last()
        self._undo_btn.set_visible(False)
        self._reveal_btn.set_visible(False)
        self._status.remove_css_class("rresult")
        self._status.add_css_class("rlabel")
        self._status.set_label(tr("o_undone") if n else tr("wheel_failed"))
        self._arm_close(1.8)

    def _start_pulse(self):
        if not self._pulse_id:
            self._pulse_id = GLib.timeout_add(110, self._pulse)

    def _stop_pulse(self):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = 0

    def _pulse(self):
        if self.kind != "running":
            self._pulse_id = 0
            return False
        if self._unknown:
            self._bar.pulse()
        return True

    def job_updated(self, job):
        if self.kind != "running" or job not in self.jobs:
            return
        n = len(self.jobs)
        self._update_rows()
        finished = sum(1 for j in self.jobs if j.state in TERMINAL)
        if finished < n:
            cur = next((j for j in self.jobs if j.state == "running"), None)
            frac = None
            if cur is not None and cur.progress is not None:
                frac = (finished + cur.progress) / float(n)
            elif cur is None:
                frac = finished / float(n)
            self._unknown = frac is None
            if frac is not None:
                self._bar.set_fraction(frac)
            self._status.set_label(tr("o_working_n", finished + 1, n) if n > 1 else tr("o_working"))
            return
        self._stop_pulse()
        self.app.jobs_finished(self.jobs, self.is_open())
        sm = run_summary(self.jobs)
        errors, done = sm["errors"], sm["done"]
        self._outs = sm["outs"]
        self.kind = "done"
        self._stop_btn.set_label(tr("btn_close"))
        self._status.remove_css_class("rlabel")
        if errors and done:                                   # some worked, some did not: say both
            self._bar.set_fraction(1.0)
            self._status.add_css_class("rerror")
            self._status.set_label(tr("o_mixed", len(done), len(errors)) + " — " + (errors[0].error or tr("wheel_failed")))
            self._reveal_btn.set_visible(bool(self._outs))
            self._undo_btn.set_visible(False)
            self._arm_close(9.0)
        elif errors:
            self._bar.set_fraction(0.0)
            self._status.add_css_class("rerror")
            self._status.set_label(errors[0].error or tr("wheel_failed"))
            self._arm_close(7.0)
        elif done:
            self._bar.set_fraction(1.0)
            self._status.add_css_class("rresult")
            msg = os.path.basename(self._outs[0]) if len(self._outs) == 1 else tr("n_outputs", len(self._outs))
            extra = [t for t in (sm["saved_txt"], tr("o_orig_trashed") if sm["trashed"] else "") if t]
            self._status.set_label("✓  " + msg + ("  ·  " + "  ·  ".join(extra) if extra else ""))
            self._reveal_btn.set_visible(bool(self._outs))
            self._undo_btn.set_visible(sm["can_undo"])
            self._arm_close(6.5 if sm["can_undo"] else 3.6)
        elif any(j.state == "nogain" for j in self.jobs):
            self._bar.set_fraction(0.0)
            self._status.add_css_class("rlabel")
            self._status.set_label(next((j.error for j in self.jobs if j.state == "nogain" and j.error), tr("wheel_nogain")))
            self._arm_close(4.0)
        else:
            self.close_card(0.0)

    def _reveal(self):
        if self._outs:
            reveal_path(self._outs[0], self)
        self.close_card(0.2)

    # --------------------------------------------------------------- closing --
    def dismiss(self):
        """A new wheel opens, so this page is out of date. One that was not started is simply dropped; one whose jobs are
        still running is only hidden: the jobs go on and the notification tells the result."""
        if self.kind == "running" and any(j.state in ("queued", "running") for j in self.jobs):
            run = DetachedRun(self.app, self.jobs)
            for j in self.jobs:
                j.listener = run
            run.job_updated(self.jobs[0])
        self.close_card(0.0)

    def cancel(self):
        """Esc / the cross / Cancel: stop whatever is running and close."""
        for j in self.jobs:
            if j.state in ("queued", "running"):
                j.cancel()
        self.close_card(0.0)

    def close_card(self, delay=0.0):
        self._cancel_close()
        gen = self._gen

        def go():
            self._close_id = 0
            if gen == self._gen:
                self._hide_now()
            return False
        if delay <= 0:
            go()
        else:
            self._close_id = GLib.timeout_add(int(delay * 1000), go)

    def _arm_close(self, seconds):
        GLib.idle_add(self._fit_height)                       # the result view was just filled in
        GLib.timeout_add(250, self._fit_height)               # ... and once more when its rows have settled
        self.close_card(seconds)
        self._close_after = seconds

    def _hide_now(self):
        self._cancel_idle()
        self._stop_pulse()
        self._undo_btn.set_visible(False)
        self._rows_scroll.set_visible(False)
        self.set_visible(False)
        self._reset_size()
        self.kind = "idle"
        self.jobs = []
        try:
            self.app.backend.after_hide(self)
        except Exception:
            pass

    def _cancel_close(self):
        if self._close_id:
            GLib.source_remove(self._close_id)
            self._close_id = 0

    def _arm_idle(self):
        if self.kind != "options" or self._dialog_open:
            return
        self._cancel_idle()
        gen = self._gen

        def idle():
            self._idle_id = 0
            if gen == self._gen and self.kind == "options":
                self.close_card(0.0)
            return False
        self._idle_id = GLib.timeout_add_seconds(OPTIONS_IDLE_SECONDS, idle)

    def _cancel_idle(self):
        if self._idle_id:
            GLib.source_remove(self._idle_id)
            self._idle_id = 0

    def _on_motion(self):
        if self.kind == "options":
            self._arm_idle()
        elif self.kind == "done" and self._close_id:
            self.close_card(max(2.5, self._close_after))        # still looking at the result: give it more time

    # -------------------------------------------------------------------- keys --
    def _on_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.cancel()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.kind == "options":
                self.run()
                return True
            if self.kind == "done":
                self.close_card(0.0)
                return True
        return False
# ============================================================================
#  [9e] The edge strip: a thin window on the screen edge that turns into the wheel.
#       For Wayland desktops that have the layer-shell protocol (KDE Plasma, Sway, Hyprland, labwc, COSMIC ...) but give an
#       ordinary program no way to see a drag from outside. The strip receives the drag itself.
#
#       A dragged file that rests on the strip for a moment makes the SAME window grow into the wheel (so the drag keeps
#       going to it, no other surface has to be found by the compositor); when the wheel is closed it shrinks back.
#       The layer-shell calls are made through ctypes straight into libgtk4-layer-shell: nothing else has to be installed.
# ============================================================================
STRIP_THICK = 4            # px: thick enough for a pointer that is pushed against the screen edge to always land on it
STRIP_LEN = 240            # px: and this long, in the middle of the edge
STRIP_DWELL_MS = 260       # a dragged file has to rest on the strip this long before the wheel opens (a quick pass does nothing)
STRIP_GRACE_MS = 450       # the pointer left the wheel without dropping: close after this long (a drop arrives well inside it)
EDGE_NAMES = ("right", "left", "top", "bottom")


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def current_edge():
    e = SETTINGS.get("edge")
    return e if e in EDGE_NAMES else "right"


class LayerShell:
    """The few calls of gtk4-layer-shell that the strip needs, made through ctypes (no typelib, only the library)."""
    EDGE = {"left": 0, "right": 1, "top": 2, "bottom": 3}
    LAYER_TOP = 2
    KB_NONE, KB_EXCLUSIVE, KB_ON_DEMAND = 0, 1, 2

    def __init__(self, lib):
        self.lib = lib
        c = ctypes
        lib.gtk_layer_is_supported.restype = c.c_int
        lib.gtk_layer_is_supported.argtypes = []
        lib.gtk_layer_init_for_window.argtypes = [c.c_void_p]
        lib.gtk_layer_set_layer.argtypes = [c.c_void_p, c.c_int]
        lib.gtk_layer_set_anchor.argtypes = [c.c_void_p, c.c_int, c.c_int]
        lib.gtk_layer_set_margin.argtypes = [c.c_void_p, c.c_int, c.c_int]
        lib.gtk_layer_set_keyboard_mode.argtypes = [c.c_void_p, c.c_int]
        lib.gtk_layer_set_namespace.argtypes = [c.c_void_p, c.c_char_p]
        lib.gtk_layer_set_exclusive_zone.argtypes = [c.c_void_p, c.c_int]
        lib.gtk_layer_set_monitor.argtypes = [c.c_void_p, c.c_void_p]
        for name in ("major", "minor", "micro"):
            fn = getattr(lib, "gtk_layer_get_%s_version" % name, None)
            if fn is not None:
                fn.restype = c.c_uint
                fn.argtypes = []
        get = c.pythonapi.PyCapsule_GetPointer
        get.restype = c.c_void_p
        get.argtypes = [c.py_object, c.c_char_p]
        self._capsule = get

    def ptr(self, obj):
        return self._capsule(obj.__gpointer__, None)

    def supported(self):
        """True when this session is Wayland and its compositor offers layer-shell."""
        try:
            return bool(self.lib.gtk_layer_is_supported())
        except Exception as e:
            dbg("layer-shell supported()", e)
            return False

    def version(self):
        try:
            return "%d.%d.%d" % tuple(getattr(self.lib, "gtk_layer_get_%s_version" % n)() for n in ("major", "minor", "micro"))
        except Exception:
            return "?"

    def init(self, win):
        self.lib.gtk_layer_init_for_window(self.ptr(win))

    def set_layer(self, win, layer):
        self.lib.gtk_layer_set_layer(self.ptr(win), layer)

    def set_namespace(self, win, name):
        self.lib.gtk_layer_set_namespace(self.ptr(win), name.encode("utf-8"))

    def set_keyboard_mode(self, win, mode):
        self.lib.gtk_layer_set_keyboard_mode(self.ptr(win), mode)

    def set_exclusive_zone(self, win, zone):
        self.lib.gtk_layer_set_exclusive_zone(self.ptr(win), zone)

    def set_anchor(self, win, edge, on):
        self.lib.gtk_layer_set_anchor(self.ptr(win), self.EDGE[edge], 1 if on else 0)

    def set_margin(self, win, edge, px):
        self.lib.gtk_layer_set_margin(self.ptr(win), self.EDGE[edge], int(px))

    def set_monitor(self, win, monitor):
        self.lib.gtk_layer_set_monitor(self.ptr(win), self.ptr(monitor))


class EdgeWheelWindow(WheelWindow):
    """The wheel window of the edge backend: one per screen. Always on screen as a thin strip on the screen edge; a dragged
    file that rests on it makes it grow into the wheel; when the wheel is done it shrinks back into the strip.

    It is the ordinary wheel window (same drop handling, same slices, same options page) with two looks:
        collapsed - the strip; the canvas is hidden. A drag that enters starts the dwell timer.
        expanded  - the wheel; the canvas is shown and the surface is a WHEEL_SIZE square next to the pointer.
    """

    def __init__(self, app, backend, monitor):
        super().__init__(app)
        self.backend = backend
        self.shell = backend.shell
        self.monitor = monitor
        geo = monitor.get_geometry()
        self.mon_w, self.mon_h = geo.width, geo.height
        self.edge = current_edge()
        self.expanded = False
        self.rect = (0, 0, 1, 1)               # where the window is, on its screen: x, y, width, height
        self._on_strip = False                 # a drag is on the strip right now
        self._ignore_drag = False              # this drag is not made of files: leave it alone until it ends
        self._strip_xy = None                  # the pointer, on its screen
        self._dwell_id = 0
        self._dwell_gen = 0
        self._dwell_tries = 0
        self._leave_id = 0
        self._inside = False                   # expanded: the drag is over the wheel
        self.set_title("rumman-edge")
        self.add_css_class("edge")
        sh = self.shell
        sh.init(self)                          # before the window exists on screen
        sh.set_layer(self, LayerShell.LAYER_TOP)
        sh.set_namespace(self, "rumman-edge")
        sh.set_keyboard_mode(self, LayerShell.KB_NONE)
        sh.set_exclusive_zone(self, -1)        # measured from the real screen edge, whatever panels there are
        sh.set_anchor(self, "left", True)
        sh.set_anchor(self, "top", True)
        sh.set_monitor(self, monitor)
        self._collapse()
        self.present()

    # ------------------------------------------------------------ the two hooks of the wheel window --
    def _attach_canvas(self):
        self.stage = Gtk.Box()                 # the only child: its size is the size of the window
        self.stage.add_css_class("rumman-stage")
        self.stage.append(self.canvas)
        self.set_child(self.stage)

    def _drop_widget(self):
        return self.stage

    # -------------------------------------------------------------------------------- geometry --
    def strip_rect(self):
        L, T = STRIP_LEN, STRIP_THICK
        w, h = self.mon_w, self.mon_h
        e = self.edge
        if e == "right":
            return (w - T, (h - L) // 2, T, L)
        if e == "left":
            return (0, (h - L) // 2, T, L)
        if e == "top":
            return ((w - L) // 2, 0, L, T)
        return ((w - L) // 2, h - T, L, T)

    def wheel_rect(self, px, py):
        """The square the wheel grows into: against the same screen edge, as close to the pointer's place along it as fits."""
        S = WHEEL_SIZE
        w, h = self.mon_w, self.mon_h
        e = self.edge
        if e == "right":
            return (w - S, _clamp(int(py - S / 2), 0, max(0, h - S)), S, S)
        if e == "left":
            return (0, _clamp(int(py - S / 2), 0, max(0, h - S)), S, S)
        if e == "top":
            return (_clamp(int(px - S / 2), 0, max(0, w - S)), 0, S, S)
        return (_clamp(int(px - S / 2), 0, max(0, w - S)), h - S, S, S)

    def center_rect(self):
        S = WHEEL_SIZE
        return (max(0, (self.mon_w - S) // 2), max(0, (self.mon_h - S) // 2), S, S)

    def _apply_rect(self, rect):
        x, y, w, h = rect
        self.rect = rect
        self.shell.set_margin(self, "left", x)
        self.shell.set_margin(self, "top", y)
        self.stage.set_size_request(w, h)
        self.set_default_size(-1, -1)          # GTK would otherwise keep the size the window last had

    def _screen_xy(self, x, y):
        return self.rect[0] + x, self.rect[1] + y

    def _log_size_later(self, what):
        """(debugging aid, only with RUMMAN_DEBUG=1) what size the compositor really gave this window, a moment after a change."""
        if not DEBUG:
            return

        def go():
            self.app.event("edge-size %s %dx%d" % (what, self.get_width(), self.get_height()))
            return False
        GLib.timeout_add(350, go)

    # ------------------------------------------------------------------------------ the two looks --
    def _collapse(self):
        self.expanded = False
        self._inside = False
        self.canvas.set_visible(False)
        for e in EDGE_NAMES:
            self.stage.remove_css_class("edge-" + e)
        self.stage.add_css_class("strip")
        self.stage.add_css_class("edge-" + self.edge)
        self.stage.remove_css_class("hot")
        self.shell.set_keyboard_mode(self, LayerShell.KB_NONE)
        self._apply_rect(self.strip_rect())
        self._log_size_later("strip")

    def _expand(self, rect, keyboard):
        self.expanded = True
        self.stage.remove_css_class("strip")
        self.stage.remove_css_class("hot")
        self.canvas.set_visible(True)
        self.shell.set_keyboard_mode(self, keyboard)
        self._apply_rect(rect)
        self.canvas.pop_in()
        self._log_size_later("wheel")
        self.backend.current = self
        self.app.event("edge-expand %s %s" % (self.kind, "x=%d y=%d" % rect[:2]))

    def _hot(self, on):
        if on:
            self.stage.add_css_class("hot")
        else:
            self.stage.remove_css_class("hot")

    def set_edge(self, edge):
        """The user chose another edge (the strip is only moved while it is a strip)."""
        self.edge = edge if edge in EDGE_NAMES else "right"
        if not self.expanded:
            self._collapse()

    # ---------------------------------------------------------------------------------- opening --
    def open_for_drag(self):
        """The action 'wheel-open' (the other backends' way in): open the wheel in the middle of the strip, files not known yet."""
        self.app.dismiss_options()
        self._reset("drag")
        self.canvas.set_loading()
        x, y, w, h = self.strip_rect()
        self._expand(self.wheel_rect(x + w / 2.0, y + h / 2.0), LayerShell.KB_NONE)

    def open_for_files(self, files):
        """`rumman FILE`, "Open with", the demo: the wheel in the middle of the screen; you click instead of dropping."""
        self._reset("pick")
        self.files = list(files)
        self.dropped = True
        self._show_files()
        self._expand(self.center_rect(), LayerShell.KB_ON_DEMAND)
        self._arm_idle()

    def _open_for_dragged(self, files):
        """The dwell is over and the dragged files are known: grow the wheel next to the pointer."""
        self.app.dismiss_options()
        self._reset("drag")
        self.files = list(files)
        self._show_files()
        px, py = self._strip_xy if self._strip_xy else (self.rect[0] + self.rect[2] / 2.0, self.rect[1] + self.rect[3] / 2.0)
        self._inside = True
        self._expand(self.wheel_rect(px, py), LayerShell.KB_NONE)

    def _open_picker_at(self, files):
        """Files were dropped on the thin strip itself: show the wheel there and let the user click a format."""
        self.app.dismiss_options()
        self._reset("pick")
        self.files = list(files)
        self.dropped = True
        self._show_files()
        px, py = self._strip_xy if self._strip_xy else (self.rect[0], self.rect[1])
        self._expand(self.wheel_rect(px, py), LayerShell.KB_ON_DEMAND)
        self._arm_idle()

    # ---------------------------------------------------------------------------------- closing --
    def _hide_now(self):
        self._cancel_idle()
        self._cancel_noval()
        if self.expanded and self.canvas.get_mapped():
            self.canvas.fade_out(self._finish_hide)
        else:
            self._finish_hide()

    def _finish_hide(self):
        """Not hidden: back to being the strip."""
        self._cancel_leave()
        self.kind = "idle"
        self.files = []
        self.jobs = []
        self.drag_inside = False
        self._was_active = False
        self._gen += 1
        self.canvas.set_loading()
        self._collapse()
        self.app.event("edge-collapse")
        try:
            self.app.backend.after_hide(self)
        except Exception:
            pass

    def _cancel_leave(self):
        if self._leave_id:
            GLib.source_remove(self._leave_id)
            self._leave_id = 0

    def _leave_check(self):
        """The drag left the wheel. A drop on the way out arrives within a moment; when nothing came, the drag went elsewhere."""
        self._cancel_leave()
        gen = self._gen

        def go():
            self._leave_id = 0
            if gen == self._gen and self.kind == "drag" and not self.dropped and not self._inside:
                self.close_wheel(0.0)
            return False
        self._leave_id = GLib.timeout_add(STRIP_GRACE_MS, go)

    # ---------------------------------------------------------------- the drag (both looks) --
    def _on_enter(self, dt, x, y):
        if self.expanded:
            self._cancel_leave()
            self._inside = True
            return super()._on_enter(dt, x, y)
        self.app.event("edge-enter")
        self._on_strip = True
        self._ignore_drag = False
        self._strip_xy = self._screen_xy(x, y)
        self._hot(True)
        self._arm_dwell()
        return Gdk.DragAction.COPY

    def _on_motion(self, dt, x, y):
        if self.expanded:
            self._inside = True
            return super()._on_motion(dt, x, y)
        self._strip_xy = self._screen_xy(x, y)
        return Gdk.DragAction.COPY

    def _on_leave(self, dt):
        if self.expanded:
            self._inside = False
            super()._on_leave(dt)
            if self.kind == "drag" and not self.dropped:
                self._leave_check()
            return
        self.app.event("edge-leave")
        self._on_strip = False
        self._ignore_drag = False
        self._cancel_dwell()
        self._hot(False)

    def _on_value(self, dt, pspec):
        if self.expanded:
            super()._on_value(dt, pspec)

    def _on_drop(self, dt, value, x, y):
        if self.expanded:
            self._cancel_leave()
            self._inside = False
            return super()._on_drop(dt, value, x, y)
        files = paths_from_value(value)
        self._cancel_dwell()
        self._on_strip = False
        self._hot(False)
        if not files:
            return False
        self.app.event("edge-drop-on-strip")
        self._open_picker_at(files)
        return True

    # ---------------------------------------------------------------------------------- the dwell --
    def _arm_dwell(self):
        self._cancel_dwell()
        self._dwell_tries = 0
        self._dwell_gen += 1
        self._dwell_id = GLib.timeout_add(STRIP_DWELL_MS, self._dwell_done, self._dwell_gen)

    def _cancel_dwell(self):
        self._dwell_gen += 1
        if self._dwell_id:
            GLib.source_remove(self._dwell_id)
            self._dwell_id = 0

    def _dwell_done(self, gen):
        self._dwell_id = 0
        if gen != self._dwell_gen or self.expanded or not self._on_strip or self._ignore_drag:
            return False
        value = self.drop_target.get_value()
        if value is None:                      # the data of the drag has not arrived yet: give it a little longer
            self._dwell_tries += 1
            if self._dwell_tries <= 4:
                self._dwell_id = GLib.timeout_add(150, self._dwell_done, gen)
            return False
        files = paths_from_value(value)
        if not files:
            self._ignore_drag = True           # text, a link, ...: not for us
            self._hot(False)
            return False
        self._open_for_dragged(files)
        return False

    def destroy_strip(self):
        self._cancel_dwell()
        self._cancel_leave()
        self._cancel_idle()
        self._cancel_noval()
        self._cancel_close()
        self.destroy()
# ============================================================================
#  [9f] The settings page: where results go, what happens to the original, the compression the options page starts at,
#       the tools' starting choices, and what happens when a run is over. Every change is stored at once.
# ============================================================================
SETTINGS_W, SETTINGS_H = 560, 720
LEVEL_CODES = ("auto",) + LEVELS


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_title(tr("se_title"))
        self.set_default_size(SETTINGS_W, SETTINGS_H)
        self.add_css_class("rumman-settings")
        self.switches = {}
        self.level_dds = {}
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.add_css_class("head")
        head.append(LogoMark(40, tile=False))
        t = Gtk.Label(label=tr("se_title"))
        t.add_css_class("stitle")
        t.set_xalign(0.0)
        t.set_hexpand(True)
        t.set_wrap(True)
        head.append(t)
        root.append(head)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.add_css_class("body")
        scroll.set_child(body)

        # --- results
        c = self._card(body, tr("se_h_out"))
        self.where_dd = self._dropdown([tr("se_where_source"), tr("se_where_custom")])
        self.where_dd.set_selected(1 if SETTINGS.get("out_mode") == "custom" else 0)
        self.where_dd.connect("notify::selected", self._on_where)
        c.append(self._row(tr("se_where"), None, self.where_dd))
        self.folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.folder_lbl = Gtk.Label()
        self.folder_lbl.add_css_class("pathl")
        self.folder_lbl.set_xalign(0.0)
        self.folder_lbl.set_hexpand(True)
        self.folder_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.folder_row.append(self.folder_lbl)
        self.folder_btn = _button(tr("se_choose"), "ghost", self._choose_folder)
        self.folder_row.append(self.folder_btn)
        c.append(self.folder_row)
        self.conflict_dd = self._dropdown([tr("se_c_rename"), tr("se_c_replace")])
        self.conflict_dd.set_selected(1 if SETTINGS.get("conflict") == "replace" else 0)
        self.conflict_dd.connect("notify::selected", lambda dd, p: SETTINGS.set("conflict", "replace" if dd.get_selected() == 1 else "rename"))
        c.append(self._row(tr("se_conflict"), None, self.conflict_dd))
        c.append(self._switch_row("delete_original", tr("se_delete"), tr("se_delete_sub")))
        self._sync_folder()

        # --- compression levels
        c = self._card(body, tr("se_h_lvl"))
        names = [tr("se_lvl_auto")] + [tr("lv_" + l) for l in LEVELS]
        for key, label in (("lvl_img", "se_lvl_img"), ("lvl_aud", "se_lvl_aud"), ("lvl_vid", "se_lvl_vid"), ("lvl_pdf", "se_lvl_pdf")):
            dd = self._dropdown(names)
            cur = SETTINGS.get(key)
            dd.set_selected(LEVEL_CODES.index(cur) if cur in LEVEL_CODES else 0)
            dd.connect("notify::selected", lambda d, p, k=key: SETTINGS.set(k, LEVEL_CODES[min(d.get_selected(), len(LEVEL_CODES) - 1)]))
            self.level_dds[key] = dd
            c.append(self._row(tr(label), None, dd))
        self.arc_dd = self._dropdown([tr("arc_fast"), tr("arc_normal"), tr("arc_max")])
        self.arc_dd.set_selected({3: 0, 6: 1, 9: 2}.get(int(SETTINGS.get("archive_level")), 1))
        self.arc_dd.connect("notify::selected", lambda d, p: SETTINGS.set("archive_level", (3, 6, 9)[min(d.get_selected(), 2)]))
        c.append(self._row(tr("o_arc_level"), None, self.arc_dd))

        # --- tools
        c = self._card(body, tr("se_h_tools"))
        for key, name, sub in (("strip_meta", "o_strip", "o_strip_sub_av"), ("mono", "o_mono", "o_mono_sub"),
                               ("normalize", "o_normalize", "o_normalize_sub"), ("trim_silence", "o_trim", "o_trim_sub"),
                               ("mute", "o_mute", "o_mute_sub"), ("include_hidden", "o_hidden", "o_hidden_sub"),
                               ("keep_root", "o_keep_root", "o_keep_root_sub")):
            c.append(self._switch_row(key, tr(name), tr(sub)))

        # --- when it is over
        c = self._card(body, tr("se_h_after"))
        c.append(self._switch_row("notify", tr("se_notify"), None))
        c.append(self._switch_row("open_after", tr("se_open"), None))

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.set_margin_top(4)
        bar.set_margin_bottom(18)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        bar.append(sp)
        bar.append(_button(tr("btn_close"), "primary", lambda: self.close()))
        body.append(bar)

        kc = Gtk.EventControllerKey()
        kc.connect("key-pressed", lambda c_, kv, kc_, st: (self.close(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(kc)

    # ---- small builders
    def _card(self, body, title):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        h = _label(title, "h")
        card.append(h)
        body.append(card)
        return card

    def _dropdown(self, names):
        dd = Gtk.DropDown.new_from_strings(names)
        dd.set_valign(Gtk.Align.CENTER)
        return dd

    def _row(self, title, sub, widget):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)
        col.set_valign(Gtk.Align.CENTER)
        col.append(_label(title, "setname"))
        if sub:
            col.append(_label(sub, "muted"))
        row.append(col)
        row.append(widget)
        return row

    def _switch_row(self, key, title, sub):
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(SETTINGS.get(key)))
        sw.connect("notify::active", lambda s, p, k=key: SETTINGS.set(k, s.get_active()))
        self.switches[key] = sw
        return self._row(title, sub, sw)

    # ---- the folder for the results
    def _sync_folder(self):
        custom = SETTINGS.get("out_mode") == "custom"
        self.folder_row.set_visible(custom)
        d = SETTINGS.get("out_dir")
        self.folder_lbl.set_label(d if d else tr("se_no_folder"))

    def _on_where(self, dd, pspec):
        SETTINGS.set("out_mode", "custom" if dd.get_selected() == 1 else "source")
        self._sync_folder()
        if SETTINGS.get("out_mode") == "custom" and not writable_dir(SETTINGS.get("out_dir")):
            GLib.idle_add(self._choose_folder)

    def _choose_folder(self):
        try:
            dlg = Gtk.FileDialog()
            dlg.set_title(tr("se_choose_title"))
            start = SETTINGS.get("out_dir")
            if start and os.path.isdir(start):
                dlg.set_initial_folder(Gio.File.new_for_path(start))
            dlg.select_folder(self, None, self._folder_chosen)
        except Exception as e:
            dbg("folder dialog failed", e)
        return False

    def _folder_chosen(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            f = None
        path = f.get_path() if f is not None else None
        if path and writable_dir(path):
            SETTINGS.set("out_dir", path)
            SETTINGS.set("out_mode", "custom")
        elif not writable_dir(SETTINGS.get("out_dir")):
            SETTINGS.set("out_mode", "source")          # nothing usable was chosen: back to "next to the original"
            self.where_dd.set_selected(0)
        self._sync_folder()


# ============================================================================
#  [10] Backends: how the app learns that a drag started, that Shift was pressed and where the pointer is.
#       Every backend talks to the core through the same four calls (RummanApp.backend_*):
#           drag-begin / wheel-open / wheel-cycle / drag-end          (each with a sequence number)
#       X11       : built in (this file). GNOME/Wayland: the helper extension calls the same four as GActions.
#       Edge      : KDE / Sway / Hyprland ... (Wayland with layer-shell): a thin strip on the screen edge receives the drag itself
#                   (p09e_edge.py); it opens the wheel window directly, so none of the four calls are needed.
# ============================================================================
class Backend:
    kind = "other"

    def __init__(self, app):
        self.app = app
        self.on_change = None

    def start(self):
        pass

    def stop(self):
        pass

    def status(self):
        return {"level": "warn", "text": tr("st_mode_other")}

    def info(self):
        return {"backend": self.kind}

    # the wheel window calls these around showing / hiding itself
    def before_show(self, win):
        pass

    def after_show(self, win):
        pass

    def after_hide(self, win):
        pass

    def wheel_window(self):
        """A backend that owns its wheel windows (the edge strip) hands out the one to use; the others return None."""
        return None

    def nudge_pointer(self):
        """Move the pointer by one pixel (and back). The drag source only notices a NEW window under the pointer when
        the pointer moves, so a wheel that opens under a motionless pointer needs this push."""

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass


class FallbackBackend(Backend):
    """Desktops where drags cannot be caught yet: the wheel still works through `rumman FILE` / "Open with"."""
    kind = "other"

    def __init__(self, app, reason="", text_key="st_mode_other", text_args=()):
        super().__init__(app)
        self.reason = reason
        self.text_key = text_key
        self.text_args = tuple(text_args)

    def status(self):
        return {"level": "warn", "text": tr(self.text_key, *self.text_args)}

    def info(self):
        return {"backend": self.kind, "reason": self.reason}


# ------------------------------------------------------------------- X11 (ctypes) --
class _XFixesSelectionNotify(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("subtype", ctypes.c_int),
                ("owner", ctypes.c_ulong), ("selection", ctypes.c_ulong), ("timestamp", ctypes.c_ulong),
                ("selection_timestamp", ctypes.c_ulong)]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
                ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


class _XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("cursor", ctypes.c_ulong)]


X_SHIFT_MASK = 1 << 0
X_BUTTON1_MASK = 1 << 8
X_CW_OVERRIDE_REDIRECT = 1 << 9
XFIXES_SET_SELECTION_OWNER_NOTIFY_MASK = 1
_XERR_HANDLER_TYPE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))
_X_ERR_STATE = {"installed": False, "prev": None, "mine": set(), "keep": None, "last": ""}


def _install_x_error_handler(libx11):
    """A protocol error on OUR connection (e.g. we touched a window that just went away) must not kill the program,
    which is what Xlib's default handler does. Errors of every other connection (GDK's) go to the previous handler."""
    if _X_ERR_STATE["installed"]:
        return

    def handler(dpy, ev):
        e = ev.contents
        if e.display in _X_ERR_STATE["mine"]:
            _X_ERR_STATE["last"] = "X error %d (request %d)" % (e.error_code, e.request_code)
            dbg(_X_ERR_STATE["last"])
            return 0
        prev = _X_ERR_STATE["prev"]
        return prev(dpy, ev) if prev else 0
    cb = _XERR_HANDLER_TYPE(handler)
    libx11.XSetErrorHandler.restype = ctypes.c_void_p
    libx11.XSetErrorHandler.argtypes = [_XERR_HANDLER_TYPE]
    prev_addr = libx11.XSetErrorHandler(cb)
    _X_ERR_STATE["keep"] = cb
    _X_ERR_STATE["prev"] = _XERR_HANDLER_TYPE(prev_addr) if prev_addr else None
    _X_ERR_STATE["installed"] = True


class XConn:
    """A second, private connection to the X server: pointer polling, XDND ownership notifications, window placement."""

    def __init__(self):
        c = ctypes
        self.x = c.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.xf = c.CDLL(ctypes.util.find_library("Xfixes") or "libXfixes.so.3")
        x, xf = self.x, self.xf
        x.XOpenDisplay.restype = c.c_void_p
        x.XOpenDisplay.argtypes = [c.c_char_p]
        x.XCloseDisplay.argtypes = [c.c_void_p]
        x.XDefaultRootWindow.restype = c.c_ulong
        x.XDefaultRootWindow.argtypes = [c.c_void_p]
        x.XInternAtom.restype = c.c_ulong
        x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
        x.XQueryPointer.restype = c.c_int
        x.XQueryPointer.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_ulong),
                                    c.POINTER(c.c_int), c.POINTER(c.c_int), c.POINTER(c.c_int), c.POINTER(c.c_int),
                                    c.POINTER(c.c_uint)]
        x.XPending.restype = c.c_int
        x.XPending.argtypes = [c.c_void_p]
        x.XNextEvent.argtypes = [c.c_void_p, c.c_void_p]
        x.XConnectionNumber.restype = c.c_int
        x.XConnectionNumber.argtypes = [c.c_void_p]
        x.XFlush.argtypes = [c.c_void_p]
        x.XSync.argtypes = [c.c_void_p, c.c_int]
        x.XMoveWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int]
        x.XRaiseWindow.argtypes = [c.c_void_p, c.c_ulong]
        x.XChangeWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_void_p]
        x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
        x.XWarpPointer.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint,
                                   c.c_int, c.c_int]
        xf.XFixesQueryExtension.restype = c.c_int
        xf.XFixesQueryExtension.argtypes = [c.c_void_p, c.POINTER(c.c_int), c.POINTER(c.c_int)]
        xf.XFixesSelectSelectionInput.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_ulong]
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("cannot open the X display")
        _X_ERR_STATE["mine"].add(self.dpy)
        _install_x_error_handler(x)
        self.root = x.XDefaultRootWindow(self.dpy)
        eb, er = c.c_int(), c.c_int()
        if not xf.XFixesQueryExtension(self.dpy, c.byref(eb), c.byref(er)):
            raise RuntimeError("the X server has no XFixes extension")
        self.fixes_base = eb.value
        self._ev = (c.c_long * 24)()

    def close(self):
        if self.dpy:
            _X_ERR_STATE["mine"].discard(self.dpy)
            self.x.XCloseDisplay(self.dpy)
            self.dpy = None

    def atom(self, name):
        return self.x.XInternAtom(self.dpy, name.encode(), 0)

    def fd(self):
        return self.x.XConnectionNumber(self.dpy)

    def watch_selection(self, atom):
        self.xf.XFixesSelectSelectionInput(self.dpy, self.root, atom, XFIXES_SET_SELECTION_OWNER_NOTIFY_MASK)
        self.x.XFlush(self.dpy)

    def pointer(self):
        """(x, y, modifier+button mask) of the pointer, in device pixels of the whole screen; None on failure."""
        c = ctypes
        root, child = c.c_ulong(), c.c_ulong()
        rx, ry, wx, wy = c.c_int(), c.c_int(), c.c_int(), c.c_int()
        mask = c.c_uint()
        ok = self.x.XQueryPointer(self.dpy, self.root, c.byref(root), c.byref(child), c.byref(rx), c.byref(ry),
                                  c.byref(wx), c.byref(wy), c.byref(mask))
        if not ok:
            return None
        return rx.value, ry.value, mask.value

    def events(self):
        """Selection-owner notifications that arrived: list of (selection_atom, owner_window)."""
        out = []
        base = self.fixes_base
        while self.x.XPending(self.dpy):
            self.x.XNextEvent(self.dpy, ctypes.byref(self._ev))
            if self._ev[0] & 0xFFFFFFFF == (base & 0xFFFFFFFF):          # XFixesSelectionNotify = base + 0
                n = _XFixesSelectionNotify.from_buffer(self._ev)
                out.append((n.selection, n.owner))
        return out

    def set_override_redirect(self, xid):
        attrs = _XSetWindowAttributes()
        attrs.override_redirect = 1
        self.x.XChangeWindowAttributes(self.dpy, xid, X_CW_OVERRIDE_REDIRECT, ctypes.byref(attrs))
        self.x.XFlush(self.dpy)

    def move(self, xid, x, y):
        self.x.XMoveWindow(self.dpy, xid, int(x), int(y))
        self.x.XFlush(self.dpy)

    def raise_(self, xid):
        self.x.XRaiseWindow(self.dpy, xid)
        self.x.XFlush(self.dpy)

    def warp(self, dx, dy):
        self.x.XWarpPointer(self.dpy, 0, 0, 0, 0, 0, 0, int(dx), int(dy))      # relative move of the pointer
        self.x.XFlush(self.dpy)

    def focus(self, xid):
        self.x.XSetInputFocus(self.dpy, xid, 2, 0)                  # RevertToParent, CurrentTime
        self.x.XFlush(self.dpy)

    def sync(self):
        self.x.XSync(self.dpy, 0)


class X11Backend(Backend):
    """X11 sessions (Xfce, Cinnamon, MATE, i3, ...): the drag is noticed through the owner of the XdndSelection, Shift and the
    button through the pointer state, and the wheel is placed with XMoveWindow."""
    kind = "x11"
    POLL_MS = 20

    def __init__(self, app):
        super().__init__(app)
        self.conn = None
        self.error = ""
        self.seq = 0
        self.dragging = False
        self.opened = False
        self.shift_prev = False
        self.btn_off = 0
        self.xdnd = 0
        self._watch = 0
        self._poll = 0
        self._safety = 0

    def start(self):
        if GdkX11 is None:
            self.error = "GdkX11 typelib missing"
            log_error("x11 backend: " + self.error)
            return
        try:
            self.conn = XConn()
            self.xdnd = self.conn.atom("XdndSelection")
            self.conn.watch_selection(self.xdnd)
        except Exception as e:
            self.error = str(e)
            self.conn = None
            log_error("x11 backend: %s" % e)
            return
        self._watch = GLib.io_add_watch(self.conn.fd(), GLib.PRIORITY_DEFAULT,
                                        GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, self._on_fd)
        self._safety = GLib.timeout_add(400, self._on_safety)
        self.app.event("x11 backend ready")

    def stop(self):
        for attr in ("_watch", "_poll", "_safety"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def status(self):
        if self.conn is None:
            return {"level": "bad", "text": "X11: %s" % (self.error or "?")}
        return {"level": "ok", "text": tr("st_mode_x11")}

    def info(self):
        return {"backend": self.kind, "ok": self.conn is not None, "error": self.error,
                "dragging": self.dragging, "seq": self.seq}

    # ------------------------------------------------------------ drag tracking --
    def _on_fd(self, fd, cond):
        if self.conn is None:
            return False
        self._drain()
        return True

    def _on_safety(self):
        if self.conn is None:
            return False
        self._drain()
        return True

    def _drain(self):
        for sel, owner in self.conn.events():
            if sel != self.xdnd:
                continue
            if owner:
                self._begin()
            elif self.dragging:
                self._end("owner-cleared")

    def _begin(self):
        if self.dragging:
            return
        q = self.conn.pointer()
        self.seq += 1
        self.dragging = True
        self.opened = False
        self.btn_off = 0
        self.shift_prev = bool(q and q[2] & X_SHIFT_MASK)          # Shift already held: release and press it again
        self._poll = GLib.timeout_add(self.POLL_MS, self._tick)
        self.app.backend_drag_begin(self.seq)

    def _end(self, why):
        if not self.dragging:
            return
        self.dragging = False
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0
        self.app.event("x11 drag end (%s)" % why)
        self.app.backend_drag_end(self.seq)

    def _tick(self):
        if self.conn is None or not self.dragging:
            self._poll = 0
            return False
        self._drain()
        q = self.conn.pointer()
        if q is None:
            self._poll = 0
            self._end("no-pointer")
            return False
        _x, _y, mask = q
        if mask & X_BUTTON1_MASK:
            self.btn_off = 0
        else:
            self.btn_off += 1
            if self.btn_off >= 3:                                   # ~60 ms without the button: the drag is over
                self._poll = 0
                self._end("button-up")
                return False
        shift = bool(mask & X_SHIFT_MASK)
        if shift and not self.shift_prev:
            if not self.opened:
                self.opened = True
                self.app.backend_wheel_open(self.seq)
            else:
                self.app.backend_wheel_cycle(self.seq)
        self.shift_prev = shift
        return True

    def nudge_pointer(self):
        if self.conn is None:
            return
        self.conn.warp(1, 0)

        def back():
            if self.conn is not None:
                self.conn.warp(-1, 0)
            return False
        GLib.timeout_add(25, back)
        self.app.event("x11 pointer nudge")

    # ---------------------------------------------------------------- placement --
    def _xid(self, win):
        try:
            surface = win.get_surface()
            if surface is None or GdkX11 is None or not isinstance(surface, GdkX11.X11Surface):
                return 0
            return int(GdkX11.X11Surface.get_xid(surface))
        except Exception as e:
            dbg("xid", e)
            return 0

    def _monitor_rect(self, px, py):
        """(x, y, w, h, scale) of the monitor under (px, py), in device pixels."""
        best = None
        try:
            mons = Gdk.Display.get_default().get_monitors()
            for i in range(mons.get_n_items()):
                m = mons.get_item(i)
                g = m.get_geometry()
                sc = max(1, m.get_scale_factor())
                r = (g.x * sc, g.y * sc, g.width * sc, g.height * sc, sc)
                if best is None:
                    best = r
                if r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]:
                    return r
        except Exception as e:
            dbg("monitor", e)
        return best or (0, 0, 1 << 15, 1 << 15, 1)

    @staticmethod
    def _win_size(win):
        """(width, height) of a window in logical pixels, also before it is on screen. What the content asks for now is
        measured: the window's own size can still be the one of the previous content when it is shown again."""
        w = h = 0
        try:
            dw, dh = win.get_default_size()
            child = win.get_child()
            if child is not None:
                cw = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
                w = max(cw, dw if dw > 0 else 0)
                h = child.measure(Gtk.Orientation.VERTICAL, w)[1]
                if dh > 0:
                    h = max(h, dh)
            if not (w and h):
                _minimum, nat = win.get_preferred_size()
                w, h = nat.width, nat.height
            if not (w and h) and win.get_mapped():
                w, h = win.get_width(), win.get_height()
            dbg("win size: wants %dx%d (default %dx%d, now %dx%d)" % (w, h, dw, dh, win.get_width(), win.get_height()))
        except Exception as e:
            dbg("win size", e)
        return (w or WHEEL_SIZE), (h or WHEEL_SIZE)

    def _place(self, win, xid):
        q = self.conn.pointer()
        if q is None:
            return
        px, py = q[0], q[1]
        mx, my, mw, mh, sc = self._monitor_rect(px, py)
        wl, hl = self._win_size(win)
        w, h = wl * sc, hl * sc
        x = px - w // 2                                             # the window's centre is under the pointer
        y = py - h // 2
        x = max(mx, min(x, mx + mw - w))
        y = max(my, min(y, my + mh - h))
        self.conn.move(xid, x, y)
        dbg("window placed at", x, y, "size", wl, hl, "pointer", px, py)

    def before_show(self, win):
        if self.conn is None:
            return
        win.realize()
        xid = self._xid(win)
        if not xid:
            return
        self.conn.set_override_redirect(xid)                        # no window manager: placement and stacking are ours
        self._place(win, xid)

    def after_show(self, win):
        if self.conn is None:
            return
        xid = self._xid(win)
        if not xid:
            return
        self.conn.raise_(xid)

        def again(tries=[0]):
            if self.conn is None:
                return False
            x2 = self._xid(win)
            if x2 and win.get_visible():
                self._place(win, x2)
                self.conn.raise_(x2)
                if getattr(win, "kind", "") in ("pick", "options"):    # clicked / typed use: Esc, Enter and the digits need the keyboard
                    self.conn.focus(x2)
            return False
        GLib.timeout_add(40, again)                                 # GTK may move the window once more while mapping it


# ------------------------------------------------------------------- GNOME/Wayland --
class GnomeBackend(Backend):
    """GNOME on Wayland: a small Shell extension (gnome-extension/) watches drags, reads Shift and places the wheel. It
    calls the app's actions over D-Bus, so this class only watches whether the extension is there."""
    kind = "gnome"
    HELPER_WANTED = 2          # the version of the helper extension that goes with this program (see its metadata.json)

    def __init__(self, app):
        super().__init__(app)
        self.present = False
        self.helper_version = None       # asked from the helper when it appears; None = unknown
        self._watch = 0

    def start(self):
        try:
            self._watch = Gio.bus_watch_name(Gio.BusType.SESSION, SHELL_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                             self._appeared, self._vanished)
        except Exception as e:
            log_error("gnome backend: %s" % e)

    def stop(self):
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0

    def _appeared(self, conn, name, owner):
        self.present = True
        self.helper_version = None
        self.app.event("helper extension appeared")
        self._changed()
        try:
            conn.call(SHELL_BUS_NAME, SHELL_BUS_PATH, SHELL_BUS_NAME, "Info", None, GLib.VariantType.new("(s)"),
                      Gio.DBusCallFlags.NO_AUTO_START, 2000, None, self._got_info)
        except Exception as e:
            self.app.event("helper Info: %s" % e)

    def _got_info(self, conn, res):
        """An older helper (loaded before this version was installed) stays in the Shell until the next login."""
        try:
            self.helper_version = int(json.loads(conn.call_finish(res).unpack()[0]).get("helper"))
        except Exception as e:
            self.helper_version = None
            self.app.event("helper Info failed: %s" % e)
        self.app.event("helper version %s (this program wants %d)" % (self.helper_version, self.HELPER_WANTED))
        self._changed()

    def _vanished(self, conn, name):
        self.present = False
        self.helper_version = None
        self.app.event("helper extension not on the bus")
        self._changed()

    def status(self):
        if self.present:
            if self.helper_version is not None and self.helper_version < self.HELPER_WANTED:
                return {"level": "warn", "text": tr("st_mode_gnome_old")}
            return {"level": "ok", "text": tr("st_mode_gnome_ok")}
        return {"level": "warn", "text": tr("st_mode_gnome_missing")}

    def info(self):
        return {"backend": self.kind, "extension_on_bus": self.present, "helper_version": self.helper_version}

    # ---- the two things the app asks of the extension (both fire-and-forget; nothing happens without the extension) --
    def _ask_helper(self, method):
        if not self.present:
            return
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            conn.call(SHELL_BUS_NAME, SHELL_BUS_PATH, SHELL_BUS_NAME, method, None, None,
                      Gio.DBusCallFlags.NO_AUTO_START, 1500, None, self._asked, method)
        except Exception as e:
            self.app.event("helper %s: %s" % (method, e))

    def _asked(self, conn, res, method):
        try:
            conn.call_finish(res)
        except Exception as e:
            self.app.event("helper %s failed: %s" % (method, e))

    def before_show(self, win):
        """The wheel window is about to open: tell the extension, so that it recognises the new window at once, puts it
        under the pointer and skips the window-opening animation."""
        self._ask_helper("Expect")

    def nudge_pointer(self):
        self._ask_helper("Nudge")


# ------------------------------------------------------------- the edge strip (layer-shell) --
def layer_shell_install_hint():
    """The command that installs gtk4-layer-shell on this distribution ("" when we do not know the distribution)."""
    ids = set()
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k in ("ID", "ID_LIKE"):
                    ids.update(v.strip("\"'").lower().split())
    except OSError:
        pass
    if ids & {"fedora", "rhel", "centos", "rocky", "almalinux", "nobara"}:
        return "sudo dnf install gtk4-layer-shell"
    if ids & {"arch", "manjaro", "endeavouros", "cachyos", "garuda"}:
        return "sudo pacman -S gtk4-layer-shell"
    if ids & {"debian", "ubuntu", "linuxmint", "pop", "neon"}:
        return "sudo apt install libgtk4-layer-shell0"
    if ids & {"opensuse", "suse", "opensuse-tumbleweed", "opensuse-leap", "opensuse-slowroll"}:
        return "sudo zypper install libgtk4-layer-shell0"
    return ""


class EdgeBackend(Backend):
    """Wayland desktops that have layer-shell but no way to see a drag from outside (KDE Plasma, Sway, Hyprland, labwc,
    COSMIC ...): a thin strip on the edge of every screen receives the drag itself. A file held on it for a moment makes
    that same window grow into the wheel (see EdgeWheelWindow)."""
    kind = "edge"

    def __init__(self, app):
        super().__init__(app)
        self.shell = None
        self.windows = []
        self.current = None
        self.error = ""
        self._model = None
        self._model_id = 0
        self._sync_id = 0

    def start(self):
        try:
            self.shell = LayerShell(LAYER_LIB)
            if not self.shell.supported():
                self.error = "layer-shell is not offered by this compositor"
                return
            disp = Gdk.Display.get_default()
            self._model = disp.get_monitors()
            self._model_id = self._model.connect("items-changed", lambda *a: self._schedule_sync())
            self._sync()
        except Exception as e:
            self.error = str(e)
            log_error("edge backend: %s" % traceback.format_exc())

    def stop(self):
        if self._sync_id:
            GLib.source_remove(self._sync_id)
            self._sync_id = 0
        if self._model is not None and self._model_id:
            try:
                self._model.disconnect(self._model_id)
            except Exception:
                pass
            self._model_id = 0
        self._drop_windows()

    def _drop_windows(self):
        for w in self.windows:
            try:
                w.destroy_strip()
            except Exception:
                pass
        self.windows = []
        self.current = None

    def _schedule_sync(self):
        if not self._sync_id:
            self._sync_id = GLib.timeout_add(250, self._sync_later)

    def _sync_later(self):
        self._sync_id = 0
        self._sync()
        return False

    def _sync(self):
        """One strip window for every screen; built again when screens come or go."""
        self._drop_windows()
        model = self._model
        for i in range(model.get_n_items()):
            mon = model.get_item(i)
            try:
                self.windows.append(EdgeWheelWindow(self.app, self, mon))
            except Exception:
                log_error("edge strip on screen %d: %s" % (i, traceback.format_exc()))
        self.app.event("edge: %d strip(s) on the %s edge" % (len(self.windows), current_edge()))
        self._changed()

    def apply_edge(self):
        """The user chose another edge in the status window."""
        edge = current_edge()
        for w in self.windows:
            w.set_edge(edge)
        self._changed()

    def wheel_window(self):
        return self.windows[0] if self.windows else None

    def status(self):
        if self.error:
            return {"level": "bad", "text": self.error}
        if not self.windows:
            return {"level": "warn", "text": tr("st_mode_other")}
        return {"level": "ok", "text": tr("st_mode_edge", tr("edge_" + current_edge()))}

    def info(self):
        return {"backend": self.kind, "layer_shell": self.shell.version() if self.shell else "-", "library": LAYER_LIB_NAME,
                "edge": current_edge(), "strips": len(self.windows), "error": self.error}


def _edge_or_explain(app):
    """The edge strip when the library and the compositor allow it; otherwise a fallback that says what is missing."""
    if LAYER_LIB is None:
        hint = layer_shell_install_hint()
        if hint:
            return FallbackBackend(app, "gtk4-layer-shell is not installed (%s)" % (LAYER_LIB_ERROR or "not found"),
                                   "st_mode_edge_missing", (hint,))
        return FallbackBackend(app, "gtk4-layer-shell is not installed (%s)" % (LAYER_LIB_ERROR or "not found"),
                               "st_mode_edge_missing_generic")
    try:
        if LayerShell(LAYER_LIB).supported():
            return EdgeBackend(app)
    except Exception as e:
        dbg("layer-shell check", e)
    return FallbackBackend(app, "this compositor does not offer layer-shell", "st_mode_edge_nosupport")


def detect_backend(app):
    """Pick the backend for this session (see the capability ladder in the README).
    RUMMAN_BACKEND=x11|gnome|none forces one (for testing and for hunting problems)."""
    forced = (os.environ.get("RUMMAN_BACKEND", "") or "").strip().lower()
    if forced == "x11":
        return X11Backend(app)
    if forced == "gnome":
        return GnomeBackend(app)
    if forced in ("none", "off", "fallback"):
        return FallbackBackend(app, "forced by RUMMAN_BACKEND")
    if forced == "edge":
        return _edge_or_explain(app)
    desk = (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").lower()
    sess = (os.environ.get("XDG_SESSION_TYPE", "") or "").lower()
    disp = Gdk.Display.get_default()
    dname = type(disp).__name__.lower() if disp is not None else ""
    if sess == "wayland" or "wayland" in dname:
        if "gnome" in desk:
            return GnomeBackend(app)
        return _edge_or_explain(app)
    if "x11" in dname:
        return X11Backend(app)
    return FallbackBackend(app, "no display found")


# --------------------------------------------------------------------- diagnose --
def _run_text(argv, timeout=6):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (("\n" + r.stderr) if r.stderr.strip() else "")
    except Exception as e:
        return "(%s)" % e


def _tail(path, n):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:]).rstrip() or "(empty)"
    except OSError:
        return "(none)"


def _dbus_name_owned(name):
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                             GLib.Variant("(s)", (name,)), GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None)
        return bool(res.unpack()[0])
    except Exception:
        return None


def _shell_info():
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = conn.call_sync(SHELL_BUS_NAME, SHELL_BUS_PATH, SHELL_BUS_NAME, "Info", None, GLib.VariantType.new("(s)"),
                             Gio.DBusCallFlags.NONE, 2000, None)
        return res.unpack()[0]
    except Exception as e:
        return "(no answer: %s)" % e


def _file_version(path):
    """APP_VERSION of a Rumman program file, or None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def collect_report(app=None):
    lines = []
    add = lines.append
    add("== Rumman %s ==" % APP_VERSION)
    add("python %s | GTK %d.%d.%d" % (sys.version.split()[0], Gtk.get_major_version(), Gtk.get_minor_version(),
                                      Gtk.get_micro_version()))
    env = {k: os.environ.get(k, "") for k in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY", "DISPLAY",
                                              "GDK_BACKEND", "RUMMAN_BACKEND", "LANG")}
    add("session: " + "  ".join("%s=%s" % (k, v or "-") for k, v in env.items()))
    disp = Gdk.Display.get_default()
    if disp is not None:
        try:
            comp = disp.is_composited()
        except Exception:
            comp = "?"
        add("gdk display: %s (composited: %s)" % (type(disp).__name__, comp))
    else:
        add("gdk display: none")
    if app is not None and app.backend is not None:
        add("backend: %s %s" % (app.backend.kind, json.dumps(app.backend.info(), ensure_ascii=False)))
    else:
        be = None
        try:
            be = detect_backend(None)
        except Exception:
            pass
        add("backend (would be): %s" % (be.kind if be else "?"))
    desk = (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").lower()
    if LAYER_LIB is not None:
        try:
            add("layer-shell library: %s (version %s, offered by this compositor: %s)" % (
                LAYER_LIB_NAME, LayerShell(LAYER_LIB).version(), LayerShell(LAYER_LIB).supported()))
        except Exception as e:
            add("layer-shell library: %s (%s)" % (LAYER_LIB_NAME, e))
    elif _wants_layer_shell():
        add("layer-shell library: not found (%s)  install: %s" % (LAYER_LIB_ERROR or "-", layer_shell_install_hint() or "gtk4-layer-shell"))
    if "gnome" in desk:
        add("gnome-shell: " + (_run_text(["gnome-shell", "--version"]).strip() or "?"))
        ext_id = "rumman-helper@rumman.app"
        info = _run_text(["gnome-extensions", "info", ext_id]).strip()
        add("helper extension (%s):\n  %s" % (ext_id, info.replace("\n", "\n  ") if info else "(gnome-extensions not found)"))
        add("helper on the bus: %s" % _dbus_name_owned(SHELL_BUS_NAME))
        if _dbus_name_owned(SHELL_BUS_NAME):
            add("helper says: %s" % _shell_info())
    owned = _dbus_name_owned(APP_ID)
    add("app on the bus: %s" % owned)
    if owned:
        if app is not None:
            add("running program: this one (%s)" % APP_VERSION)
        else:
            rv = running_app_version(4000)
            if rv is None:
                txt = "none"
            elif rv is False:
                txt = "NO ANSWER: the background service is frozen   <-- run: rumman --install (or log out and in)"
            elif rv == "":
                txt = "older than 0.5.0 (it cannot tell)   <-- run: rumman --install"
            elif rv == APP_VERSION:
                txt = rv
            else:
                txt = "%s   <-- not the version of this report (%s): run: rumman --install" % (rv, APP_VERSION)
            add("running program version: " + txt)
    add("-- installation --")
    try:
        ip = install_paths()
        for key, label in (("launcher", "launcher"), ("app", "program"), ("desktop", "menu entry"), ("autostart", "autostart")):
            path = ip[key]
            state = "ok" if os.path.exists(path) else "MISSING"
            if state == "ok" and key == "launcher" and not os.access(path, os.X_OK):
                state = "NOT EXECUTABLE"
            add("%-11s %s  [%s]" % (label + ":", short_home(path), state))
        add("installed version: %s   (this report: %s)" % (_file_version(ip["app"]) or "?", APP_VERSION))
        fields = _entry_fields(ip["desktop"])
        if fields:
            add("menu entry runs: %s" % fields[1])
        for path, why in stale_menu_entries():
            add("OLD ENTRY: %s (%s)   <-- 'rumman --install' removes it" % (short_home(path), why))
    except Exception as e:
        add("installation check failed: %s" % e)
    tools = []
    for t in ("ffmpeg", "ffprobe", "magick", "convert", "7zz", "7z", "pdftoppm", "gs", "qpdf", "soffice", "pandoc"):
        tools.append("%s=%s" % (t, shutil.which(t) or "-"))
    add("tools: " + "  ".join(tools))
    fc = _run_text(["fc-list", ":lang=ar", "family"], 4)
    add("arabic fonts: IBM Plex Sans Arabic=%s  Reem Kufi=%s" % ("IBM Plex Sans Arabic" in fc, "Reem Kufi" in fc))
    cache = os.path.join(GLib.get_user_cache_dir(), "rumman")
    add("-- last errors --\n" + _tail(os.path.join(cache, "log.txt"), 12))
    add("-- last events --\n" + _tail(os.path.join(cache, "events.log"), 30))
    add("-- last launches from the menu / by the helper --\n" + _tail(os.path.join(cache, "launch.log"), 8))
    add("-- what the program printed when started from the menu --\n" + _tail(os.path.join(cache, "stderr.log"), 20))
    if "gnome" in desk:
        j = _run_text(["journalctl", "--user", "-b", "--no-pager", "-g", "rumman", "-n", "25", "-o", "cat"], 8).strip()
        add("-- gnome-shell journal (rumman) --\n" + (j or "(none)"))
    return "\n".join(lines)


# ============================================================================
#  [11] Application, installer, command line
# ============================================================================
EXT_UUID = "rumman-helper@rumman.app"

HELP_TEXT = """Rumman %s  -  drag. drop. done.

usage:  rumman                  open the status window (the background service starts with it)
        rumman --service        background service only (the GNOME helper extension / the autostart entry start this)
        rumman FILE...          show the wheel for these files (also what "Open with" does)
        rumman --diagnose       print a report to paste when something does not work
        rumman --install        add Rumman to the applications menu (+ the GNOME helper extension when present)
        rumman --uninstall      remove all of that again
        rumman --version

environment:  RUMMAN_DEBUG=1  prints what the drag-and-drop code receives (for bug reports)
""" % APP_VERSION


# ------------------------------------------------------------------------ helpers --
def apply_language(code):
    """Use this language from now on (text and left-to-right / right-to-left layout)."""
    set_language(code)
    Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL if is_rtl() else Gtk.TextDirection.LTR)


def open_path(path):
    try:
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(os.path.abspath(path), None), None)
    except Exception as e:
        log_error("open_path: %s" % e)


def reveal_path(path, parent=None):
    """Show a file inside the file manager (falls back to opening its folder)."""
    try:
        if hasattr(Gtk, "FileLauncher") and os.path.lexists(path):
            Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(parent, None, None)
            return
    except Exception as e:
        dbg("open_containing_folder failed", e)
    d = path if os.path.isdir(path) else os.path.dirname(path)
    open_path(d or "/")


class EventLog:
    """~/.cache/rumman/events.log: what the helper told the app and when (it is what the diagnose report shows)."""

    def __init__(self):
        self.path = os.path.join(GLib.get_user_cache_dir(), "rumman", "events.log")

    def add(self, text):
        dbg("event", text)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path) > 64 * 1024:
                os.replace(self.path, self.path + ".old")
            ms = int(time.time() * 1000) % 1000
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("%s.%03d  %s\n" % (time.strftime("%H:%M:%S"), ms, text))
        except OSError:
            pass


def demo_dir():
    base = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~")
    return os.path.join(base, "Rumman")


def make_demo_image():
    """A small picture with the Rumman mark, so the wheel can be tried without any file at hand."""
    d = demo_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "rumman-demo.png")
    if os.path.exists(path):
        return path
    W, H = 720, 440
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    g = cairo.LinearGradient(0, 0, W, H)
    g.add_color_stop_rgb(0, *WINE)
    g.add_color_stop_rgb(1, *WINE_DEEP)
    cr.set_source(g)
    cr.paint()
    cr.save()
    cr.translate(W / 2.0, H * 0.40)
    cr.scale(2.0, 2.0)
    cr.translate(-64, -64)
    paint_logo(cr, tile=False)
    cr.restore()
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription()
    fd.set_family("Reem Kufi, IBM Plex Sans Arabic, Noto Sans Arabic, sans-serif")
    fd.set_absolute_size(54 * Pango.SCALE)
    fd.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(fd)
    layout.set_text(APP_NAME_AR, -1)
    tw, th = layout.get_pixel_size()
    cr.set_source_rgb(*CREAM)
    cr.move_to((W - tw) / 2.0, H * 0.74)
    PangoCairo.show_layout(cr, layout)
    surf.write_to_png(path)
    return path


# ------------------------------------------------------------------------- the app --
class RummanApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.add_main_option("service", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "background service only", None)
        self.add_main_option("autostart", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "started at login: quit again when this desktop gives nothing to watch", None)
        self.service_only = False
        self.autostarted = False
        self._starting = False                 # a --service process, from its start until its main loop runs
        self.queue = JobQueue()
        self.jobs = []
        self.last_outputs = []
        self.settings_win = None
        self.backend = None
        self.wheel = None
        self.options = None
        self.status = None
        self.problem = None
        self.events = EventLog()

    # ------------------------------------------------------------ lifecycle --
    def do_handle_local_options(self, options):
        if options.contains("autostart"):
            self.autostarted = True
        if options.contains("service"):
            self.service_only = True
            try:
                self.register(None)
            except GLib.Error:
                return -1
            if self.get_is_remote():
                return 0                       # the service is already running: nothing to do
        return -1

    def do_startup(self):
        Gtk.Application.do_startup(self)
        apply_language(SETTINGS.get("language"))
        install_css()
        self._add_action("show", lambda *a: self.show_status())
        self._add_action("demo", lambda *a: self.show_demo())
        self._add_action("quit", lambda *a: self.quit_app())
        self._add_action("undo", lambda *a: self.notify(tr("o_undone")) if self.undo_last() else None)
        self._add_action("settings", lambda *a: self.show_settings())
        self._add_action("reveal", lambda a, v: reveal_path(v.get_string()), "s")
        # "which version is running in the background?" - the installer and --diagnose ask it over D-Bus
        self.add_action(Gio.SimpleAction.new_stateful("version", None, GLib.Variant.new_string(APP_VERSION)))
        # the four calls every backend makes (the GNOME helper makes them over D-Bus: org.gtk.Actions.Activate)
        self._add_action("drag-begin", lambda a, v: self.guarded("wheel", self.backend_drag_begin, v.get_uint32() if v else 0), "u")
        self._add_action("wheel-open", lambda a, v: self.guarded("wheel", self.backend_wheel_open, v.get_uint32() if v else 0), "u")
        self._add_action("wheel-cycle", lambda a, v: self.backend_wheel_cycle(v.get_uint32() if v else 0), "u")
        self._add_action("drag-end", lambda a, v: self.backend_drag_end(v.get_uint32() if v else 0), "u")
        self.set_accels_for_action("app.quit", ["<Primary>q"])
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
            except Exception:
                pass
        self.hold()                            # a service: it stays until "Stop the service" / logout
        if self.service_only:
            # GLib sends a fresh process an "activate" of its own before its main loop runs; that is not a request to open a
            # window.  Everything that arrives once the loop runs (a click on the menu entry, from another process) is.
            self._starting = True
            GLib.idle_add(self._start_done)
        self._start_watchdog()
        threading.Thread(target=Tools.warm_up, name="rumman-probe", daemon=True).start()
        threading.Thread(target=sweep_stale_temps, name="rumman-sweep", daemon=True).start()
        try:
            self.backend = detect_backend(self)
            self.backend.on_change = self._backend_changed
            self.backend.start()
        except Exception:
            # whatever went wrong while looking at this desktop must not stop the windows from opening
            tb = traceback.format_exc()
            log_error("backend start-up failed:\n%s" % tb)
            self.backend = FallbackBackend(self, "start-up failed: %s" % tb.strip().splitlines()[-1])
            self.backend.on_change = self._backend_changed
        self.event("started: backend=%s %s" % (self.backend.kind, json.dumps(self.backend.info(), ensure_ascii=False)))
        if self.autostarted and isinstance(self.backend, FallbackBackend):
            # nothing can be watched on this desktop yet: do not sit in the background for nothing
            self.event("autostart: nothing to watch here, leaving")
            GLib.idle_add(lambda: self.quit_app() or False)

    def _start_watchdog(self):
        """If the main loop stops answering for a while, write where it is stuck into the log: a frozen background
        service makes every click on the menu entry do nothing, and this is what tells why."""
        self._tick = time.monotonic()
        main_id = threading.get_ident()

        def tick():
            self._tick = time.monotonic()
            return True
        GLib.timeout_add_seconds(1, tick)

        def watch():
            reported = False
            while True:
                time.sleep(2.0)
                late = time.monotonic() - self._tick
                if late > 6.0 and not reported:
                    reported = True
                    frame = sys._current_frames().get(main_id)
                    log_error("the main loop did not answer for %.0f seconds; it is busy here:\n%s" % (
                        late, "".join(traceback.format_stack(frame)) if frame is not None else "?"))
                elif late < 3.0:
                    reported = False
        threading.Thread(target=watch, name="rumman-watchdog", daemon=True).start()

    def _add_action(self, name, cb, ptype=None):
        a = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
        a.connect("activate", cb)
        self.add_action(a)

    def _on_signal(self):
        self.quit_app()
        return False

    def _start_done(self):
        self._starting = False
        return False

    def do_activate(self):
        if self._starting:
            self.event("activate: the service's own start, nothing to show")
            return
        self.event("activate: opening the status window")
        self.guarded("status window", self.show_status)

    def do_open(self, files, n_files, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        self.event("open: %d file(s)" % len(paths))
        self.guarded("wheel", self.show_wheel_for, paths)

    def guarded(self, what, fn, *args):
        """Run something that opens a window.  When it fails, the reason is written to the log and a small window says
        so (with a button that copies the report), instead of a click that silently does nothing."""
        try:
            fn(*args)
            return True
        except Exception:
            tb = traceback.format_exc()
            log_error("%s failed:\n%s" % (what, tb))
            self.event("FAILED %s: %s" % (what, tb.strip().splitlines()[-1]))
            self.show_problem(what, tb)
            return False

    def show_problem(self, what, detail):
        try:
            if self.problem is not None:
                self.problem.add_detail(what, detail)
            else:
                win = ProblemWindow(self, what, detail)
                self._forget_on_close(win, "problem")
                self.problem = win
            self.problem.present()
        except Exception:
            log_error("the problem window failed as well:\n%s" % traceback.format_exc())
            self.problem = None
            self.notify(tr("pw_title"))

    def do_shutdown(self):
        for j in self.jobs:
            if j.state in ("queued", "running"):
                j.cancel()
        end = time.monotonic() + 5.0
        while time.monotonic() < end and any(j.state == "running" for j in self.jobs):
            time.sleep(0.05)                   # let the worker remove its temporary files
        try:
            SETTINGS.save()
        except Exception:
            pass
        if self.backend is not None:
            self.backend.stop()
        Gtk.Application.do_shutdown(self)

    def quit_app(self):
        self.quit()

    # -------------------------------------------------------------- windows --
    def ensure_wheel(self):
        be = self.backend
        owned = be.wheel_window() if be is not None else None      # the edge strip owns its wheel windows
        if owned is not None:
            self.wheel = owned
            return owned
        if self.wheel is None:
            self.wheel = WheelWindow(self)
        return self.wheel

    def ensure_options(self):
        if self.options is None:
            self.options = OptionsWindow(self)
        return self.options

    def show_options(self, act, files):
        """The options page for what was dropped on a slice (the wheel window hides itself right after)."""
        self.ensure_options().open_for(act, files)

    def dismiss_options(self):
        """Called when a new wheel is about to open."""
        if self.options is not None and self.options.is_open():
            self.options.dismiss()

    def _forget_on_close(self, win, attr):
        """A GTK4 window that the person closed is gone for good: it cannot be shown again, and the "destroy" signal does not
        come while this program still holds a reference to it.  So the reference is dropped the moment the close button is
        pressed, and the next request builds a new window.  (Keeping the closed one made "click the menu entry again" do
        nothing after the window had been closed once.)"""
        def closing(w):
            if getattr(self, attr, None) is w:
                setattr(self, attr, None)
            return False                                     # go on: the default handler closes the window
        win.connect("close-request", closing)

    def show_status(self):
        if self.status is None:
            win = StatusWindow(self)
            self._forget_on_close(win, "status")
            self.status = win
        self.status.refresh()
        self.status.present()

    def show_settings(self):
        if self.settings_win is None:
            win = SettingsWindow(self)
            self._forget_on_close(win, "settings_win")
            self.settings_win = win
        self.settings_win.present()

    def rebuild_status(self):
        """The language changed: build the status window again, so that every text and the layout follow it."""
        old = self.status
        self.status = None
        if old is not None:
            old.destroy()
        if self.settings_win is not None:                # the settings page follows the language too
            sw, self.settings_win = self.settings_win, None
            sw.destroy()
            self.show_settings()
        self.show_status()
        return False

    def _backend_changed(self):
        if self.status is not None:
            self.status.refresh()

    def show_wheel_for(self, paths):
        paths = [p for p in paths if p and os.path.lexists(p)]
        if not paths:
            self.notify(tr("file_missing"))
            return
        self.dismiss_options()
        self.ensure_wheel().open_for_files(paths)

    def show_demo(self):
        try:
            path = make_demo_image()
        except Exception as e:
            log_error("demo image: %s" % e)
            return
        self.show_wheel_for([path])
        if self.status is not None:
            self.status.show_toast(demo_dir())

    def copy_report(self, win):
        text = collect_report(self)
        try:
            win.get_clipboard().set(text)
            win.show_toast(tr("st_copied"))
        except Exception as e:
            log_error("copy report: %s" % e)

    # ----------------------------------------------------- calls from backends --
    def event(self, text):
        self.events.add(text)

    def backend_drag_begin(self, seq):
        self.event("drag-begin %s" % seq)
        self.ensure_wheel()                    # build the window now, so the Shift press only has to show it

    def backend_wheel_open(self, seq):
        self.event("wheel-open %s" % seq)
        self.dismiss_options()
        self.ensure_wheel().open_for_drag()

    def backend_wheel_cycle(self, seq):
        self.event("wheel-cycle %s (ignored: there are no modes any more)" % seq)

    def backend_drag_end(self, seq):
        self.event("drag-end %s" % seq)
        if self.wheel is not None:
            self.wheel.drag_ended()

    # ----------------------------------------------------------------- jobs --
    def run_action(self, act, files, listener=None, overrides=None):
        """Start the jobs for one drop. `overrides` are the choices of the options page (compression level, size,
        rotation, tools); without them the neutral defaults apply."""
        opts = SETTINGS.snapshot()
        opts.update(overrides or {})
        if "level" not in opts:                       # no options page: the level chosen in the settings (if any)
            dl = default_level(act.op, "", act.op != "pdf-same")
            if dl:
                opts["level"] = dl
        groups = [list(files)] if act.batch else [[f] for f in files]
        jobs = []
        for g in groups:
            job = Job(g, act, opts)
            job.listener = listener
            job.on_update = self._job_cb
            jobs.append(job)
            self.jobs.append(job)
        for job in jobs:
            self.queue.submit(job)
        return jobs

    def _job_cb(self, job):
        lst = getattr(job, "listener", None)
        if lst is not None:
            lst.job_updated(job)
        return False

    def jobs_finished(self, jobs, wheel_visible):
        """All jobs of one drop are over: notification (when the wheel is not there to show it) and "open after"."""
        sm = run_summary(jobs)
        done, errors, outs = sm["done"], sm["errors"], sm["outs"]
        self.last_outputs = list(outs) if sm["can_undo"] else []
        if errors:
            first = errors[0].error or tr("wheel_failed")
            self.notify(tr("toast_failed_n", first) if not done else tr("o_mixed", len(done), len(errors)) + " — " + first)
        elif done and SETTINGS.get("notify") and (not wheel_visible or sm["long"] or len(jobs) >= 3):
            msg = tr("toast_done_one", os.path.basename(outs[0])) if len(outs) == 1 else tr("toast_done_n", len(outs))
            if sm["saved_txt"]:
                msg += " · " + sm["saved_txt"]
            self.notify(msg, outs[0] if outs else None, undo=bool(self.last_outputs))
        if done and outs and any(j.opts.get("open_after") for j in done):
            open_path(outs[0])
        for j in jobs:                          # forget finished jobs (keeps the list short in a long-running service)
            if j in self.jobs and j.state in TERMINAL:
                self.jobs.remove(j)

    def undo_last(self):
        """Move the results of the last run to the trash. Returns how many went."""
        outs = [o for o in self.last_outputs if os.path.lexists(o)]
        self.last_outputs = []
        n = sum(1 for o in outs if trash_path(o))
        self.event("undo %d" % n)
        return n

    def notify(self, msg, path=None, undo=False):
        try:
            n = Gio.Notification.new(tr("app_name"))
            n.set_body(msg)
            n.set_default_action("app.show")
            if path:
                n.add_button(tr("show_in_folder"), Gio.Action.print_detailed_name("app.reveal", GLib.Variant.new_string(path)))
            if undo:
                n.add_button(tr("btn_undo"), "app.undo")
            self.send_notification("rumman-done", n)
        except Exception as e:
            dbg("notify failed", e)


# ---------------------------------------------------------------------- installer --
def install_paths():
    data = GLib.get_user_data_dir()
    return {
        "app": os.path.join(data, "rumman", "rumman.py"),
        "launcher": os.path.join(os.path.expanduser("~"), ".local", "bin", "rumman"),
        "desktop": os.path.join(data, "applications", APP_ID + ".desktop"),
        "icon": os.path.join(data, "icons", "hicolor", "scalable", "apps", APP_ID + ".svg"),
        "autostart": os.path.join(GLib.get_user_config_dir(), "autostart", APP_ID + ".service.desktop"),
        "metainfo": os.path.join(data, "metainfo", APP_ID + ".metainfo.xml"),
        "ext": os.path.join(data, "gnome-shell", "extensions", EXT_UUID),
    }


def desktop_entry_text(exec_path):
    """The launcher entry: the name, the kind of program and the tagline, each in the five languages."""
    esc = lambda x: '"%s"' % x.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    out = ["[Desktop Entry]", "Type=Application", "Name=Rumman"]
    for code in META_ORDER[1:]:
        out.append("Name[%s]=%s" % (code, META[code]["name"]))
    out.append("GenericName=%s" % META["en"]["generic"])
    for code in META_ORDER[1:]:
        out.append("GenericName[%s]=%s" % (code, META[code]["generic"]))
    out.append("Comment=%s" % STRINGS["en"]["tagline"])
    for code in META_ORDER[1:]:
        out.append("Comment[%s]=%s" % (code, STRINGS[code]["tagline"]))
    out += ["Exec=%s %%F" % esc(exec_path), "Icon=%s" % APP_ID, "Terminal=false", "StartupNotify=true",
            "StartupWMClass=%s" % APP_ID, "Categories=Utility;AudioVideo;Graphics;",
            "Keywords=convert;compress;zip;image;video;audio;pdf;"]
    for code in META_ORDER[1:]:
        out.append("Keywords[%s]=%s;" % (code, ";".join(META[code]["keywords"])))
    return "\n".join(out) + "\n"


def metainfo_xml():
    """The AppStream description (software centres read it): name, summary and text in five languages."""
    from xml.sax.saxutils import escape as x
    def per_lang(tag, key, value=None):
        rows = []
        for code in META_ORDER:
            v = META[code][key] if value is None else value(code)
            rows.append('  <%s%s>%s</%s>' % (tag, "" if code == "en" else ' xml:lang="%s"' % code, x(v), tag))
        return "\n".join(rows)
    desc = []
    for code in META_ORDER:
        m = META[code]
        attr = "" if code == "en" else ' xml:lang="%s"' % code
        desc.append("    <p%s>%s</p>\n    <p%s>%s</p>" % (attr, x(m["intro"]), attr, x(m["intro2"])))
        desc.append("    <ul%s>\n%s\n    </ul>" % (attr, "\n".join("      <li%s>%s</li>" % (attr, x(t)) for t in m["points"])))
    kw = "\n".join('    <keyword%s>%s</keyword>' % ("" if c == "en" else ' xml:lang="%s"' % c, x(k))
                    for c in META_ORDER for k in META[c]["keywords"])
    return """<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>%(id)s</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>LicenseRef-proprietary</project_license>
%(names)s
%(summ)s
  <description>
%(desc)s
  </description>
  <launchable type="desktop-id">%(id)s.desktop</launchable>
  <icon type="stock">%(id)s</icon>
  <categories>
    <category>Utility</category>
    <category>AudioVideo</category>
    <category>Graphics</category>
  </categories>
  <keywords>
%(kw)s
  </keywords>
  <branding>
    <color type="primary" scheme_preference="light">#E3C6CF</color>
    <color type="primary" scheme_preference="dark">#7A1F3F</color>
  </branding>
  <provides>
    <binary>rumman</binary>
  </provides>
  <supports>
    <control>pointing</control>
  </supports>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="%(ver)s" date="2026-11-08"/>
  </releases>
</component>
""" % {"id": APP_ID, "ver": APP_VERSION, "names": per_lang("name", "name"), "summ": per_lang("summary", "summary"),
       "desc": "\n".join(desc), "kw": kw}


def running_gnome():
    return "gnome" in (os.environ.get("XDG_CURRENT_DESKTOP", "") or "").lower() or bool(shutil.which("gnome-shell"))


def _enable_extension_for_next_login():
    """Put the extension on GNOME's enabled list; the Shell loads it at the next login."""
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup("org.gnome.shell", True) is None:
            return None
        st = Gio.Settings.new("org.gnome.shell")
        cur = list(st.get_strv("enabled-extensions"))
        if EXT_UUID not in cur:
            cur.append(EXT_UUID)
            st.set_strv("enabled-extensions", cur)
            Gio.Settings.sync()
        return not st.get_boolean("disable-user-extensions")
    except Exception as e:
        log_error("enable extension: %s" % e)
        return None


def _name_owner_pid(conn):
    """The process id behind Rumman's D-Bus name (0 when unknown)."""
    try:
        res = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID",
                             GLib.Variant("(s)", (APP_ID,)), GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 1500, None)
        return int(res.unpack()[0])
    except Exception:
        return 0


def running_app_version(timeout_ms=1500):
    """The version of the Rumman that runs in the background right now.  Its version text; "" when one answers but is too old
    to say (older than 0.5.0); None when nothing runs; False when something owns Rumman's name but does not answer within
    the time (a frozen service)."""
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if not _dbus_name_owned(APP_ID):
            return None
        res = conn.call_sync(APP_ID, APP_PATH, "org.gtk.Actions", "Describe", GLib.Variant("(s)", ("version",)),
                             GLib.VariantType.new("((bgav))"), Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)
        state = res.unpack()[0][2]
        return str(state[0]) if state else ""
    except GLib.Error as e:
        silent = e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT) or "imeout" in str(e) or "imed out" in str(e)
        if silent:
            return False if _dbus_name_owned(APP_ID) else None
        return "" if _dbus_name_owned(APP_ID) else None
    except Exception:
        return None


def end_process(pid):
    """Ask a process to end, then make it (a stopped or frozen one ignores the polite way).  True when Rumman's bus name is free."""
    for sig, tries in ((signal.SIGTERM, 30), (signal.SIGKILL, 30)):
        try:
            os.kill(pid, sig)
        except OSError:
            break
        for _ in range(tries):
            time.sleep(0.1)
            if _dbus_name_owned(APP_ID) is False:
                return True
    return _dbus_name_owned(APP_ID) is False


def replace_stuck_service(timeout_ms=8000):
    """A background Rumman whose main loop is frozen answers nobody: every click on the menu entry would do nothing until
    the next login.  When the one that owns Rumman's name does not answer a simple question within the time, it is ended,
    so that this start becomes the new one.  (One that answers - even "I do not know that question" - is left alone.)"""
    try:
        if running_app_version(timeout_ms) is not False:
            return False
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        pid = _name_owner_pid(conn)
        if pid <= 1 or pid == os.getpid():
            return False
        log_error("the Rumman running in the background (process %d) did not answer for %d seconds; ending it so that "
                  "this one can take over" % (pid, timeout_ms // 1000))
        EventLog().add("the background service (process %d) was frozen: replaced" % pid)
        return end_process(pid)
    except Exception as e:
        log_error("replace_stuck_service: %s" % e)
        return False


def stop_running_service():
    """An older Rumman may still be running in the background; it would keep answering instead of the new version.
    Ask it to quit; one that does not leave in a few seconds is ended by its process id (it owns Rumman's own bus name,
    so it is a Rumman).  True when there was one."""
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if not _dbus_name_owned(APP_ID):
            return False
        pid = _name_owner_pid(conn)
        try:
            conn.call_sync(APP_ID, APP_PATH, "org.gtk.Actions", "Activate",
                           GLib.Variant("(sava{sv})", ("quit", [], {})), None, Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        except Exception as e:
            dbg("stop_running_service: quit not answered", e)
        for _ in range(40):                                     # up to 4 seconds for a polite quit
            time.sleep(0.1)
            if _dbus_name_owned(APP_ID) is False:
                return True
        if pid > 1 and pid != os.getpid():
            end_process(pid)
        return True
    except Exception as e:
        dbg("stop_running_service", e)
        return False


def launcher_text(py, app):
    """~/.local/bin/rumman.  Besides starting the program it keeps one short line per launch and - when it is started from a
    menu, not from a terminal - what the program prints on its error channel; so a click that did nothing can be traced
    (`rumman --diagnose` shows both files)."""
    esc = lambda x: '"%s"' % x.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return (
        "#!/bin/sh\n"
        "# written by 'rumman --install'.  launch.log / stderr.log (in ~/.cache/rumman) are what 'rumman --diagnose' shows.\n"
        'LOGDIR="${XDG_CACHE_HOME:-$HOME/.cache}/rumman"\n'
        'mkdir -p "$LOGDIR" 2>/dev/null\n'
        'for f in launch.log stderr.log; do\n'
        '  if [ -f "$LOGDIR/$f" ] && [ "$(wc -c < "$LOGDIR/$f")" -gt 65536 ]; then mv -f "$LOGDIR/$f" "$LOGDIR/$f.old"; fi\n'
        'done 2>/dev/null\n'
        'echo "$(date \'+%F %T\')  launcher: nargs=$# token=${XDG_ACTIVATION_TOKEN:+yes} session=$XDG_SESSION_TYPE '
        'desktop=$XDG_CURRENT_DESKTOP" >> "$LOGDIR/launch.log" 2>/dev/null\n'
        'if [ -t 2 ]; then exec @PY@ @APP@ "$@"; fi\n'
        'exec @PY@ @APP@ "$@" 2>>"$LOGDIR/stderr.log"\n'
    ).replace("@PY@", esc(py)).replace("@APP@", esc(app))


def _entry_fields(path):
    """(Name, Exec) of a .desktop file, or None when it cannot be read."""
    try:
        kf = GLib.KeyFile()
        kf.load_from_file(path, GLib.KeyFileFlags.NONE)
        return kf.get_string("Desktop Entry", "Name"), kf.get_string("Desktop Entry", "Exec")
    except Exception:
        return None


def stale_menu_entries():
    """Menu / autostart entries of an older Rumman that are still lying around: another file name than today's, but they
    start a program called rumman (or rumman.py).  Returns [(path, what is wrong with it)]."""
    keep = {APP_ID + ".desktop", APP_ID + ".service.desktop"}
    out = []
    for folder in (os.path.join(GLib.get_user_data_dir(), "applications"), os.path.join(GLib.get_user_config_dir(), "autostart")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".desktop") or fn in keep:
                continue
            path = os.path.join(folder, fn)
            fields = _entry_fields(path)
            if not fields or not fields[1]:
                continue
            try:
                words = GLib.shell_parse_argv(fields[1].replace("%F", "").replace("%f", "").replace("%U", "").replace("%u", ""))[1]
            except Exception:
                continue
            prog = [w for w in words if os.path.basename(w) in ("rumman", "rumman.py")]
            if not prog:
                continue
            why = "another file name"
            if not os.path.exists(prog[0]):
                why += ", and it starts %s which does not exist" % prog[0]
            out.append((path, why))
    return out


def install_all():
    p = install_paths()
    if stop_running_service():
        print("stopped the Rumman that was running in the background (the new one starts when you open it)")
    here = os.path.abspath(__file__)
    esc = lambda s: '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    try:
        for d in (os.path.dirname(p["app"]), os.path.dirname(p["launcher"]), os.path.dirname(p["desktop"]),
                  os.path.dirname(p["icon"]), os.path.dirname(p["autostart"]), os.path.dirname(p["metainfo"])):
            os.makedirs(d, exist_ok=True)
        if os.path.abspath(p["app"]) != here:
            shutil.copy2(here, p["app"])
        with open(p["launcher"], "w", encoding="utf-8") as f:
            f.write(launcher_text(sys.executable or "python3", p["app"]))
        os.chmod(p["launcher"], 0o755)
        with open(p["icon"], "w", encoding="utf-8") as f:
            f.write(ICON_SVG)
        with open(p["desktop"], "w", encoding="utf-8") as f:
            f.write(desktop_entry_text(p["launcher"]))
        with open(p["metainfo"], "w", encoding="utf-8") as f:
            f.write(metainfo_xml())
        os.chmod(p["desktop"], 0o755)
        with open(p["autostart"], "w", encoding="utf-8") as f:        # the background service starts with the session
            f.write("[Desktop Entry]\nType=Application\nName=Rumman (service)\nName[ar]=رُمّان (الخدمة)\n"
                    "Comment=Watches drags so the wheel can appear\nExec=%s --service --autostart\nIcon=%s\nTerminal=false\n"
                    "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n" % (esc(p["launcher"]), APP_ID))
    except OSError as e:
        print("could not install: %s" % e, file=sys.stderr)
        return 1
    print("installed:\n  %s\n  %s\n  %s\n  %s\n  %s\n  %s" % (p["app"], p["launcher"], p["desktop"], p["icon"], p["autostart"], p["metainfo"]))
    for path, why in stale_menu_entries():                       # two icons for one program, one of them dead, is confusing
        try:
            os.remove(path)
            print("removed an old Rumman entry left by an earlier version: %s (%s)" % (path, why))
        except OSError as e:
            print("could not remove the old entry %s: %s" % (path, e), file=sys.stderr)
    ext_src = os.path.join(os.path.dirname(here), "gnome-extension", EXT_UUID)
    if os.path.isdir(ext_src) and running_gnome():
        try:
            shutil.rmtree(p["ext"], ignore_errors=True)               # no stale files from an older version
            shutil.copytree(ext_src, p["ext"])
            print("  %s" % p["ext"])
            on = _enable_extension_for_next_login()
            if on is False:
                print("  note: GNOME has 'disable-user-extensions' switched on; turn user extensions on in Extensions.")
            print("\nGNOME: log out and log in once; the helper extension starts with the next session.")
        except OSError as e:
            print("could not copy the GNOME extension: %s" % e, file=sys.stderr)
    if _wants_layer_shell():
        if LAYER_LIB is None:
            hint = layer_shell_install_hint()
            print("\nThis Wayland desktop shows the wheel through a thin strip on the screen edge. For that it needs the small library "
                  "gtk4-layer-shell, which is not installed yet.\n  install it with:  %s\nthen open Rumman again." %
                  (hint or "(your distribution's package named gtk4-layer-shell)"))
        else:
            print("\nThis Wayland desktop: Rumman shows a thin strip on the screen edge. Drag a file there and hold it for a moment.")
    for cmd in (["update-desktop-database", os.path.dirname(p["desktop"])],
                ["gtk-update-icon-cache", "-f", "-t", os.path.join(GLib.get_user_data_dir(), "icons", "hicolor")]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=20)
        except Exception:
            pass
    return 0


def uninstall_all():
    p = install_paths()
    for key in ("launcher", "desktop", "icon", "autostart", "metainfo"):
        try:
            os.remove(p[key])
            print("removed", p[key])
        except OSError:
            pass
    for d in (os.path.dirname(p["app"]), p["ext"]):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            print("removed", d)
    try:
        src = Gio.SettingsSchemaSource.get_default()
        if src is not None and src.lookup("org.gnome.shell", True) is not None:
            st = Gio.Settings.new("org.gnome.shell")
            cur = [x for x in st.get_strv("enabled-extensions") if x != EXT_UUID]
            st.set_strv("enabled-extensions", cur)
            Gio.Settings.sync()
    except Exception:
        pass
    return 0


def install_error_logging():
    """An error inside a signal handler or a thread is only printed to the error channel, which nobody sees when the
    program was started from a menu.  Write them to ~/.cache/rumman/log.txt as well (--diagnose shows them)."""
    def hook(kind, value, tb):
        log_error("uncaught error:\n" + "".join(traceback.format_exception(kind, value, tb)))
        sys.__excepthook__(kind, value, tb)

    def thread_hook(args):
        log_error("uncaught error in a thread (%s):\n%s" % (getattr(args.thread, "name", "?"), "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))
    sys.excepthook = hook
    threading.excepthook = thread_hook


def main(argv):
    global SETTINGS
    args = argv[1:]
    if "--help" in args or "-h" in args:
        print(HELP_TEXT)
        return 0
    if "--version" in args:
        print("Rumman", APP_VERSION)
        return 0
    if "--install" in args:
        return install_all()
    if "--uninstall" in args:
        return uninstall_all()
    if "--diagnose" in args:
        Gtk.init_check()
        print(collect_report(None))
        return 0
    SETTINGS = Settings()
    install_error_logging()
    if "--service" not in args:
        replace_stuck_service()
    EventLog().add("process %d starts: %d argument(s), python %s, GTK %d.%d.%d" % (
        os.getpid(), len(args), sys.version.split()[0], Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version()))
    app = RummanApp()
    try:
        code = app.run([argv[0]] + args)
    except Exception:
        log_error("the program stopped with an error:\n%s" % traceback.format_exc())
        code = 1
    EventLog().add("process %d ends (exit %s)" % (os.getpid(), code))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
