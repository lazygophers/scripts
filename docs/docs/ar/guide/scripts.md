# النصوص البرمجية

## التثبيت

```bash
./bin/inject            # إنشاء ~/.scripts.sh و source إلى جميع rc
./bin/inject --show     # معاينة المحتوى المكتوب
./bin/inject --uninstall  # إلغاء التثبيت
```

inject ذو طبيعة متطابقة : إعادة التشغيل لن تضيف تكرارات. بعد إعادة تشغيل shell أو `source ~/.zshrc`، يمكنك الاتصال مباشرة من أي دليل.

## جدول الوظائف

| النص البرمجي             | الوظيفة                                                         | مثال                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | فحص التجميع المؤتمت + الإشعارات الصوتية            | `checkwork`                   |
| `cpd`            | نسخ عميق (إضافة/تحديث فقط افتراضيًا؛ `-f` يحذف الزائد)        | `cpd src/* dest/`             |
| `kk`             | إنهاء العمليات بالاسم                                              | `kk nginx`                    |
| `kkp`            | إنهاء العمليات بالمنفذ                                                | `kkp 8080`                    |
| `n`              | بث صوتي macOS (`say`)                                       | `n "اكتمل البناء"`                |
| `loop`           | تنفيذ الأوامر في حلقة، تتبع النجاح/الفشل                                  | `loop 10 curl url`            |
| `merge_canary`   | دمج الفرع الحالي → canary، البقاء في canary                           | `merge_canary [--dry-run]`     |
| `merge_develop`  | دمج الفرع الحالي → develop، البقاء في develop                         | `merge_develop`                |
| `merge_master`     | دمج الفرع الحالي → الفرع الرئيسي (اكتشاف تلقائي master/main)، البقاء في الهدف                        | `merge_master`                   |
| `merge_test`     | دمج الفرع الحالي → test، البقاء في test                               | `merge_test`                   |
| `push_canary`    | دمج الفرع الحالي → canary، دفع ثم العودة إلى الفرع الأصلي                      | `push_canary [--stay]`         |
| `push_develop` / `push_master` / `push_test` | نفس الشيء، الأهداف develop / الفرع الرئيسي (اكتشاف تلقائي) / test على التوالي      |                               |
| `push_*` (دفعات)  | عند تنفيذ push_* خارج دليل git، تلقائيًا بالدفعات : مسح مستودعات Git في الدلائل الفرعية والدفع واحدة تلو الأخرى | `push_canary [--dry-run]` |
| `switch_branch`  | تبديل الفروع بالدفعات (إنشاؤها من الفرع الافتراضي (اكتشاف تلقائي) إذا لم تكن موجودة)                 | `switch_branch <branch>`      |
| `sync_master`    | مزامنة master بالدفعات                                              | `sync_master`                 |
| `sync_branch`    | مزامنة بالدفعات الفرع الحالية (أو المحددة) إلى origin/<branch>                             | `sync_branch [branch] [--force]` |
| `delete_branch` | حذف فرع محلي (مفرد/دفعة) | `delete_branch <name> [--force] [-y]` |
| `delete_branch_remote` | حذف فرع بعيد (مفرد/دفعة) | `delete_branch_remote <name> [--remote <r>] [-y]` |
| `fetch_all`  | جلب بالدفعات جميع مستودعات Git                                     | `fetch_all`               |
| `unsleep`        | منع السكون macOS caffeinate                                      | `unsleep -t 3600`             |
| `reindex`        | إعادة فهرسة المشروع (local-only، .gitignore)                        | `reindex`                     |
| `inject`         | حقن bin/ في PATH shell                                      | `inject`                      |

## ملاحظات الترحيل (الأسماء القديمة محذوفة)

- `mergec` / `mergedev` / `mergem` / `merget` → `merge_canary` / `merge_develop` / `merge_master` / `merge_test`
- `pushc` / `pushdev` / `pushm` / `pusht` → `push_canary` / `push_develop` / `push_master` / `push_test`
- `pushc_all` دمج في `push_*` : التنفيذ خارج دليل git يفعّل تلقائيًا وضع الدفعات، التنفيذ التلقائي بدون تأكيد، `--dry-run` للمعاينة.

## متغيرات البيئة

- `BATCH_CONCURRENCY` : الحد الأقصى الموازي للعمليات بالدفعات (`push_*` / `switch_branch` / `sync_branch` / `sync_master`)، الافتراضي `4`. مثال : `BATCH_CONCURRENCY=8 push_canary`.

## تبعيات البيئة

- **Python 3.10+** (المدخلات الخفيفة والمنطق الرئيسي)
- **Git** (merge_* / push_* / switch_branch / sync_master / fetch_all / delete_branch)
- **macOS** (`n` يستخدم `say`، `unsleep` يستخدم `caffeinate`)
- **rich** (تزيين الإخراج، `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)
