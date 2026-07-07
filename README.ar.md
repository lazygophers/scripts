# scripts

[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

مجموعة من أدوات كفاءة التطوير — اختصارات نصية برمجية متنوعة. مدخلات خفيفة Bash/Python، المنطق الرئيسي في `lib/`.

---

## التثبيت : حقن bin/ في PATH

```bash
./bin/inject            # إنشاء ~/.scripts.sh و source إلى جميع rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # معاينة المحتوى المكتوب
./bin/inject --uninstall  # إلغاء التثبيت
```

inject ذو طبيعة متطابقة : إعادة التشغيل لن تضيف تكرارات. بعد الإكمال، أعد تشغيل shell أو `source ~/.zshrc`، ثم اتصل بـ `checkwork` / `merge_canary` / ... من أي دليل.

---

## وظائف النصوص البرمجية

| النص البرمجي             | الوظيفة                                                         | مثال                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | فحص التجميع المؤتمت + الإشعارات الصوتية            | `checkwork`                   |
| `cpd`            | نسخ عميق (إضافة/تحديث فقط افتراضيًا؛ `-f` يحذف الزائد)        | `cpd src/* dest/`             |
| `kk`             | إنهاء العمليات بالاسم                                             | `kk nginx`                    |
| `kkp`            | إنهاء العمليات بالمنفذ                                                | `kkp 8080`                    |
| `n`              | بث صوتي macOS (`say`)                                       | `n "اكتمل البناء"`                |
| `loop`           | تنفيذ الأوامر في حلقة، تتبع النجاح/الفشل                                  | `loop 10 curl url`            |
| `merge_canary`   | دمج الفرع الحالي → canary، البقاء في canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | دمج الفرع الحالي → develop، البقاء في develop                         | `merge_develop`                |
| `merge_auto`     | دمج الفرع الحالي → الفرع الافتراضي البعيد، البقاء في الهدف                        | `merge_auto`                   |
| `merge_test`     | دمج الفرع الحالي → test، البقاء في test                               | `merge_test`                   |
| `push_canary`    | دمج الفرع الحالي → canary، دفع ثم العودة                      | `push_canary [--stay]`         |
| `push_develop` / `push_auto` / `push_test` | نفس الشيء، الأهداف develop / البعيد الافتراضي / test على التوالي      |                               |
| `push_*` (دفعات)  | عند تنفيذ push_* خارج دليل git، تلقائيًا بالدفعات : مسح مستودعات GitLab في الدلائل الفرعية والدفع واحدة تلو الأخرى | `push_canary [--dry-run]` |
| `switch_branch`  | تبديل الفروع بالدفعات (إنشاؤها من origin/master إذا لم تكن موجودة)                 | `switch_branch <branch>`      |
| `sync_master`    | مزامنة master بالدفعات = `sync_branch master`                                              | `sync_master`                 |
| `sync_branch`    | مزامنة بالدفعات الفرع الحالية (أو المحددة) إلى origin/<branch> | `sync_branch [branch] [--force]` |
| `fetch_all`  | جلب بالدفعات جميع مستودعات Git                                     | `fetch_all`               |
| `unsleep`        | منع السكون macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | إعادة فهرسة المشروع (local-only، .gitignore)                        | `reindex`                     |
| `inject`         | حقن bin/ في PATH shell                                      | `inject`                      |

> **ملاحظات الترحيل (الأسماء القديمة محذوفة)** : `mergec`/`mergedev`/`mergem`/`merget` → `merge_canary`/`merge_develop`/`merge_auto`/`merge_test` ; `pushc`/`pushdev`/`pushm`/`pusht` → `push_canary`/`push_develop`/`push_auto`/`push_test` ; `pushc_all` دُمج في `push_*` (التنفيذ خارج دليل git للدفعة التلقائية، التنفيذ التلقائي بدون تأكيد، `--dry-run` معاينة).

> **متغيرات البيئة** : `BATCH_CONCURRENCY` يتحكم في عملية الدفعات (`push_*` / `switch_branch` / `sync_branch` / `sync_master`) حد التوازي، الافتراضي `4`. مثال : `BATCH_CONCURRENCY=8 push_canary`.

---

## الوثائق

موقع الوثائق الكامل : https://lazygophers.github.io/scripts/
