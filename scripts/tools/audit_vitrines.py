import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts import build_page1_monthly_snapshot as p1
from scripts import build_page3_data as p3
from scripts import build_page5_sv_oed_data as p5
from scripts import build_page7_tm_data as p7
from scripts.utils import load_settings


REQUIRED_TABLES = {
    "dim_employees": "dim_employees.parquet",
    "dim_teams": "dim_teams.parquet",
    "dMonth": "dMonth.parquet",
    "dQuarter": "dQuarter.parquet",
    "dRegion": "dRegion.parquet",
    "dSupervisor": "dSupervisor.parquet",
    "dTM": "dTM.parquet",
    "page1_region_monthly_snapshot": "page1_region_monthly_snapshot.parquet",
    "page2_actions_monthly": "page2_actions_monthly.parquet",
    "page2_sv_monthly_snapshot": "page2_sv_monthly_snapshot.parquet",
    "page3_merch_monthly_snapshot": "page3_merch_monthly_snapshot.parquet",
    "page4_tt_monthly_snapshot": "page4_tt_monthly_snapshot.parquet",
    "page5_sv_monthly_snapshot": "page5_sv_monthly_snapshot.parquet",
    "page5_sv_oed_quarterly": "page5_sv_oed_quarterly.parquet",
    "page6_okk_region_monthly": "page6_okk_region_monthly.parquet",
    "page6_okk_insights_monthly": "page6_okk_insights_monthly.parquet",
    "page7_tm_monthly_snapshot": "page7_tm_monthly_snapshot.parquet",
    "page7_tm_score_composition": "page7_tm_score_composition.parquet",
    "page8_learning_course_summary": "page8_learning_course_summary.parquet",
    "page8_learning_effect_trend": "page8_learning_effect_trend.parquet",
    "page8_learning_employee_matrix": "page8_learning_employee_matrix.parquet",
    "page9_climate_quarterly_region": "page9_climate_quarterly_region.parquet",
    "page9_climate_blocks_region": "page9_climate_blocks_region.parquet",
}


REQUIRED_COLUMNS = {
    "page1_region_monthly_snapshot": [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "KPI проекта %",
        "Качество визитов %",
        "Обязательное обучение %",
        "Фрод %",
        "Кадровая устойчивость %",
        "Доступность метрик %",
        "Индекс региона %",
        "Статус региона",
        "Текст приоритета",
    ],
    "page3_merch_monthly_snapshot": [
        "MonthStart",
        "YearMonth",
        "ID мерчендайзера",
        "Мерчендайзер",
        "Регион BI",
        "Территориальный менеджер",
        "KPI проекта %",
        "ОКК %",
        "Обучение %",
        "Аттестация клиента %",
        "Личная эффективность МЕ %",
        "Статус личной эффективности",
    ],
    "page4_tt_monthly_snapshot": [
        "MonthStart",
        "YearMonth",
        "ТТ",
        "Регион BI",
        "Сеть",
        "ТМ территория",
        "KPI проекта %",
        "ОКК %",
        "Сложность %",
        "Статус ТТ",
    ],
    "page5_sv_monthly_snapshot": [
        "MonthStart",
        "YearMonth",
        "ID супервайзера",
        "Супервайзер",
        "Территориальный менеджер",
        "KPI месяца %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод %",
        "Стабильность команды %",
        "Текучесть команды %",
        "Индекс эффективности СВ %",
        "Статус эффективности СВ",
        "Причина статуса СВ",
        "Личная эффективность СВ %",
        "Статус личной эффективности",
    ],
    "page6_okk_insights_monthly": [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "Тип блока",
        "Категория",
        "Показатель",
        "% из проверок ОКК",
        "KPI-разрыв %",
        "Фрод %",
        "Просадка ОКК %",
        "Доля нарушения %",
        "Объект",
        "Риск",
        "Действие",
    ],
    "page7_tm_monthly_snapshot": [
        "MonthStart",
        "YearMonth",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регионы ТМ",
        "Балл эффективности %",
        "Статус ТМ",
        "Причина статуса ТМ",
        "KPI месяца территории %",
        "Качество команды %",
        "Обучение команды %",
        "Фрод %",
        "Стабильность команды %",
        "Текучесть %",
    ],
    "page8_learning_employee_matrix": [
        "MonthStart",
        "YearMonth",
        "ID сотрудника",
        "Сотрудник",
        "Территориальный менеджер",
        "Регион BI",
        "Фотоаудит",
        "PICOS",
        "Доступность",
        "Антифрод",
        "Работа с ТТ",
        "Базовые стандарты",
        "% закрытых компетенций",
        "Незакрытая компетенция",
    ],
}


