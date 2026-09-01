import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.utils import first_notna, load_settings, save_parquet, normalize_pct as _normalize_pct
from scripts.staffing_utils import missing_supervisor_keys, normalize_confirmed_tm


COURSE_CATALOG_PATH = Path("config") / "courses_catalog_ЛМ_ROI_пример.xlsx"
PAGE8_PREHIRE_LEARNING_GRACE_DAYS = 0
NO_SV_ID = "NO_SV"
NO_SV_NAME = "Вакансия / нет СВ"
LEARNING_READY_MIN = 0.90

COMPETENCY_COLUMNS = [
    "Фотоаудит",
    "Доступность",
    "PICOS",
    "Антифрод",
    "Работа с ТТ",
    "Базовые стандарты",
]

ROI_COMPETENCY_TO_DASHBOARD = {
    "Базовое знание стандартов ЛМ и стартовая готовность к работе": "Базовые стандарты",
    "PICOS и корректная отчётность в OPTIMUM": "PICOS",
    "Работа с приоритетным ассортиментом": "Доступность",
    "Техническая грамотность работы с ТСД и отчётностью": "Работа с ТТ",
    "Соблюдение стандартного процесса визита": "Базовые стандарты",
    "Корректная работа с фейсами без фальсификации": "Антифрод",
    "Фотоаудит и достоверность фотоотчёта": "Фотоаудит",
    "PICOS и фотоотчётность в OPTIMUM для новичков": "PICOS",
    "Практика качественной отчётности и hard skills МЕ": "Работа с ТТ",
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


def _safe_mean(series: pd.Series):
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return clean.mean() if not clean.empty else pd.NA


def _safe_divide(numerator, denominator):
    if pd.isna(numerator) or pd.isna(denominator) or float(denominator) == 0:
        return pd.NA
    return round(float(numerator) / float(denominator), 4)


def _mode_text(series: pd.Series):
    clean = series.dropna().astype(str).str.strip()
    clean = clean[clean.ne("")]
    if clean.empty:
        return pd.NA
    counts = clean.value_counts()
    return counts.index[0]


def _tenure_months_at(month_start, hire_date):
    month_ts = pd.to_datetime(month_start, errors="coerce")
    hire_ts = pd.to_datetime(hire_date, errors="coerce")
    if pd.isna(month_ts) or pd.isna(hire_ts):
        return pd.NA
    month_end = month_ts + pd.offsets.MonthEnd(0)
    days = (month_end - hire_ts).days
    if days < 0:
        return pd.NA
    return round(days / 30.44, 1)


def _month_label(month_start):
    value = pd.to_datetime(month_start, errors="coerce")
    if pd.isna(value):
        return pd.NA
    months = {
        1: "январь",
        2: "февраль",
        3: "март",
        4: "апрель",
        5: "май",
        6: "июнь",
        7: "июль",
        8: "август",
        9: "сентябрь",
        10: "октябрь",
        11: "ноябрь",
        12: "декабрь",
    }
    return f"{months[value.month]} {value.year}"


def _short_course_name(value, limit=34):
    if pd.isna(value):
        return pd.NA
    text = " ".join(str(value).strip().split())
    for suffix in [" - 1054", "-1054"]:
        text = text.replace(suffix, "")
    text = text.replace("Welcome. Водный курс", "Welcome")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _join_short_courses(series: pd.Series, max_items=3):
    items = []
    for value in series.dropna().astype(str):
        if value and value not in items:
            items.append(value)
    if not items:
        return pd.NA
    shown = items[:max_items]
    suffix = f" +{len(items) - max_items}" if len(items) > max_items else ""
    return "; ".join(shown) + suffix


def _current_hire_learning_mask(
    frame: pd.DataFrame,
    completion_column: str = "Дата завершения для проверки",
) -> pd.Series:
    completion_date = pd.to_datetime(frame.get(completion_column), errors="coerce")
    hire_date = pd.to_datetime(frame.get("Дата приёма"), errors="coerce")
    earliest_valid_date = hire_date - pd.to_timedelta(PAGE8_PREHIRE_LEARNING_GRACE_DAYS, unit="D")
    return hire_date.isna() | (completion_date.notna() & completion_date.ge(earliest_valid_date))


def _load_course_catalog() -> pd.DataFrame:
    base = pd.read_excel(COURSE_CATALOG_PATH, sheet_name="Лист1")[
        [
            "Номер курса в КУ",
            "Название курса в КУ",
            "Какую компетенцию развивает",
            "Обязательный курс",
            "Есть тестирование или считать по прогрессу (посещению)",
            "Балл тестирования, который считается успешной сдачей теста",
        ]
    ].copy()
    base = base.rename(
        columns={
            "Номер курса в КУ": "Номер курса",
            "Название курса в КУ": "Название курса",
            "Какую компетенцию развивает": "Компетенция из каталога",
            "Обязательный курс": "Обязательный каталог",
            "Есть тестирование или считать по прогрессу (посещению)": "Метод закрытия",
            "Балл тестирования, который считается успешной сдачей теста": "Порог закрытия",
        }
    )
    base["Номер курса"] = pd.to_numeric(base["Номер курса"], errors="coerce").astype("Int64")
    base["Обязательный каталог"] = base["Обязательный каталог"].map(_to_bool)
    base["Порог закрытия"] = pd.to_numeric(base["Порог закрытия"], errors="coerce")

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
        roi[["Номер курса", "Компетенция ROI"]],
        on="Номер курса",
        how="inner",
    ).drop_duplicates(["Номер курса"])
    return catalog


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
        on="Номер курса",
        how="inner",
        suffixes=("", " из маппинга"),
    )
    lookup["Компетенция"] = lookup["Компетенция ROI"].map(ROI_COMPETENCY_TO_DASHBOARD)
    lookup["Обязательный курс"] = lookup["Обязательный факт"].fillna(False) | lookup["Обязательный каталог"].fillna(False)
    return lookup


