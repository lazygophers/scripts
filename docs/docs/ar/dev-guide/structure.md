# بنية الدليل

```
scripts/
├── bin/                          # نصوص برمجية للمدخل الخفيف (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_canary, merge_develop, merge_auto, merge_test   # يستدعي lib git_workflow.merge_to(target)
│   ├── push_canary, push_develop, push_auto, push_test       # push_to مستودع واحد / تلقائي بالدفعات خارج git
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, reindex
│   └── inject                    # حقن bin/ في PATH shell
├── lib/
│   ├── commands/{مجال}/{أمر}.py    # منطق الأعمال لكل أمر، يعرض main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py يعرض أيضًا run(target, argv)
│   └── {مجال}.py                    # المكتبة المشتركة (git/exec/ui/notify/build/process/...)
├── tests/                        # مجموعة unittest
├── commit / prc / issue          # نصوص برمجية bash، لإعادة كتابتها في py (مخزنة مؤقتًا في الجذر)
└── README.md
```

## سلسلة الاستدعاء

```
bin/{script}            (3 أسطر من اختراق المسار + استيراد)
  → lib.commands.{مجال}.{أمر}.main(argv)
    → المكتبة المشتركة lib/{مجال}.py
```

المدخلات الخفيفة فقط تنقل argv إلى وحدة الأعمال، **لا تكتب منطق الأعمال**. القدرات المشتركة (عمليات git، تنفيذ الأوامر، UI، الإشعارات، اكتشاف البناء، إدارة العمليات...) تستقر في `lib/{مجال}.py`، قابلة لإعادة الاستخدام عبر الأوامر.
