import sys
from pathlib import Path
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import get_active_users_scope, get_as_of_date, load_region_map, load_settings, normalize_dim, save_parquet
from scripts.staffing_utils import is_tm_role, normalize_confirmed_tm


REPORT_START_YEARMONTH = load_settings()["reporting"]["start_yearmonth"]
REPORT_LEVEL_ORDER = {
    "Регион": 1,
    "ТМ": 2,
    "СВ": 3,
}
NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"


def _latest_source_month(*frames: pd.DataFrame) -> pd.Timestamp:
    current_month = get_as_of_date().to_period("M").to_timestamp()
    month_values: list[pd.Series] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for column in ["MonthStart", "MonthStart найм", "MonthStart увольнение"]:
            if column not in frame.columns:
                continue
            values = pd.to_datetime(frame[column], errors="coerce").dropna()
            if values.empty:
                continue
            values = values.dt.to_period("M").dt.to_timestamp()
            month_values.append(values[values.le(current_month)])

    if not month_values:
        return current_month
    months = pd.concat(month_values, ignore_index=True).dropna()
    if months.empty:
        return current_month
    return months.max()


def _users_snapshot_month() -> pd.Timestamp:
    return get_as_of_date().to_period("M").to_timestamp()


def _with_level_columns(df: pd.DataFrame, level: str) -> pd.DataFrame:
    result = df.copy()
    result["Уровень анализа"] = level
    if level == "Регион":
        result["ID территориального менеджера"] = pd.NA
        result["Территориальный менеджер"] = pd.NA
        result["ID супервайзера"] = pd.NA
        result["Супервайзер"] = pd.NA
    elif level == "ТМ":
        result["ID супервайзера"] = pd.NA
        result["Супервайзер"] = pd.NA
    return result