def _required_course_success_mask(work: pd.DataFrame) -> pd.Series:
    passed = work["Пройдено"].eq(True)
    method = work.get("Метод закрытия", pd.Series("", index=work.index)).astype("string").str.lower()
    threshold = pd.to_numeric(work.get("Порог закрытия"), errors="coerce")
    test_score = pd.to_numeric(work.get("Балл теста норм"), errors="coerce")
    progress = _normalize_pct(work.get("Прогресс", pd.Series(pd.NA, index=work.index)))

    is_test = method.str.contains("тест", na=False)
    is_progress = method.str.contains("прогресс|посещ", na=False)

    test_threshold = threshold
    progress_threshold = threshold

    return passed & (
        (is_test & test_threshold.notna() & test_score.ge(test_threshold))
        | (is_progress & progress_threshold.notna() & progress.ge(progress_threshold))
    )


def _build_users_monthly_base(
    dim_employees: pd.DataFrame,
    merch_monthly: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    sv_dim = dim_employees[
        dim_employees["Должность"].astype(str).str.contains("Супервайзер", case=False, na=False)
        & dim_employees["Проект"].astype(str).eq("H&N")
        & dim_employees["Активен"].fillna(False).eq(True)
    ].copy()
    valid_sv_ids = set(sv_dim["ID сотрудника"].dropna().astype(str).str.strip())

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
    base["Стаж, мес."] = base.apply(lambda row: _tenure_months_at(row["MonthStart"], row["Дата приёма"]), axis=1)

    teams_dir = (
        teams.replace("", pd.NA)
        .dropna(subset=["ID мерчендайзера"])
        .groupby("ID мерчендайзера", dropna=False)
        .agg(
            **{
                "СВ teams": ("Супервайзер", first_notna),
                "ID супервайзера": ("ID супервайзера", first_notna),
                "ID территориального менеджера": ("ID территориального менеджера", first_notna),
                "Территориальный менеджер": ("Территориальный менеджер", first_notna),
                "Регион BI teams": ("Регион BI", first_notna),
                "Группа региона teams": ("Группа региона", first_notna),
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

    base = base.rename(columns={"ФИО": "Сотрудник"})
    base["СВ"] = base["СВ teams"].combine_first(base.get("ФИО руководителя"))
    base["ID супервайзера"] = (
        base["ID супервайзера"]
        .replace("", pd.NA)
        .combine_first(base.get("ID руководителя"))
    )
    base["Регион BI"] = base["Регион BI teams"].combine_first(base.get("Регион BI"))
    base["Группа региона"] = base["Группа региона teams"].combine_first(base.get("Группа региона"))
    base = normalize_confirmed_tm(base)
    sv_ids = base["ID супервайзера"].astype("string").str.strip()
    invalid_sv = sv_ids.isna() | sv_ids.eq("") | sv_ids.eq(NO_SV_ID) | ~sv_ids.isin(valid_sv_ids)
    if invalid_sv.any():
        base.loc[invalid_sv, "ID супервайзера"] = missing_supervisor_keys(base.loc[invalid_sv])
        base.loc[invalid_sv, "СВ"] = NO_SV_NAME
    keep = [
        "MonthStart",
        "YearMonth",
        "ID сотрудника",
        "Сотрудник",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "Дата приёма",
        "Стаж, мес.",
    ]
    return base[keep].drop_duplicates(["MonthStart", "YearMonth", "ID сотрудника"])


def _prepare_learning_with_competency(learning_fact: pd.DataFrame) -> pd.DataFrame:
    course_lookup = _build_course_lookup(learning_fact, _load_course_catalog())
    work = learning_fact.copy()
    work["Номер курса"] = pd.to_numeric(work["Номер курса"], errors="coerce").astype("Int64")
    work["Балл теста норм"] = _normalize_pct(work["Балл теста"])
    work = work.merge(
        course_lookup,
        left_on=[
            "Номер курса",
            "Название курса",
            "Развиваемая компетенция",
            "Связанная метрика",
            "Обязательный",
            "В программе адаптации",
        ],
        right_on=[
            "Номер курса",
            "Название курса",
            "Развиваемая компетенция",
            "Связанная метрика",
            "Обязательный факт",
            "В программе адаптации",
        ],
        how="left",
    )
    return work


def _prepare_merch_metrics(merch_monthly: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "MonthStart",
        "ID мерчендайзера",
        "KPI проекта %",
        "ОКК %",
    ]
    work = merch_monthly[[c for c in columns if c in merch_monthly.columns]].copy()
    work["MonthStart"] = pd.to_datetime(work["MonthStart"], errors="coerce")
    for column in ["KPI проекта %", "ОКК %"]:
        if column in work.columns:
            work[column] = _normalize_pct(work[column])
    return work


def _build_employee_matrix(learning_fact: pd.DataFrame, users_monthly_base: pd.DataFrame) -> pd.DataFrame:
    work = _prepare_learning_with_competency(learning_fact)
    work = work[
        work["Обязательный курс"].fillna(False).eq(True)
        & work["Компетенция"].isin(COMPETENCY_COLUMNS)
        & work["MonthStart"].notna()
    ].copy()
    work["Дата запуска компетенции"] = pd.to_datetime(work.get("StartMonth"), errors="coerce").combine_first(
        pd.to_datetime(work["MonthStart"], errors="coerce")
    )
    launch_by_competency = (
        work.groupby("Компетенция", dropna=False)["Дата запуска компетенции"]
        .min()
        .to_dict()
    )

    completed = (
        work[work["Пройдено"].eq(True)]
        .groupby(["ID сотрудника", "Компетенция"], dropna=False)["MonthStart"]
        .min()
        .reset_index()
        .rename(columns={"MonthStart": "Дата закрытия компетенции"})
    )

    matrix = users_monthly_base.copy()
    for column in COMPETENCY_COLUMNS:
        completed_part = completed[completed["Компетенция"].eq(column)][
            ["ID сотрудника", "Дата закрытия компетенции"]
        ].copy()
        completed_part = completed_part.rename(columns={"Дата закрытия компетенции": f"{column} дата"})
        matrix = matrix.merge(completed_part, on="ID сотрудника", how="left")
        launch_month = pd.to_datetime(launch_by_competency.get(column), errors="coerce")
        is_applicable = (
            pd.to_datetime(matrix["MonthStart"], errors="coerce").ge(launch_month)
            if pd.notna(launch_month)
            else pd.Series(False, index=matrix.index)
        )
        is_closed = (
            pd.to_datetime(matrix[f"{column} дата"], errors="coerce")
            .le(pd.to_datetime(matrix["MonthStart"], errors="coerce"))
            .fillna(False)
        )
        matrix[column] = pd.Series(pd.NA, index=matrix.index, dtype="Int64")
        matrix.loc[is_applicable, column] = is_closed.loc[is_applicable].astype(int)
        matrix = matrix.drop(columns=[f"{column} дата"], errors="ignore")

    matrix["Закрыто компетенций"] = matrix[COMPETENCY_COLUMNS].sum(axis=1, skipna=True).astype("Int64")
    matrix["Доступно компетенций"] = matrix[COMPETENCY_COLUMNS].notna().sum(axis=1).astype("Int64")
    matrix["% закрытых компетенций"] = (
        matrix["Закрыто компетенций"] / matrix["Доступно компетенций"].replace(0, pd.NA)
    ).round(4)

    def gap(row: pd.Series):
        for column in COMPETENCY_COLUMNS:
            value = row.get(column)
            if pd.isna(value):
                continue
            if int(value) != 1:
                return column
        return "Все бизнес-компетенции закрыты" if row.get("Доступно компетенций", 0) else pd.NA

    matrix["Незакрытая компетенция"] = matrix.apply(gap, axis=1)

    output_columns = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "ID сотрудника",
        "Сотрудник",
        "Стаж, мес.",
        *COMPETENCY_COLUMNS,
        "Закрыто компетенций",
        "Доступно компетенций",
        "% закрытых компетенций",
        "Незакрытая компетенция",
    ]
    return matrix[output_columns].sort_values(["MonthStart", "Регион BI", "СВ", "Сотрудник"]).reset_index(drop=True)


def _experienced_benchmark(users_monthly_base: pd.DataFrame, merch_metrics: pd.DataFrame) -> pd.DataFrame:
    experienced = users_monthly_base[
        pd.to_numeric(users_monthly_base["Стаж, мес."], errors="coerce") > 3
    ].copy()
    experienced = experienced.merge(
        merch_metrics,
        left_on=["MonthStart", "ID сотрудника"],
        right_on=["MonthStart", "ID мерчендайзера"],
        how="left",
    )
    return (
        experienced.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            **{
                "ОКК опытных %": ("ОКК %", _safe_mean),
                "KPI опытных %": ("KPI проекта %", _safe_mean),
                "Опытных сотрудников": ("ID сотрудника", "nunique"),
            }
        )
        .reset_index()
    )


def _required_learning_progress_as_of(
    learning: pd.DataFrame,
    as_of_month,
    hire_dates: pd.DataFrame | None = None,
) -> pd.DataFrame:
    month_start = pd.to_datetime(as_of_month, errors="coerce")
    if pd.isna(month_start):
        return pd.DataFrame(
            columns=[
                "ID сотрудника",
                "Назначено обязательных",
                "Успешно закрыто обязательных",
                "Незакрыто обязательных",
                "Незакрытые обучения",
                "Обязательное обучение закрыто %",
            ]
        )

    work = learning[
        learning["Обязательный курс"].fillna(False).eq(True)
    ].copy()
    work["Месяц назначения"] = pd.to_datetime(work.get("StartMonth"), errors="coerce").combine_first(
        pd.to_datetime(work["MonthStart"], errors="coerce")
    )
    work["Месяц завершения"] = pd.to_datetime(work["MonthStart"], errors="coerce")
    work["Дата завершения для проверки"] = pd.to_datetime(work.get("Дата завершения"), errors="coerce").combine_first(
        work["Месяц завершения"] + pd.offsets.MonthEnd(0)
    )
    if hire_dates is not None and not hire_dates.empty:
        hires = hire_dates[["ID сотрудника", "Дата приёма"]].drop_duplicates("ID сотрудника").copy()
        hires["Дата приёма"] = pd.to_datetime(hires["Дата приёма"], errors="coerce")
        work = work.merge(hires, on="ID сотрудника", how="left")
    else:
        work["Дата приёма"] = pd.NaT
    work["Обучение текущего найма"] = _current_hire_learning_mask(work)
    work = work[work["Месяц назначения"].notna() & work["Месяц назначения"].le(month_start)].copy()
    if work.empty:
        return pd.DataFrame(
            columns=[
                "ID сотрудника",
                "Назначено обязательных",
                "Успешно закрыто обязательных",
                "Незакрыто обязательных",
                "Незакрытые обучения",
                "Обязательное обучение закрыто %",
            ]
        )

    work["Ключ курса"] = (
        work["Номер курса"].astype("string").fillna("")
        + "|"
        + work["Название курса"].astype("string").fillna("")
    )
    assigned = (
        work[["ID сотрудника", "Ключ курса"]]
        .drop_duplicates()
        .groupby("ID сотрудника", dropna=False)["Ключ курса"]
        .nunique()
        .reset_index(name="Назначено обязательных")
    )
    successful = work[
        work["Месяц завершения"].notna()
        & work["Месяц завершения"].le(month_start)
        & work["Дата завершения для проверки"].notna()
        & work["Обучение текущего найма"].eq(True)
        & _required_course_success_mask(work)
    ].copy()
    successful = (
        successful[["ID сотрудника", "Ключ курса"]]
        .drop_duplicates()
        .groupby("ID сотрудника", dropna=False)["Ключ курса"]
        .nunique()
        .reset_index(name="Успешно закрыто обязательных")
    )
    assigned_courses = work[["ID сотрудника", "Ключ курса", "Название курса"]].drop_duplicates()
    successful_courses = (
        work[
            work["Месяц завершения"].notna()
            & work["Месяц завершения"].le(month_start)
            & work["Дата завершения для проверки"].notna()
            & work["Обучение текущего найма"].eq(True)
            & _required_course_success_mask(work)
        ][["ID сотрудника", "Ключ курса"]]
        .drop_duplicates()
        .assign(_success=1)
    )
    missing = assigned_courses.merge(successful_courses, on=["ID сотрудника", "Ключ курса"], how="left")
    missing = missing[missing["_success"].isna()].copy()
    missing["Курс коротко"] = missing["Название курса"].map(_short_course_name)
    missing_list = (
        missing.groupby("ID сотрудника", dropna=False)["Курс коротко"]
        .apply(_join_short_courses)
        .reset_index(name="Незакрытые обучения")
    )
    progress = assigned.merge(successful, on="ID сотрудника", how="left")
    progress["Успешно закрыто обязательных"] = progress["Успешно закрыто обязательных"].fillna(0).astype("Int64")
    progress["Незакрыто обязательных"] = (
        progress["Назначено обязательных"] - progress["Успешно закрыто обязательных"]
    ).astype("Int64")
    progress = progress.merge(missing_list, on="ID сотрудника", how="left")
    progress["Обязательное обучение закрыто %"] = (
        progress["Успешно закрыто обязательных"] / progress["Назначено обязательных"].replace(0, pd.NA)
    ).round(4)
    return progress


def _adaptation_status(row: pd.Series) -> str:
    okk_first = row.get("ОКК 1-й месяц")
    okk_second = row.get("ОКК 2-й месяц")
    learning_score = row.get("Обязательное обучение закрыто %")
    gap = row.get("Разрыв с опытными")

    if pd.isna(learning_score) or pd.isna(okk_first) or pd.isna(okk_second):
        return "Мало данных"
    if float(learning_score) < 0.90 or float(okk_second) < 0.50 or float(okk_second) < float(okk_first):
        return "Нужна поддержка"
    if (
        float(learning_score) >= 0.95
        and float(okk_second) >= 0.60
        and pd.notna(gap)
        and float(gap) >= -0.05
    ):
        return "Вышел на уровень"
    return "Есть прогресс"


def _required_learning_status(value):
    if pd.isna(value):
        return pd.NA
    score = float(value)
    if score >= 1:
        return "Все обязательные курсы закрыты"
    if score >= 0.95:
        return "Почти все обязательные курсы закрыты"
    return "Есть незакрытые обязательные курсы"


def _newcomer_readiness(row: pd.Series):
    required_columns = ["Обязательное обучение закрыто %", "ОКК 1-й месяц", "ОКК 2-й месяц"]
    if any(pd.isna(row.get(column)) for column in required_columns):
        return pd.NA

    learning_score = float(row.get("Обязательное обучение закрыто %"))
    okk_first = float(row.get("ОКК 1-й месяц"))
    okk_second = float(row.get("ОКК 2-й месяц"))
    gap = row.get("Разрыв с опытными")
    operation_checks = [
        okk_second >= 0.60,
        okk_second >= okk_first,
        pd.notna(gap) and float(gap) >= -0.05,
    ]
    operation_readiness = sum(1 for check in operation_checks if check) / len(operation_checks)
    return round(min(learning_score, operation_readiness), 4)


def _build_newcomer_adaptation(
    learning_fact: pd.DataFrame,
    users_monthly_base: pd.DataFrame,
    merch_monthly: pd.DataFrame,
) -> pd.DataFrame:
    learning = _prepare_learning_with_competency(learning_fact)
    learning["Месяц завершения"] = pd.to_datetime(learning["MonthStart"], errors="coerce")
    learning["Дата завершения для проверки"] = pd.to_datetime(learning.get("Дата завершения"), errors="coerce").combine_first(
        learning["Месяц завершения"] + pd.offsets.MonthEnd(0)
    )
    learning["Закрыл обязательное обучение в месяце"] = (
        learning["Обязательный курс"].fillna(False).eq(True)
        & learning["Месяц завершения"].notna()
        & _required_course_success_mask(learning)
    )

    merch_metrics = _prepare_merch_metrics(merch_monthly)
    benchmark = _experienced_benchmark(users_monthly_base, merch_metrics)

    rows: list[pd.DataFrame] = []
    months = users_monthly_base[["MonthStart", "YearMonth"]].drop_duplicates().copy()
    for _, month_row in months.iterrows():
        current_month = pd.to_datetime(month_row["MonthStart"], errors="coerce")
        if pd.isna(current_month):
            continue
        test_month = current_month + pd.DateOffset(months=-1)
        current_users = users_monthly_base[
            users_monthly_base["MonthStart"].eq(current_month)
            & (pd.to_numeric(users_monthly_base["Стаж, мес."], errors="coerce") < 3)
        ].copy()
        if current_users.empty:
            continue

        tested_rows = learning[
            learning["Закрыл обязательное обучение в месяце"].eq(True)
            & learning["Месяц завершения"].eq(test_month)
        ].merge(
            current_users[["ID сотрудника", "Дата приёма"]],
            on="ID сотрудника",
            how="inner",
        )
        tested_rows = tested_rows[_current_hire_learning_mask(tested_rows)].copy()
        tested_employees = (
            tested_rows
            .groupby("ID сотрудника", dropna=False)["Дата завершения для проверки"]
            .max()
            .reset_index()
            .rename(columns={"Дата завершения для проверки": "Дата последнего обучения"})
        )
        if tested_employees.empty:
            continue

        part = current_users.merge(tested_employees, on="ID сотрудника", how="inner")
        if part.empty:
            continue
        learning_progress = _required_learning_progress_as_of(
            learning,
            test_month,
            part[["ID сотрудника", "Дата приёма"]],
        )
        learning_progress["Месяц обучения дата"] = test_month
        part = part.merge(learning_progress, on="ID сотрудника", how="left")
        if part.empty:
            continue

        first_metrics = merch_metrics[
            merch_metrics["MonthStart"].eq(test_month)
        ][["ID мерчендайзера", "ОКК %", "KPI проекта %"]].rename(
            columns={
                "ID мерчендайзера": "ID сотрудника",
                "ОКК %": "ОКК 1-й месяц",
                "KPI проекта %": "KPI 1-й месяц",
            }
        )
        second_metrics = merch_metrics[
            merch_metrics["MonthStart"].eq(current_month)
        ][["ID мерчендайзера", "ОКК %", "KPI проекта %"]].rename(
            columns={
                "ID мерчендайзера": "ID сотрудника",
                "ОКК %": "ОКК 2-й месяц",
                "KPI проекта %": "KPI 2-й месяц",
            }
        )
        part = part.merge(first_metrics, on="ID сотрудника", how="left")
        part = part.merge(second_metrics, on="ID сотрудника", how="left")
        part = part.merge(
            benchmark[benchmark["MonthStart"].eq(current_month)][
                ["MonthStart", "Регион BI", "ОКК опытных %", "KPI опытных %", "Опытных сотрудников"]
            ],
            on=["MonthStart", "Регион BI"],
            how="left",
        )
        part["Месяц обучения"] = part["Месяц обучения дата"].map(_month_label)
        part["Динамика ОКК"] = part["ОКК 2-й месяц"] - part["ОКК 1-й месяц"]
        part["Разрыв с опытными"] = part["ОКК 2-й месяц"] - part["ОКК опытных %"]
        part["Статус обязательного обучения"] = part["Обязательное обучение закрыто %"].map(_required_learning_status)
        part["Готовность новичка %"] = part.apply(_newcomer_readiness, axis=1)
        part["Статус адаптации"] = part.apply(_adaptation_status, axis=1)
        rows.append(part)

    if not rows:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Месяц обучения",
                "Регион BI",
                "ID территориального менеджера",
                "Территориальный менеджер",
                "ID супервайзера",
                "СВ",
                "ID сотрудника",
                "Новичок",
                "Дата приёма",
                "Стаж, мес.",
                "Назначено обязательных",
                "Успешно закрыто обязательных",
                "Незакрыто обязательных",
                "Незакрытые обучения",
                "Обязательное обучение закрыто %",
                "Статус обязательного обучения",
                "ОКК 1-й месяц",
                "ОКК 2-й месяц",
                "Динамика ОКК",
                "ОКК опытных %",
                "Разрыв с опытными",
                "KPI 1-й месяц",
                "KPI 2-й месяц",
                "KPI опытных %",
                "Опытных сотрудников",
                "Готовность новичка %",
                "Статус адаптации",
            ]
        )

    result = pd.concat(rows, ignore_index=True)
    result = result.rename(columns={"Сотрудник": "Новичок"})
    output_columns = [
        "MonthStart",
        "YearMonth",
        "Месяц обучения",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "ID сотрудника",
        "Новичок",
        "Дата приёма",
        "Стаж, мес.",
        "Назначено обязательных",
        "Успешно закрыто обязательных",
        "Незакрыто обязательных",
        "Незакрытые обучения",
        "Обязательное обучение закрыто %",
        "Статус обязательного обучения",
        "ОКК 1-й месяц",
        "ОКК 2-й месяц",
        "Динамика ОКК",
        "ОКК опытных %",
        "Разрыв с опытными",
        "KPI 1-й месяц",
        "KPI 2-й месяц",
        "KPI опытных %",
        "Опытных сотрудников",
        "Готовность новичка %",
        "Статус адаптации",
    ]
    return result[output_columns].sort_values(["MonthStart", "Регион BI", "СВ", "Новичок"]).reset_index(drop=True)


