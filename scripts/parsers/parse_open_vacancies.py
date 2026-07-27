import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.staffing_utils import build_staffing_reference, match_leader_name, resolve_region, role_bucket
from scripts.utils import load_settings, save_parquet


def parse_open_vacancies() -> pd.DataFrame:
    settings = load_settings()
    users_folder = Path(settings["sources"]["users"]["folder"])
    out_dir = Path(settings["paths"]["out"])
    source = users_folder / "open_vacation" / "ВАКАНСИИ И ЭТАПЫ ПОДБОРА.xlsx"
    output = out_dir / "fact_open_vacancies.parquet"

    if not source.exists():
        print("  OPEN VACANCIES: файл не найден, пропускаем")
        return pd.DataFrame()

    dim = pd.read_parquet(settings["sources"]["users"]["output"])
    teams = pd.read_parquet(settings["sources"]["teams"]["output"])
    reference = build_staffing_reference(dim, teams)

    raw = pd.read_excel(source, header=2)
    raw = raw[raw["Проект"].astype(str).str.contains("Danone", case=False, na=False)].copy()
    if raw.empty:
        print("  OPEN VACANCIES: данных по Danone нет")
        return pd.DataFrame()

    snapshot_date = pd.Timestamp.fromtimestamp(source.stat().st_mtime).normalize()
    month_start = snapshot_date.to_period("M").to_timestamp()

    work = raw.copy()
    work["SnapshotDate"] = snapshot_date
    work["MonthStart"] = month_start
    work["YearMonth"] = month_start.year * 100 + month_start.month
    work["Роль вакансии"] = work["Название"].map(role_bucket)
    work["Дата открытия"] = pd.to_datetime(work["Последние Дата открытия"], errors="coerce")
    work["Статус вакансии"] = work["Последние Статус"]
    work["Причина открытия вакансии"] = work["Последние Причина открытия"]
    work["Этап подбора"] = work["Последние Этап подбора"]
    work["Контактное лицо"] = work["Последние Контактное лицо"]
    work["Приостановлена"] = work["Статус вакансии"].astype(str).str.contains("приост", case=False, na=False)

    enriched_rows: list[dict] = []
    for _, row in work.iterrows():
        leader = match_leader_name(row.get("Контактное лицо"), reference, allow_tm=True)
        region_bi = resolve_region(
            row.get("Территория"),
            row.get("Область"),
            row.get("Город"),
            reference=reference,
        )
        if not region_bi:
            region_bi = leader.get("Регион BI")

        supervisor_id = leader.get("ID супервайзера", pd.NA)
        supervisor_name = leader.get("Супервайзер", pd.NA)
        tm_id = leader.get("ID территориального менеджера", pd.NA)
        tm_name = leader.get("Территориальный менеджер", pd.NA)

        enriched_rows.append(
            {
                "SnapshotDate": row["SnapshotDate"],
                "MonthStart": row["MonthStart"],
                "YearMonth": row["YearMonth"],
                "ID вакансии": row.get("id вакансии"),
                "Проект": row.get("Проект"),
                "Область": row.get("Область"),
                "Город": row.get("Город"),
                "Территория": row.get("Территория"),
                "Название вакансии": row.get("Название"),
                "Роль вакансии": row.get("Роль вакансии"),
                "Характер работы": row.get("Характер работы"),
                "Дата открытия": row.get("Дата открытия"),
                "Причина открытия вакансии": row.get("Причина открытия вакансии"),
                "Статус вакансии": row.get("Статус вакансии"),
                "Причина приостановки вакансии": row.get("Причина приостановки вакансии"),
                "Этап подбора": row.get("Этап подбора"),
                "Сложность поиска": row.get("Последние Сложность поиска"),
                "Контактное лицо": row.get("Контактное лицо"),
                "Рекрутер": row.get("Последние Рекрутер"),
                "Маршрут": row.get("Последние Маршрут"),
                "Ставка": pd.to_numeric(row.get("Последние Ставка"), errors="coerce"),
                "Доход": pd.to_numeric(row.get("Медиана Доход"), errors="coerce"),
                "Рыночная зарплата": pd.to_numeric(row.get("Медиана Рыночная зарплата"), errors="coerce"),
                "Отклонение от рынка": pd.to_numeric(row.get("Среднее значение Отклонение от рынка"), errors="coerce"),
                "Дней в работе": pd.to_numeric(row.get("Медиана Дней в работе"), errors="coerce"),
                "Дней в приостановке": pd.to_numeric(row.get("Среднее значение Количество дней в приостановке"), errors="coerce"),
                "Приостановлена": bool(row.get("Приостановлена")),
                "Регион BI": region_bi,
                "ID супервайзера": supervisor_id,
                "Супервайзер": supervisor_name,
                "ID территориального менеджера": tm_id,
                "Территориальный менеджер": tm_name,
            }
        )

    result = pd.DataFrame(enriched_rows)
    save_parquet(result, str(output))
    print(f"\n  Open vacancies: {len(result)} строк")
    return result


if __name__ == "__main__":
    parse_open_vacancies()
