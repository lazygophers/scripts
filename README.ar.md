# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

مجموعة من أدوات كفاءة التطوير — اختصارات نصية برمجية متنوعة. لا يحوي `bin/` سوى أغلفة رقيقة، والتنفيذ في `lib/cli/`، والقدرات المشتركة في `lib/`.

---

## بلا تثبيت: التشغيل مباشرة من GitHub

```bash
uvx git+https://github.com/lazygophers/scripts                     # سرد كل الأدوات (مثل lazyhelp)
uvx --from git+https://github.com/lazygophers/scripts checkwork    # تشغيل أداة واحدة
```

‏`uvx` يأتي مع [uv](https://docs.astral.sh/uv/): ينزّل الأداة، يشغّلها مرة واحدة ولا يترك أثرًا — بلا استنساخ المستودع. بدون اسم أمر يشغّل مدخل `scripts`، وهو `lazyhelp`. مع `--from` يطلب uv اسم أمر صريحًا.

للإبقاء عليها ثبّتها مرة واحدة (`uv tool install` تثبيت دائم، وتبقى الأوامر في PATH):

```bash
uv tool install git+https://github.com/lazygophers/scripts
```

---

## التثبيت : حقن bin/ في PATH

```bash
./bin/inject            # إنشاء ~/.scripts.sh و source إلى جميع rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject show     # معاينة المحتوى المكتوب
./bin/inject uninstall  # إلغاء التثبيت
```

inject ذو طبيعة متطابقة : إعادة التشغيل لن تضيف تكرارات. بعد الإكمال، أعد تشغيل shell أو `source ~/.zshrc`، ثم اتصل بـ `checkwork` / `merge_canary` / ... من أي دليل.

---

## وظائف النصوص البرمجية

سبع فئات حسب الاستخدام. الفهرس في الطرفية: `lazyhelp`؛ الاستخدام الكامل: `<أداة> --help`؛ دليل للـ AI: `<أداة> --skills`.

### مسارات Git

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `merge_canary` | دمج الفرع الحالي → canary، والبقاء عليه | `merge_canary [--dry-run]` |
| `merge_develop` / `merge_dev` / `merge_test` | نفس الشيء، الأهداف develop / dev / test | `merge_develop` |
| `merge_master` | دمج الفرع الحالي → الفرع الرئيسي (master/main تلقائيًا)، والبقاء على الهدف | `merge_master` |
| `merge_branch` | دمج الفرع الحالي → فرع محدد (اسم الفرع وسيط أول إلزامي) | `merge_branch feature/x` |
| `push_canary` | دمج الفرع الحالي → canary، ثم الدفع والعودة | `push_canary [--stay]` |
| `push_develop` / `push_dev` / `push_test` | نفس الشيء، الأهداف develop / dev / test |  |
| `push_master` | نفس الشيء، الهدف هو الفرع الرئيسي المكتشف تلقائيًا |  |
| `push_branch` | دفع الفرع الحالي إلى فرع محدد (اسم الفرع وسيط أول إلزامي) | `push_branch feature/x` |
| `switch_branch` | تبديل الفروع دفعيًا (إنشاؤه من الرئيسي إن لم يوجد) | `switch_branch <branch>` |
| `sync_branch` | مزامنة الفرع الحالي (أو المحدد) مع origin/<branch> دفعيًا | `sync_branch [branch] [--force]` |
| `sync_master` | مزامنة الفرع الرئيسي دفعيًا (كشف تلقائي) | `sync_master` |
| `delete_branch` | حذف فرع محلي (مستودع واحد؛ دفعيًا خارج git) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | حذف فرع بعيد (مستودع واحد؛ دفعيًا خارج git) | `delete_branch_remote <name> [--remote <r>] [-y]` |

> تشغيل الأوامر أعلاه خارج مستودع git يفعّل الوضع الدفعي: يمسح مستودعات git في المجلدات الفرعية وينفّذها واحدة تلو الأخرى.

### تعاون Git

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `commit` | تنفيذ تلقائي (claude يولّد الرسالة) | `commit` |
| `mr` | إنشاء PR/MR تلقائيًا (claude يولّد العنوان/المحتوى، draft افتراضيًا) | `mr [base]` |
| `issue` | إنشاء Issue تلقائيًا (claude يولّد العنوان/المحتوى) | `issue` |
| `squash_pr` | ضغط الفرع الحالي في commit واحد → فتح PR عبر mr (الوسيط الثاني اسم فرع الـ PR، افتراضيًا `<الحالي>_pr`) | `squash_pr <target> [pr_branch]` |
| `fetch_all` | جلب جميع مستودعات Git دفعيًا | `fetch_all` |
| `list_branch` | سرد الفروع المحلية (مستودع واحد أو مسح الكل، المكررة ⟱) | `list_branch` |

### البناء والفحص

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `checkwork` | بوابة بناء متعددة اللغات قبل push (Go/Rust/Python/Java/Node) + إشعار صوتي | `checkwork` |
| `check_ai` | فحص اتصال نقاط AI API (POST فارغ) | `check_ai` |
| `cicd` | استقصاء CI/CD للفرع الحالي، طباعة النتيجة النهائية | `cicd` |

### البيانات والشبكة

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `archery` | CLI لمنصة Archery SQL (استعلامات / workflow، دخول لكل نطاق) | `archery query execute 'select 1' --instance-name prod --db-name orders` |
| `grafana` | CLI لواجهة Grafana HTTP (دخول لكل نطاق) | `grafana health` |
| `ovpn` | عميل OpenVPN (تعبئة تلقائية للاعتماديات وTOTP، split tunneling) | `ovpn connect` |
| `vpn-prio` | تعديل أولوية خدمات الشبكة في macOS (خفض مسار OpenVPN الافتراضي) | `vpn-prio --help` |
| `ipinfo` | استعلام IP الشبكة المحلية + نوع الشبكة (كشف hotspot) | `ipinfo` |
| `disable-ipv6` / `enable-ipv6` | تعطيل/تمكين IPv6 على جميع خدمات الشبكة (يتطلب sudo) | `sudo disable-ipv6` |

### بحث الويب

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `websearch` | بحث ويب متعدد المحركات (بدون مفاتيح، متوازٍ، إزالة التكرار بالرابط) | `websearch rust async` |
| `webgrab` | تحويل الصفحة إلى Markdown (تجاوز anti-bot + تصيير Playwright + 34 موقعًا + تسجيل دائم) | `webgrab https://example.com` |

### العمليات والتشغيل

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `kk` | إنهاء العمليات بالاسم | `kk nginx` |
| `kkp` | إنهاء العمليات بالمنفذ | `kkp 8080` |
| `loop` | تنفيذ أمر في حلقة مع تتبع النجاح/الفشل | `loop 10 curl url` |
| `unsleep` | منع سكون macOS (caffeinate) | `unsleep timed 2h` |

### الملفات والنظام

| السكربت | الوظيفة | مثال |
| :--- | :--- | :--- |
| `cpd` | نسخ عميق (افتراضيًا إضافة/تحديث فقط؛ `-f` يحذف الزائد) | `cpd src/* dest/` |
| `n` | بث صوتي في macOS (`say`) | `n "build complete"` |
| `inject` | حقن bin/ في PATH الغلاف | `inject` |
| `graphwatch` | خدمة graphify: إعادة بناء تلقائية لرسم المعرفة | `graphwatch add <dir>` |
| `lazyhelp` | فهرس الأدوات في الطرفية + تمرير `--help` | `lazyhelp help <tool>` |

> **ملاحظات الترحيل (الأسماء القديمة محذوفة)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_master`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_master`/`push_test` ; `pushc_all` دُمج في `push_*` (التنفيذ خارج دليل git للدفعة التلقائية، التنفيذ التلقائي بدون تأكيد، `--dry-run` معاينة).

> **متغيرات البيئة** : `BATCH_CONCURRENCY` يتحكم في عملية الدفعات (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) حد التوازي، الافتراضي `4`. مثال : `BATCH_CONCURRENCY=8 push_canary`.
>
> **خيار شامل `--no-say`** : جميع `bin/*` (عدا `n` نفسه) تدعم `--no-say` لكتم صوت macOS ؛ يكافئ `SCRIPTS_NO_SAY=1`. مثال : `delete_branch --no-say hotfix/x`، `push_canary --no-say`.
>
> **خيار `push_*` `--no-check`** : يتخطى بوابات checkwork (فحص مسبق للفرع الحالي + فحص نتيجة الدمج) ؛ بقية التدفق دون تغيير. مثال : `push_canary --no-check`.

---

## الوثائق

موقع الوثائق الكامل : https://lazygophers.github.io/scripts/
