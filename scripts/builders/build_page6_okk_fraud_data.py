import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.utils import load_settings, save_parquet, normalize_pct as _normalize_pct


TARGET_KPI = 0.75
TARGET_KPI_CURRENT = 0.95
TARGET_OSA = 0.85
TARGET_PICOS = 0.85
TARGET_PHOTO = 0.75


def _safe_mean_as_pct(series: pd.Series):
    values = _normalize_pct(series)
    if values.notna().any():
        return float(values.mean())
    return pd.NA


def _build_region_trend(okk: pd.DataFrame, region_snapshot: pd.DataFrame) -> pd.DataFrame:
    base = (
        okk.groupby(["MonthStart", "YearMonth", "Регион BI"], dropna=False)
        .agg(
            {
                "Качество визита": "mean",
                "Флаг фальсификации": "mean",
                "% наличия товара на полке": "mean",
                "% наличия PICoS": "mean",
            }
        )
        .reset_index()
        .rename(
            columns={
                "Качество визита": "ОКК %",
                "Флаг фальсификации": "Фрод %",
                "% наличия товара на полке": "OSA %",
                "% наличия PICoS": "PICOS %",
            }
        )
    )

    keep = region_snapshot[
        ["MonthStart", "YearMonth", "Регион BI", "KPI проекта %", "Фрод кол-во"]
    ].copy()
    merged = base.merge(keep, on=["MonthStart", "YearMonth", "Регион BI"], how="left")
    return merged.sort_values(["MonthStart", "Регион BI"]).reset_index(drop=True)


def _build_block_anomalies(
    okk: pd.DataFrame,
    region_trend: pd.DataFrame,
) -> pd.DataFrame:
    metrics = [
        ("Фотоаудит", "Корректность фото", "Правила фотографирования: все соблюдены", "mean"),
        ("Полка", "Бренд-блок", "Стандарты: бренд-блок", "mean"),
        ("Наличие", "OSA", "% наличия товара на полке", "mean"),
        ("PICOS", "Стандарт выкладки", "% наличия PICoS", "mean"),
        ("Отчетность", "Антифрод", "Флаг фальсификации", "antifraud"),
    ]

    rows: list[dict] = []
    for month_start, month_df in okk.groupby("MonthStart", dropna=False):
        year_month = month_df["YearMonth"].dropna().iloc[0] if month_df["YearMonth"].notna().any() else pd.NA
        for block, metric_name, source_col, mode in metrics:
            if source_col not in month_df.columns:
                continue

            region_rows: list[dict] = []
            for region, region_df in month_df.groupby("Регион BI", dropna=False):
                if mode == "antifraud":
                    value = 1 - _normalize_pct(region_df[source_col]).mean() if region_df[source_col].notna().any() else pd.NA
                else:
                    value = _safe_mean_as_pct(region_df[source_col])
                region_rows.append(
                    {
                        "MonthStart": month_start,
                        "YearMonth": year_month,
                        "Регион BI": region,
                        "Блок": block,
                        "Метрика": metric_name,
                        "ОКК %": value,
                    }
                )

            region_metric = pd.DataFrame(region_rows)
            region_metric = region_metric[region_metric["ОКК %"].notna()].copy()
            if region_metric.empty:
                continue

            worst = region_metric.sort_values(["ОКК %", "Регион BI"], ascending=[True, True]).iloc[0].to_dict()
            region_row = region_trend[
                (region_trend["MonthStart"] == month_start)
                & (region_trend["Регион BI"] == worst["Регион BI"])
            ]
            if not region_row.empty:
                worst["KPI проекта %"] = region_row["KPI проекта %"].iloc[0]
                worst["Фрод %"] = region_row["Фрод %"].iloc[0]
                worst["Просадка KPI %"] = (
                    max(0.0, TARGET_KPI_CURRENT - float(region_row["KPI проекта %"].iloc[0]))
                    if pd.notna(region_row["KPI проекта %"].iloc[0])
                    else pd.NA
                )
            else:
                worst["KPI проекта %"] = pd.NA
                worst["Фрод %"] = pd.NA
                worst["Просадка KPI %"] = pd.NA
            worst["Зона"] = worst["Регион BI"]
            rows.append(worst)

    return pd.DataFrame(rows)[
        [
            "MonthStart",
            "YearMonth",
            "Регион BI",
            "Блок",
            "Метрика",
            "ОКК %",
            "KPI проекта %",
            "Просадка KPI %",
            "Фрод %",
            "Зона",
        ]
    ].sort_values(["MonthStart", "Блок"]).reset_index(drop=True)


