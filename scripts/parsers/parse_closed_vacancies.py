import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.staffing_utils import build_staffing_reference, match_leader_name, resolve_region, role_bucket
from scripts.utils import load_settings, save_parquet


def _close_category(status) -> str:
    text = str(status or "").strip().lower()
    if "закрыт" in text:
        return "Успешно закрыта"
    if "отмен" in text:
        return "Отменена"
    return "Прочее"


def parse_closed_vacancies(
    dim: pd.DataFrame | None = None,
    teams: pd.DataFrame | None = None,
) -> pd.DataFrame:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    folder = Path(settings["sources"]["closed_vacancies"]["folder"])
    output = out_dir / "fact_closed_vacancies.parquet"

    files = sorted([p for p in folder.glob("*.xlsx") if p.is_file()])
    if not files:
        print("  CLOSED VACANCIES: файлы не найдены, пропускаем")
        return pd.DataFrame()

    source = files[-1]
    if dim is None or dim.empty:
        dim = pd.read_parquet(settings["sources"]["users"]["output"])
    if teams is None or teams.empty:
        teams = pd.read_parquet(settings["sources"]["teams"]["output"])
    reference = build_staffing_reference(dim, teams)

    raw = pd.read_excel(source, header=2)
    raw = raw[raw["Проект"].astype(str).str.contains("Danone", case=False, na=False)].copy()
    if raw.empty:
        print("  CLOSED VACANCIES: данных по Danone нет")
        return pd.DataFrame()

    work = raw.copy()
    work["Дата открытия"] = pd.to_datetime(work["Дата открытия"], errors="coerce")
    work["Дата закрытия"] = pd.to_datetime(work["Дата закрытия"], errors="coerce")
    work["MonthStart"] = work["Дата закрытия"].dt.to_period("M").dt.to_timestamp()
    work["YearMonth"] = (work["MonthStart"].dt.year * 100 + work["MonthStart"].dt.month).astype("Int64")
    work["Роль вакансии"] = work["Наименование вакансии"].map(role_bucket)
    work["Категория закрытия"] = work["Статус"].map(_close_category)

    enriched_rows: list[dict] = []
    for _, row in work.iterrows():
        leader = match_leader_name(row.get("Супервайзер"), reference, allow_tm=True)
        region_bi = resolve_region(
            row.get("Территория"),
            row.get("Город"),
            row.get("Зона ответственности"),
            reference=reference,
        )
        if not region_bi:
            region_bi = leader.get("Регион BI")

        supervisor_id = leader.get("ID супервайзера", pd.NA)
        supervisor_name = leader.get("Супервайзер", row.get("Супервайзер"))
        tm_id = leader.get("ID территориального менеджера", pd.NA)
        tm_name = leader.get("Территориальный менеджер", pd.NA)

        enriched_rows.append(
            {
                "MonthStart": row.get("MonthStart"),
                "YearMonth": row.get("YearMonth"),
                "ID вакансии": row.get("id вакансии"),
                "Проект": row.get("Проект"),
                "Территория": row.get("Территория"),
                "Статус вакансии": row.get("Статус"),
                "Категория закрытия": row.get("Категория закрытия"),
                "Супервайзер источник": row.get("Супервайзер"),
                "Супервайзер": supervisor_name,
                "ID супервайзера": supervisor_id,
                "ID территориального менеджера": tm_id,
                "Территориальный менеджер": tm_name,
                "Рекрутер": row.get("Рекрутер"),
                "Дата открытия": row.get("Дата открытия"),
                "Дата закрытия": row.get("Дата закрытия"),
                "Приоритет заявки": row.get("Приоритет заявки"),
                "Название вакансии": row.get("Наименование вакансии"),
                "Роль вакансии": row.get("Роль вакансии"),
                "Этап подбора": row.get("Этап подбора"),
                "Маршрут": row.get("Маршрут"),
                "Сложность поиска": row.get("Сложность поиска"),
                "Направление клиента": row.get("Направление клиента"),
                "Город": row.get("Город"),
                "Зона ответственности": row.get("Зона ответственности"),
                "Доход": pd.to_numeric(row.get("Доход"), errors="coerce"),
                "Рыночная зарплата": pd.to_numeric(row.get("Рыночная зарплата"), errors="coerce"),
                "Тип занятости": pd.to_numeric(row.get("Тип занятости"), errors="coerce"),
                "Дней в работе": pd.to_numeric(row.get("Дней в работе"), errors="coerce"),
                "Дней в приостановке": pd.to_numeric(row.get("Количество дней в приостановке"), errors="coerce"),
                "Отсмотрено": pd.to_numeric(row.get("Отсмотрено"), errors="coerce"),
                "Интервью у рекрутера": pd.to_numeric(row.get("Всего состоялось интервью у рекрутера"), errors="coerce"),
                "Направлено заказчику": pd.to_numeric(row.get("Направлено заказчику"), errors="coerce"),
                "Финалист": row.get("Финалист"),
                "Комментарий": row.get("Комментарий"),
                "Регион BI": region_bi,
            }
        )

    result = pd.DataFrame(enriched_rows)
    save_parquet(result, str(output))
    print(f"\n  Closed vacancies: {len(result)} строк")
    return result


if __name__ == "__main__":
    parse_closed_vacancies()
