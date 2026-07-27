import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet, normalize_pct as _normalize_pct

COURSE_CATALOG_PATH = Path("config") / "courses_catalog_ЛМ_ROI_пример.xlsx"

PAGE8_COMPETENCY_BY_COURSE_ID = {
    4327: "Базовые стандарты",
    3488: "PICOS",
    3439: "Доступность",
    5105: "Работа с ТТ",
    5501: "Базовые стандарты",
    4895: "Антифрод",
    5178: "Фотоаудит",
    2078: "PICOS",
}


def _to_bool(value):
    if pd.isna(value):
        return pd.NA
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"да", "true", "1", "истина"}:
        return True
    if text in {"нет", "false", "0", "ложь"}:
        return False
    return pd.NA


def _course_type(row: pd.Series) -> str:
    if row.get("Обязательный факт") is True or row.get("Обязательный каталог") is True:
        return "обязательный"
    return "дополнительный"


def _load_course_catalog() -> pd.DataFrame:
    base = pd.read_excel(COURSE_CATALOG_PATH, sheet_name="Лист1")[
        [
            "Номер курса в КУ",
            "Название курса в КУ",
            "Какую компетенцию развивает",
            "Обязательный курс",
        ]
    ].copy()
    base = base.rename(
        columns={
            "Номер курса в КУ": "Номер курса",
            "Название курса в КУ": "Название курса",
            "Какую компетенцию развивает": "Компетенция из каталога",
            "Обязательный курс": "Обязательный каталог",
        }
    )
    base["Номер курса"] = pd.to_numeric(base["Номер курса"], errors="coerce").astype("Int64")
    base["Обязательный каталог"] = base["Обязательный каталог"].map(_to_bool)

    roi = pd.read_excel(COURSE_CATALOG_PATH, sheet_name="ROI_карта_курсов")[
        [
            "Номер курса в КУ",
            "Название курса в КУ",
            "Уточнённая компетенция для ROI",
        ]
    ].copy()
    roi = roi.rename(
        columns={
            "Номер курса в КУ": "Номер курса",
            "Название курса в КУ": "Название курса",
            "Уточнённая компетенция для ROI": "Компетенция ROI",
        }
    )
    roi["Номер курса"] = pd.to_numeric(roi["Номер курса"], errors="coerce").astype("Int64")

    catalog = base.merge(
        roi,
        on=["Номер курса", "Название курса"],
        how="left",
    ).drop_duplicates(["Номер курса", "Название курса"])

    catalog["Компетенция"] = catalog["Номер курса"].map(PAGE8_COMPETENCY_BY_COURSE_ID)
    catalog["Компетенция"] = catalog["Компетенция"].combine_first(catalog["Компетенция ROI"])
    catalog["Компетенция"] = catalog["Компетенция"].combine_first(catalog["Компетенция из каталога"])
    return catalog


def _status_from_effect(effect_value: float | int | None) -> str:
    if pd.isna(effect_value):
        return "недостаточно данных"
    effect = float(effect_value)
    if effect >= 0.05:
        return "подтвержден"
    if effect >= 0.02:
        return "умеренный эффект"
    if effect >= -0.02:
        return "эффект не подтвержден"
    return "отрицательная динамика"


def _combine_effects(okk_effect, kpi_effect):
    okk_present = pd.notna(okk_effect)
    kpi_present = pd.notna(kpi_effect)
    if okk_present and kpi_present:
        return 0.35 * float(okk_effect) + 0.65 * float(kpi_effect)
    if kpi_present:
        return float(kpi_effect)
    if okk_present:
        return float(okk_effect)
    return pd.NA


def _course_status(row: pd.Series) -> str:
    has_before = (
        pd.notna(row.get("ОКК до"))
        or pd.notna(row.get("KPI до"))
    )
    has_30 = (
        pd.notna(row.get("ОКК после 30"))
        or pd.notna(row.get("KPI после 30"))
    )
    has_60 = (
        pd.notna(row.get("ОКК после 60"))
        or pd.notna(row.get("KPI после 60"))
    )
    effect = row.get("Эффект")

    if has_before and not has_30 and not has_60:
        return "в процессе замера"
    if pd.isna(effect):
        return "недостаточно данных"
    return _status_from_effect(effect)


