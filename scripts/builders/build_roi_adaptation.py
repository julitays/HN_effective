import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet


TOTAL_COURSES = 9          # всего курсов в программе адаптации
ADAPTATION_DAYS = 90       # порог адаптации в днях


def _category(row) -> str:
    """Ученик / Новичок (0-90 дней) / Опытный (>90 дней)."""
    if row.get("Ученический договор") == True:
        return "Ученик"
    days = row.get("мин_дней_после_найма")
    if pd.isna(days):
        return "Нет данных"
    return "Новичок (0-90 дней)" if days <= ADAPTATION_DAYS else "Опытный (>90 дней)"


def build_roi_adaptation() -> pd.DataFrame:
    settings = load_settings()

    learn = pd.read_parquet(settings["sources"]["learning"]["output"])
    dim   = pd.read_parquet(settings["sources"]["users"]["output"])
    oed   = pd.read_parquet(settings["sources"]["oed"]["output"])
    okk   = pd.read_parquet(settings["sources"]["okk"]["output"])

    if "Балл теста" in learn.columns:
        learn["Балл теста"] = pd.to_numeric(learn["Балл теста"], errors="coerce")
    if "Дней после найма" in learn.columns:
        learn["Дней после найма"] = pd.to_numeric(learn["Дней после найма"], errors="coerce")

    emp_col   = "ID сотрудника"
    course_col = "Номер курса"

    # ── Читаем ROI-каталог для названий курсов ────────────────────────────────
    roi_files = list(Path("config").glob("*ROI*.xlsx")) + list(Path("config").glob("*roi*.xlsx"))
    roi = pd.read_excel(roi_files[0], dtype=str) if roi_files else pd.DataFrame()
    course_names = {}
    if not roi.empty:
        id_col_roi = "Номер курса в КУ"
        name_col   = "Название курса в КУ"
        if id_col_roi in roi.columns and name_col in roi.columns:
            course_names = dict(zip(
                roi[id_col_roi].str.strip(),
                roi[name_col].str.strip()
            ))

    # ── Базовые метрики на уровне сотрудника ─────────────────────────────────
    grp = learn.groupby(emp_col)

    base = pd.DataFrame({
        "Курсов всего":         grp[course_col].nunique(),
        "Сертификат (все 9)":   grp["Пройдено"].apply(
                                    lambda x: (x.eq(True).sum() >= TOTAL_COURSES)
                                ).astype("boolean"),
        "Курсов пройдено":      grp["Пройдено"].apply(lambda x: x.eq(True).sum()),
        "Средний балл теста":   grp["Балл теста"].mean().round(3),
        "Ученический договор":  grp["Ученический договор"].apply(
                                    lambda x: x.eq(True).any()
                                ).astype("boolean"),
        "мин_дней_после_найма": grp["Дней после найма"].min(),
        "Дней обучения до найма": grp["Дней после найма"].apply(
                                    lambda x: abs(x[x < 0].min()) if (x < 0).any() else None
                                ),
    }).reset_index()

    # Скорость адаптации: дней от Даты приёма до завершения ВСЕХ пройденных курсов
    completed = learn[learn["Пройдено"] == True].copy()
    if "Дата завершения" in completed.columns:
        last_completion = completed.groupby(emp_col)["Дата завершения"].max().reset_index()
        last_completion.columns = [emp_col, "Дата последнего курса"]
        base = base.merge(last_completion, on=emp_col, how="left")

        dim_dates = dim[[emp_col, "Дата приёма"]].drop_duplicates(emp_col)
        base = base.merge(dim_dates, on=emp_col, how="left")
        base["Скорость адаптации (дней)"] = (
            (base["Дата последнего курса"] - base["Дата приёма"]).dt.days
        )
        base = base.drop(columns=["Дата последнего курса", "Дата приёма"], errors="ignore")

    # Категория сотрудника
    base["Категория"] = base.apply(_category, axis=1)
    base = base.drop(columns=["мин_дней_после_найма"], errors="ignore")

    # ── Пивот по курсам: Пройден + Балл теста ────────────────────────────────
    courses = sorted(learn[course_col].dropna().unique())
    for cid in courses:
        cname = course_names.get(str(cid), str(cid))
        short = cname[:25].rstrip()          # короткое имя для колонки BI

        sub = learn[learn[course_col] == cid]
        passed = sub.groupby(emp_col)["Пройдено"].apply(lambda x: x.eq(True).any()).astype("boolean")
        score  = sub.groupby(emp_col)["Балл теста"].mean().round(3)

        base = base.merge(
            passed.rename(f"Пройден: {short}").reset_index(), on=emp_col, how="left"
        )
        base = base.merge(
            score.rename(f"Балл: {short}").reset_index(), on=emp_col, how="left"
        )

    # ── Обогащение dim ────────────────────────────────────────────────────────
    dim_keep = dim[[emp_col, "ФИО", "Должность", "Город", "Регион",
                    "Дата приёма", "Стаж (дней)", "Активен"]].drop_duplicates(emp_col)
    base = base.merge(dim_keep, on=emp_col, how="left")

    # ── ОЭД: последний период ────────────────────────────────────────────────
    oed_id = "ID сотрудника" if "ID сотрудника" in oed.columns else None
    if oed_id:
        latest = (oed.sort_values(["Год", "Квартал"], ascending=False)
                     .drop_duplicates(subset=[oed_id], keep="first"))
        oed_cols = [c for c in ["Рейтинг", "Класс", "Риск оттока", "Период"] if c in latest.columns]
        oed_agg = latest[[oed_id] + oed_cols].rename(columns={
            oed_id:   emp_col,
            "Рейтинг":"Рейтинг ОЭД",
            "Класс":  "Класс ОЭД",
            "Риск оттока": "Риск оттока",
            "Период": "Период ОЭД",
        })
        base = base.merge(oed_agg, on=emp_col, how="left")

    # ── ОКК: по мерчендайзеру ─────────────────────────────────────────────────
    me_col  = "ID мерчендайзера" if "ID мерчендайзера" in okk.columns else None
    qty_col = "Качество визита"   if "Качество визита"  in okk.columns else None
    fal_col = "Флаг фальсификации"if "Флаг фальсификации" in okk.columns else None

    if me_col and qty_col:
        agg_dict = {"Ср. качество визита": (qty_col, "mean")}
        if fal_col:
            agg_dict["% визитов с фальсификацией"] = (fal_col, lambda x: x.eq(True).mean())
        agg_dict["Кол-во визитов"] = (me_col, "count")

        okk_agg = (okk.groupby(me_col)
                       .agg(**agg_dict)
                       .reset_index()
                       .rename(columns={me_col: emp_col}))
        for c in ["Ср. качество визита", "% визитов с фальсификацией"]:
            if c in okk_agg.columns:
                okk_agg[c] = okk_agg[c].round(3)
        base = base.merge(okk_agg, on=emp_col, how="left")

    # ── Итоговый порядок ─────────────────────────────────────────────────────
    front = [emp_col, "ФИО", "Категория", "Должность", "Город", "Регион",
             "Дата приёма", "Стаж (дней)", "Активен",
             "Ученический договор", "Дней обучения до найма",
             "Курсов всего", "Курсов пройдено", "Сертификат (все 9)",
             "Средний балл теста", "Скорость адаптации (дней)",
             "Рейтинг ОЭД", "Класс ОЭД", "Риск оттока", "Период ОЭД",
             "Ср. качество визита", "% визитов с фальсификацией", "Кол-во визитов"]
    course_cols = [c for c in base.columns if c.startswith(("Пройден:", "Балл:"))]
    col_order = [c for c in front + course_cols if c in base.columns]
    base = base[col_order]

    output = "data/out/витрина_адаптации.parquet"
    save_parquet(base, output)

    total = len(base)
    cats  = base["Категория"].value_counts().to_dict()
    cert  = base["Сертификат (все 9)"].eq(True).sum()
    print(f"\n  ROI Адаптация: {total} сотрудников")
    for k, v in sorted(cats.items()):
        print(f"    {k}: {v} ({v/total*100:.0f}%)")
    print(f"  Сертификат (все 9 курсов): {cert} ({cert/total*100:.0f}%)")
    if "Рейтинг ОЭД" in base.columns:
        print(f"  С данными ОЭД: {base['Рейтинг ОЭД'].notna().sum()}")
    if "Ср. качество визита" in base.columns:
        print(f"  С данными ОКК: {base['Ср. качество визита'].notna().sum()}")
    return base
