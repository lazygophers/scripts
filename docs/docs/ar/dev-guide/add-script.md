# إضافة نص برمجي

خطوتان، بدون تداخل. أدناه `{مجال}` يمثل أحد `build` / `file` / `git` / `process` / `misc` / `system`، `{اسم}` يمثل اسم نصك البرمجي.

## 1. كتابة منطق الأعمال في `lib/commands/{مجال}/{اسم}.py`

```python
#!/usr/bin/env python3
"""foo - ما يفعله."""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... منطق الأعمال ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

تصنيف المجالات : `build` / `file` / `git` / `process` / `misc` / `system`. للمجالات الجديدة، لا تنس إضافة `lib/commands/{مجال}/__init__.py`.

## 2. إضافة المدخل الخفيف `bin/{اسم}`

```python
#!/usr/bin/env python3
"""foo المدخل الخفيف."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.{مجال}.foo import main

raise SystemExit(main(sys.argv))
```

```bash
chmod +x bin/{اسم}
```

تم. **لا حاجة لتسجيل أي قواميس أو قوائم.**

## النصوص البرمجية المستعارة (عدة أسماء لنفس المنطق بمعاملات مختلفة)

انظر `merge_canary` / `merge_develop` / ... : في `lib/commands/git/merge.py` اعرض `run(target, argv)`، كل مدخل خفيف ينقل هدف ثابت :

```python
# bin/merge_canary
from lib.commands.git.merge import run
raise SystemExit(run("canary", sys.argv))
```