def _build_course_lookup(learning_fact: pd.DataFrame, course_catalog: pd.DataFrame) -> pd.DataFrame:
    lookup = (
        learning_fact[
            [
                "Номер курса",
                "Название курса",
                "Развиваемая компетенция",
                "Связанная метрика",
                "Обязательный",
                "В программе адаптации",
            ]
        ]
        .drop_duplicates()
        .copy()
    )
    lookup = lookup.rename(columns={"Обязательный": "Обязательный факт"})
    lookup["Номер курса"] = pd.to_numeric(lookup["Номер курса"], errors="coerce").astype("Int64")
    lookup = lookup.merge(
        course_catalog,
        on=["Номер курса", "Название курса"],
        how="left",
    )
    lookup["Тип"] = lookup.apply(_course_type, axis=1)
    lookup["Компетенция"] = lookup["Компетенция"].combine_first(lookup["Компетенция ROI"])
    lookup["Компетенция"] = lookup["Компетенция"].combine_first(lookup["Компетенция из каталога"])
    lookup["Компетенция"] = lookup["Компетенция"].combine_first(lookup["Развиваемая компетенция"])
    return lookup


def _build_users_monthly_base(
    dim_employees: pd.DataFrame,
    merch_monthly: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    users = dim_employees.copy()
    users = users[
        users["Должность"].astype(str).str.contains("Мерч", case=False, na=False)
        & users["Проект"].astype(str).eq("H&N")
        & users["Активен"].fillna(False).eq(True)
    ].copy()
    users["Дата приёма"] = pd.to_datetime(users["Дата приёма"], errors="coerce")

    months = (
        merch_monthly[["MonthStart", "YearMonth"]]
        .drop_duplicates()
        .copy()
    )
    months["key"] = 1
    users["key"] = 1

    base = months.merge(users, on="key", how="inner").drop(columns=["key"])
    base["MonthEndExclusive"] = pd.to_datetime(base["MonthStart"], errors="coerce") + pd.DateOffset(months=1)
    base = base[
        base["Дата приёма"].isna()
        | (base["Дата приёма"] < base["MonthEndExclusive"])
    ].copy()

    teams_dir = (
        teams.replace("", pd.NA)
        .dropna(subset=["ID мерчендайзера"])
        .groupby("ID мерчендайзера", dropna=False)
        .agg(
            **{
                "ID супервайзера teams": ("ID супервайзера", "first"),
                "Супервайзер teams": ("Супервайзер", "first"),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
                "Регион BI teams": (
                    "Регион BI",
                    lambda s: s.mode().iloc[0]
                    if not s.mode().empty
                    else s.dropna().iloc[0]
                    if not s.dropna().empty
                    else pd.NA,
                ),
                "Группа региона teams": (
                    "Группа региона",
                    lambda s: s.mode().iloc[0]
                    if not s.mode().empty
                    else s.dropna().iloc[0]
                    if not s.dropna().empty
                    else pd.NA,
                ),
            }
        )
        .reset_index()
    )
    base = base.merge(
        teams_dir,
        left_on="ID сотрудника",
        right_on="ID мерчендайзера",
        how="inner",
    )

    base = base.rename(
        columns={
            "ФИО": "Сотрудник",
            "ФИО руководителя": "СВ",
        }
    )
    if "Супервайзер teams" in base.columns:
        base["СВ"] = base["Супервайзер teams"]
    if "Регион BI teams" in base.columns:
        base["Регион BI"] = base["Регион BI teams"]
    if "Группа региона teams" in base.columns:
        base["Группа региона"] = base["Группа региона teams"]
    if "Территориальный менеджер" in base.columns:
        base["Территориальный менеджер"] = (
            base["Территориальный менеджер"]
            .replace("", pd.NA)
            .fillna("Вакансия / нет ТМ")
        )
    if "ID территориального менеджера" in base.columns:
        base["ID территориального менеджера"] = (
            base["ID территориального менеджера"]
            .replace("", pd.NA)
            .fillna("NO_TM")
        )
    keep = [
        "MonthStart",
        "YearMonth",
        "ID сотрудника",
        "Сотрудник",
        "СВ",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
        "Группа региона",
        "Дата приёма",
    ]
    return base[keep].drop_duplicates(["MonthStart", "YearMonth", "ID сотрудника"])


def _build_course_summary(learning_fact: pd.DataFrame, users_monthly_base: pd.DataFrame, merch_monthly: pd.DataFrame) -> pd.DataFrame:
    course_lookup = _build_course_lookup(learning_fact, _load_course_catalog())

    work = learning_fact.copy()
    work["Номер курса"] = pd.to_numeric(work["Номер курса"], errors="coerce").astype("Int64")
    work["Прогресс норм"] = _normalize_pct(work["Прогресс"])
    work["Балл теста норм"] = _normalize_pct(work["Балл теста"])
    work["Пройдено числом"] = work["Пройдено"].eq(True).astype(int)
    work = work.merge(
        course_lookup,
        left_on=["Номер курса", "Название курса", "Развиваемая компетенция", "Связанная метрика", "Обязательный", "В программе адаптации"],
        right_on=["Номер курса", "Название курса", "Развиваемая компетенция", "Связанная метрика", "Обязательный факт", "В программе адаптации"],
        how="left",
    )

    users_scope = users_monthly_base[
        ["MonthStart", "YearMonth", "ID сотрудника", "Регион BI", "СВ"]
    ].copy().rename(
        columns={
            "MonthStart": "StartMonth",
            "YearMonth": "StartYearMonth",
            "Регион BI": "Регион BI сотрудника",
            "СВ": "СВ сотрудника",
        }
    )
    work = work.merge(
        users_scope,
        on=["StartMonth", "StartYearMonth", "ID сотрудника"],
        how="inner",
    )

    summary = (
        work.groupby(
            [
                "StartMonth",
                "StartYearMonth",
                "Номер курса",
                "Название курса",
                "Тип",
                "Компетенция",
                "Компетенция из каталога",
                "Компетенция ROI",
            ],
            dropna=False,
        )
        .agg(
            **{
                "Назначено": ("ID сотрудника", "size"),
                "Пройдено": ("Пройдено числом", "sum"),
                "Покрытие %": ("Пройдено числом", "mean"),
                "Тест %": ("Балл теста норм", "mean"),
            }
        )
        .reset_index()
        .rename(columns={"StartMonth": "MonthStart", "StartYearMonth": "YearMonth"})
    )

    merch = merch_monthly[
        [
            "MonthStart",
            "YearMonth",
            "ID мерчендайзера",
            "KPI проекта %",
            "ОКК %",
            "OSA из ОКК %",
            "PICOS из ОКК %",
            "Фрод %",
        ]
    ].copy()
    for col in ["KPI проекта %", "ОКК %", "OSA из ОКК %", "PICOS из ОКК %", "Фрод %"]:
        merch[col] = _normalize_pct(merch[col])

    completed = work[work["Пройдено"] == True].copy()
    completed = completed[completed["MonthStart"].notna()].copy()
    completed = completed.merge(
        merch,
        left_on=["ID сотрудника", "MonthStart"],
        right_on=["ID мерчендайзера", "MonthStart"],
        how="left",
        suffixes=("", "_merch"),
    )
    completed["YearMonth"] = completed["YearMonth"].combine_first(completed.get("YearMonth_merch"))

    effects_rows: list[dict] = []
    for (year_month, course_id), part in completed.groupby(["YearMonth", "Номер курса"], dropna=False):
        lookup_row = course_lookup[course_lookup["Номер курса"] == course_id]
        if lookup_row.empty:
            continue
        lookup_first = lookup_row.iloc[0]
        competency = lookup_first["Компетенция"]

        temp = part[["ID сотрудника", "MonthStart"]].copy()
        temp["m_prev"] = pd.to_datetime(temp["MonthStart"], errors="coerce") + pd.DateOffset(months=-1)
        temp["m_curr"] = pd.to_datetime(temp["MonthStart"], errors="coerce")
        temp["m_next"] = pd.to_datetime(temp["MonthStart"], errors="coerce") + pd.DateOffset(months=1)

        compare = merch[["ID мерчендайзера", "MonthStart", "ОКК %", "KPI проекта %"]].copy().rename(
            columns={"ID мерчендайзера": "ID сотрудника"}
        )

        temp_prev = temp.merge(
            compare.rename(columns={"MonthStart": "m_prev", "ОКК %": "ОКК до", "KPI проекта %": "KPI до"}),
            on=["ID сотрудника", "m_prev"],
            how="left",
        )
        temp_curr = temp.merge(
            compare.rename(columns={"MonthStart": "m_curr", "ОКК %": "ОКК 30", "KPI проекта %": "KPI 30"}),
            on=["ID сотрудника", "m_curr"],
            how="left",
        )
        temp_next = temp.merge(
            compare.rename(columns={"MonthStart": "m_next", "ОКК %": "ОКК 60", "KPI проекта %": "KPI 60"}),
            on=["ID сотрудника", "m_next"],
            how="left",
        )

        before_okk = temp_prev["ОКК до"].mean()
        before_kpi = temp_prev["KPI до"].mean()
        after_30_okk = temp_curr["ОКК 30"].mean()
        after_30_kpi = temp_curr["KPI 30"].mean()
        after_60_okk = temp_next["ОКК 60"].mean()
        after_60_kpi = temp_next["KPI 60"].mean()

        effects_rows.append(
            {
                "YearMonth": year_month,
                "Номер курса": course_id,
                "Название курса": lookup_first["Название курса"],
                "Тип": lookup_first["Тип"],
                "Компетенция": competency,
                "ОКК до": before_okk,
                "ОКК после 30": after_30_okk,
                "ОКК после 60": after_60_okk,
                "KPI до": before_kpi,
                "KPI после 30": after_30_kpi,
                "KPI после 60": after_60_kpi,
            }
        )

    effects = pd.DataFrame(effects_rows)
    if not effects.empty:
        effects["Эффект ОКК %"] = effects["ОКК после 60"] - effects["ОКК до"]
        effects["Эффект KPI %"] = effects["KPI после 60"] - effects["KPI до"]
    else:
        effects["Эффект ОКК %"] = pd.Series(dtype="float64")
        effects["Эффект KPI %"] = pd.Series(dtype="float64")

    merged = summary.merge(
        effects[
            [
                "YearMonth",
                "Номер курса",
                "ОКК до",
                "ОКК после 30",
                "ОКК после 60",
                "KPI до",
                "KPI после 30",
                "KPI после 60",
                "Эффект ОКК %",
                "Эффект KPI %",
            ]
        ],
        on=["YearMonth", "Номер курса"],
        how="left",
    )

    merged["Эффект ОКК %"] = (
        merged["ОКК после 60"].combine_first(merged["ОКК после 30"]) - merged["ОКК до"]
    )
    merged["Эффект KPI %"] = (
        merged["KPI после 60"].combine_first(merged["KPI после 30"]) - merged["KPI до"]
    )
    merged["Эффект"] = merged.apply(
        lambda row: _combine_effects(
            row.get("Эффект ОКК %"),
            row.get("Эффект KPI %"),
        ),
        axis=1,
    )
    merged["Статус"] = merged.apply(_course_status, axis=1)
    return merged


def _build_effect_trend(course_summary: pd.DataFrame) -> pd.DataFrame:
    if course_summary.empty:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Шаг",
                "Порядок",
                "ОКК %",
                "KPI %",
            ]
        )

    base = (
        course_summary.groupby(["MonthStart", "YearMonth"], dropna=False)
        .agg(
            {
                "ОКК до": "mean",
                "ОКК после 30": "mean",
                "ОКК после 60": "mean",
                "KPI до": "mean",
                "KPI после 30": "mean",
                "KPI после 60": "mean",
            }
        )
        .reset_index()
    )

    rows: list[dict] = []
    for row in base.to_dict("records"):
        rows.extend(
            [
                {
                    "MonthStart": row["MonthStart"],
                    "YearMonth": row["YearMonth"],
                    "Шаг": "До",
                    "Порядок": 1,
                    "ОКК %": row["ОКК до"],
                    "KPI %": row["KPI до"],
                },
                {
                    "MonthStart": row["MonthStart"],
                    "YearMonth": row["YearMonth"],
                    "Шаг": "После 30 дней",
                    "Порядок": 2,
                    "ОКК %": row["ОКК после 30"],
                    "KPI %": row["KPI после 30"],
                },
                {
                    "MonthStart": row["MonthStart"],
                    "YearMonth": row["YearMonth"],
                    "Шаг": "После 60 дней",
                    "Порядок": 3,
                    "ОКК %": row["ОКК после 60"],
                    "KPI %": row["KPI после 60"],
                },
            ]
        )
    return pd.DataFrame(rows)


