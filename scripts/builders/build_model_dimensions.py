import sys
import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import (
    REGION_SORT_ORDER,
    _canonical_region_group_lookup,
    get_as_of_date,
    load_settings,
    load_region_map,
    save_parquet,
)
MONTH_SHORT_RU = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "май",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}

MONTH_FULL_RU = {
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

DIMENSION_OUTPUTS = {"dMonth.parquet", "dQuarter.parquet", "dRegion.parquet"}
OPERATIONAL_MONTH_TABLES = {
    "kpi_fact.parquet",
    "kpi_employee_monthly_metrics.parquet",
    "okk_fact.parquet",
    "page3_merch_monthly_snapshot.parquet",
    "page5_sv_monthly_snapshot.parquet",
    "page7_tm_monthly_snapshot.parquet",
}
DIMENSION_SOURCE_COLUMNS = {
    "Активен",
    "Проект",
    "Регион BI",
    "MonthStart",
    "QuarterStart",
    "QuarterStart ОЭД",
}


def _collect_parquet_tables(out_dir: Path) -> list[tuple[str, pd.DataFrame]]:
    tables = []
    for path in sorted(out_dir.glob("*.parquet")):
        if path.name in DIMENSION_OUTPUTS:
            continue
        try:
            available = set(pq.ParquetFile(path).schema.names)
            columns = sorted(DIMENSION_SOURCE_COLUMNS & available)
            if columns:
                tables.append((path.name, pd.read_parquet(path, columns=columns)))
        except Exception as exc:
            print(f"  Пропуск {path.name}: {exc}")
    return tables


def _build_dregion(tables: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    dim_employees_df = next((df for name, df in tables if name == "dim_employees.parquet"), None)
    if dim_employees_df is not None:
        active_users = dim_employees_df.copy()
        if {"Активен", "Проект", "Регион BI"}.issubset(active_users.columns):
            active_users = active_users[
                active_users["Активен"].fillna(False).eq(True)
                & active_users["Проект"].astype(str).eq("H&N")
            ].copy()
            values = set(
                str(v).strip()
                for v in active_users["Регион BI"].dropna().unique()
                if str(v).strip()
            )
            dregion = pd.DataFrame({"Регион BI": sorted(values, key=lambda x: (REGION_SORT_ORDER.get(x, 90), x))})
            dregion["Порядок региона"] = dregion["Регион BI"].map(lambda x: REGION_SORT_ORDER.get(x, 90))
            dregion["Группа региона"] = dregion["Регион BI"].map(_canonical_region_group_lookup(load_region_map()))
            return dregion

    values = set()
    for _, df in tables:
        if "Регион BI" in df.columns:
            values.update(
                str(v).strip()
                for v in df["Регион BI"].dropna().unique()
                if str(v).strip()
            )
    dregion = pd.DataFrame({"Регион BI": sorted(values, key=lambda x: (REGION_SORT_ORDER.get(x, 90), x))})
    dregion["Порядок региона"] = dregion["Регион BI"].map(lambda x: REGION_SORT_ORDER.get(x, 90))
    region_map = load_region_map()
    dregion["Группа региона"] = dregion["Регион BI"].map(_canonical_region_group_lookup(region_map))
    return dregion


def _build_dmonth(tables: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    month_values = []
    for _, df in tables:
        if "MonthStart" in df.columns:
            month_values.append(pd.to_datetime(df["MonthStart"], errors="coerce"))

    if not month_values:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Year",
                "MonthNumber",
                "MonthName",
                "MonthLabel",
                "MonthShort",
                "Порядок месяца",
                "Год",
                "Месяц номер",
                "Название месяца",
                "Месяц коротко",
                "Месяц",
                "QuarterNum",
                "QuarterLabel",
            ]
        )

    month_series = pd.concat(month_values, ignore_index=True).dropna().drop_duplicates().sort_values()
    start = month_series.min()
    current_month = get_as_of_date().replace(day=1)
    end = min(month_series.max(), current_month)
    operational_month_values = []
    for name, df in tables:
        if name in OPERATIONAL_MONTH_TABLES and "MonthStart" in df.columns:
            operational_month_values.append(pd.to_datetime(df["MonthStart"], errors="coerce"))
    if operational_month_values:
        operational_month_series = pd.concat(operational_month_values, ignore_index=True).dropna()
        if not operational_month_series.empty:
            end = min(end, operational_month_series.max())
    full_range = pd.date_range(start=start, end=end, freq="MS")

    dmonth = pd.DataFrame({"MonthStart": full_range})
    dmonth["YearMonth"] = dmonth["MonthStart"].dt.year * 100 + dmonth["MonthStart"].dt.month
    dmonth["Year"] = dmonth["MonthStart"].dt.year
    dmonth["MonthNumber"] = dmonth["MonthStart"].dt.month
    dmonth["Порядок месяца"] = dmonth["YearMonth"]
    dmonth["MonthName"] = dmonth["MonthNumber"].map(MONTH_FULL_RU)
    dmonth["MonthShort"] = dmonth["MonthNumber"].map(MONTH_SHORT_RU)
    dmonth["MonthLabel"] = dmonth["MonthShort"] + " " + dmonth["Year"].astype(str)
    dmonth["Год"] = dmonth["Year"]
    dmonth["Месяц номер"] = dmonth["MonthNumber"]
    dmonth["Название месяца"] = dmonth["MonthName"]
    dmonth["Месяц коротко"] = dmonth["MonthShort"]
    dmonth["Месяц"] = dmonth["MonthLabel"]
    dmonth["QuarterNum"] = dmonth["MonthStart"].dt.quarter
    dmonth["QuarterLabel"] = "Q" + dmonth["QuarterNum"].astype(str) + " " + dmonth["Year"].astype(str)
    return dmonth


def _build_dquarter(tables: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    quarter_values = []
    for _, df in tables:
        if "QuarterStart" in df.columns:
            quarter_values.append(pd.to_datetime(df["QuarterStart"], errors="coerce"))
        if "QuarterStart ОЭД" in df.columns:
            quarter_values.append(pd.to_datetime(df["QuarterStart ОЭД"], errors="coerce"))

    if not quarter_values:
        return pd.DataFrame(columns=["QuarterStart", "YearQuarter", "Year", "QuarterNum", "QuarterLabel"])

    quarter_series = pd.concat(quarter_values, ignore_index=True).dropna().drop_duplicates().sort_values()
    start = quarter_series.min()
    end = quarter_series.max()
    full_range = pd.date_range(start=start, end=end, freq="QS")

    dquarter = pd.DataFrame({"QuarterStart": full_range})
    dquarter["Year"] = dquarter["QuarterStart"].dt.year
    dquarter["QuarterNum"] = dquarter["QuarterStart"].dt.quarter
    dquarter["YearQuarter"] = dquarter["Year"] * 10 + dquarter["QuarterNum"]
    dquarter["QuarterLabel"] = "Q" + dquarter["QuarterNum"].astype(str) + " " + dquarter["Year"].astype(str)
    return dquarter


def build_model_dimensions() -> None:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    tables = _collect_parquet_tables(out_dir)

    dregion = _build_dregion(tables)
    dmonth = _build_dmonth(tables)
    dquarter = _build_dquarter(tables)

    save_parquet(dregion, str(out_dir / "dRegion.parquet"))
    save_parquet(dmonth, str(out_dir / "dMonth.parquet"))
    save_parquet(dquarter, str(out_dir / "dQuarter.parquet"))

    print("\n  Размерности модели собраны:")
    print(f"    dRegion: {len(dregion)} строк")
    print(f"    dMonth: {len(dmonth)} строк")
    print(f"    dQuarter: {len(dquarter)} строк")


if __name__ == "__main__":
    build_model_dimensions()
