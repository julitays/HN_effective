import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings, save_parquet, extract_sv_code as _extract_sv_code
from scripts.staffing_utils import (
    attach_last_quarter_metric as _attach_last_quarter_metric,
    build_enps_quarterly as _build_enps_quarterly,
    is_tm_role,
    mode_or_first,
    normalize_confirmed_tm,
)
from scripts.kpi_metric_utils import KPI_COMPONENT_COLUMNS
from scripts.kpi_org_mapping import build_rtm_month_org


SETTINGS = load_settings()
REPORT_START_YEAR = SETTINGS["reporting"]["start_yearmonth"] // 100
PAGE2_RULES = SETTINGS["business_rules"]["page2"]

TARGET_KPI = float(PAGE2_RULES["kpi_red_min"])
TARGET_OKK = 0.60
TARGET_LEARN = float(PAGE2_RULES["learning_red_min"])
TARGET_FRAUD = 0.10
TARGET_RISK = 0.16

TARGET_OSA = 0.85
TARGET_PICOS = 0.85
TARGET_COMPLEX_STORES_SHARE = 0.15
COMPLEX_OSA_MIN = 0.80
COMPLEX_PICOS_MIN = 0.80
COMPLEX_OKK_MIN = 0.55
NO_TM_ID = "NO_TM"
NO_TM_NAME = "Вакансия / нет ТМ"


def _coerce_numeric_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = df.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _build_region_monthly_base(
    kpi: pd.DataFrame,
    okk: pd.DataFrame,
    learning_monthly: pd.DataFrame,
    enps: pd.DataFrame,
    kpi_tt_direct: pd.DataFrame | None = None,
) -> pd.DataFrame:
    kpi_work = kpi.copy()
    if "Вакансия" in kpi_work.columns:
        kpi_work = kpi_work[kpi_work["Вакансия"] != True].copy()
    allowed_months = set(pd.to_datetime(okk["MonthStart"], errors="coerce").dropna().unique())
    if kpi_tt_direct is not None and not kpi_tt_direct.empty:
        allowed_months |= set(
            pd.to_datetime(kpi_tt_direct["MonthStart"], errors="coerce").dropna().unique()
        )
    okk = okk[okk["MonthStart"].isin(allowed_months)].copy()
    learning_monthly = learning_monthly[learning_monthly["MonthStart"].isin(allowed_months)].copy()

    if kpi_tt_direct is not None and not kpi_tt_direct.empty:
        kpi_tt_direct = kpi_tt_direct[kpi_tt_direct["Регион BI"].notna()].copy()
        kpi_aggregations = {"KPI проекта %": ("KPI проекта %", "mean")}
        kpi_aggregations.update(
            {
                column: (column, "mean")
                for column in KPI_COMPONENT_COLUMNS
                if column in kpi_tt_direct.columns
            }
        )
        kpi_monthly = (
            kpi_tt_direct.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
            .agg(**kpi_aggregations)
            .reset_index()
        )
    else:
        kpi_monthly = pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "Регион BI",
                "KPI проекта %",
                *KPI_COMPONENT_COLUMNS,
            ]
        )

    okk_monthly = (
        okk.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            **{
                "OSA %": ("% наличия товара на полке", "mean"),
                "PICOS %": ("% наличия PICoS", "mean"),
                "ОКК %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("Флаг фальсификации", lambda s: s.fillna(False).eq(True).sum()),
                "ТТ всего": ("Код ТТ", pd.Series.nunique),
            }
        )
        .reset_index()
    )

    stores = (
        okk.groupby(["MonthStart", "YearMonth", "Регион BI", "Код ТТ"], dropna=False)
        .agg(
            **{
                "OSA %": ("% наличия товара на полке", "mean"),
                "PICOS %": ("% наличия PICoS", "mean"),
                "ОКК %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
            }
        )
        .reset_index()
    )
    stores["Количество сигналов ТТ"] = (
        (stores["OSA %"] < COMPLEX_OSA_MIN).astype(int)
        + (stores["PICOS %"] < COMPLEX_PICOS_MIN).astype(int)
        + (stores["ОКК %"] < COMPLEX_OKK_MIN).astype(int)
        + (stores["Фрод %"] > TARGET_FRAUD).astype(int)
    )
    stores["Сложная ТТ"] = stores["Количество сигналов ТТ"] >= 3

    complex_stores = (
        stores.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            **{
                "Сложных ТТ": ("Сложная ТТ", "sum"),
            }
        )
        .reset_index()
    )

    learn_monthly = learning_monthly.rename(
        columns={"Обязательное обучение %": "Обучение %"}
    )[
        ["MonthStart", "YearMonth", "Регион BI", "Обучение %"]
    ].copy()

    enps_quarterly = _build_enps_quarterly(enps)

    base = (
        kpi_monthly.merge(okk_monthly, on=["MonthStart", "YearMonth", "Регион BI"], how="outer")
        .merge(complex_stores, on=["MonthStart", "YearMonth", "Регион BI"], how="left")
        .merge(learn_monthly, on=["MonthStart", "YearMonth", "Регион BI"], how="left")
    )

    base = _attach_last_quarter_metric(base, enps_quarterly, "Риск ухода региона %", period="year")
    base["Доля сложных ТТ %"] = base["Сложных ТТ"] / base["ТТ всего"]
    base = _coerce_numeric_columns(
        base,
        [
            "KPI проекта %",
            *KPI_COMPONENT_COLUMNS,
            "OSA %",
            "PICOS %",
            "ОКК %",
            "Фрод %",
            "Фрод кол-во",
            "ТТ всего",
            "Сложных ТТ",
            "Обучение %",
            "Риск ухода региона %",
            "Доля сложных ТТ %",
        ],
    )
    return base