def _build_violation_impact(okk: pd.DataFrame) -> pd.DataFrame:
    checks = [
        ("Фрод", okk["Флаг фальсификации"].eq(True)),
        ("OSA ниже нормы", _normalize_pct(okk["% наличия товара на полке"]) < TARGET_OSA if "% наличия товара на полке" in okk.columns else pd.Series(False, index=okk.index)),
        ("PICOS ниже нормы", _normalize_pct(okk["% наличия PICoS"]) < TARGET_PICOS if "% наличия PICoS" in okk.columns else pd.Series(False, index=okk.index)),
        ("Фото ниже нормы", _normalize_pct(okk["Правила фотографирования: все соблюдены"]) < TARGET_PHOTO if "Правила фотографирования: все соблюдены" in okk.columns else pd.Series(False, index=okk.index)),
        ("Бренд-блок нарушен", _normalize_pct(okk["Стандарты: бренд-блок"]) < 1 if "Стандарты: бренд-блок" in okk.columns else pd.Series(False, index=okk.index)),
    ]

    rows: list[dict] = []
    for month_start, month_df in okk.groupby("MonthStart", dropna=False):
        year_month = month_df["YearMonth"].dropna().iloc[0] if month_df["YearMonth"].notna().any() else pd.NA
        for region, region_df in month_df.groupby("Регион BI", dropna=False):
            quality = _normalize_pct(region_df["Качество визита"])
            for name, global_flag in checks:
                flag = global_flag.loc[region_df.index]
                quality_bad = quality[flag.fillna(False)]
                quality_ok = quality[~flag.fillna(False)]
                impact_pp = pd.NA
                if quality_bad.notna().any() and quality_ok.notna().any():
                    impact_pp = max(0.0, float(quality_ok.mean()) - float(quality_bad.mean()))
                rows.append(
                    {
                        "MonthStart": month_start,
                        "YearMonth": year_month,
                        "Регион BI": region,
                        "Нарушение": name,
                        "Просадка ОКК %": impact_pp,
                        "Доля нарушения %": float(flag.fillna(False).mean()) if len(flag) else pd.NA,
                    }
                )

    return pd.DataFrame(rows).sort_values(["MonthStart", "Просадка ОКК %"], ascending=[True, False]).reset_index(drop=True)