def _aggregate_levels(
    df: pd.DataFrame,
    metrics: dict[str, tuple[str, str]],
    *,
    month_col: str = "MonthStart",
    year_month_col: str = "YearMonth",
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    region = (
        df.dropna(subset=[month_col, "Регион BI"])
        .groupby([month_col, year_month_col, "Регион BI"], dropna=False)
        .agg(**metrics)
        .reset_index()
    )
    frames.append(_with_level_columns(region, "Регион"))

    if "ID территориального менеджера" in df.columns:
        tm = (
            df.dropna(subset=[month_col, "Регион BI", "ID территориального менеджера"])
            .groupby(
                [month_col, year_month_col, "Регион BI", "ID территориального менеджера", "Территориальный менеджер"],
                dropna=False,
            )
            .agg(**metrics)
            .reset_index()
        )
        frames.append(_with_level_columns(tm, "ТМ"))

    if "ID супервайзера" in df.columns:
        sv = (
            df.dropna(subset=[month_col, "Регион BI", "ID супервайзера"])
            .groupby(
                [
                    month_col,
                    year_month_col,
                    "Регион BI",
                    "ID территориального менеджера",
                    "Территориальный менеджер",
                    "ID супервайзера",
                    "Супервайзер",
                ],
                dropna=False,
            )
            .agg(**metrics)
            .reset_index()
        )
        frames.append(_with_level_columns(sv, "СВ"))

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _build_active_headcount(dim: pd.DataFrame, teams: pd.DataFrame, active_month: pd.Timestamp) -> pd.DataFrame:
    scope = get_active_users_scope(dim)
    active = scope["frame"].copy()
    current_month = pd.to_datetime(active_month, errors="coerce").to_period("M").to_timestamp()

    merch = teams.replace("", pd.NA).copy()
    merch["MonthStart"] = current_month
    merch["YearMonth"] = current_month.year * 100 + current_month.month
    merch["Открытых вакансий"] = 0
    merch["Закрытых вакансий"] = 0
    merch["Успешно закрытых вакансий"] = 0
    merch["Отмененных вакансий"] = 0
    merch["Средний срок закрытия вакансии, дни"] = pd.NA
    merch["Нанято"] = 0
    merch["Уволено"] = 0
    merch["Активных МЕ"] = 1
    merch["Активных СВ"] = 0
    merch["Активных ТМ"] = 0
    merch["Открытых вакансий МЕ"] = 0
    merch["Открытых вакансий СВ"] = 0
    merch["Приостановленных вакансий"] = 0

    teams_sv = (
        teams.replace("", pd.NA)
        .dropna(subset=["ID супервайзера"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "ID территориального менеджера teams": ("ID территориального менеджера", "first"),
                "Территориальный менеджер teams": ("Территориальный менеджер", "first"),
                "Регион BI teams": ("Регион BI", "first"),
            }
        )
        .reset_index()
        .rename(columns={"ID супервайзера": "employee_id"})
    )
    sv_people = active[active["position"].astype(str).str.lower().str.contains("супервайзер", na=False)].copy()
    sv_people = sv_people.merge(teams_sv, on="employee_id", how="left")
    if "Регион BI" not in sv_people.columns:
        sv_people["Регион BI"] = pd.NA
    sv_people["Регион BI"] = sv_people["Регион BI teams"].combine_first(sv_people["Регион BI"])
    sv_people["MonthStart"] = current_month
    sv_people["YearMonth"] = current_month.year * 100 + current_month.month
    sv_people["ID территориального менеджера"] = sv_people["ID территориального менеджера teams"].replace("", pd.NA)
    sv_people["Территориальный менеджер"] = sv_people["Территориальный менеджер teams"].replace("", pd.NA)
    sv_people = normalize_confirmed_tm(sv_people)
    sv_people["ID супервайзера"] = sv_people["employee_id"]
    sv_people["Супервайзер"] = sv_people["full_name"]
    sv_people["Открытых вакансий"] = 0
    sv_people["Закрытых вакансий"] = 0
    sv_people["Успешно закрытых вакансий"] = 0
    sv_people["Отмененных вакансий"] = 0
    sv_people["Средний срок закрытия вакансии, дни"] = pd.NA
    sv_people["Нанято"] = 0
    sv_people["Уволено"] = 0
    sv_people["Активных МЕ"] = 0
    sv_people["Активных СВ"] = 1
    sv_people["Активных ТМ"] = 0
    sv_people["Открытых вакансий МЕ"] = 0
    sv_people["Открытых вакансий СВ"] = 0
    sv_people["Приостановленных вакансий"] = 0

    tm_people = active[active["position"].map(is_tm_role)].copy()
    if "Регион BI" not in tm_people.columns:
        tm_people["Регион BI"] = pd.NA
    tm_people["MonthStart"] = current_month
    tm_people["YearMonth"] = current_month.year * 100 + current_month.month
    tm_people["ID территориального менеджера"] = tm_people["employee_id"]
    tm_people["Территориальный менеджер"] = tm_people["full_name"]
    tm_people["ID супервайзера"] = pd.NA
    tm_people["Супервайзер"] = pd.NA
    tm_people["Открытых вакансий"] = 0
    tm_people["Закрытых вакансий"] = 0
    tm_people["Успешно закрытых вакансий"] = 0
    tm_people["Отмененных вакансий"] = 0
    tm_people["Средний срок закрытия вакансии, дни"] = pd.NA
    tm_people["Нанято"] = 0
    tm_people["Уволено"] = 0
    tm_people["Активных МЕ"] = 0
    tm_people["Активных СВ"] = 0
    tm_people["Активных ТМ"] = 1
    tm_people["Открытых вакансий МЕ"] = 0
    tm_people["Открытых вакансий СВ"] = 0
    tm_people["Приостановленных вакансий"] = 0

    metrics = {
        "Активных МЕ": ("Активных МЕ", "sum"),
        "Активных СВ": ("Активных СВ", "sum"),
        "Активных ТМ": ("Активных ТМ", "sum"),
        "Открытых вакансий": ("Открытых вакансий", "sum"),
        "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
        "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
        "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
        "Закрытых вакансий": ("Закрытых вакансий", "sum"),
        "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
        "Отмененных вакансий": ("Отмененных вакансий", "sum"),
        "Нанято": ("Нанято", "sum"),
        "Уволено": ("Уволено", "sum"),
    }

    return pd.concat(
        [
            _aggregate_levels(merch, metrics),
            _aggregate_levels(sv_people, metrics),
            _aggregate_levels(tm_people, metrics),
        ],
        ignore_index=True,
    )


def _build_historical_headcount(hr: pd.DataFrame, active_month: pd.Timestamp) -> pd.DataFrame:
    if hr is None or hr.empty:
        return pd.DataFrame()

    work = hr.replace("", pd.NA).copy()
    required = {
        "Сотрудник",
        "Роль",
        "Дата приема",
        "Дата увольнения",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
    }
    if not required.issubset(work.columns):
        return pd.DataFrame()

    work["Дата приема"] = pd.to_datetime(work["Дата приема"], errors="coerce")
    work["Дата увольнения"] = pd.to_datetime(work["Дата увольнения"], errors="coerce")
    work = work[
        work["Роль"].isin(["МЕ", "СВ"])
        & work["Дата приема"].notna()
        & work["Регион BI"].notna()
    ].copy()
    if work.empty:
        return pd.DataFrame()

    start_year = REPORT_START_YEARMONTH // 100
    start_month = REPORT_START_YEARMONTH % 100
    first_month = pd.Timestamp(year=start_year, month=start_month, day=1)
    last_month = pd.to_datetime(active_month, errors="coerce").to_period("M").to_timestamp() - pd.DateOffset(months=1)
    if last_month < first_month:
        return pd.DataFrame()

    employee_id = work.get("ID сотрудника", pd.Series(index=work.index, dtype="object"))
    employee_id = employee_id.astype("string").str.strip().fillna("NO_ID")
    employee_name = work["Сотрудник"].astype("string").str.strip().fillna("NO_NAME")
    work["Ключ кадрового эпизода"] = (
        employee_id
        + "|"
        + employee_name
        + "|"
        + work["Дата приема"].dt.strftime("%Y-%m-%d")
        + "|"
        + work["Роль"].astype("string")
    )
    work = work.drop_duplicates("Ключ кадрового эпизода", keep="last")

    frames: list[pd.DataFrame] = []
    for month_start in pd.date_range(first_month, last_month, freq="MS"):
        month_end = month_start + pd.offsets.MonthEnd(0)
        active = work[
            work["Дата приема"].le(month_end)
            & (work["Дата увольнения"].isna() | work["Дата увольнения"].gt(month_end))
        ].copy()
        if active.empty:
            continue

        active["MonthStart"] = month_start
        active["YearMonth"] = month_start.year * 100 + month_start.month
        active["Активных МЕ"] = active["Роль"].eq("МЕ").astype(int)
        active["Активных СВ"] = active["Роль"].eq("СВ").astype(int)
        active["Активных ТМ"] = 0
        for column in [
            "Открытых вакансий",
            "Открытых вакансий МЕ",
            "Открытых вакансий СВ",
            "Приостановленных вакансий",
            "Закрытых вакансий",
            "Успешно закрытых вакансий",
            "Отмененных вакансий",
            "Нанято",
            "Уволено",
        ]:
            active[column] = 0
        active["Средний срок закрытия вакансии, дни"] = pd.NA
        frames.append(
            _aggregate_levels(
                active,
                {
                    "Активных МЕ": ("Активных МЕ", "sum"),
                    "Активных СВ": ("Активных СВ", "sum"),
                    "Активных ТМ": ("Активных ТМ", "sum"),
                    "Открытых вакансий": ("Открытых вакансий", "sum"),
                    "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
                    "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
                    "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
                    "Закрытых вакансий": ("Закрытых вакансий", "sum"),
                    "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
                    "Отмененных вакансий": ("Отмененных вакансий", "sum"),
                    "Нанято": ("Нанято", "sum"),
                    "Уволено": ("Уволено", "sum"),
                },
            )
        )

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _build_open_vacancy_monthly(
    open_vacancies: pd.DataFrame,
    closed_vacancies: pd.DataFrame | None = None,
    active_month: pd.Timestamp | None = None,
) -> pd.DataFrame:
    vacancy_frames: list[pd.DataFrame] = []

    if open_vacancies is not None and not open_vacancies.empty:
        current = open_vacancies.copy()
        current["Дата закрытия"] = pd.NaT
        current["Источник вакансии"] = "текущий список открытых вакансий"
        vacancy_frames.append(current)

    if closed_vacancies is not None and not closed_vacancies.empty:
        history = closed_vacancies.copy()
        history["Приостановлена"] = False
        history["Источник вакансии"] = "история закрытых вакансий"
        vacancy_frames.append(history)

    if not vacancy_frames:
        return pd.DataFrame()

    work = pd.concat(vacancy_frames, ignore_index=True, sort=False)
    required_columns = [
        "ID вакансии",
        "Дата открытия",
        "Дата закрытия",
        "Роль вакансии",
        "Приостановлена",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
    ]
    for column in required_columns:
        if column not in work.columns:
            work[column] = pd.NA

    work["ID вакансии"] = work["ID вакансии"].astype("string").str.strip()
    work["ID вакансии"] = work["ID вакансии"].replace({"": pd.NA, "<NA>": pd.NA, "nan": pd.NA})
    work["Дата открытия"] = pd.to_datetime(work["Дата открытия"], errors="coerce")
    work["Дата закрытия"] = pd.to_datetime(work["Дата закрытия"], errors="coerce")
    work = work[
        work["ID вакансии"].notna()
        & work["Дата открытия"].notna()
        & work["Регион BI"].notna()
    ].copy()
    if work.empty:
        return pd.DataFrame()

    work["_закрыта"] = work["Дата закрытия"].notna().astype(int)
    work = (
        work.sort_values(
            ["ID вакансии", "_закрыта", "Дата закрытия"],
            na_position="first",
        )
        .drop_duplicates("ID вакансии", keep="last")
        .drop(columns="_закрыта")
    )

    end_month = pd.to_datetime(
        active_month if active_month is not None else _latest_source_month(open_vacancies, closed_vacancies),
        errors="coerce",
    ).to_period("M").to_timestamp()
    first_month = pd.Timestamp(
        year=REPORT_START_YEARMONTH // 100,
        month=REPORT_START_YEARMONTH % 100,
        day=1,
    )
    if pd.isna(end_month) or end_month < first_month:
        return pd.DataFrame()

    monthly_frames: list[pd.DataFrame] = []
    for month_start in pd.date_range(first_month, end_month, freq="MS"):
        month_end = month_start + pd.offsets.MonthEnd(0)
        active_mask = work["Дата открытия"].le(month_end) & (
            work["Дата закрытия"].isna() | work["Дата закрытия"].gt(month_end)
        )
        month_work = work.loc[active_mask].copy()
        if month_work.empty:
            continue

        month_work["MonthStart"] = month_start
        month_work["YearMonth"] = month_start.year * 100 + month_start.month
        month_work["Источник вакансии"] = "незакрытый остаток на конец месяца"
        month_work["Открытых вакансий"] = 1
        month_work["Открытых вакансий МЕ"] = month_work["Роль вакансии"].eq("МЕ").astype(int)
        month_work["Открытых вакансий СВ"] = month_work["Роль вакансии"].eq("СВ").astype(int)
        month_work["Приостановленных вакансий"] = (
            month_work["Приостановлена"].fillna(False).astype(int)
            if month_start == end_month
            else 0
        )
        month_work["Закрытых вакансий"] = 0
        month_work["Успешно закрытых вакансий"] = 0
        month_work["Отмененных вакансий"] = 0
        month_work["Средний срок закрытия вакансии, дни"] = pd.NA
        month_work["Нанято"] = 0
        month_work["Уволено"] = 0
        month_work["Активных МЕ"] = 0
        month_work["Активных СВ"] = 0
        month_work["Активных ТМ"] = 0
        monthly_frames.append(month_work)

    return pd.concat(monthly_frames, ignore_index=True) if monthly_frames else pd.DataFrame()


def _build_reporting_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    report = snapshot.copy()
    report["YearMonth"] = pd.to_numeric(report["YearMonth"], errors="coerce").astype("Int64")
    if "Группа региона" not in report.columns:
        region_map = load_region_map()
        group_lookup = (
            region_map.sort_values(["canonical_region", "region_group"])
            .drop_duplicates("canonical_region")
            .set_index("canonical_region")["region_group"]
            .to_dict()
        )
        report["Группа региона"] = report["Регион BI"].map(group_lookup)
    report = report[
        report["YearMonth"].ge(REPORT_START_YEARMONTH)
        & report["Группа региона"].eq("core")
    ].copy()

    region_mask = report["Уровень анализа"].eq("Регион")
    tm_mask = report["Уровень анализа"].eq("ТМ")
    report.loc[region_mask, ["ID территориального менеджера", "Территориальный менеджер"]] = pd.NA
    report.loc[region_mask | tm_mask, ["ID супервайзера", "Супервайзер"]] = pd.NA

    numeric_columns = [
        "Активных МЕ",
        "Активных СВ",
        "Активных ТМ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Закрытых вакансий",
        "Успешно закрытых вакансий",
        "Отмененных вакансий",
        "Средний срок закрытия вакансии, дни",
        "Нанято",
        "Уволено",
        "Доля отмен вакансий %",
        "Доля успешных закрытий %",
        "Доля вакансий к активным МЕ %",
        "Кадровый отток",
        "Доля кадрового оттока %",
        "Чистый отток",
        "Баланс персонала",
    ]
    for column in numeric_columns:
        if column in report.columns:
            report[column] = pd.to_numeric(report[column], errors="coerce")

    count_columns = [
        "Активных МЕ",
        "Активных СВ",
        "Активных ТМ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Закрытых вакансий",
        "Успешно закрытых вакансий",
        "Отмененных вакансий",
        "Нанято",
        "Уволено",
        "Кадровый отток",
        "Чистый отток",
        "Баланс персонала",
    ]
    for column in count_columns:
        if column in report.columns:
            report[column] = report[column].fillna(0).astype("int64")

    report["Порядок уровня"] = report["Уровень анализа"].map(REPORT_LEVEL_ORDER).astype("int64")
    report["Период отчета"] = report["MonthStart"].dt.strftime("%Y-%m")
    report["Ключ уровня"] = (
        report["Уровень анализа"].astype(str)
        + "|"
        + report["Регион BI"].astype(str)
        + "|"
        + report["ID территориального менеджера"].fillna("").astype(str)
        + "|"
        + report["ID супервайзера"].fillna("").astype(str)
    )
    report["Ключ регион-месяц"] = report["YearMonth"].astype("Int64").astype(str) + "|" + report["Регион BI"].astype(str)

    return report.sort_values(
        ["MonthStart", "Порядок уровня", "Регион BI", "Территориальный менеджер", "Супервайзер"],
        na_position="last",
    ).reset_index(drop=True)


def build_org_staffing_monthly_snapshot() -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    dim = pd.read_parquet(settings["sources"]["users"]["output"])
    dim = normalize_dim(dim)
    teams = pd.read_parquet(settings["sources"]["teams"]["output"])
    open_vacancies = pd.read_parquet(out_dir / "fact_open_vacancies.parquet")
    closed_vacancies = pd.read_parquet(out_dir / "fact_closed_vacancies.parquet")
    hr = pd.read_parquet(out_dir / "fact_hr_registry.parquet")

    active_month = _users_snapshot_month()
    current_headcount = _build_active_headcount(dim, teams, active_month)
    historical_headcount = _build_historical_headcount(hr, active_month)
    headcount = pd.concat([historical_headcount, current_headcount], ignore_index=True)

    open_work = _build_open_vacancy_monthly(open_vacancies, closed_vacancies, active_month)
    open_metrics = {
        "Открытых вакансий": ("Открытых вакансий", "sum"),
        "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
        "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
        "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
        "Закрытых вакансий": ("Закрытых вакансий", "sum"),
        "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
        "Отмененных вакансий": ("Отмененных вакансий", "sum"),
        "Нанято": ("Нанято", "sum"),
        "Уволено": ("Уволено", "sum"),
        "Активных МЕ": ("Активных МЕ", "sum"),
        "Активных СВ": ("Активных СВ", "sum"),
        "Активных ТМ": ("Активных ТМ", "sum"),
    }
    open_agg = _aggregate_levels(open_work, open_metrics) if not open_work.empty else pd.DataFrame()

    closed_work = closed_vacancies.copy()
    closed_work["Открытых вакансий"] = 0
    closed_work["Открытых вакансий МЕ"] = 0
    closed_work["Открытых вакансий СВ"] = 0
    closed_work["Приостановленных вакансий"] = 0
    closed_work["Закрытых вакансий"] = 1
    closed_work["Успешно закрытых вакансий"] = closed_work["Категория закрытия"].eq("Успешно закрыта").astype(int)
    closed_work["Отмененных вакансий"] = closed_work["Категория закрытия"].eq("Отменена").astype(int)
    closed_work["Нанято"] = 0
    closed_work["Уволено"] = 0
    closed_work["Активных МЕ"] = 0
    closed_work["Активных СВ"] = 0
    closed_work["Активных ТМ"] = 0
    closed_metrics = {
        "Открытых вакансий": ("Открытых вакансий", "sum"),
        "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
        "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
        "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
        "Закрытых вакансий": ("Закрытых вакансий", "sum"),
        "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
        "Отмененных вакансий": ("Отмененных вакансий", "sum"),
        "Средний срок закрытия вакансии, дни": ("Дней в работе", "mean"),
        "Нанято": ("Нанято", "sum"),
        "Уволено": ("Уволено", "sum"),
        "Активных МЕ": ("Активных МЕ", "sum"),
        "Активных СВ": ("Активных СВ", "sum"),
        "Активных ТМ": ("Активных ТМ", "sum"),
    }
    closed_agg = _aggregate_levels(closed_work, closed_metrics)

    hires = hr.dropna(subset=["MonthStart найм", "Регион BI"]).copy()
    if "Учитывать в найме" in hires.columns:
        hires = hires[hires["Учитывать в найме"].fillna(False).eq(True)].copy()
    hires["MonthStart"] = hires["MonthStart найм"]
    hires["YearMonth"] = hires["YearMonth найм"]
    hires["Нанято"] = 1
    hires["Уволено"] = 0
    hires["Открытых вакансий"] = 0
    hires["Открытых вакансий МЕ"] = 0
    hires["Открытых вакансий СВ"] = 0
    hires["Приостановленных вакансий"] = 0
    hires["Закрытых вакансий"] = 0
    hires["Успешно закрытых вакансий"] = 0
    hires["Отмененных вакансий"] = 0
    hires["Активных МЕ"] = 0
    hires["Активных СВ"] = 0
    hires["Активных ТМ"] = 0
    hire_metrics = {
        "Нанято": ("Нанято", "sum"),
        "Уволено": ("Уволено", "sum"),
        "Открытых вакансий": ("Открытых вакансий", "sum"),
        "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
        "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
        "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
        "Закрытых вакансий": ("Закрытых вакансий", "sum"),
        "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
        "Отмененных вакансий": ("Отмененных вакансий", "sum"),
        "Активных МЕ": ("Активных МЕ", "sum"),
        "Активных СВ": ("Активных СВ", "sum"),
        "Активных ТМ": ("Активных ТМ", "sum"),
    }
    hire_agg = _aggregate_levels(hires, hire_metrics)

    fires = hr[
        hr["Состояние"].astype(str).str.contains("увольнение", case=False, na=False)
        & hr["MonthStart увольнение"].notna()
        & hr["Регион BI"].notna()
    ].copy()
    if "Учитывать в увольнении" in fires.columns:
        fires = fires[fires["Учитывать в увольнении"].fillna(False).eq(True)].copy()
    fires["MonthStart"] = fires["MonthStart увольнение"]
    fires["YearMonth"] = fires["YearMonth увольнение"]
    fires["Нанято"] = 0
    fires["Уволено"] = 1
    fires["Открытых вакансий"] = 0
    fires["Открытых вакансий МЕ"] = 0
    fires["Открытых вакансий СВ"] = 0
    fires["Приостановленных вакансий"] = 0
    fires["Закрытых вакансий"] = 0
    fires["Успешно закрытых вакансий"] = 0
    fires["Отмененных вакансий"] = 0
    fires["Активных МЕ"] = 0
    fires["Активных СВ"] = 0
    fires["Активных ТМ"] = 0
    fire_agg = _aggregate_levels(fires, hire_metrics)

    numeric_cols = [
        "Активных МЕ",
        "Активных СВ",
        "Активных ТМ",
        "Открытых вакансий",
        "Открытых вакансий МЕ",
        "Открытых вакансий СВ",
        "Приостановленных вакансий",
        "Закрытых вакансий",
        "Успешно закрытых вакансий",
        "Отмененных вакансий",
        "Средний срок закрытия вакансии, дни",
        "Нанято",
        "Уволено",
    ]
    pieces = [headcount, open_agg, closed_agg, hire_agg, fire_agg]
    keys = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "Уровень анализа",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
    ]
    valid_pieces = []
    for piece in pieces:
        if piece.empty and any(key not in piece.columns for key in keys):
            continue
        for col in numeric_cols:
            if col not in piece.columns:
                piece[col] = pd.NA
        valid_pieces.append(piece)
    combined = pd.concat([piece[keys + numeric_cols] for piece in valid_pieces], ignore_index=True)
    for col in [
        "Регион BI",
        "Уровень анализа",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
    ]:
        combined[col] = combined[col].astype("string").str.strip()
        combined[col] = combined[col].replace({"": pd.NA, "<NA>": pd.NA})

    sentinel = "__ALL__"
    grouped_input = combined.copy()
    for col in [c for c in keys if c not in ["MonthStart", "YearMonth"]]:
        grouped_input[col] = grouped_input[col].astype("string").str.strip()
        grouped_input[col] = grouped_input[col].replace({"": pd.NA, "<NA>": pd.NA, "nan": pd.NA}).fillna(sentinel)

    merged = (
        grouped_input
        .groupby(keys, dropna=False)
        .agg(
            **{
                "Активных МЕ": ("Активных МЕ", "sum"),
                "Активных СВ": ("Активных СВ", "sum"),
                "Активных ТМ": ("Активных ТМ", "sum"),
                "Открытых вакансий": ("Открытых вакансий", "sum"),
                "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
                "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
                "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
                "Закрытых вакансий": ("Закрытых вакансий", "sum"),
                "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
                "Отмененных вакансий": ("Отмененных вакансий", "sum"),
                "Средний срок закрытия вакансии, дни": ("Средний срок закрытия вакансии, дни", "mean"),
                "Нанято": ("Нанято", "sum"),
                "Уволено": ("Уволено", "sum"),
            }
        )
        .reset_index()
    )
    for col in [
        "Регион BI",
        "Уровень анализа",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "Супервайзер",
    ]:
        merged[col] = merged[col].replace(sentinel, pd.NA)

    for col in numeric_cols:
        if col in merged.columns and col != "Средний срок закрытия вакансии, дни":
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)

    final_input = merged.copy()
    for col in [c for c in keys if c not in ["MonthStart", "YearMonth"]]:
        final_input[col] = final_input[col].astype("string").str.strip()
        final_input[col] = final_input[col].replace(
            {"": pd.NA, "<NA>": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA}
        )

    region_mask = final_input["Уровень анализа"].eq("Регион")
    tm_mask = final_input["Уровень анализа"].eq("ТМ")
    sv_mask = final_input["Уровень анализа"].eq("СВ")

    final_input.loc[region_mask, "ID территориального менеджера"] = "ALL_TM"
    final_input.loc[region_mask, "Территориальный менеджер"] = "Все ТМ"
    final_input.loc[region_mask, "ID супервайзера"] = "ALL_SV"
    final_input.loc[region_mask, "Супервайзер"] = "Все СВ"
    final_input.loc[tm_mask, "ID супервайзера"] = "ALL_SV"
    final_input.loc[tm_mask, "Супервайзер"] = "Все СВ"
    final_input.loc[sv_mask & final_input["ID территориального менеджера"].isna(), "ID территориального менеджера"] = (
        "NO_TM"
    )
    final_input = normalize_confirmed_tm(final_input)

    for col in [c for c in keys if c not in ["MonthStart", "YearMonth"]]:
        final_input[col] = final_input[col].fillna(sentinel).astype(str)
    final_input["MonthStart"] = pd.to_datetime(final_input["MonthStart"], errors="coerce").dt.normalize()
    final_input["YearMonth"] = pd.to_numeric(final_input["YearMonth"], errors="coerce").astype("Int64")

    merged = (
        final_input.groupby(keys, dropna=False)
        .agg(
            **{
                "Активных МЕ": ("Активных МЕ", "sum"),
                "Активных СВ": ("Активных СВ", "sum"),
                "Активных ТМ": ("Активных ТМ", "sum"),
                "Открытых вакансий": ("Открытых вакансий", "sum"),
                "Открытых вакансий МЕ": ("Открытых вакансий МЕ", "sum"),
                "Открытых вакансий СВ": ("Открытых вакансий СВ", "sum"),
                "Приостановленных вакансий": ("Приостановленных вакансий", "sum"),
                "Закрытых вакансий": ("Закрытых вакансий", "sum"),
                "Успешно закрытых вакансий": ("Успешно закрытых вакансий", "sum"),
                "Отмененных вакансий": ("Отмененных вакансий", "sum"),
                "Средний срок закрытия вакансии, дни": ("Средний срок закрытия вакансии, дни", "mean"),
                "Нанято": ("Нанято", "sum"),
                "Уволено": ("Уволено", "sum"),
            }
        )
        .reset_index()
    )
    merged = merged.replace(sentinel, pd.NA)
    merged = merged[merged["Регион BI"].notna()].copy()
    merged["MonthStart"] = pd.to_datetime(merged["MonthStart"], errors="coerce").dt.normalize()
    merged = merged[merged["MonthStart"].le(active_month)].copy()

    merged["Доля отмен вакансий %"] = merged["Отмененных вакансий"] / merged["Закрытых вакансий"].replace(0, pd.NA)
    merged["Доля успешных закрытий %"] = merged["Успешно закрытых вакансий"] / merged["Закрытых вакансий"].replace(0, pd.NA)
    planned_team_base = (merged["Активных МЕ"] + merged["Открытых вакансий МЕ"]).replace(0, pd.NA)
    staffing_exposure_base = (merged["Активных МЕ"] + merged["Уволено"]).replace(0, pd.NA)
    merged["Доля вакансий к активным МЕ %"] = merged["Открытых вакансий МЕ"] / planned_team_base
    merged["Чистый отток"] = merged["Уволено"] - merged["Нанято"]
    merged["Кадровый отток"] = (merged["Уволено"] - merged["Нанято"]).clip(lower=0)
    merged["Доля кадрового оттока %"] = merged["Кадровый отток"] / staffing_exposure_base
    merged["Баланс персонала"] = merged["Нанято"] - merged["Уволено"]
    for col in [
        "Средний срок закрытия вакансии, дни",
        "Доля отмен вакансий %",
        "Доля успешных закрытий %",
        "Доля вакансий к активным МЕ %",
        "Доля кадрового оттока %",
    ]:
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged = merged.sort_values(
        ["MonthStart", "Уровень анализа", "Регион BI", "Территориальный менеджер", "Супервайзер"],
        na_position="last",
    ).reset_index(drop=True)

    output = out_dir / "org_staffing_monthly_snapshot.parquet"
    save_parquet(merged, str(output))
    report_all = _build_reporting_snapshot(merged)
    report_region = report_all[report_all["Уровень анализа"].eq("Регион")].copy()
    save_parquet(report_region, str(out_dir / "org_staffing_report_snapshot.parquet"))
    print(f"\n  Org staffing snapshot: {len(merged)} строк")
    print(f"  Org staffing report snapshot: {len(report_region)} строк")
    return merged


if __name__ == "__main__":
    build_org_staffing_monthly_snapshot()
