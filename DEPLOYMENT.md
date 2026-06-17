# DEPLOYMENT — Terminal v1.0

دليل نشر اللوحة على **Streamlit Cloud** لفريق داخلي، مع بقاء جمع البيانات
محليًا، وضمان **عدم فقد البيانات اليومية**.

---

## المعمارية (هجين محلي + سحابي)

```
[جهازك المحلي]                    [Postgres سحابي]              [Streamlit Cloud]
Task Scheduler + Playwright  ─كتابة─►  المخزن الدائم   ◄─قراءة─  main_app.py (read-only)
السكرابرات (Panda/Tadawul/سيولة)       (Supabase/Neon)           + authentication
SQLite محلي = نسخة ثانية دائمة                                   لا سكرابر هنا
```

**لماذا هجين:** Streamlit Cloud له نظام ملفات مؤقّت (يمحو أي `.db`)، والسكرابرات
تحتاج متصفحًا حقيقيًا (Akamai) لا يعمل على السحابة. لذا تُفصل طبقة البيانات إلى
Postgres مُدار يصله الطرفان.

**مفتاح التبديل:** ملف `db.py` يقرأ `DATABASE_URL`:
- غير مضبوط → SQLite محلي (السلوك الحالي، بلا تغيير).
- `postgresql://…` → Postgres. لا تغيير كود إضافي.

---

## حقائق مهمة عن النظام (تم التحقق 2026-06-17)

- **402 صنف** = 342 `external_proxy` (مؤشرات GASTAT تاريخية تعود لـ2013) + 60 `supermarket`. المؤشر **مُهجّن** (GASTAT + كشط حيّ).
- `daily_prices`: ~55,525 صف، 212 تاريخًا (2013→2026). `daily_index`: 10 صفوف (منذ 2026-06-03) — **هذا مقصود بالتصميم الحالي، ليس عطلًا. لا تُشغّل `calculator.py --rebuild` دون مراجعة منهجية الكالكوليتر (1195 سطرًا) أولًا** — قد يعيد الحساب بأساس 2013 ويفسد المؤشر المنشور.
- 3 مهام Task Scheduler يومية تعمل: تضخم 12:00، صناديق 13:00، سيولة 15:00.
- `openpyxl` مطلوب لقراءات `.xlsx` (كان مفقودًا من requirements — أُصلح).

---

## الأسرار (حرج — الوجهة عامة عبر GitHub)

- المشروع **ليس git repo بعد** → لم يُسرَّب شيء.
- `.gitignore` يستثني: `*.db`, `.runtime/`, `*auth_state*.json`, `.env`, `.streamlit/secrets.toml`, `*.log`, `debug_*`, `*_html.txt`.
- **لا تُرفع أبدًا:** `.runtime/panda_auth_state.json` (JWT + هاتف)، أي `.db`، connection strings.
- مستودع GitHub يحوي **الكود فقط** (لا بيانات، لا أسرار).

---

## التسلسل (مراحل)

### المرحلة 0 — إصلاح Danube/Ninja (قبل النشر)
1. محليًا: `python scraper.py` (يحفظ عند الفشل `debug_ninja_zero_cards_*.html` / `debug_danube_*_*.html`).
2. استخراج المحدّدات الصحيحة من الـ HTML الحيّ وتثبيتها في `scraper.py`.
3. تحقق: تشغيل حيّ يُرجع ≥ صنفين من كل متجر.

### المرحلة 1 — تحصين ✅ (مُنجز جزئيًا)
- ✅ `.gitignore` مُحصّن. ✅ `requirements.txt` (openpyxl + طبقة Postgres).
- ✅ `db.py` + توصيل مسار القراءة (`mod_inflation/funds/liquidity`).
- ⏳ متبقٍّ: تقييد CORS في `tadawul_api.py` (إن نُشر الـAPI)، وإصلاح المسارات المطلقة في `.bat`/`.ps1` (مؤجّل — يُغذّي Task Scheduler العامل).

### المرحلة 2 — Postgres (طبقة البيانات الدائمة)
1. أنشئ مشروع **Supabase** (مجاني): supabase.com → New Project → احفظ كلمة مرور DB.
2. خذ الـ connection string: Settings → Database → URI.
3. محليًا: `pip install -r requirements.txt` (يثبّت psycopg2-binary + dotenv).
4. أنشئ `.env` (مستثنى من git): `DATABASE_URL=postgresql://…?sslmode=require`.
5. أنشئ المخطط على Postgres + رحّل التاريخ عبر سكربت الترحيل (`migrate_to_pg.py` — يُكتب في هذه المرحلة) مع **بوّابة تحقق**: تطابق عدد الصفوف + checksums (مثلًا `daily_prices=55,525`).
6. حوّل **مسار الكتابة** (calculator + scrapers + `mod_real_estate`) إلى `db.py` بوضع **dual-write** (يكتب Postgres + SQLite معًا) — لا cutover إلا بعد أسبوع تطابق يومي.
7. أنشئ دور **read-only** على Postgres للوحة، وفعّل `pg_dump` مجدول للنسخ الاحتياطي.

### المرحلة 3 — Streamlit Cloud
1. ادفع الكود (بلا بيانات/أسرار) إلى مستودع GitHub خاص.
2. share.streamlit.io → New app → اختر المستودع و `main_app.py`.
3. **Requirements:** اضبط Streamlit Cloud ليستخدم `requirements-cloud.txt` (lean، بلا playwright). تحقق محليًا من استيراد نظيف في venv جديد قبل الدفع.
4. **Secrets:** Settings → Secrets → الصق محتوى `.streamlit/secrets.toml` (انظر `.streamlit/secrets.toml.example`): `DATABASE_URL` (دور read-only) + بيانات `[auth]`.
5. **Authentication:** فعّل `streamlit-authenticator` في `main_app.py` (gate قبل عرض أي شيء). ولّد هاش كلمة المرور:
   `python -c "import streamlit_authenticator as a; print(a.Hasher(['PASS']).generate())"`
6. تحقق: اللوحة تفتح، تطلب تسجيل دخول، تقرأ من Postgres، ولا أسرار في المستودع.

---

## ملفات النشر في هذا المجلد

| ملف | الغرض | يُرفع لـGitHub؟ |
|---|---|---|
| `db.py` | طبقة بيانات موحّدة (SQLite/Postgres) | ✅ |
| `requirements.txt` | بيئة محلية كاملة (سكرابرات) | ✅ |
| `requirements-cloud.txt` | بيئة السحابة (قراءة فقط، بلا playwright) | ✅ |
| `.streamlit/config.toml` | إعدادات + ثيم (بلا أسرار) | ✅ |
| `.streamlit/secrets.toml.example` | قالب الأسرار | ✅ (القالب فقط) |
| `.streamlit/secrets.toml` | الأسرار الحقيقية | ❌ مستثنى |
| `.env` | `DATABASE_URL` المحلي | ❌ مستثنى |
| `*.db`, `.runtime/` | بيانات + كوكيز | ❌ مستثنى |

---

## التحقق النهائي قبل الإعلان
- [ ] `git status` لا يُظهر أي `.db`/أسرار.
- [ ] venv نظيف + `requirements-cloud.txt` → `import main_app` بلا أخطاء.
- [ ] بوّابة الترحيل: عدّ الصفوف + checksums متطابقة 100%.
- [ ] أسبوع dual-write بتطابق يومي PG↔SQLite.
- [ ] اللوحة السحابية: المصادقة تمنع الوصول، تقرأ من Postgres.
- [ ] `pg_dump` مجدول يعمل + SQLite يبقى نسخة ثانية.