def _build_employee_matrix(learning_fact: pd.DataFrame, users_monthly_base: pd.DataFrame) -> pd.DataFrame:
    course_lookup = _build_course_lookup(learning_fact, _load_course_catalog())
    work = learning_fact.copy()
    work["Номер курса"] = pd.to_numeric(work["Номер курса"], errors="coerce").astype("Int64")
    work = work.merge(
        course_lookup,
        left_on=["Номер курса", "Название курса", "Развиваемая компетенция", "Связанная метрика", "Обязательный", "В программе адаптации"],
        right_on=["Номер курса", "Название курса", "Развиваемая компетенция", "Связанная метрика", "Обязательный факт", "В программе адаптации"],
        how="left",
    )
    work = work[work["Обязательный"] == True].copy()
    completed = work[work["Пройдено"] == True].copy()
    completed = completed[completed["MonthStart"].notna()].copy()
    completed = completed[completed["Номер курса"].map(PAGE8_COMPETENCY_BY_COURSE_ID).notna()].copy()
    completed = (
        completed.groupby(["ID сотрудника", "Компетенция"], dropna=False)["MonthStart"]
        .min()
        .reset_index()
        .rename(columns={"MonthStart": "Дата закрытия компетенции"})
    )

    competency_cols = ["Фотоаудит", "PICOS", "Доступность", "Антифрод", "Работа с ТТ", "Базовые стандарты"]
    matrix = users_monthly_base.copy()
    for col in competency_cols:
        comp_done = completed[completed["Компетенция"] == col][["ID сотрудника", "Дата закрытия компетенции"]].copy()
        comp_done = comp_done.rename(columns={"Дата закрытия компетенции": f"{col} дата"})
        matrix = matrix.merge(comp_done, on="ID сотрудника", how="left")
        matrix[col] = (
            pd.to_datetime(matrix[f"{col} дата"], errors="coerce")
            .le(pd.to_datetime(matrix["MonthStart"], errors="coerce"))
            .map({True: 1, False: 0})
        )
        matrix[col] = matrix[col].fillna(0).astype("Int64")
        matrix = matrix.drop(columns=[f"{col} дата"], errors="ignore")

    matrix["Закрыто компетенций"] = matrix[competency_cols].fillna(0).sum(axis=1).astype("Int64")
    matrix["% закрытых компетенций"] = (matrix["Закрыто компетенций"] / len(competency_cols)).round(4)

    def _gap(row: pd.Series) -> str:
        for col in competency_cols:
            if row.get(col) != 1:
                return col
        return "Все закрыто"

    matrix["Незакрытая компетенция"] = matrix.apply(_gap, axis=1)
    return matrix


