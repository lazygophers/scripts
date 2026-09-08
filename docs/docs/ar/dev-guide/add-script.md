# إضافة سكربت

خطوتان مستقلتان. `{اسم}` هو اسم السكربت.

## 1. اكتب منطق العمل في `lib/{الاسم}.py`

```python
"""What foo does (one line; quoted by the lazyhelp catalog)."""


def run() -> int:
    # ... business logic ...
    return 0
```

## 2. أضف المدخل الرقيق `bin/{الاسم}`

```python
#!/usr/bin/env python3
"""foo — what it does (fire refactor)"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.foo import run as do_foo


class FooCli(BaseCli):
    """What it does"""

    @timed_cli
    def run(self):
        """Run foo"""
        return do_foo()


if __name__ == "__main__":
    run_cli(FooCli())
```

```bash
chmod +x bin/{الاسم}
```

## 3. سجِّل في الفهرس والإرشاد

- أضف سطرًا إلى `TOOLS` في `lib/lazyhelp.py`: `"foo": ("الفئة", "وصف من سطر")` (استخدم فئة موجودة من `CATEGORIES_ORDER` أو أضف جديدة).
- للإرشاد الموجه للـ AI أضف `"foo": ["أمثلة قابلة للنسخ الحرفي", ...]` إلى `COMMAND_SKILLS` في `lib/skills_help.py` (اختياري — وإلا يُستخدم الوصف).

تغليف `timed_cli` إلزامي: يطبع في stderr سطر بداية/نهاية/مدة باهتًا عند الخروج.

## عدة أسماء مداخل، منطق واحد

انظر `merge_canary` / `merge_develop` / ...: اكتب موزّعًا واحدًا `bin/_foo` يستنتج الوسيط من basename الخاص بـ argv[0]، ثم اجعل الأسماء الأخرى symlinks إليه.
