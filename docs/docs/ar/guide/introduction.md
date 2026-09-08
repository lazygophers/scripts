# مقدمة

`scripts` مجموعة أدوات لرفع الكفاءة — اختصارات للمهام الشائعة في التطوير والتشغيل. لا يحوي `bin/` سوى أغلفة رقيقة، والتنفيذ في `lib/cli/`، والقدرات المشتركة في `lib/`.

## أبرز الميزات

- **مداخل رقيقة**: كل سكربت في `bin/` هو الغلاف نفسه (تعديل path + ‏`from lib.cli.<module> import main`) — بلا منطق عمل وبلا روابط رمزية؛ التنفيذ في `lib/cli/<name>.py`، والقدرات المشتركة في وحدات `lib/` المسطحة.
- **تشغيل عن بُعد بلا تثبيت**: كل الأوامر مسجّلة في `[project.scripts]`، فيعمل `uvx git+https://github.com/lazygophers/scripts` دون استنساخ المستودع.
- **تصنيف حسب الاستخدام**: مسارات Git / تعاون Git / البناء والفحص / البيانات والشبكة / بحث الويب / العمليات والتشغيل / الملفات والنظام — صفحة واحدة عبر `lazyhelp`.
- **عمليات دفعية**: `merge_*` / `push_*` / `switch_branch` / `sync_master` تغطي مستودعًا واحدًا ودفعات متعددة.
- **الأمان أولًا**: استبعاد ذاتي في إدارة العمليات، وفحص نظافة شجرة العمل والتراجع قبل عمليات Git.

## البدء السريع

شغّلها مرة واحدة بلا تثبيت:

```bash
uvx git+https://github.com/lazygophers/scripts                     # سرد كل الأدوات
uvx --from git+https://github.com/lazygophers/scripts checkwork    # تشغيل أداة واحدة
```

للإبقاء عليها على هذا الجهاز:

```bash
./bin/inject            # حقن bin/ المستنسخ في PATH الغلاف
```

أعد تشغيل الغلاف ثم نادِ `checkwork` / `merge_canary` / ... من أي مجلد.

انظر [السكربتات](./scripts.md) ومستودع GitHub [lazygophers/scripts](https://github.com/lazygophers/scripts).