KEYS = {
    "page1_region_monthly_snapshot": ["YearMonth", "Регион BI"],
    "page2_sv_monthly_snapshot": ["YearMonth", "ID супервайзера"],
    "page3_merch_monthly_snapshot": ["YearMonth", "ID мерчендайзера"],
    "page4_tt_monthly_snapshot": ["YearMonth", "ТТ"],
    "page5_sv_monthly_snapshot": ["YearMonth", "ID супервайзера"],
    "page7_tm_monthly_snapshot": ["YearMonth", "ID территориального менеджера"],
    "page8_learning_employee_matrix": ["YearMonth", "ID сотрудника"],
    "dSupervisor": ["ID супервайзера"],
    "dTM": ["ID территориального менеджера"],
    "dRegion": ["Регион BI"],
}


ALLOWED_STATUSES = {
    "page1_region_monthly_snapshot": {"Стабильно", "Контроль", "Высокий риск", "Недостаточно данных"},
    "page3_merch_monthly_snapshot": {"Высокая личная готовность", "Соответствует роли", "Зона развития", "Недостаточно данных"},
    "page4_tt_monthly_snapshot": {"Недостаточно данных", "Эталон", "Контроль", "Не вина МЕ", "Сложная ТТ"},
    "page5_sv_monthly_snapshot": {"Стабильно", "Контроль", "Зона риска", "Недостаточно данных"},
    "page5_sv_monthly_snapshot_personal": {"Высокая личная готовность", "Соответствует роли", "Зона развития", "Новичок", "Недостаточно данных"},
    "page7_tm_monthly_snapshot": {"Стабильно", "Зона риска", "Недостаточно данных"},
}


PERCENT_DELTA_MARKERS = ("разрыв", "просадка", "эффект", "Δ", "изменение")


def _is_close(left, right, tolerance=0.0005) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    if pd.isna(left) or pd.isna(right):
        return False
    return abs(float(left) - float(right)) <= tolerance


def _clean_reason(value):
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text if text else pd.NA