def build_page8_learning_competencies_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    learning_fact = pd.read_parquet(out_dir / "learning_fact.parquet")
    merch_monthly = pd.read_parquet(out_dir / "page3_merch_monthly_snapshot.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    users_monthly_base = _build_users_monthly_base(dim_employees, merch_monthly, teams)

    course_summary = _build_course_summary(learning_fact, users_monthly_base, merch_monthly)
    effect_trend = _build_effect_trend(course_summary)
    employee_matrix = _build_employee_matrix(learning_fact, users_monthly_base)

    for frame, numeric_columns in [
        (
            course_summary,
            [
                "YearMonth",
                "Номер курса",
                "Назначено",
                "Пройдено",
                "Покрытие %",
                "Тест %",
                "ОКК до",
                "ОКК после 30",
                "ОКК после 60",
                "KPI до",
                "KPI после 30",
                "KPI после 60",
                "Эффект ОКК %",
                "Эффект KPI %",
                "Эффект",
            ],
        ),
        (
            effect_trend,
            ["YearMonth", "Порядок", "ОКК %", "KPI %"],
        ),
        (
            employee_matrix,
            ["YearMonth", "Фотоаудит", "PICOS", "Доступность", "Антифрод", "Работа с ТТ", "Базовые стандарты", "Закрыто компетенций", "% закрытых компетенций"],
        ),
    ]:
        for column in numeric_columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

    save_parquet(course_summary, str(out_dir / "page8_learning_course_summary.parquet"))
    save_parquet(effect_trend, str(out_dir / "page8_learning_effect_trend.parquet"))
    save_parquet(employee_matrix, str(out_dir / "page8_learning_employee_matrix.parquet"))

    print(f"\n  Page8 course summary: {len(course_summary)} строк")
    print(f"  Page8 effect trend: {len(effect_trend)} строк")
    print(f"  Page8 employee matrix: {len(employee_matrix)} строк")
    return course_summary, effect_trend, employee_matrix


if __name__ == "__main__":
    build_page8_learning_competencies_data()