def _build_signals(
    okk: pd.DataFrame,
    region_trend: pd.DataFrame,
    d_supervisor: pd.DataFrame,
) -> pd.DataFrame:
    sv_lookup = (
        d_supervisor[["ID супервайзера", "Код СВ", "Супервайзер"]]
        .dropna(subset=["ID супервайзера"])
        .drop_duplicates("ID супервайзера")
        .set_index("ID супервайзера")
        .to_dict("index")
    )

    def _sv_label(supervisor_id):
        row = sv_lookup.get(supervisor_id, {})
        full_name = row.get("Супервайзер")
        if isinstance(full_name, str) and full_name.strip():
            return full_name.strip()
        sv_code = row.get("Код СВ")
        if isinstance(sv_code, str) and sv_code.strip():
            return sv_code.strip()
        return supervisor_id

    rows: list[dict] = []
    for month_start, month_df in okk.groupby("MonthStart", dropna=False):
        year_month = month_df["YearMonth"].dropna().iloc[0] if month_df["YearMonth"].notna().any() else pd.NA

        sv_group = (
            month_df.groupby("ID супервайзера", dropna=False)
            .agg({"Флаг фальсификации": ["sum", "mean"]})
        )
        if not sv_group.empty:
            sv_group.columns = ["Фрод кол-во", "Фрод %"]
            sv_group = sv_group.reset_index().sort_values(["Фрод кол-во", "Фрод %"], ascending=[False, False])
            top_sv = sv_group.iloc[0]
            sv_name = _sv_label(top_sv["ID супервайзера"])
            sv_region = (
                month_df.loc[month_df["ID супервайзера"] == top_sv["ID супервайзера"], "Регион BI"]
                .dropna()
                .mode()
            )
            fraud_count = float(top_sv["Фрод кол-во"])
            if fraud_count >= 5:
                fraud_risk = "Критично"
            elif fraud_count >= 2:
                fraud_risk = "Высокий риск"
            else:
                # 0-1 нарушение — это ещё не "повторяется", спокойный уровень
                # (как у остальных 3 сигналов в этой функции)
                fraud_risk = "Контроль"
            rows.append(
                {
                    "MonthStart": month_start,
                    "YearMonth": year_month,
                    "Регион BI": sv_region.iloc[0] if not sv_region.empty else pd.NA,
                    "Сигнал": "Фрод повторяется у СВ",
                    "Объект": sv_name if pd.notna(sv_name) else "СВ",
                    "Риск": fraud_risk,
                    "Действие": "аудит контроля",
                }
            )

        prev = region_trend[region_trend["MonthStart"] < month_start].copy()
        curr = region_trend[region_trend["MonthStart"] == month_start].copy()
        if not curr.empty and not prev.empty:
            prev_last = (
                prev.sort_values("MonthStart")
                .groupby("Регион BI", as_index=False)
                .tail(1)[["Регион BI", "ОКК %", "KPI проекта %"]]
                .rename(columns={"ОКК %": "ОКК_prev", "KPI проекта %": "KPI_prev"})
            )
            trend_cmp = curr.merge(prev_last, on="Регион BI", how="left")
            trend_cmp["ΔOKK"] = trend_cmp["ОКК %"] - trend_cmp["ОКК_prev"]
            trend_cmp["ΔKPI"] = trend_cmp["KPI проекта %"] - trend_cmp["KPI_prev"]
            trend_cmp["Разрыв тренда"] = trend_cmp["ΔOKK"] - trend_cmp["ΔKPI"]
            trend_cmp = trend_cmp[trend_cmp["ΔOKK"].notna()].sort_values(["Разрыв тренда", "ОКК %"])
            if not trend_cmp.empty:
                region_row = trend_cmp.iloc[0]
                rows.append(
                    {
                        "MonthStart": month_start,
                        "YearMonth": year_month,
                        "Регион BI": region_row["Регион BI"],
                        "Сигнал": "ОКК падает раньше KPI",
                        "Объект": region_row["Регион BI"],
                        "Риск": "Высокий риск" if float(region_row["ΔOKK"]) <= -0.03 else "Контроль",
                        "Действие": "проверка маршрутов",
                    }
                )

        net_cols = [c for c in ["Сеть", "% наличия товара на полке", "% наличия PICoS"] if c in month_df.columns]
        if len(net_cols) == 3:
            network = (
                month_df.groupby(["Сеть", "Регион BI"], dropna=False)
                .agg(
                    {
                        "% наличия товара на полке": "mean",
                        "% наличия PICoS": "mean",
                    }
                )
                .reset_index()
                .rename(
                    columns={
                        "% наличия товара на полке": "OSA %",
                        "% наличия PICoS": "PICOS %",
                    }
                )
            )
            network["Gap"] = (TARGET_OSA - network["OSA %"]).clip(lower=0) + (TARGET_PICOS - network["PICOS %"]).clip(lower=0)
            network = network.sort_values(["Gap", "OSA %", "PICOS %"], ascending=[False, True, True])
            if not network.empty:
                net_row = network.iloc[0]
                rows.append(
                    {
                        "MonthStart": month_start,
                        "YearMonth": year_month,
                        "Регион BI": net_row["Регион BI"],
                        "Сигнал": "PICOS слабый, OSA ниже",
                        "Объект": net_row["Сеть"],
                        "Риск": "Высокий риск" if float(net_row["Gap"]) >= 0.15 else "Контроль",
                        "Действие": "проверить выкладку",
                    }
                )

        if {"Мерчендайзер", "Флаг фальсификации", "Правила фотографирования: все соблюдены"} <= set(month_df.columns):
            merch = (
                month_df.groupby(["Мерчендайзер", "Регион BI"], dropna=False)
                .agg(
                    {
                        "Флаг фальсификации": "mean",
                        "Правила фотографирования: все соблюдены": "mean",
                    }
                )
                .reset_index()
                .rename(
                    columns={
                        "Флаг фальсификации": "Фрод %",
                        "Правила фотографирования: все соблюдены": "Фото %",
                    }
                )
            )
            merch["Риск score"] = merch["Фрод %"].fillna(0) + (1 - merch["Фото %"].fillna(0))
            merch = merch.sort_values("Риск score", ascending=False)
            if not merch.empty:
                merch_row = merch.iloc[0]
                rows.append(
                    {
                        "MonthStart": month_start,
                        "YearMonth": year_month,
                        "Регион BI": merch_row["Регион BI"],
                        "Сигнал": "Низкое фото при высоком фроде",
                        "Объект": merch_row["Мерчендайзер"],
                        "Риск": "Высокий риск" if float(merch_row["Риск score"]) >= 0.8 else "Контроль",
                        "Действие": "контроль СВ",
                    }
                )

    return pd.DataFrame(rows).sort_values(["MonthStart", "Сигнал"]).reset_index(drop=True)