def _build_adaptation_trend(newcomers: pd.DataFrame) -> pd.DataFrame:
    group_columns = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
    ]
    if newcomers.empty:
        return pd.DataFrame(
            columns=[
                *group_columns,
                "Шаг",
                "Порядок",
                "ОКК %",
                "KPI %",
                "Сотрудников",
            ]
        )

    rows: list[dict] = []
    for group_values, part in newcomers.groupby(group_columns, dropna=False):
        base = dict(zip(group_columns, group_values, strict=False))
        rows.extend(
            [
                {
                    **base,
                    "Шаг": "Новички: 1-й месяц",
                    "Порядок": 1,
                    "ОКК %": _safe_mean(part["ОКК 1-й месяц"]),
                    "KPI %": _safe_mean(part["KPI 1-й месяц"]),
                    "Сотрудников": int(part["ID сотрудника"].nunique()),
                },
                {
                    **base,
                    "Шаг": "Новички: 2-й месяц",
                    "Порядок": 2,
                    "ОКК %": _safe_mean(part["ОКК 2-й месяц"]),
                    "KPI %": _safe_mean(part["KPI 2-й месяц"]),
                    "Сотрудников": int(part["ID сотрудника"].nunique()),
                },
                {
                    **base,
                    "Шаг": "Опытные >3 мес.",
                    "Порядок": 3,
                    "ОКК %": _safe_mean(part["ОКК опытных %"]),
                    "KPI %": _safe_mean(part["KPI опытных %"]),
                    "Сотрудников": int(pd.to_numeric(part["Опытных сотрудников"], errors="coerce").fillna(0).max())
                    if "Опытных сотрудников" in part.columns
                    else pd.NA,
                },
            ]
        )
    return pd.DataFrame(rows).sort_values(["MonthStart", "Регион BI", "Территориальный менеджер", "СВ", "Порядок"]).reset_index(drop=True)


