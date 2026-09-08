# البنية

```
scripts/
├── bin/                          # مداخل رقيقة (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── merge_* / push_*          # كلها symlinks → bin/_gitwf، تُوزَّع حسب اسم المدخل
│   ├── switch_branch, sync_master, sync_branch, fetch_all, delete_branch, delete_branch_remote
│   ├── loop, unsleep, websearch, webgrab, archery, grafana, ovpn, ...
│   └── inject                    # حقن bin/ في PATH الغلاف
├── lib/                          # كل المنطق (مسطح، بلا مجلدات فرعية)
│   ├── {اسم}.py                  # وحدة عمل لكل أمر (git_workflow / batch_git / build / ...)
│   ├── fire_base.py              # BaseCli + run_cli + timed_cli، الهيكل الموحد
│   ├── lazyhelp.py               # سجل الأدوات (TOOLS = اسم → فئة + سطر وصف)
│   ├── skills_help.py            # إرشاد --skills للـ AI (COMMAND_SKILLS)
│   └── ui / notify / exec / process   # مكتبات مشتركة
├── skills/lazyscripts/           # فهرس skill للـ AI (SKILL.md + ملفات حسب الموضوع)
├── docs/                         # موقع وثائق Rspress (ست لغات في docs/docs/<lang>/)
├── tests/                        # مجموعة unittest
└── README.md (+ 5 ترجمات)
```

## سلسلة الاستدعاء

```
bin/{سكربت}            (3 أسطر + import)
  → run_cli(<الاسم>Cli())      # lib/fire_base.py، توزيع أوامر fire الفرعية
    → دالة العمل في lib/{الاسم}.py
      → المشتركة lib/ui.py / lib/exec.py / ...
```

المدخل الرقيق يمرر argv فقط إلى `run_cli` — **بلا منطق عمل هنا**. ‏`merge_*` / `push_*` روابط إلى `bin/_gitwf`؛ اسم argv[0] يحدد الإجراء والفرع الهدف. القدرات المشتركة في `lib/{النطاق}.py`.

كل أداة عامة جديدة تُسجَّل في `TOOLS` داخل `lib/lazyhelp.py`؛ وإرشاد AI في `COMMAND_SKILLS` داخل `lib/skills_help.py`.