def _build_insights_monthly(
    block_anomalies: pd.DataFrame,
    impact: pd.DataFrame,
    signals: pd.DataFrame,
) -> pd.DataFrame:
    anomaly = block_anomalies.copy()
    anomaly["Тип блока"] = "Аномалия"
    anomaly["Категория"] = anomaly["Блок"]
    anomaly["Показатель"] = anomaly["Метрика"]
    anomaly["% из проверок ОКК"] = anomaly["ОКК %"]
    anomaly["Просадка KPI %"] = anomaly["Просадка KPI %"]
    anomaly["Объект"] = anomaly["Зона"]
    anomaly["Просадка ОКК %"] = pd.NA
    anomaly["Доля нарушения %"] = pd.NA
    anomaly["Риск"] = pd.NA
    anomaly["Действие"] = pd.NA
    anomaly["Порядок"] = 1

    impact_df = impact.copy()
    impact_df["Тип блока"] = "Влияние"
    impact_df["Категория"] = "Нарушение"
    impact_df["Показатель"] = impact_df["Нарушение"]
    impact_df["% из проверок ОКК"] = pd.NA
    impact_df["Просадка KPI %"] = pd.NA
    impact_df["Фрод %"] = pd.NA
    impact_df["Объект"] = impact_df["Регион BI"]
    impact_df["Риск"] = pd.NA
    impact_df["Действие"] = pd.NA
    impact_df["Порядок"] = 2

    signal = signals.copy()
    signal["Тип блока"] = "Сигнал"
    signal["Категория"] = signal["Сигнал"]
    signal["Показатель"] = signal["Сигнал"]
    signal["% из проверок ОКК"] = pd.NA
    signal["Просадка KPI %"] = pd.NA
    signal["Фрод %"] = pd.NA
    signal["Просадка ОКК %"] = pd.NA
    signal["Доля нарушения %"] = pd.NA
    signal["Порядок"] = 3

    keep_cols = [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "Тип блока",
        "Категория",
        "Показатель",
        "% из проверок ОКК",
        "Просадка KPI %",
        "Фрод %",
        "Просадка ОКК %",
        "Доля нарушения %",
        "Объект",
        "Риск",
        "Действие",
        "Порядок",
    ]

    anomaly = anomaly[keep_cols]
    impact_df = impact_df[keep_cols]
    signal = signal[keep_cols]
    return pd.concat([anomaly, impact_df, signal], ignore_index=True).sort_values(
        ["MonthStart", "Порядок", "Категория", "Показатель", "Объект"]
    ).reset_index(drop=True)


def build_page6_okk_fraud_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    okk = pd.read_parquet(out_dir / "okk_fact.parquet")
    region_snapshot = pd.read_parquet(out_dir / "page1_region_monthly_snapshot.parquet")
    d_supervisor = pd.read_parquet(out_dir / "dSupervisor.parquet")
    region_trend = _build_region_trend(okk, region_snapshot)
    block_anomalies = _build_block_anomalies(okk, region_trend)
    impact = _build_violation_impact(okk)
    signals = _build_signals(okk, region_trend, d_supervisor)
    insights = _build_insights_monthly(block_anomalies, impact, signals)

    for frame, numeric_columns in [
        (
            region_trend,
            ["YearMonth", "ОКК %", "Фрод %", "OSA %", "PICOS %", "KPI проекта %", "Фрод кол-во"],
        ),
        (
            insights,
            ["YearMonth", "% из проверок ОКК", "Просадка KPI %", "Фрод %", "Просадка ОКК %", "Доля нарушения %", "Порядок"],
        ),
    ]:
        for column in numeric_columns:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

    save_parquet(region_trend, str(out_dir / "page6_okk_region_monthly.parquet"))
    save_parquet(insights, str(out_dir / "page6_okk_insights_monthly.parquet"))

    print(f"\n  Page6 OKK trend: {len(region_trend)} строк")
    print(f"  Page6 OKK insights: {len(insights)} строк")
    return region_trend, insights


if __name__ == "__main__":
    build_page6_okk_fraud_data()