def _format_pct(value: float | None) -> str:
    if pd.isna(value):
        return "—"
    return f"{value:.0%}"


def _format_pp(value: float | None) -> str:
    if pd.isna(value):
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _format_count(value: float | None) -> str:
    if pd.isna(value):
        return "—"
    return f"{int(round(value))}"


def _action_impact_label(value_pp: float) -> str:
    sign = "+" if value_pp >= 0 else ""
    return f"{sign}{value_pp:.1f}% KPI"


def _worst_regions_text(
    month_df: pd.DataFrame,
    metric_col: str,
    target: float,
    direction: str,
) -> str:
    work = month_df.dropna(subset=[metric_col, "Регион BI"]).copy()
    if work.empty:
        return "данных пока недостаточно"

    if direction == "low":
        bad = work[work[metric_col] < target].sort_values(metric_col, ascending=True)
    else:
        bad = work[work[metric_col] > target].sort_values(metric_col, ascending=False)

    if bad.empty:
        return "критичных отклонений нет"

    top_regions = bad["Регион BI"].head(2).tolist()
    if len(top_regions) == 1:
        return top_regions[0]
    return " и ".join(top_regions)


def _build_actions_monthly(
    region_base: pd.DataFrame,
    okk: pd.DataFrame,
    learning_monthly: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []

    for month_start, month_df in region_base.groupby("MonthStart"):
        month_okk = okk[okk["MonthStart"] == month_start].copy()
        month_learning = learning_monthly[learning_monthly["MonthStart"] == month_start].copy()

        overall = {
            "MonthStart": month_start,
            "YearMonth": month_df["YearMonth"].dropna().iloc[0] if month_df["YearMonth"].notna().any() else pd.NA,
            "Фрод %": month_okk["Флаг фальсификации"].mean(),
            "Фрод кол-во": int(month_okk["Флаг фальсификации"].fillna(False).eq(True).sum()),
            "Обучение %": (
                month_learning["Пройдено обязательных курсов"].sum()
                / month_learning["Назначено обязательных курсов"].sum()
                if month_learning["Назначено обязательных курсов"].sum() else pd.NA
            ),
            "Сложных ТТ": month_df["Сложных ТТ"].sum(),
            "ТТ всего": month_df["ТТ всего"].sum(),
        }
        for metric in ("PICOS", "OSA", "TOP16"):
            for value in ("план %", "факт %", "выполнение %"):
                column = f"{metric} {value}"
                overall[column] = (
                    pd.to_numeric(month_df[column], errors="coerce").mean()
                    if column in month_df.columns
                    else pd.NA
                )
        overall["Доля сложных ТТ %"] = overall["Сложных ТТ"] / overall["ТТ всего"] if overall["ТТ всего"] else pd.NA

        current_complex_share = overall["Доля сложных ТТ %"]
        target_complex_share = TARGET_COMPLEX_STORES_SHARE
        current_complex_count = overall["Сложных ТТ"]
        target_complex_count = (
            overall["ТТ всего"] * target_complex_share if pd.notna(overall["ТТ всего"]) else pd.NA
        )

        actions = [
            *[
                {
                    "Порядок действия": order,
                    "Что сделать": action_name,
                    "Метрика": metric,
                    "Сейчас значение": overall[f"{metric} факт %"],
                    "Цель значение": overall[f"{metric} план %"],
                    "Выполнение значение": overall[f"{metric} выполнение %"],
                    "Разрыв значение": (
                        max(0.0, overall[f"{metric} план %"] - overall[f"{metric} факт %"])
                        if pd.notna(overall[f"{metric} план %"])
                        and pd.notna(overall[f"{metric} факт %"])
                        else pd.NA
                    ),
                    "Оценка влияния значение": (
                        max(0.0, overall[f"{metric} план %"] - overall[f"{metric} факт %"])
                        * weight
                        * 100
                        if pd.notna(overall[f"{metric} план %"])
                        and pd.notna(overall[f"{metric} факт %"])
                        else 0.0
                    ),
                    "Ответственный контур": "СВ / ТТ",
                    "Фокус": _worst_regions_text(
                        month_df,
                        f"{metric} факт %",
                        overall[f"{metric} план %"],
                        "low",
                    )
                    if pd.notna(overall[f"{metric} план %"])
                    else "данных пока недостаточно",
                }
                for order, metric, action_name, weight in [
                    (1, "PICOS", "Устранить просадку PICOS", 1.0),
                    (2, "OSA", "Подтянуть OSA", 0.5),
                    (3, "TOP16", "Подтянуть TOP16", 0.5),
                ]
            ],
            {
                "Порядок действия": 4,
                "Что сделать": "Снизить фрод",
                "Метрика": "Фрод %",
                "Сейчас значение": overall["Фрод %"],
                "Цель значение": TARGET_FRAUD,
                "Разрыв значение": max(0.0, overall["Фрод %"] - TARGET_FRAUD) if pd.notna(overall["Фрод %"]) else pd.NA,
                "Оценка влияния значение": max(0.0, (overall["Фрод %"] - TARGET_FRAUD) * 30) if pd.notna(overall["Фрод %"]) else 0.0,
                "Ответственный контур": "ОКК / СВ",
                "Фокус": _worst_regions_text(month_df, "Фрод %", TARGET_FRAUD, "high"),
            },
            {
                "Порядок действия": 5,
                "Что сделать": "Разобрать сложные ТТ",
                "Метрика": "Сложных ТТ",
                "Сейчас значение": current_complex_count,
                "Цель значение": target_complex_count,
                "Разрыв значение": max(0.0, current_complex_count - target_complex_count) if pd.notna(current_complex_count) and pd.notna(target_complex_count) else pd.NA,
                "Оценка влияния значение": max(0.0, (current_complex_count - target_complex_count) * 0.12) if pd.notna(current_complex_count) and pd.notna(target_complex_count) else 0.0,
                "Ответственный контур": "РГ / КАМ",
                "Фокус": _worst_regions_text(month_df, "Доля сложных ТТ %", TARGET_COMPLEX_STORES_SHARE, "high"),
            },
            {
                "Порядок действия": 6,
                "Что сделать": "Подтянуть обучение",
                "Метрика": "Обучение %",
                "Сейчас значение": overall["Обучение %"],
                "Цель значение": TARGET_LEARN,
                "Разрыв значение": max(0.0, TARGET_LEARN - overall["Обучение %"]) if pd.notna(overall["Обучение %"]) else pd.NA,
                "Оценка влияния значение": max(0.0, (TARGET_LEARN - overall["Обучение %"]) * 8) if pd.notna(overall["Обучение %"]) else 0.0,
                "Ответственный контур": "Обучение / СВ",
                "Фокус": _worst_regions_text(month_df, "Обучение %", TARGET_LEARN, "low"),
            },
        ]

        actionable_rows = []
        for action in actions:
            current_value = action["Сейчас значение"]
            target_value = action["Цель значение"]
            gap_value = action["Разрыв значение"]
            impact_value = action["Оценка влияния значение"]

            if action["Метрика"] == "Сложных ТТ":
                action["Сейчас"] = _format_count(current_value) + " ТТ"
                action["Цель"] = _format_count(target_value) + " ТТ"
                action["Разрыв"] = _format_count(gap_value) + " ТТ" if pd.notna(gap_value) else "—"
            else:
                action["Сейчас"] = _format_pct(current_value)
                action["Цель"] = _format_pct(target_value)
                action["Разрыв"] = _format_pp(gap_value)
            action["Выполнение"] = _format_pct(action.get("Выполнение значение"))

            action["Оценка влияния"] = _action_impact_label(impact_value)
            action["MonthStart"] = overall["MonthStart"]
            action["YearMonth"] = overall["YearMonth"]
            actionable_rows.append(action)

        actionable_rows = [
            row for row in actionable_rows
            if pd.notna(row["Разрыв значение"]) and row["Разрыв значение"] > 0
        ]
        actionable_rows = sorted(
            actionable_rows,
            key=lambda row: (-row["Оценка влияния значение"], row["Порядок действия"]),
        )
        for rank, row in enumerate(actionable_rows, start=1):
            row["Порядок действия"] = rank
            row["ДБ заголовок"] = row["Что сделать"]
            if row["Метрика"] == "OSA":
                row["ДБ текст"] = (
                    f"OSA: факт {row['Сейчас']}, план {row['Цель']}, выполнение {row['Выполнение']}; "
                    f"основная просадка — {row['Фокус']}."
                )
            elif row["Метрика"] == "PICOS":
                row["ДБ текст"] = (
                    f"PICOS: факт {row['Сейчас']}, план {row['Цель']}, выполнение {row['Выполнение']}; "
                    f"слабые зоны — {row['Фокус']}."
                )
            elif row["Метрика"] == "TOP16":
                row["ДБ текст"] = (
                    f"TOP16: факт {row['Сейчас']}, план {row['Цель']}, выполнение {row['Выполнение']}; "
                    f"слабые зоны — {row['Фокус']}."
                )
            elif row["Метрика"] == "Фрод %":
                row["ДБ текст"] = (
                    f"Фрод {row['Сейчас']} при целевом уровне до {row['Цель']}; "
                    f"максимальный риск — {row['Фокус']}."
                )
            elif row["Метрика"] == "Сложных ТТ":
                row["ДБ текст"] = (
                    f"Сложных ТТ {row['Сейчас']} при целевом уровне {row['Цель']}; "
                    f"наибольшая концентрация — {row['Фокус']}."
                )
            elif row["Метрика"] == "Обучение %":
                row["ДБ текст"] = (
                    f"Обучение {row['Сейчас']} при цели {row['Цель']}; "
                    f"сильнее всего отстают {row['Фокус']}."
                )
            else:
                row["ДБ текст"] = row["Фокус"]
            row["Доказательная база"] = row["ДБ текст"]
            rows.append(row)

    return pd.DataFrame(rows)


def _build_learning_sv_monthly(
    learning_fact: pd.DataFrame,
    teams: pd.DataFrame,
    allowed_months: list[pd.Timestamp],
) -> pd.DataFrame:
    mapping = teams[
        ["ID мерчендайзера", "ID супервайзера", "Супервайзер"]
    ].dropna(subset=["ID мерчендайзера"]).drop_duplicates("ID мерчендайзера")

    work = learning_fact.merge(
        mapping,
        left_on="ID сотрудника",
        right_on="ID мерчендайзера",
        how="left",
        suffixes=("", "_team"),
    )
    work = work[work["Обязательный"] == True].copy()
    work["StartMonth"] = pd.to_datetime(work["StartMonth"], errors="coerce")

    rows: list[pd.DataFrame] = []
    for month_start in allowed_months:
        snapshot = work[work["StartMonth"] <= month_start].copy()
        if snapshot.empty:
            continue
        grouped = (
            snapshot.groupby(
                ["ID супервайзера"],
                dropna=False,
            )
            .agg(
                **{
                    "Супервайзер": ("Супервайзер", "first"),
                    "Назначено обязательных курсов": ("ID сотрудника", "count"),
                    "Пройдено обязательных курсов": ("Пройдено", lambda s: s.eq(True).sum()),
                }
            )
            .reset_index()
        )
        grouped["MonthStart"] = month_start
        grouped["YearMonth"] = month_start.year * 100 + month_start.month
        grouped["Обучение %"] = grouped["Пройдено обязательных курсов"] / grouped["Назначено обязательных курсов"]
        rows.append(grouped)

    if not rows:
        return pd.DataFrame(
            columns=[
                "MonthStart",
                "YearMonth",
                "ID супервайзера",
                "Супервайзер",
                "Назначено обязательных курсов",
                "Пройдено обязательных курсов",
                "Обучение %",
            ]
        )

    return pd.concat(rows, ignore_index=True)


def _build_supervisor_directory(kpi: pd.DataFrame, teams: pd.DataFrame, dim_employees: pd.DataFrame) -> pd.DataFrame:
    teams_work = teams.copy()
    for column in ["ID супервайзера", "ID территориального менеджера", "Супервайзер", "Территориальный менеджер", "Регион BI", "Группа региона"]:
        if column in teams_work.columns:
            teams_work[column] = teams_work[column].replace("", pd.NA)
    tm_dim = dim_employees.copy()
    if {"Активен", "Должность"}.issubset(tm_dim.columns):
        tm_dim = tm_dim[tm_dim["Активен"].fillna(False).eq(True) & tm_dim["Должность"].map(is_tm_role)].copy()
    else:
        tm_dim = tm_dim[tm_dim["Должность"].map(is_tm_role)].copy()
    valid_tm_ids = set(tm_dim["ID сотрудника"].dropna().astype(str).str.strip()) if "ID сотрудника" in tm_dim.columns else set()
    tm_ids = teams_work["ID территориального менеджера"].astype("string").str.strip()
    invalid_tm = tm_ids.notna() & ~tm_ids.isin(valid_tm_ids)
    teams_work.loc[invalid_tm, "ID территориального менеджера"] = pd.NA
    teams_work.loc[invalid_tm, "Территориальный менеджер"] = pd.NA

    teams_dir = (
        teams_work.dropna(subset=["ID супервайзера"])
        .groupby("ID супервайзера", dropna=False)
        .agg(
            **{
                "Супервайзер": ("Супервайзер", "first"),
                "Регион BI": ("Регион BI", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
                "Группа региона": ("Группа региона", lambda s: s.mode().iloc[0] if not s.mode().empty else s.dropna().iloc[0] if s.dropna().any() else pd.NA),
                "ID территориального менеджера": ("ID территориального менеджера", "first"),
                "Территориальный менеджер": ("Территориальный менеджер", "first"),
            }
        )
        .reset_index()
    )

    dim = dim_employees.copy()
    if {"Активен", "Проект", "Должность"}.issubset(dim.columns):
        sv_dim = dim[
            dim["Активен"].fillna(False).eq(True)
            & dim["Проект"].astype(str).eq("H&N")
            & dim["Должность"].astype(str).str.lower().str.contains("супервайзер", na=False)
        ].copy()
    else:
        sv_dim = dim[dim["Должность"].astype(str).str.lower().str.contains("супервайзер", na=False)].copy()
    tm_lookup = (
        tm_dim[["ID сотрудника", "ФИО", "Регион BI", "Группа региона"]]
        .dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника")
        .rename(
            columns={
                "ID сотрудника": "ID территориального менеджера",
                "ФИО": "Территориальный менеджер dim",
                "Регион BI": "Регион BI tm dim",
                "Группа региона": "Группа региона tm dim",
            }
        )
    )
    sv_dim = (
        sv_dim[["ID сотрудника", "ФИО", "Регион BI", "Группа региона", "ID руководителя", "ФИО руководителя"]]
        .rename(
            columns={
                "ID сотрудника": "ID супервайзера",
                "ФИО": "Супервайзер dim",
                "Регион BI": "Регион BI dim",
                "Группа региона": "Группа региона dim",
                "ID руководителя": "ID территориального менеджера dim",
                "ФИО руководителя": "Территориальный менеджер dim raw",
            }
        )
    )
    sv_dim = sv_dim.merge(
        tm_lookup,
        left_on="ID территориального менеджера dim",
        right_on="ID территориального менеджера",
        how="left",
    ).drop(columns=["ID территориального менеджера"], errors="ignore")
    sv_dim["Территориальный менеджер dim"] = sv_dim["Территориальный менеджер dim"].combine_first(
        sv_dim["Территориальный менеджер dim raw"]
    )

    directory = teams_dir.merge(sv_dim, on="ID супервайзера", how="outer")
    directory["Супервайзер"] = directory.get("Супервайзер dim").combine_first(directory["Супервайзер"])
    directory["ID территориального менеджера"] = directory["ID территориального менеджера"].replace("", pd.NA)
    directory["Территориальный менеджер"] = directory["Территориальный менеджер"].replace("", pd.NA)
    directory["ID территориального менеджера"] = directory["ID территориального менеджера"].combine_first(
        directory.get("ID территориального менеджера dim")
    )
    directory["Территориальный менеджер"] = directory["Территориальный менеджер"].combine_first(
        directory.get("Территориальный менеджер dim")
    )
    tm_ids = directory["ID территориального менеджера"].astype("string").str.strip()
    invalid_tm = tm_ids.notna() & ~tm_ids.isin(valid_tm_ids) & tm_ids.ne(NO_TM_ID)
    directory.loc[invalid_tm, "ID территориального менеджера"] = pd.NA
    directory.loc[invalid_tm, "Территориальный менеджер"] = pd.NA
    directory = normalize_confirmed_tm(directory)
    directory["Регион BI"] = (
        directory["Регион BI"]
        .combine_first(directory.get("Регион BI dim"))
        .combine_first(directory.get("Регион BI tm dim"))
    )
    directory["Группа региона"] = (
        directory["Группа региона"]
        .combine_first(directory.get("Группа региона dim"))
        .combine_first(directory.get("Группа региона tm dim"))
    )
    directory = directory.drop(
        columns=[
            "Супервайзер dim",
            "Регион BI dim",
            "Группа региона dim",
            "ID территориального менеджера dim",
            "Территориальный менеджер dim",
            "Территориальный менеджер dim raw",
            "Регион BI tm dim",
            "Группа региона tm dim",
        ],
        errors="ignore",
    )
    directory = directory.sort_values(["Регион BI", "Супервайзер", "ID супервайзера"], na_position="last")
    directory = directory.drop_duplicates("ID супервайзера", keep="first")
    return directory


def _build_sv_monthly_snapshot(
    kpi: pd.DataFrame,
    okk: pd.DataFrame,
    learning_fact: pd.DataFrame,
    teams: pd.DataFrame,
    enps: pd.DataFrame,
    dim_employees: pd.DataFrame,
) -> pd.DataFrame:
    kpi_work = kpi.copy()
    for column in ["Супервайзер"]:
        if column in kpi_work.columns:
            kpi_work[column] = kpi_work[column].replace("", pd.NA)
    if "Вакансия" in kpi_work.columns:
        kpi_work = kpi_work[kpi_work["Вакансия"] != True].copy()
    allowed_months = set(pd.to_datetime(kpi_work["MonthStart"], errors="coerce").dropna().unique())
    allowed_months |= set(pd.to_datetime(okk["MonthStart"], errors="coerce").dropna().unique())
    allowed_months = {month for month in allowed_months if pd.notna(month) and month.year >= REPORT_START_YEAR}
    allowed_months_list = sorted(pd.to_datetime(list(allowed_months)))
    okk = okk[okk["MonthStart"].isin(allowed_months)].copy()
    max_allowed_month = max(allowed_months_list) if allowed_months_list else None
    if max_allowed_month is not None:
        learning_fact = learning_fact[pd.to_datetime(learning_fact["StartMonth"], errors="coerce") <= max_allowed_month].copy()
    supervisor_directory = _build_supervisor_directory(kpi_work, teams, dim_employees)

    kpi_sv = (
        kpi_work.groupby(
            ["MonthStart", "YearMonth", "ID супервайзера"],
            dropna=False,
        )
        .agg(
            **{
                "Супервайзер": ("Супервайзер", "first"),
                "Регион BI": ("Регион BI", mode_or_first),
                "ID территориального менеджера": ("ID территориального менеджера", mode_or_first),
                "Территориальный менеджер": ("Территориальный менеджер", mode_or_first),
                "Количество ТМ по RTM": ("Территориальный менеджер", "nunique"),
                "KPI проекта %": ("KPI проекта %", "mean"),
                "Код маршрута СВ": ("Код маршрута СВ", "first"),
                **{
                    column: (column, "mean")
                    for column in KPI_COMPONENT_COLUMNS
                    if column in kpi_work.columns
                },
            }
        )
        .reset_index()
    )
    multiple_tm = kpi_sv["Количество ТМ по RTM"].gt(1)
    kpi_sv.loc[multiple_tm, "ID территориального менеджера"] = pd.NA
    kpi_sv.loc[multiple_tm, "Территориальный менеджер"] = pd.NA

    okk_sv = (
        okk.groupby(
            ["MonthStart", "YearMonth", "ID супервайзера"],
            dropna=False,
        )
        .agg(
            **{
                "Супервайзер": ("Супервайзер", "first"),
                "OSA %": ("% наличия товара на полке", "mean"),
                "PICOS %": ("% наличия PICoS", "mean"),
                "ОКК %": ("Качество визита", "mean"),
                "Фрод %": ("Флаг фальсификации", "mean"),
                "Фрод кол-во": ("Флаг фальсификации", lambda s: s.fillna(False).eq(True).sum()),
                "ТТ всего": ("Код ТТ", pd.Series.nunique),
            }
        )
        .reset_index()
    )

    learn_sv = _build_learning_sv_monthly(learning_fact, teams, allowed_months_list)

    sv_base = kpi_sv.merge(
        okk_sv,
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="outer",
        suffixes=("", "_okk"),
    )
    if "Супервайзер_okk" in sv_base.columns:
        sv_base["Супервайзер"] = sv_base["Супервайзер"].combine_first(sv_base["Супервайзер_okk"])
        sv_base = sv_base.drop(columns=["Супервайзер_okk"])
    if "Регион BI_okk" in sv_base.columns:
        sv_base = sv_base.drop(columns=["Регион BI_okk"])

    sv_base = sv_base.merge(
        learn_sv,
        on=["MonthStart", "YearMonth", "ID супервайзера"],
        how="left",
        suffixes=("", "_learn"),
    )
    if "Супервайзер_learn" in sv_base.columns:
        sv_base["Супервайзер"] = sv_base["Супервайзер"].combine_first(sv_base["Супервайзер_learn"])
        sv_base = sv_base.drop(columns=["Супервайзер_learn"])
    if "Регион BI_learn" in sv_base.columns:
        sv_base = sv_base.drop(columns=["Регион BI_learn"])

    sv_base = sv_base.merge(
        supervisor_directory,
        on=["ID супервайзера"],
        how="left",
        suffixes=("", "_dir"),
    )
    active_sv_ids = set(supervisor_directory["ID супервайзера"].dropna().astype(str))
    sv_base = sv_base[sv_base["ID супервайзера"].astype(str).isin(active_sv_ids)].copy()
    sv_base["Супервайзер"] = sv_base["Супервайзер_dir"].combine_first(sv_base["Супервайзер"])
    if "Территориальный менеджер_dir" in sv_base.columns:
        sv_base["Территориальный менеджер"] = sv_base["Территориальный менеджер_dir"].combine_first(
            sv_base.get("Территориальный менеджер")
        )
    if "Регион BI_dir" in sv_base.columns:
        sv_base["Регион BI"] = sv_base["Регион BI_dir"].combine_first(sv_base["Регион BI"])
    if "Группа региона_dir" in sv_base.columns:
        sv_base["Группа региона"] = sv_base["Группа региона_dir"]
    if "ID территориального менеджера_dir" in sv_base.columns:
        sv_base["ID территориального менеджера"] = sv_base["ID территориального менеджера_dir"].combine_first(
            sv_base.get("ID территориального менеджера")
        )
    sv_base = sv_base.drop(
        columns=[
            "Супервайзер_dir",
            "Регион BI_dir",
            "Группа региона_dir",
            "Территориальный менеджер_dir",
            "ID территориального менеджера_dir",
        ],
        errors="ignore",
    )
    sv_base = normalize_confirmed_tm(sv_base)

    enps_quarterly = _build_enps_quarterly(enps)
    sv_base = _attach_last_quarter_metric(
        sv_base, enps_quarterly, "Риск ухода региона %", period="year"
    )
    sv_base = _coerce_numeric_columns(
        sv_base,
        [
            "KPI проекта %",
            *KPI_COMPONENT_COLUMNS,
            "ОКК %",
            "Обучение %",
            "Фрод %",
            "Фрод кол-во",
            "Риск ухода региона %",
            "ТТ всего",
        ],
    )

    sv_base["Код СВ"] = _extract_sv_code(sv_base["Код маршрута СВ"])
    sv_base["СВ / Объект"] = sv_base["Супервайзер"].fillna(sv_base["Код СВ"]).fillna("СВ")

    def _driver_gaps(row: pd.Series) -> dict[str, float]:
        return {
            "Разобрать KPI": max(0.0, TARGET_KPI - row["KPI проекта %"]) if pd.notna(row.get("KPI проекта %")) else 0.0,
            "Снизить фрод": max(0.0, row["Фрод %"] - TARGET_FRAUD) if pd.notna(row.get("Фрод %")) else 0.0,
            "Подтянуть ОКК": max(0.0, TARGET_OKK - row["ОКК %"]) if pd.notna(row.get("ОКК %")) else 0.0,
            "Подтянуть OSA": max(0.0, 1.0 - row["OSA выполнение %"]) if pd.notna(row.get("OSA выполнение %")) else 0.0,
            "Устранить просадку PICOS": max(0.0, 1.0 - row["PICOS выполнение %"]) if pd.notna(row.get("PICOS выполнение %")) else 0.0,
            "Подтянуть TOP16": max(0.0, 1.0 - row["TOP16 выполнение %"]) if pd.notna(row.get("TOP16 выполнение %")) else 0.0,
            "Подтянуть обучение": max(0.0, TARGET_LEARN - row["Обучение %"]) if pd.notna(row.get("Обучение %")) else 0.0,
        }

    def _first_action(row: pd.Series) -> str | None:
        gaps = _driver_gaps(row)
        action_priority = [
            "Разобрать KPI",
            "Снизить фрод",
            "Подтянуть ОКК",
            "Подтянуть OSA",
            "Устранить просадку PICOS",
            "Подтянуть TOP16",
            "Подтянуть обучение",
        ]
        active = [name for name in action_priority if gaps.get(name, 0) > 0]
        if not active:
            return None
        if gaps["Разобрать KPI"] > 0:
            return "Разобрать KPI"
        return max(active, key=lambda name: gaps[name])

    def _action_comment(row: pd.Series) -> str | None:
        gaps = _driver_gaps(row)
        reasons = []
        if gaps["Снизить фрод"] > 0:
            reasons.append(f"фрод {_format_pct(row['Фрод %'])}")
        if gaps["Подтянуть ОКК"] > 0:
            reasons.append(f"ОКК {_format_pct(row['ОКК %'])}")
        if gaps["Подтянуть OSA"] > 0:
            reasons.append(f"OSA {_format_pct(row['OSA выполнение %'])}")
        if gaps["Устранить просадку PICOS"] > 0:
            reasons.append(f"PICOS {_format_pct(row['PICOS выполнение %'])}")
        if gaps["Подтянуть TOP16"] > 0:
            reasons.append(f"TOP16 {_format_pct(row['TOP16 выполнение %'])}")
        if gaps["Подтянуть обучение"] > 0:
            reasons.append(f"обучение {_format_pct(row['Обучение %'])}")

        if gaps["Разобрать KPI"] > 0:
            if reasons:
                return "просадка KPI: " + ", ".join(reasons[:2])
            return "просадка KPI без расшифровки драйвера"

        if reasons:
            return ", ".join(reasons[:2])
        return None

    sv_base["Первое действие"] = sv_base.apply(_first_action, axis=1)
    sv_base["Комментарий действия"] = sv_base.apply(_action_comment, axis=1)

    def _team_status(row: pd.Series) -> str:
        breaches = 0
        breaches += 1 if pd.notna(row.get("KPI проекта %")) and row["KPI проекта %"] < TARGET_KPI else 0
        breaches += 1 if pd.notna(row.get("ОКК %")) and row["ОКК %"] < TARGET_OKK else 0
        breaches += 1 if pd.notna(row.get("Обучение %")) and row["Обучение %"] < TARGET_LEARN else 0
        breaches += 1 if pd.notna(row.get("Фрод %")) and row["Фрод %"] > TARGET_FRAUD else 0
        breaches += 1 if pd.notna(row.get("Риск ухода региона %")) and row["Риск ухода региона %"] >= TARGET_RISK else 0
        if breaches >= 2:
            return "Высокий риск"
        if breaches == 1:
            return "Контроль"
        return "Стабильно"

    sv_base["Статус команды"] = sv_base.apply(_team_status, axis=1)

    columns = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "ID супервайзера",
        "Супервайзер",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Код маршрута СВ",
        "Код СВ",
        "СВ / Объект",
        "KPI проекта %",
        *KPI_COMPONENT_COLUMNS,
        "ОКК %",
        "Обучение %",
        "Фрод %",
        "Фрод кол-во",
        "Риск ухода региона %",
        "Первое действие",
        "Комментарий действия",
        "Статус команды",
        "Группа региона",
    ]
    return sv_base[[c for c in columns if c in sv_base.columns]].copy()


def build_page2_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    kpi = pd.read_parquet(out_dir / "kpi_fact.parquet")
    kpi_tt_path = out_dir / "kpi_client_tt_fact.parquet"
    kpi_tt_direct = pd.read_parquet(kpi_tt_path) if kpi_tt_path.exists() else pd.DataFrame()
    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    learning_monthly = pd.read_parquet(out_dir / "learning_monthly.parquet")
    learning_fact = pd.read_parquet(out_dir / "learning_fact.parquet")
    teams = pd.read_parquet(out_dir / "dim_teams.parquet")
    enps = pd.read_parquet(out_dir / "enps_fact.parquet")
    dim_employees = pd.read_parquet(out_dir / "dim_employees.parquet")

    region_base = _build_region_monthly_base(kpi, okk, learning_monthly, enps, kpi_tt_direct=kpi_tt_direct)
    actions = _build_actions_monthly(region_base, okk, learning_monthly)
    sv_snapshot = _build_sv_monthly_snapshot(kpi, okk, learning_fact, teams, enps, dim_employees)
    rtm_path = out_dir / "rtm_employee_visits.parquet"
    if rtm_path.exists():
        source_org = build_rtm_month_org(pd.read_parquet(rtm_path), "ID супервайзера")
        if not source_org.empty:
            source_org = source_org.rename(
                columns={
                    "ID территориального менеджера": "ID территориального менеджера RTM",
                    "Территориальный менеджер": "Территориальный менеджер RTM",
                    "Регион BI": "Регион BI RTM",
                }
            )
            sv_snapshot = sv_snapshot.merge(
                source_org,
                on=["MonthStart", "YearMonth", "ID супервайзера"],
                how="left",
            )
            tm_ids = sv_snapshot["ID территориального менеджера"].astype("string").str.strip()
            missing_tm = tm_ids.isna() | tm_ids.eq("")
            source_available = sv_snapshot["ID территориального менеджера RTM"].notna()
            source_fill = missing_tm & source_available
            sv_snapshot.loc[source_fill, "ID территориального менеджера"] = sv_snapshot.loc[
                source_fill, "ID территориального менеджера RTM"
            ]
            sv_snapshot.loc[source_fill, "Территориальный менеджер"] = sv_snapshot.loc[
                source_fill, "Территориальный менеджер RTM"
            ]
            sv_snapshot["Регион BI"] = sv_snapshot["Регион BI"].combine_first(sv_snapshot["Регион BI RTM"])
            sv_snapshot = normalize_confirmed_tm(sv_snapshot)
            sv_snapshot = sv_snapshot.drop(
                columns=["ID территориального менеджера RTM", "Территориальный менеджер RTM", "Регион BI RTM"],
                errors="ignore",
            )

    save_parquet(actions, str(out_dir / "page2_actions_monthly.parquet"))
    save_parquet(sv_snapshot, str(out_dir / "page2_sv_monthly_snapshot.parquet"))

    print(f"\n  Page2 actions: {len(actions)} строк")
    print(f"  Page2 SV snapshot: {len(sv_snapshot)} строк")
    return actions, sv_snapshot


if __name__ == "__main__":
    build_page2_data()