class Audit:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.tables: dict[str, pd.DataFrame] = {}
        self.rows: list[dict] = []

    def add(self, level: str, table: str, check: str, details: str = "", count: int | None = None) -> None:
        self.rows.append(
            {
                "Уровень": level,
                "Таблица": table,
                "Проверка": check,
                "Количество": count,
                "Детали": details,
            }
        )

    def load_tables(self) -> None:
        for table, filename in REQUIRED_TABLES.items():
            path = self.out_dir / filename
            if not path.exists():
                self.add("ERROR", table, "Файл существует", f"Не найден {path}", 1)
                continue
            frame = pd.read_parquet(path)
            self.tables[table] = frame
            self.add("OK", table, "Файл существует", f"{len(frame)} строк, {len(frame.columns)} колонок")

    def check_required_columns(self) -> None:
        for table, columns in REQUIRED_COLUMNS.items():
            frame = self.tables.get(table)
            if frame is None:
                continue
            missing = [column for column in columns if column not in frame.columns]
            if missing:
                self.add("ERROR", table, "Обязательные колонки", ", ".join(missing), len(missing))
            else:
                self.add("OK", table, "Обязательные колонки", "Все на месте")

    def check_keys(self) -> None:
        for table, keys in KEYS.items():
            frame = self.tables.get(table)
            if frame is None:
                continue
            missing = [column for column in keys if column not in frame.columns]
            if missing:
                self.add("ERROR", table, "Ключи", f"Нет колонок: {', '.join(missing)}", len(missing))
                continue
            null_rows = frame[keys].isna().any(axis=1).sum()
            duplicate_rows = frame.duplicated(keys, keep=False).sum()
            if null_rows:
                self.add("ERROR", table, "Пустые ключи", f"Ключ: {', '.join(keys)}", int(null_rows))
            else:
                self.add("OK", table, "Пустые ключи", f"Ключ: {', '.join(keys)}")
            if duplicate_rows:
                self.add("ERROR", table, "Дубли ключей", f"Ключ: {', '.join(keys)}", int(duplicate_rows))
            else:
                self.add("OK", table, "Дубли ключей", f"Ключ: {', '.join(keys)}")

    def check_percent_types_and_ranges(self) -> None:
        for table, frame in self.tables.items():
            for column in frame.columns:
                if "%" not in str(column):
                    continue
                series = frame[column]
                if not pd.api.types.is_numeric_dtype(series):
                    sample = series.dropna().astype(str).head(3).tolist()
                    self.add("ERROR", table, "Тип процента", f"{column}: не numeric, пример {sample}", len(sample))
                    continue
                values = pd.to_numeric(series, errors="coerce")
                if not values.notna().any():
                    continue
                lower, upper = (-1.0, 1.0) if any(marker in str(column).lower() for marker in PERCENT_DELTA_MARKERS) else (0.0, 1.0)
                bad = values.notna() & ((values < lower - 0.00001) | (values > upper + 0.00001))
                if bad.any():
                    self.add(
                        "ERROR",
                        table,
                        "Диапазон процента",
                        f"{column}: min={values.min():.4f}, max={values.max():.4f}, ожидается {lower}..{upper}",
                        int(bad.sum()),
                    )

    def check_status_values(self) -> None:
        checks = [
            ("page1_region_monthly_snapshot", "Статус региона", ALLOWED_STATUSES["page1_region_monthly_snapshot"]),
            ("page3_merch_monthly_snapshot", "Статус личной эффективности", ALLOWED_STATUSES["page3_merch_monthly_snapshot"]),
            ("page4_tt_monthly_snapshot", "Статус ТТ", ALLOWED_STATUSES["page4_tt_monthly_snapshot"]),
            ("page5_sv_monthly_snapshot", "Статус эффективности СВ", ALLOWED_STATUSES["page5_sv_monthly_snapshot"]),
            ("page5_sv_monthly_snapshot", "Статус личной эффективности", ALLOWED_STATUSES["page5_sv_monthly_snapshot_personal"]),
            ("page7_tm_monthly_snapshot", "Статус ТМ", ALLOWED_STATUSES["page7_tm_monthly_snapshot"]),
        ]
        for table, column, allowed in checks:
            frame = self.tables.get(table)
            if frame is None or column not in frame.columns:
                continue
            unexpected = sorted(set(frame[column].dropna().astype(str)) - allowed)
            if unexpected:
                self.add("ERROR", table, f"Статусы: {column}", ", ".join(unexpected), len(unexpected))
            else:
                self.add("OK", table, f"Статусы: {column}", "Только согласованные значения")

    def check_region_scope(self) -> None:
        allowed_regions = set(self.tables.get("dRegion", pd.DataFrame()).get("Регион BI", pd.Series(dtype="object")).dropna().astype(str))
        if not allowed_regions:
            return
        for table, frame in self.tables.items():
            if "Регион BI" not in frame.columns:
                continue
            if table == "dim_employees":
                active_project = frame[
                    frame.get("Активен", pd.Series(False, index=frame.index)).fillna(False).eq(True)
                    & frame.get("Проект", pd.Series("", index=frame.index)).astype(str).eq("H&N")
                ].copy()
                extra = sorted(set(active_project["Регион BI"].dropna().astype(str)) - allowed_regions - {"Несколько регионов"})
                if extra:
                    self.add("ERROR", table, "Региональный периметр активных USERS", ", ".join(extra), len(extra))
                else:
                    self.add("OK", table, "Региональный периметр активных USERS", "Активные USERS только в рабочих регионах")
                continue
            extra = sorted(set(frame["Регион BI"].dropna().astype(str)) - allowed_regions - {"Несколько регионов"})
            if extra:
                level = "WARN" if table.startswith("page9") or table in {"enps_fact"} else "ERROR"
                self.add(level, table, "Региональный периметр", ", ".join(extra), len(extra))

    def check_page1_formulas(self) -> None:
        frame = self.tables.get("page1_region_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        if "Кадровый отток" not in work.columns and {"Уволено", "Нанято"}.issubset(work.columns):
            work["Кадровый отток"] = (pd.to_numeric(work["Уволено"], errors="coerce") - pd.to_numeric(work["Нанято"], errors="coerce")).clip(lower=0)
        for column, fn in [
            ("Доступность метрик %", p1._region_metric_availability),
            ("Индекс региона %", p1._region_index),
            ("Статус региона", p1._status),
            ("Текст приоритета", p1._priority_text),
        ]:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if "%" in column:
                mismatches = [
                    idx for idx, (a, e) in enumerate(zip(actual, expected, strict=False))
                    if not _is_close(a, e)
                ]
            else:
                mismatches = [
                    idx for idx, (a, e) in enumerate(zip(actual.map(_clean_reason), expected.map(_clean_reason), strict=False))
                    if (pd.isna(a) != pd.isna(e)) or (pd.notna(a) and str(a) != str(e))
                ]
            if mismatches:
                self.add("ERROR", "page1_region_monthly_snapshot", f"Формула: {column}", "Есть расхождения с ETL-формулой", len(mismatches))
            else:
                self.add("OK", "page1_region_monthly_snapshot", f"Формула: {column}", "Совпадает")
        bad_priority = work[
            work["Статус региона"].isin(["Стабильно", "Недостаточно данных"])
            & work.get("Текст приоритета", pd.Series(pd.NA, index=work.index)).notna()
        ]
        if not bad_priority.empty:
            self.add("ERROR", "page1_region_monthly_snapshot", "Fallback приоритетов", "Стабильные/недостаточные регионы попали в приоритеты", len(bad_priority))
        else:
            self.add("OK", "page1_region_monthly_snapshot", "Fallback приоритетов", "Стабильные и недостаточные регионы не выводятся")

    def check_page3_formulas(self) -> None:
        frame = self.tables.get("page3_merch_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        if "Доступность личных метрик %" not in work.columns:
            work["Доступность личных метрик %"] = work.apply(p3._merch_personal_available_weight_from_row, axis=1)
        checks = [
            ("Доступность личных метрик %", p3._merch_personal_available_weight_from_row, True),
            ("Личная эффективность МЕ %", p3._merch_personal_score_from_row, True),
            ("Статус личной эффективности", p3._merch_personal_status_from_row, False),
            ("Причина личной эффективности", p3._merch_personal_reason_from_row, False),
        ]
        for column, fn, numeric in checks:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if numeric:
                mismatches = [idx for idx, (a, e) in enumerate(zip(actual, expected, strict=False)) if not _is_close(a, e)]
            else:
                mismatches = [
                    idx for idx, (a, e) in enumerate(zip(actual.map(_clean_reason), expected.map(_clean_reason), strict=False))
                    if (pd.isna(a) != pd.isna(e)) or (pd.notna(a) and str(a) != str(e))
                ]
            if mismatches:
                self.add("ERROR", "page3_merch_monthly_snapshot", f"Формула: {column}", "Есть расхождения", len(mismatches))
            else:
                self.add("OK", "page3_merch_monthly_snapshot", f"Формула: {column}", "Совпадает")

    def check_page5_formulas(self) -> None:
        frame = self.tables.get("page5_sv_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        checks = [
            ("Доступность метрик СВ %", p5._available_weight_from_row, True),
            ("Индекс эффективности СВ %", p5._weighted_score_from_row, True),
            ("Статус эффективности СВ", p5._status_from_row, False),
            ("Причина статуса СВ", p5._status_reason_from_row, False),
            ("Доступность личных метрик %", p5._personal_available_weight_from_row, True),
            ("Личная эффективность СВ %", p5._personal_score_from_row, True),
            ("Статус личной эффективности", p5._personal_status_from_row, False),
            ("Причина личной эффективности", p5._personal_reason_from_row, False),
        ]
        for column, fn, numeric in checks:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if numeric:
                mismatches = [idx for idx, (a, e) in enumerate(zip(actual, expected, strict=False)) if not _is_close(a, e)]
            else:
                mismatches = [
                    idx for idx, (a, e) in enumerate(zip(actual.map(_clean_reason), expected.map(_clean_reason), strict=False))
                    if (pd.isna(a) != pd.isna(e)) or (pd.notna(a) and str(a) != str(e))
                ]
            if mismatches:
                self.add("ERROR", "page5_sv_monthly_snapshot", f"Формула: {column}", "Есть расхождения", len(mismatches))
            else:
                self.add("OK", "page5_sv_monthly_snapshot", f"Формула: {column}", "Совпадает")

    def check_page7_formulas(self) -> None:
        frame = self.tables.get("page7_tm_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        if "Статус" not in work.columns and "Статус ТМ" in work.columns:
            work["Статус"] = work["Статус ТМ"]
        checks = [
            ("Доступность индекса ТМ %", p7._tm_available_weight_from_row, True),
            ("Балл эффективности %", p7._tm_effectiveness_score_from_row, True),
            ("Статус ТМ", p7._status_from_row, False),
            ("Причина статуса ТМ", p7._status_reason_from_row, False),
        ]
        for column, fn, numeric in checks:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if numeric:
                mismatches = [idx for idx, (a, e) in enumerate(zip(actual, expected, strict=False)) if not _is_close(a, e)]
            else:
                mismatches = [
                    idx for idx, (a, e) in enumerate(zip(actual.map(_clean_reason), expected.map(_clean_reason), strict=False))
                    if (pd.isna(a) != pd.isna(e)) or (pd.notna(a) and str(a) != str(e))
                ]
            if mismatches:
                self.add("ERROR", "page7_tm_monthly_snapshot", f"Формула: {column}", "Есть расхождения", len(mismatches))
            else:
                self.add("OK", "page7_tm_monthly_snapshot", f"Формула: {column}", "Совпадает")
        old_control = work["Статус ТМ"].astype(str).eq("Контроль").sum() if "Статус ТМ" in work.columns else 0
        if old_control:
            self.add("ERROR", "page7_tm_monthly_snapshot", "Старый статус ТМ", "Найдено значение Контроль", int(old_control))
        else:
            self.add("OK", "page7_tm_monthly_snapshot", "Старый статус ТМ", "Контроль не используется")

    def check_page4_fallback(self) -> None:
        frame = self.tables.get("page4_tt_monthly_snapshot")
        if frame is None:
            return
        if {"Сложность %", "Статус ТТ"}.issubset(frame.columns):
            bad_insufficient = frame[frame["Статус ТТ"].eq("Недостаточно данных") & frame["Сложность %"].notna()]
            bad_scored = frame[~frame["Статус ТТ"].eq("Недостаточно данных") & frame["Сложность %"].isna()]
            if not bad_insufficient.empty:
                self.add("ERROR", "page4_tt_monthly_snapshot", "Fallback сложности ТТ", "Недостаточно данных со значением сложности", len(bad_insufficient))
            else:
                self.add("OK", "page4_tt_monthly_snapshot", "Fallback сложности ТТ", "Недостаточно данных без сложности")
            if not bad_scored.empty:
                self.add("ERROR", "page4_tt_monthly_snapshot", "Fallback сложности ТТ", "Рабочий статус без сложности", len(bad_scored))
            else:
                self.add("OK", "page4_tt_monthly_snapshot", "Fallback сложности ТТ", "Рабочие статусы имеют сложность")

    def check_staffing_consistency(self) -> None:
        page1 = self.tables.get("page1_region_monthly_snapshot")
        if page1 is not None and {"Нанято", "Уволено", "Баланс персонала"}.issubset(page1.columns):
            expected = pd.to_numeric(page1["Нанято"], errors="coerce") - pd.to_numeric(page1["Уволено"], errors="coerce")
            bad = ~np.isclose(expected.fillna(0), pd.to_numeric(page1["Баланс персонала"], errors="coerce").fillna(0))
            if bad.any():
                self.add("ERROR", "page1_region_monthly_snapshot", "Баланс персонала", "Баланс != Нанято - Уволено", int(bad.sum()))
            else:
                self.add("OK", "page1_region_monthly_snapshot", "Баланс персонала", "Совпадает")
        page5 = self.tables.get("page5_sv_monthly_snapshot")
        if page5 is not None and {"Размер команды", "Активных МЕ"}.issubset(page5.columns):
            bad_team = page5[pd.to_numeric(page5["Активных МЕ"], errors="coerce") > pd.to_numeric(page5["Плановая команда"], errors="coerce")]
            if not bad_team.empty:
                self.add("ERROR", "page5_sv_monthly_snapshot", "Плановая команда", "Активных МЕ больше плановой команды", len(bad_team))
            else:
                self.add("OK", "page5_sv_monthly_snapshot", "Плановая команда", "Активные не превышают план")

    def write_report(self) -> Path:
        report = pd.DataFrame(self.rows)
        columns_report = []
        for table, frame in self.tables.items():
            for idx, column in enumerate(frame.columns, start=1):
                columns_report.append(
                    {
                        "Таблица": table,
                        "Порядок": idx,
                        "Колонка": column,
                        "Тип": str(frame[column].dtype),
                        "Заполнено": int(frame[column].notna().sum()),
                        "Пусто": int(frame[column].isna().sum()),
                    }
                )
        status_report = []
        for table, frame in self.tables.items():
            for column in ["Статус региона", "Статус эффективности СВ", "Статус личной эффективности", "Статус ТМ", "Статус профиля", "Статус ТТ", "Статус"]:
                if column in frame.columns:
                    counts = frame[column].fillna("<пусто>").astype(str).value_counts().reset_index()
                    counts.columns = ["Значение", "Строк"]
                    counts.insert(0, "Колонка", column)
                    counts.insert(0, "Таблица", table)
                    status_report.extend(counts.to_dict("records"))

        output_path = self.out_dir / "qa_vitrines_report.xlsx"
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            report.to_excel(writer, sheet_name="checks", index=False)
            pd.DataFrame(columns_report).to_excel(writer, sheet_name="columns", index=False)
            pd.DataFrame(status_report).to_excel(writer, sheet_name="statuses", index=False)
        return output_path

    def run(self) -> Path:
        self.load_tables()
        self.check_required_columns()
        self.check_keys()
        self.check_percent_types_and_ranges()
        self.check_status_values()
        self.check_region_scope()
        self.check_page1_formulas()
        self.check_page3_formulas()
        self.check_page4_fallback()
        self.check_page5_formulas()
        self.check_page7_formulas()
        self.check_staffing_consistency()
        return self.write_report()


def main() -> None:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    audit = Audit(out_dir)
    report_path = audit.run()
    result = pd.DataFrame(audit.rows)
    errors = result[result["Уровень"].eq("ERROR")]
    warnings = result[result["Уровень"].eq("WARN")]
    print(f"QA report: {report_path}")
    print(f"OK: {(result['Уровень'] == 'OK').sum()} | WARN: {len(warnings)} | ERROR: {len(errors)}")
    if not errors.empty:
        print("\nОшибки:")
        print(errors[["Таблица", "Проверка", "Количество", "Детали"]].to_string(index=False))
    if not warnings.empty:
        print("\nПредупреждения:")
        print(warnings[["Таблица", "Проверка", "Количество", "Детали"]].to_string(index=False))


if __name__ == "__main__":
    main()
