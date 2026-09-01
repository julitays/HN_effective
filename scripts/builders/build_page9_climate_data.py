import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet


MIN_REGION_RESPONSES = 10

HIGH_RISK_MAX_SAT = 0.55
HIGH_RISK_MIN_RISK = 0.24

CONTROL_MAX_SAT = 0.63
CONTROL_MIN_RISK = 0.18

BLOCK_ORDER = {
    "Оплата": 1,
    "Нагрузка": 2,
    "Руководство": 3,
    "Обучение": 4,
    "Инструменты": 5,
    "Рост": 6,
    "Команда": 7,
}

BLOCK_SOURCE_MAP = {
    "Оплата": "Блок: Оплата",
    "Нагрузка": "Блок: Нагрузка",
    "Руководство": "Блок: Руководство",
    "Обучение": "Блок: Обучение",
    "Инструменты": "Блок: Инструменты",
    "Рост": "Блок: Рост",
    "Команда": "Блок: Команда",
}


def _safe_sum(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().any():
        return float(values.sum())
    return 0.0


def _safe_count(series: pd.Series) -> int:
    return int(pd.to_numeric(series, errors="coerce").notna().sum())


def _status_from_row(row: pd.Series) -> str:
    sat = row.get("Удовлетворённость %")
    risk = row.get("Риск ухода %")

    if (
        (pd.notna(risk) and float(risk) >= HIGH_RISK_MIN_RISK)
        or (pd.notna(sat) and float(sat) < HIGH_RISK_MAX_SAT)
    ):
        return "высокий риск"

    if (
        (pd.notna(risk) and float(risk) >= CONTROL_MIN_RISK)
        or (pd.notna(sat) and float(sat) < CONTROL_MAX_SAT)
    ):
        return "контроль"

    return "стабильно"


def _active_report_regions(out_dir: Path) -> set[str]:
    dregion_path = out_dir / "dRegion.parquet"
    if dregion_path.exists():
        dregion = pd.read_parquet(dregion_path)
        if "Регион BI" in dregion.columns:
            return set(dregion["Регион BI"].dropna().astype(str).str.strip())

    dim_path = out_dir / "dim_employees.parquet"
    if not dim_path.exists():
        return set()
    dim = pd.read_parquet(dim_path)
    required = {"Активен", "Проект", "Регион BI"}
    if not required.issubset(dim.columns):
        return set()
    active = dim[
        dim["Активен"].fillna(False).eq(True)
        & dim["Проект"].astype(str).eq("H&N")
    ].copy()
    return set(active["Регион BI"].dropna().astype(str).str.strip())


def _build_quarterly_region_base(enps: pd.DataFrame) -> pd.DataFrame:
    work = enps[enps["QuarterStart"].notna()].copy()
    work = work[work["Регион BI"].notna()].copy()

    grouped = (
        work.groupby(
            ["QuarterStart", "YearQuarter", "QuarterLabel", "Регион BI", "Группа региона"],
            dropna=False,
        )
        .agg(
            **{
                "Ответов": ("Балл eNPS", lambda s: int(s.notna().sum())),
                "eNPS ответов": ("Категория eNPS", lambda s: int(s.notna().sum())),
                "Промоутеры": ("Категория eNPS", lambda s: int(s.eq("Промоутер").sum())),
                "Критики": ("Категория eNPS", lambda s: int(s.eq("Критик").sum())),
                "Высокий риск кол-во": ("Уровень риска ухода", lambda s: int(s.eq("Высокий").sum())),
                "Сумма удовлетворённости": ("Удовлетворённость", _safe_sum),
                "Кол-во удовлетворённости": ("Удовлетворённость", _safe_count),
                "Сумма вовлечённости": ("Вовлечённость", _safe_sum),
                "Кол-во вовлечённости": ("Вовлечённость", _safe_count),
                "Сумма лояльности": ("Лояльность", _safe_sum),
                "Кол-во лояльности": ("Лояльность", _safe_count),
            }
        )
        .reset_index()
    )

    grouped["Удовлетворённость %"] = (
        grouped["Сумма удовлетворённости"] / grouped["Кол-во удовлетворённости"] / 10
    ).where(grouped["Кол-во удовлетворённости"] > 0)
    grouped["Вовлечённость %"] = (
        grouped["Сумма вовлечённости"] / grouped["Кол-во вовлечённости"] / 10
    ).where(grouped["Кол-во вовлечённости"] > 0)
    grouped["Лояльность %"] = (
        grouped["Сумма лояльности"] / grouped["Кол-во лояльности"] / 10
    ).where(grouped["Кол-во лояльности"] > 0)
    grouped["Риск ухода %"] = (
        grouped["Высокий риск кол-во"] / grouped["Ответов"]
    ).where(grouped["Ответов"] > 0)
    grouped["eNPS"] = (
        (grouped["Промоутеры"] - grouped["Критики"]) / grouped["eNPS ответов"] * 100
    ).where(grouped["eNPS ответов"] > 0)

    numeric_columns = [
        "YearQuarter",
        "Ответов",
        "eNPS ответов",
        "Промоутеры",
        "Критики",
        "Высокий риск кол-во",
        "Сумма удовлетворённости",
        "Кол-во удовлетворённости",
        "Сумма вовлечённости",
        "Кол-во вовлечённости",
        "Сумма лояльности",
        "Кол-во лояльности",
        "Удовлетворённость %",
        "Вовлечённость %",
        "Лояльность %",
        "Риск ухода %",
        "eNPS",
    ]
    for column in numeric_columns:
        grouped[column] = pd.to_numeric(grouped[column], errors="coerce")

    return grouped.sort_values(["QuarterStart", "Регион BI"]).reset_index(drop=True)


def _build_blocks(enps: pd.DataFrame) -> pd.DataFrame:
    work = enps[enps["QuarterStart"].notna()].copy()
    work = work[work["Регион BI"].notna()].copy()

    rows: list[dict] = []
    for block_name, source_col in BLOCK_SOURCE_MAP.items():
        if source_col not in work.columns:
            continue

        part = work[["QuarterStart", "YearQuarter", "QuarterLabel", "Регион BI", "Группа региона", source_col]].copy()
        part[source_col] = pd.to_numeric(part[source_col], errors="coerce")
        grouped = (
            part.groupby(
                ["QuarterStart", "YearQuarter", "QuarterLabel", "Регион BI", "Группа региона"],
                dropna=False,
            )
            .agg(
                **{
                    "Сумма баллов": (source_col, _safe_sum),
                    "Ответов блока": (source_col, _safe_count),
                }
            )
            .reset_index()
        )
        grouped["Блок"] = block_name
        grouped["Порядок блока"] = BLOCK_ORDER[block_name]
        grouped["Значение %"] = (
            grouped["Сумма баллов"] / grouped["Ответов блока"] / 10
        ).where(grouped["Ответов блока"] > 0)
        rows.append(grouped)

    result = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if result.empty:
        return result, result

    result["Предыдущий период %"] = result.groupby(["Регион BI", "Блок"], dropna=False)["Значение %"].shift(1)
    result["Изменение к предыдущему %"] = result["Значение %"] - result["Предыдущий период %"]
    return result.sort_values(["QuarterStart", "Регион BI", "Порядок блока"]).reset_index(drop=True)


def build_page9_climate_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    enps = pd.read_parquet(out_dir / "enps_fact.parquet")

    quarterly_region = _build_quarterly_region_base(enps)
    active_regions = _active_report_regions(out_dir)
    if active_regions:
        quarterly_region = quarterly_region[quarterly_region["Регион BI"].astype(str).isin(active_regions)].copy()
    quarterly_region["Статус"] = quarterly_region.apply(
        lambda row: _status_from_row(row) if pd.notna(row.get("Ответов")) and int(row.get("Ответов")) >= MIN_REGION_RESPONSES else pd.NA,
        axis=1,
    )
    quarterly_region = quarterly_region.sort_values(["QuarterStart", "Регион BI"]).reset_index(drop=True)

    blocks_region = _build_blocks(enps)
    if active_regions and not blocks_region.empty:
        blocks_region = blocks_region[blocks_region["Регион BI"].astype(str).isin(active_regions)].copy()

    save_parquet(quarterly_region, str(out_dir / "page9_climate_quarterly_region.parquet"))
    save_parquet(blocks_region, str(out_dir / "page9_climate_blocks_region.parquet"))

    print(f"\n  Page9 climate quarterly region: {len(quarterly_region)} строк")
    print(f"  Page9 climate blocks region: {len(blocks_region)} строк")
    return quarterly_region, blocks_region


if __name__ == "__main__":
    build_page9_climate_data()
