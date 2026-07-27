import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet


def build_adaptation() -> pd.DataFrame:
    """
    Витрина адаптации: одна строка на сотрудника.
    Сравнивает учеников (обучение до найма) и обычных новичков (0-90 дней).
    Обогащает данными ОЭД и ОКК для ROI-анализа.
    """
    settings = load_settings()

    learn = pd.read_parquet(settings["sources"]["learning"]["output"])
    dim   = pd.read_parquet(settings["sources"]["users"]["output"])
    oed   = pd.read_parquet(settings["sources"]["oed"]["output"])
    okk   = pd.read_parquet(settings["sources"]["okk"]["output"])

    if "Балл теста" in learn.columns:
        learn["Балл теста"] = pd.to_numeric(learn["Балл теста"], errors="coerce")
    if "Дней после найма" in learn.columns:
        learn["Дней после найма"] = pd.to_numeric(learn["Дней после найма"], errors="coerce")

    # Нормализуем имена — в витринах уже русские названия
    id_col_learn = "ID сотрудника"
    id_col_dim   = "ID сотрудника"

    # ── Базовые метрики из обучения ──────────────────────────────────────────
    def phase(mask):
        """Агрегирует метрики обучения для строк с заданной маской."""
        grp = learn[mask].groupby(id_col_learn)
        return pd.DataFrame({
            "n_курсов":    grp[id_col_learn].count(),
            "пройдено":    grp["Пройдено"].apply(lambda x: x.eq(True).mean()),
            "ср_балл":     grp["Балл теста"].mean().round(3),
        })

    # Все курсы
    all_stats   = phase(pd.Series([True] * len(learn), index=learn.index))

    # До найма (ученический)
    before_mask = learn["Ученический договор"] == True
    before_stats = phase(before_mask).add_suffix("_до_найма") if before_mask.any() else pd.DataFrame()

    # В период адаптации (0-90 дней)
    adapt_mask   = learn["В период адаптации"] == True
    adapt_stats  = phase(adapt_mask).add_suffix("_адаптация") if adapt_mask.any() else pd.DataFrame()

    # Собираем базу
    fact = all_stats.rename(columns={
        "n_курсов": "Курсов всего",
        "пройдено": "Доля пройденных",
        "ср_балл":  "Средний балл теста",
    })
    fact.index.name = id_col_learn

    if not before_stats.empty:
        fact = fact.join(before_stats.rename(columns={
            "n_курсов_до_найма": "Курсов до найма",
            "пройдено_до_найма": "Доля пройденных до найма",
            "ср_балл_до_найма":  "Средний балл до найма",
        }), how="left")

    if not adapt_stats.empty:
        fact = fact.join(adapt_stats.rename(columns={
            "n_курсов_адаптация": "Курсов в адаптации",
            "пройдено_адаптация": "Доля пройденных в адаптации",
            "ср_балл_адаптация":  "Средний балл в адаптации",
        }), how="left")

    fact = fact.reset_index()

    # Флаги и временны́е метрики
    uch_flag = learn[learn["Ученический договор"] == True].groupby(id_col_learn).size() > 0
    fact["Ученический договор"] = fact[id_col_learn].map(uch_flag).fillna(False).astype("boolean")

    # Дней обучения до найма (первый курс до найма)
    pre_days = (
        learn[before_mask]
        .groupby(id_col_learn)["Дней после найма"]
        .min()
        .abs()  # делаем положительным для удобства чтения
    )
    fact["Дней обучения до найма"] = fact[id_col_learn].map(pre_days)

    # Все курсы пройдены?
    all_passed = learn.groupby(id_col_learn)["Пройдено"].apply(lambda x: x.eq(True).all())
    fact["Все курсы пройдены"] = fact[id_col_learn].map(all_passed).astype("boolean")

    # ── Обогащение из dim_employees ──────────────────────────────────────────
    dim_cols = ["ID сотрудника", "ФИО", "Должность", "Город", "Регион",
                "Дата приёма", "Стаж (дней)", "Активен"]
    dim_keep = dim[[c for c in dim_cols if c in dim.columns]].drop_duplicates("ID сотрудника")
    fact = fact.merge(dim_keep, on="ID сотрудника", how="left")

    # ── Обогащение из ОЭД (последний период) ─────────────────────────────────
    oed_id = "ID сотрудника" if "ID сотрудника" in oed.columns else None
    if oed_id:
        latest_oed = (
            oed.sort_values(["Год", "Квартал"], ascending=False)
            .drop_duplicates(subset=[oed_id], keep="first")
        )
        oed_keep = latest_oed[[
            oed_id, "Рейтинг", "Класс", "Риск оттока", "Период"
        ]].rename(columns={
            oed_id:   "ID сотрудника",
            "Рейтинг":"Рейтинг ОЭД",
            "Класс":  "Класс ОЭД",
            "Период": "Период ОЭД",
        })
        fact = fact.merge(oed_keep, on="ID сотрудника", how="left")

    # ── Обогащение из ОКК ────────────────────────────────────────────────────
    okk_id = "ID супервайзера" if "ID супервайзера" in okk.columns else None
    me_id  = "ID мерчендайзера" if "ID мерчендайзера" in okk.columns else None

    # Считаем по мерчу (если они мерчи)
    if me_id and "Качество визита" in okk.columns:
        okk_agg = okk.groupby(me_id).agg(
            **{
                "Ср. качество визита": ("Качество визита", "mean"),
                "% визитов с фальсификацией": ("Флаг фальсификации", lambda x: x.eq(True).mean()),
                "Кол-во визитов": (me_id, "count"),
            }
        ).reset_index().rename(columns={me_id: "ID сотрудника"})
        okk_agg["Ср. качество визита"]          = okk_agg["Ср. качество визита"].round(3)
        okk_agg["% визитов с фальсификацией"]   = okk_agg["% визитов с фальсификацией"].round(3)
        fact = fact.merge(okk_agg, on="ID сотрудника", how="left")

    # ── Итоговый порядок ─────────────────────────────────────────────────────
    col_order = [
        "ID сотрудника", "ФИО", "Должность", "Город", "Регион",
        "Дата приёма", "Стаж (дней)", "Активен",
        # Признаки
        "Ученический договор", "Дней обучения до найма",
        # Обучение — сводно
        "Курсов всего", "Все курсы пройдены",
        "Доля пройденных", "Средний балл теста",
        # Фазы
        "Курсов до найма", "Доля пройденных до найма", "Средний балл до найма",
        "Курсов в адаптации", "Доля пройденных в адаптации", "Средний балл в адаптации",
        # ОЭД
        "Рейтинг ОЭД", "Класс ОЭД", "Риск оттока", "Период ОЭД",
        # ОКК
        "Ср. качество визита", "% визитов с фальсификацией", "Кол-во визитов",
    ]
    fact = fact[[c for c in col_order if c in fact.columns]]

    output = "data/out/fact_адаптация.parquet"
    save_parquet(fact, output)

    total  = len(fact)
    uch    = fact["Ученический договор"].eq(True).sum()
    adapt  = fact["Курсов в адаптации"].notna().sum() if "Курсов в адаптации" in fact.columns else 0
    print(f"\n  Адаптация итого: {total} сотрудников")
    print(f"  Ученический договор: {uch} ({uch/total*100:.1f}%)")
    print(f"  С данными ОЭД: {fact['Рейтинг ОЭД'].notna().sum() if 'Рейтинг ОЭД' in fact.columns else 0}")
    print(f"  С данными ОКК: {fact['Ср. качество визита'].notna().sum() if 'Ср. качество визита' in fact.columns else 0}")
    return fact