def build_page8_learning_competencies_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    learning_fact = pd.read_parquet(out_dir / "learning_fact.parquet")
    merch_monthly = pd.read_parquet(out_dir / "page3_merch_monthly_snapshot.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")

    users_monthly_base = _build_users_monthly_base(dim_employees, merch_monthly, teams)
    employee_matrix = _build_employee_matrix(learning_fact, users_monthly_base)
    newcomer_adaptation = _build_newcomer_adaptation(
        learning_fact,
        users_monthly_base,
        merch_monthly,
    )
    adaptation_trend = _build_adaptation_trend(newcomer_adaptation)

    numeric_frames = [
        (
            newcomer_adaptation,
            [
                "YearMonth",
                "Стаж, мес.",
                "Назначено обязательных",
                "Успешно закрыто обязательных",
                "Незакрыто обязательных",
                "Обязательное обучение закрыто %",
                "ОКК 1-й месяц",
                "ОКК 2-й месяц",
                "Динамика ОКК",
                "ОКК опытных %",
                "Разрыв с опытными",
                "KPI 1-й месяц",
                "KPI 2-й месяц",
                "KPI опытных %",
                "Готовность новичка %",
            ],
        ),
        (
            adaptation_trend,
            ["YearMonth", "Порядок", "ОКК %", "KPI %", "Сотрудников"],
        ),
        (
            employee_matrix,
            [
                "YearMonth",
                "Стаж, мес.",
                *COMPETENCY_COLUMNS,
                "Закрыто компетенций",
                "Доступно компетенций",
                "% закрытых компетенций",
            ],
        ),
    ]
    for frame, numeric_columns in numeric_frames:
        for column in numeric_columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

    save_parquet(newcomer_adaptation, str(out_dir / "page8_learning_course_summary.parquet"))
    save_parquet(adaptation_trend, str(out_dir / "page8_learning_effect_trend.parquet"))
    save_parquet(employee_matrix, str(out_dir / "page8_learning_employee_matrix.parquet"))

    print(f"\n  Page8 newcomer adaptation: {len(newcomer_adaptation)} строк")
    print(f"  Page8 adaptation trend: {len(adaptation_trend)} строк")
    print(f"  Page8 employee matrix: {len(employee_matrix)} строк")
    return newcomer_adaptation, adaptation_trend, employee_matrix


if __name__ == "__main__":
    build_page8_learning_competencies_data()
