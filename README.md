# Saifdora Silverblue 45 🚀

توزيعة مخصصة مبنية على **Fedora Atomic (Silverblue 45)** باستخدام إطار عمل **[BlueBuild](https://blue-build.org/)**.

تتضمن هذه النسخة حزم وبرامج إضافية مدمجة مسبقاً لتوفير تجربة متكاملة وجاهزة للاستخدام اليومي والإنتاجية، مع دعم ممتاز للغة العربية وتسريع الوسائط المتعددة والعتاد.

---

## ✨ المميزات الرئيسية

- **الترميز وتسريع العتاد:**
  - مستودعات **RPM Fusion** (الحرة وغير الحرة) مفعلة مسبقاً.
  - تعريفات تسريع العتاد للبطاقات الرسومية: `mesa-va-drivers-freeworld`، `mesa-vdpau-drivers-freeworld`، `intel-media-driver`.
  - مشغلات وتراميز الوسائط: `libavcodec-freeworld`، `gstreamer1-plugins-*`، `gstreamer1-plugin-libav`.
- **دعم صيغ الصور والمصغرات:**
  - `libheif-freeworld`، `heif-pixbuf-loader`، `webp-pixbuf-loader`، `ffmpegthumbnailer`.
- **الضغط والأرشفة:**
  - `unrar`، `p7zip`، `p7zip-plugins`.
- **الخطوط والتعريب:**
  - حزم اللغات: `langpacks-ar`، `glibc-langpack-ar`.
  - خطوط عربية ومكتبية مدمجة: `google-noto-sans-arabic-fonts`، `vazirmatn-fonts`، `amiri-fonts`، `google-carlito-fonts`، `google-caladea-fonts`، `liberation-fonts`.
  - إعدادات `dconf` افتراضية: خط الواجهة `Vazirmatn 11`، وخط المستندات `Noto Sans Arabic 11`.
- **إدارة الحزم وتطبيقات Flatpak:**
  - مستودع **Flathub** الكامل مفعل افتراضياً للنظام.
  - تعطيل مستودع Fedora Flatpak المحدود تلقائياً.
  - ضبط لغات Flatpak على `"ar;en"` عند الإقلاع عبر خدمة systemd مخصصة.
  - تطبيقات مثبتة مسبقاً: **Extension Manager**, **Flatseal**, **VLC**.
- **أدوات جنوم والمظهر:**
  - `gnome-tweaks`، وثيم `adw-gtk3-theme`.

---

## 🛠️ التثبيت والتبديل (Rebase)

### التبديل إلى الصورة عبر rpm-ostree:

للانتقال من أي نظام Fedora Silverblue حالي إلى صورة Saifdora:

```bash
# استيراد مفتاح التحقق من التوقيع (Cosign)
sudo rpm-ostree rebase ostree-unverified-registry:ghcr.io/saifeldinosama9677-ctrl/saifdora-silverblue:45
```

أو بعد تفعيل التوقيع الرقمي:

```bash
sudo rpm-ostree rebase ostree-image-signed:docker://ghcr.io/saifeldinosama9677-ctrl/saifdora-silverblue:45
```

### تثبيت النظام عبر ملف ISO:

يمكن تنزيل ملف الـ ISO المتولد تلقائياً من تبويب **Actions** في مستودع GitHub لكل عملية بناء مكتملة (Artifacts)، وحرقه على ذاكرة فلاشية باستخدام **Fedora Media Writer** أو **Etcher**.

---

## 🔐 التوقيع الرقمي (Image Signing)

يتم توقيع الصورة الناتجة تلقائياً بواسطة أداة **Sigstore/Cosign**. يمكن التحقق من سلامة الصورة باستخدام المفتاح العام المتوفر في جذر المستودع `cosign.pub`:

```bash
cosign verify --key cosign.pub ghcr.io/saifeldinosama9677-ctrl/saifdora-silverblue:45
```
