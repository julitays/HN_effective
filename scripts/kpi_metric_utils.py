import numpy as np
import pandas as pd


KPI_COMPONENT_COLUMNS = [
    f"{metric} {value}"
    for metric in ("PICOS", "OSA", "TOP16")
    for value in ("план %", "факт %", "выполнение %")
]
KPI_PUBLIC_COLUMNS = ["KPI проекта %", *KPI_COMPONENT_COLUMNS]
KPI_SCORE_WEIGHT_COLUMNS = [f"{metric} вес в KPI %" for metric in ("PICOS", "OSA", "TOP16")]


def _prepare_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    required = {"MonthStart", "YearMonth", *KPI_PUBLIC_COLUMNS}
    missing = sorted(required - set(metrics.columns))
    if missing:
        raise ValueError(f"Витрина KPI не содержит поля {missing}")

    work = metrics.copy()
    work["MonthStart"] = pd.to_datetime(work["MonthStart"], errors="coerce")
    work["YearMonth"] = pd.to_numeric(work["YearMonth"], errors="coerce").astype("Int64")
    for column in KPI_PUBLIC_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")
    for column in KPI_SCORE_WEIGHT_COLUMNS:
        if column in work.columns:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    if "Визитов" in work.columns:
        work["Визитов"] = pd.to_numeric(work["Визитов"], errors="coerce")
    return work.dropna(subset=["MonthStart", "YearMonth"])


def pivot_employee_kpi_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    work = _prepare_metrics(metrics)
    if "ID сотрудника" not in work.columns:
        raise ValueError("Витрина KPI сотрудников не содержит поле ID сотрудника")
    columns = [
        "MonthStart",
        "YearMonth",
        "ID сотрудника",
        *KPI_PUBLIC_COLUMNS,
        *[column for column in KPI_SCORE_WEIGHT_COLUMNS if column in work.columns],
    ]
    return work.dropna(subset=["ID сотрудника"])[columns].drop_duplicates(
        ["MonthStart", "YearMonth", "ID сотрудника"]
    )


def pivot_tt_kpi_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    work = _prepare_metrics(metrics)
    if "ТТ" not in work.columns:
        raise ValueError("Витрина KPI ТТ не содержит поле ТТ")
    work["ТТ"] = work["ТТ"].astype("string").str.strip()
    columns = ["MonthStart", "YearMonth", "ТТ", *KPI_PUBLIC_COLUMNS]
    return work.dropna(subset=["ТТ"])[columns].drop_duplicates(
        ["MonthStart", "YearMonth", "ТТ"]
    )


def _weighted_metrics(part: pd.DataFrame) -> pd.Series:
    weights = pd.to_numeric(part.get("Визитов", 1.0), errors="coerce")
    if not isinstance(weights, pd.Series):
        weights = pd.Series(float(weights), index=part.index)
    result = {}
    metric_columns = [
        *KPI_PUBLIC_COLUMNS,
        *[column for column in KPI_SCORE_WEIGHT_COLUMNS if column in part.columns],
    ]
    for column in metric_columns:
        values = pd.to_numeric(part[column], errors="coerce")
        valid = values.notna() & weights.notna() & weights.gt(0)
        if valid.any():
            result[column] = float((values[valid] * weights[valid]).sum() / weights[valid].sum())
        else:
            result[column] = float(values.mean()) if values.notna().any() else np.nan
    return pd.Series(result)


def aggregate_employee_kpi_to_org(
    metrics: pd.DataFrame,
    assignment: pd.DataFrame,
    entity_column: str,
    assignment_employee_column: str = "ID мерчендайзера",
) -> pd.DataFrame:
    work = _prepare_metrics(metrics)
    if "ID сотрудника" not in work.columns:
        raise ValueError("Витрина KPI сотрудников не содержит поле ID сотрудника")

    keys = ["MonthStart", "YearMonth"]
    required_assignment = {*keys, assignment_employee_column, entity_column}
    missing = sorted(required_assignment - set(assignment.columns))
    if missing:
        raise ValueError(f"Привязка сотрудников не содержит поля {missing}")

    mapping = assignment[[*keys, assignment_employee_column, entity_column]].copy()
    mapping = mapping.rename(columns={assignment_employee_column: "ID сотрудника"})
    mapping["MonthStart"] = pd.to_datetime(mapping["MonthStart"], errors="coerce")
    mapping["YearMonth"] = pd.to_numeric(mapping["YearMonth"], errors="coerce").astype("Int64")
    mapping = mapping.dropna(subset=[*keys, "ID сотрудника", entity_column]).drop_duplicates()

    merged = work.merge(mapping, on=[*keys, "ID сотрудника"], how="inner")
    if merged.empty:
        return pd.DataFrame(
            columns=[
                *keys,
                entity_column,
                *KPI_PUBLIC_COLUMNS,
                *[column for column in KPI_SCORE_WEIGHT_COLUMNS if column in work.columns],
            ]
        )

    group_keys = [*keys, entity_column]
    return (
        merged.groupby(group_keys, dropna=False)
        .apply(_weighted_metrics, include_groups=False)
        .reset_index()
    )
