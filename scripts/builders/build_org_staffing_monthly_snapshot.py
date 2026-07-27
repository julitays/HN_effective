import sys
from datetime import date
from pathlib import Path
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import get_active_users_scope, load_region_map, load_settings, normalize_dim, save_parquet


TODAY = pd.Timestamp(date.today())
REPORT_START_YEARMONTH = load_settings()["reporting"]["start_yearmonth"]
REPORT_LEVEL_ORDER = {
    "Регион": 1,
    "ТМ": 2,
    "СВ": 3,
}


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


def _build_active_headcount(dim: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    scope = get_active_users_scope(dim)
    active = scope["frame"].copy()
    current_month = TODAY.to_period("M").to_timestamp()

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

    sv_people = active[active["position"].astype(str).str.lower().str.contains("супервайзер", na=False)].copy()
    if "Регион BI" not in sv_people.columns:
        sv_people["Регион BI"] = pd.NA
    sv_people["MonthStart"] = current_month
    sv_people["YearMonth"] = current_month.year * 100 + current_month.month
    sv_people["ID территориального менеджера"] = sv_people["manager_id"]
    sv_people["Территориальный менеджер"] = sv_people["manager_full_name"]
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

    tm_people = active[
        active["position"].astype(str).str.lower().str.contains("территориальный", na=False)
        | active["position"].astype(str).str.lower().str.fullmatch("tm", na=False)
        | active["position"].astype(str).str.lower().str.fullmatch("rm", na=False)
        | active["position"].astype(str).str.lower().str.contains("менеджер", na=False)
    ].copy()
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


def _expand_vacancy_months(
    vacancies: pd.DataFrame,
    *,
    start_col: str = "Дата открытия",
    end_col: str,
    end_fallback_col: str | None = None,
    include_end_month: bool = True,
) -> pd.DataFrame:
    if vacancies.empty or start_col not in vacancies.columns:
        return pd.DataFrame()

    work = vacancies.copy()
    work[start_col] = pd.to_datetime(work[start_col], errors="coerce")
    work[end_col] = pd.to_datetime(work[end_col], errors="coerce") if end_col in work.columns else pd.NaT
    if end_fallback_col and end_fallback_col in work.columns:
        fallback_end = pd.to_datetime(work[end_fallback_col], errors="coerce")
        work[end_col] = work[end_col].fillna(fallback_end)
    work[end_col] = work[end_col].fillna(TODAY)
    work = work[work[start_col].notna() & work[end_col].notna() & work["Регион BI"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    work["StartMonth"] = work[start_col].dt.to_period("M").dt.to_timestamp()
    end_month = work[end_col].dt.to_period("M").dt.to_timestamp()
    work["EndMonth"] = end_month if include_end_month else end_month - pd.DateOffset(months=1)
    work = work[work["EndMonth"].ge(work["StartMonth"])].copy()
    work["MonthStart"] = work.apply(
        lambda row: pd.date_range(row["StartMonth"], row["EndMonth"], freq="MS"),
        axis=1,
    )
    work = work.explode("MonthStart").copy()
    work["YearMonth"] = work["MonthStart"].dt.year * 100 + work["MonthStart"].dt.month
    work = work.drop_duplicates(["ID вакансии", "MonthStart"])
    return work


def _build_open_vacancy_monthly(open_vacancies: pd.DataFrame, closed_vacancies: pd.DataFrame | None = None) -> pd.DataFrame:
    if open_vacancies.empty or "Дата открытия" not in open_vacancies.columns:
        return pd.DataFrame()

    work = open_vacancies.copy()
    work["Дата открытия"] = pd.to_datetime(work["Дата открытия"], errors="coerce")
    work = work[work["Дата открытия"].notna() & work["Регион BI"].notna()].copy()
    if work.empty:
        return pd.DataFrame()

    work["MonthStart"] = work["Дата открытия"].dt.to_period("M").dt.to_timestamp()
    work["YearMonth"] = work["MonthStart"].dt.year * 100 + work["MonthStart"].dt.month
    work = work.drop_duplicates(["ID вакансии"]).copy()
    work["Источник вакансии"] = "открытая вакансия из текущего списка"
    work["Открытых вакансий"] = 1
    work["Открытых вакансий МЕ"] = work["Роль вакансии"].eq("МЕ").astype(int)
    work["Открытых вакансий СВ"] = work["Роль вакансии"].eq("СВ").astype(int)
    work["Приостановленных вакансий"] = (
        work["Приостановлена"].fillna(False).astype(int)
        if "Приостановлена" in work.columns
        else 0
    )
    work["Закрытых вакансий"] = 0
    work["Успешно закрытых вакансий"] = 0
    work["Отмененных вакансий"] = 0
    work["Средний срок закрытия вакансии, дни"] = pd.NA
    work["Нанято"] = 0
    work["Уволено"] = 0
    work["Активных МЕ"] = 0
    work["Активных СВ"] = 0
    work["Активных ТМ"] = 0
    return work


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

    headcount = _build_active_headcount(dim, teams)

    open_work = _build_open_vacancy_monthly(open_vacancies, closed_vacancies)
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
    final_input.loc[sv_mask & final_input["Территориальный менеджер"].isna(), "Территориальный менеджер"] = (
        "Вакансия / нет ТМ"
    )

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

    merged["Доля отмен вакансий %"] = merged["Отмененных вакансий"] / merged["Закрытых вакансий"].replace(0, pd.NA)
    merged["Доля успешных закрытий %"] = merged["Успешно закрытых вакансий"] / merged["Закрытых вакансий"].replace(0, pd.NA)
    merged["Доля вакансий к активным МЕ %"] = merged["Открытых вакансий МЕ"] / merged["Активных МЕ"].replace(0, pd.NA)
    merged["Чистый отток"] = merged["Уволено"] - merged["Нанято"]
    merged["Баланс персонала"] = merged["Нанято"] - merged["Уволено"]
    for col in [
        "Средний срок закрытия вакансии, дни",
        "Доля отмен вакансий %",
        "Доля успешных закрытий %",
        "Доля вакансий к активным МЕ %",
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
    report_tm = report_all[report_all["Уровень анализа"].eq("ТМ")].copy()
    report_sv = report_all[report_all["Уровень анализа"].eq("СВ")].copy()

    save_parquet(report_region, str(out_dir / "org_staffing_report_snapshot.parquet"))
    save_parquet(report_tm, str(out_dir / "org_staffing_tm_monthly_snapshot.parquet"))
    save_parquet(report_sv, str(out_dir / "org_staffing_sv_monthly_snapshot.parquet"))
    print(f"\n  Org staffing snapshot: {len(merged)} строк")
    print(f"  Org staffing report snapshot: {len(report_region)} строк")
    print(f"  Org staffing TM snapshot: {len(report_tm)} строк")
    print(f"  Org staffing SV snapshot: {len(report_sv)} строк")
    return merged


if __name__ == "__main__":
    build_org_staffing_monthly_snapshot()
