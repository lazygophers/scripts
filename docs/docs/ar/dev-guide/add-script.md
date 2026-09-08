# إضافة سكربت

خطوتان مستقلتان. `{اسم}` هو اسم السكربت.

## 1. اكتب منطق العمل في `lib/{الاسم}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. اكتب الـ CLI في `lib/cli/{الاسم}.py`

```python
"""foo — ماذا يفعل"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """ماذا يفعل"""

    @timed_cli
    def run(self):
        """تشغيل foo"""
        return do_foo()


def main():
    run_cli(FooCli())
```

## 3. أضف المدخل الرقيق `bin/{الاسم}`

```python
#!/usr/bin/env python3
"""foo مدخل رقيق — ماذا يفعل"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.cli.foo import main

raise SystemExit(main())
```

```bash
chmod +x bin/{الاسم}
```

## 4. سجِّل الأمر والفهرس والإرشاد

- أضف سطرًا إلى `[project.scripts]` في `pyproject.toml`: `foo = "lib.cli.foo:main"`. بدونه لا يوجد الأمر بعد التثبيت، ويفشل `uvx --from git+https://github.com/lazygophers/scripts foo`.
- أضف سطرًا إلى `TOOLS` في `lib/lazyhelp.py`: `"foo": ("الفئة", "وصف من سطر")` (استخدم فئة موجودة من `CATEGORIES_ORDER` أو أضف جديدة).
- للإرشاد الموجه للـ AI أضف `"foo": ["أمثلة قابلة للنسخ الحرفي", ...]` إلى `COMMAND_SKILLS` في `lib/skills_help.py` (اختياري — وإلا يُستخدم الوصف).

تغليف `timed_cli` إلزامي: يطبع في stderr سطر بداية/نهاية/مدة باهتًا عند الخروج.

## عدة أسماء مداخل، منطق واحد

انظر `merge_canary` / `merge_develop` / ...: ضع الصنف المشترك في ملف واحد `lib/cli/gitwf.py`، ثم صدِّر لكل اسم دالة بلا وسائط تمرر معاملاتها صراحةً (`def merge_canary(): _run("merge_canary", "merge", "canary")`). لكل اسم غلافه في `bin/` وسطره في `[project.scripts]` — بلا روابط رمزية وبلا استنتاج من argv[0].
