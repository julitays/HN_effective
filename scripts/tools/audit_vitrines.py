import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from scripts.builders import build_page1_monthly_snapshot as p1
from scripts.builders import build_page3_data as p3
from scripts.builders import build_page5_sv_oed_data as p5
from scripts.builders import build_page7_tm_data as p7
from scripts.builders import build_page8_learning_competencies_data as p8
from scripts.utils import load_settings


REQUIRED_TABLES = {
    "dim_employees": "dim_employees.parquet",
    "dim_teams": "dim_teams.parquet",
    "dMonth": "dMonth.parquet",
    "dQuarter": "dQuarter.parquet",
    "dRegion": "dRegion.parquet",
    "dSupervisor": "dSupervisor.parquet",
    "dTM": "dTM.parquet",
    "kpi_employee_monthly_metrics": "kpi_employee_monthly_metrics.parquet",
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
        "Балл эффективности",
        "Статус эффективности СВ",
        "Причина статуса СВ",
        "Балл личной эффективности",
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
        "Просадка KPI %",
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
        "Балл эффективности",
        "Статус ТМ",
        "Причина статуса ТМ",
        "KPI месяца территории %",
        "Качество команды %",
        "Обучение команды %",
        "Фрод %",
        "Стабильность команды %",
        "Текучесть %",
    ],
    "page8_learning_course_summary": [
        "MonthStart",
        "YearMonth",
        "Месяц обучения",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "ID сотрудника",
        "Новичок",
        "Дата приёма",
        "Стаж, мес.",
        "Назначено обязательных",
        "Успешно закрыто обязательных",
        "Незакрыто обязательных",
        "Незакрытые обучения",
        "Обязательное обучение закрыто %",
        "Статус обязательного обучения",
        "ОКК 1-й месяц",
        "ОКК 2-й месяц",
        "ОКК опытных %",
        "Разрыв с опытными",
        "Готовность новичка %",
        "Статус адаптации",
    ],
    "page8_learning_effect_trend": [
        "MonthStart",
        "YearMonth",
        "Регион BI",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "Шаг",
        "Порядок",
        "ОКК %",
        "KPI %",
        "Сотрудников",
    ],
    "page8_learning_employee_matrix": [
        "MonthStart",
        "YearMonth",
        "ID сотрудника",
        "Сотрудник",
        "ID супервайзера",
        "СВ",
        "Стаж, мес.",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
        "Фотоаудит",
        "PICOS",
        "Доступность",
        "Антифрод",
        "Работа с ТТ",
        "Базовые стандарты",
        "Доступно компетенций",
        "% закрытых компетенций",
        "Незакрытая компетенция",
    ],
}


KPI_COMPONENT_COLUMNS = [
    "PICOS план",
    "PICOS факт",
    "PICOS выполнение %",
    "OSA план %",
    "OSA факт %",
    "OSA выполнение %",
    "TOP16 план %",
    "TOP16 факт %",
    "TOP16 выполнение %",
]


PUBLISHED_REQUIRED_COLUMNS = {
    "dim_employees": ["ID сотрудника", "ФИО", "Должность", "Проект", "Дата приёма", "Активен", "Регион BI"],
    "dim_teams": ["ID территориального менеджера", "ID супервайзера", "ID мерчендайзера", "Регион BI"],
    "dMonth": ["MonthStart", "YearMonth", "MonthLabel"],
    "dRegion": ["Регион BI"],
    "dSupervisor": ["ID супервайзера", "Супервайзер", "Регион BI", "Территориальный менеджер"],
    "dTM": ["ID территориального менеджера", "Территориальный менеджер", "Регион BI", "Регионы ТМ"],
    "learning_monthly": ["YearMonth", "Регион BI", "Назначено обязательных курсов", "Пройдено обязательных курсов"],
    "okk_fact": ["Качество визита", "Регион BI", "YearMonth"],
    "org_staffing_report_snapshot": [
        "YearMonth",
        "Регион BI",
        "Активных МЕ",
        "Открытых вакансий",
        "Нанято",
        "Уволено",
        "Доля кадрового оттока %",
    ],
    "page1_region_monthly_snapshot": [
        "YearMonth",
        "Регион BI",
        "KPI проекта %",
        "PICOS выполнение %",
        "OSA выполнение %",
        "TOP16 выполнение %",
        "Качество визитов %",
        "Фрод %",
        "Обязательное обучение %",
        "Кадровая устойчивость %",
        "Статус региона",
        "Текст приоритета",
    ],
    "page2_actions_monthly": ["Что сделать", "Ответственный контур", "Сейчас", "Цель", "YearMonth"],
    "page2_sv_monthly_snapshot": [
        "YearMonth",
        "Регион BI",
        "Территориальный менеджер",
        "СВ / Объект",
        "KPI проекта %",
        "PICOS план",
        "PICOS факт",
        "Первое действие",
    ],
    "page3_merch_monthly_snapshot": [
        "YearMonth",
        "Мерчендайзер",
        "Регион BI",
        "Супервайзер",
        "Территориальный менеджер",
        "ОКК %",
        "Обучение %",
        "Статус личной эффективности",
        "Причина личной эффективности",
        *KPI_COMPONENT_COLUMNS,
    ],
    "page4_tt_formula": ["Порядок", "Формула", "Описание"],
    "page4_tt_monthly_snapshot": [
        "MonthStart",
        "ТТ",
        "Регион BI",
        "Сеть",
        "ТМ территория",
        "Визиты",
        "ОКК %",
        "Сложность %",
        "Статус ТТ",
        *KPI_COMPONENT_COLUMNS,
    ],
    "page4_tt_status_legend": ["Порядок", "Статус ТТ", "Описание"],
    "page5_sv_monthly_snapshot": [
        "YearMonth",
        "ID супервайзера",
        "Супервайзер",
        "Территориальный менеджер",
        "Размер команды",
        "Текучесть команды %",
        "Стабильность команды %",
        "ОКК команды %",
        "Обучение команды %",
        "Фрод %",
        "Балл эффективности",
        "Статус эффективности СВ",
        "Причина статуса СВ",
        "Балл личной эффективности",
        "Статус личной эффективности",
        "Причина личной эффективности",
        *KPI_COMPONENT_COLUMNS,
    ],
    "page6_okk_insights_monthly": [
        "YearMonth",
        "Регион BI",
        "Тип блока",
        "Категория",
        "Показатель",
        "% из проверок ОКК",
        "Фрод %",
        "Просадка ОКК %",
        "Риск",
        "Действие",
    ],
    "page6_okk_region_monthly": ["YearMonth", "Регион BI", "ОКК %"],
    "page7_tm_monthly_snapshot": [
        "MonthStart",
        "ID территориального менеджера",
        "Территориальный менеджер",
        "Регион BI",
        "Балл эффективности",
        "Статус ТМ",
        "Причина статуса ТМ",
        "Результат территории %",
        "Стабильность команды %",
        "Качество команды %",
        "Обучение команды %",
        "Фрод %",
        "Текучесть %",
        *KPI_COMPONENT_COLUMNS,
    ],
    "page8_learning_course_summary": [
        "YearMonth",
        "Месяц обучения",
        "Регион BI",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "ID сотрудника",
        "Новичок",
        "Дата приёма",
        "Стаж, мес.",
        "Обязательное обучение закрыто %",
        "ОКК 1-й месяц",
        "ОКК 2-й месяц",
        "Готовность новичка %",
        "Статус адаптации",
    ],
    "page8_learning_employee_matrix": [
        "YearMonth",
        "Регион BI",
        "Территориальный менеджер",
        "ID супервайзера",
        "СВ",
        "Сотрудник",
        "Фотоаудит",
        "Доступность",
        "PICOS",
        "Антифрод",
        "Работа с ТТ",
        "Базовые стандарты",
        "% закрытых компетенций",
        "Незакрытая компетенция",
    ],
    "page9_climate_blocks_region": ["Регион BI", "Блок", "Значение %", "Предыдущий период %"],
    "page9_climate_quarterly_region": [
        "QuarterStart",
        "QuarterLabel",
        "Регион BI",
        "Удовлетворённость %",
        "Вовлечённость %",
        "Лояльность %",
        "Риск ухода %",
        "eNPS",
        "Статус",
    ],
}


KEYS = {
    "page1_region_monthly_snapshot": ["YearMonth", "Регион BI"],
    "page2_sv_monthly_snapshot": ["YearMonth", "ID супервайзера"],
    "page3_merch_monthly_snapshot": ["YearMonth", "ID мерчендайзера"],
    "page4_tt_monthly_snapshot": ["MonthStart", "ТТ"],
    "page5_sv_monthly_snapshot": ["YearMonth", "ID супервайзера"],
    "page7_tm_monthly_snapshot": ["YearMonth", "ID территориального менеджера"],
    "page8_learning_course_summary": ["YearMonth", "ID сотрудника"],
    "page8_learning_effect_trend": ["YearMonth", "Регион BI", "ID территориального менеджера", "СВ", "Шаг"],
    "page8_learning_employee_matrix": ["YearMonth", "ID сотрудника"],
    "dSupervisor": ["ID супервайзера"],
    "dTM": ["ID территориального менеджера"],
    "dRegion": ["Регион BI"],
}


PUBLISHED_KEYS = {
    "page1_region_monthly_snapshot": ["YearMonth", "Регион BI"],
    "page2_sv_monthly_snapshot": ["YearMonth", "СВ / Объект"],
    "page3_merch_monthly_snapshot": ["YearMonth", "Мерчендайзер"],
    "page4_tt_monthly_snapshot": ["MonthStart", "ТТ"],
    "page5_sv_monthly_snapshot": ["YearMonth", "ID супервайзера"],
    "page7_tm_monthly_snapshot": ["MonthStart", "ID территориального менеджера"],
    "page8_learning_course_summary": ["YearMonth", "ID сотрудника"],
    "page8_learning_employee_matrix": ["YearMonth", "Сотрудник"],
    "dSupervisor": ["ID супервайзера"],
    "dTM": ["ID территориального менеджера"],
    "dRegion": ["Регион BI"],
}


ALLOWED_STATUSES = {
    "page1_region_monthly_snapshot": {"Стабильно", "Контроль", "Высокий риск", "Недостаточно данных"},
    "page3_merch_monthly_snapshot": {"Высокая личная готовность", "Соответствует роли", "Зона развития", "Новичок", "Недостаточно данных"},
    "page4_tt_monthly_snapshot": {"Недостаточно данных", "Эталон", "Контроль", "Не вина МЕ", "Сложная ТТ"},
    "page5_sv_monthly_snapshot": {"Высокая готовность", "Соответствует роли", "Зона развития", "Недостаточно данных"},
    "page5_sv_monthly_snapshot_personal": {"Высокая личная готовность", "Соответствует роли", "Зона развития", "Новичок", "Недостаточно данных"},
    "page7_tm_monthly_snapshot": {"Высокая эффективность", "Зона развития", "Зона риска", "Недостаточно данных"},
    "page8_learning_course_summary": {"Вышел на уровень", "Есть прогресс", "Нужна поддержка", "Мало данных"},
}


PERCENT_DELTA_MARKERS = ("разрыв", "просадка", "эффект", "динамика", "Δ", "изменение")


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
        self.settings = load_settings()
        self.published_mode = (out_dir / "etl_run_manifest.json").exists()
        self.tables: dict[str, pd.DataFrame] = {}
        self.rows: list[dict] = []

    def _required_tables(self) -> dict[str, str]:
        if not self.published_mode:
            return REQUIRED_TABLES
        return {
            Path(filename).stem: filename
            for filename in self.settings["reporting"]["publish_tables"]
        }

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
        for table, filename in self._required_tables().items():
            path = self.out_dir / filename
            if not path.exists():
                self.add("ERROR", table, "Файл существует", f"Не найден {path}", 1)
                continue
            frame = pd.read_parquet(path)
            self.tables[table] = frame
            self.add("OK", table, "Файл существует", f"{len(frame)} строк, {len(frame.columns)} колонок")

    def check_required_columns(self) -> None:
        required_columns = (
            PUBLISHED_REQUIRED_COLUMNS if self.published_mode else REQUIRED_COLUMNS
        )
        for table, columns in required_columns.items():
            frame = self.tables.get(table)
            if frame is None:
                continue
            missing = [column for column in columns if column not in frame.columns]
            if missing:
                self.add("ERROR", table, "Обязательные колонки", ", ".join(missing), len(missing))
            else:
                self.add("OK", table, "Обязательные колонки", "Все на месте")

    def check_keys(self) -> None:
        keys_by_table = PUBLISHED_KEYS if self.published_mode else KEYS
        for table, keys in keys_by_table.items():
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

    def check_published_output_contract(self) -> None:
        if not self.published_mode:
            return
        expected_files = {
            filename
            for filename in self.settings["reporting"]["publish_tables"]
        }
        actual_files = {path.name for path in self.out_dir.glob("*.parquet")}
        missing_files = sorted(expected_files - actual_files)
        extra_files = sorted(actual_files - expected_files)
        if missing_files:
            self.add(
                "ERROR",
                "Power BI",
                "Согласованный набор файлов",
                "Отсутствуют: " + ", ".join(missing_files),
                len(missing_files),
            )
        if extra_files:
            self.add(
                "ERROR",
                "Power BI",
                "Технические parquet",
                "Лишние: " + ", ".join(extra_files),
                len(extra_files),
            )
        if not missing_files and not extra_files:
            self.add(
                "OK",
                "Power BI",
                "Согласованный набор файлов",
                f"Опубликовано {len(actual_files)} таблицы",
            )

        cleanup_path = Path(
            self.settings["reporting"]["powerbi_column_contract"]
        )
        cleanup = json.loads(cleanup_path.read_text(encoding="utf-8"))
        leftovers: list[str] = []
        for table, configured_columns in cleanup.get("columns", {}).items():
            frame = self.tables.get(table)
            if frame is None:
                continue
            for column in configured_columns:
                if column in frame.columns:
                    leftovers.append(f"{table}[{column}]")
        if leftovers:
            self.add(
                "ERROR",
                "Power BI",
                "Технические колонки",
                ", ".join(leftovers),
                len(leftovers),
            )
        else:
            self.add(
                "OK",
                "Power BI",
                "Технические колонки",
                "Колонки из плана очистки отсутствуют",
            )

        old_kpi_columns = {"PICOS план %", "PICOS факт %"}
        old_kpi_leftovers = [
            f"{table}[{column}]"
            for table, frame in self.tables.items()
            for column in frame.columns
            if column in old_kpi_columns
            or str(column).endswith(" SQL")
            or "potential" in str(column).lower()
        ]
        if old_kpi_leftovers:
            self.add(
                "ERROR",
                "Power BI",
                "Старые KPI-колонки",
                ", ".join(old_kpi_leftovers),
                len(old_kpi_leftovers),
            )
        else:
            self.add(
                "OK",
                "Power BI",
                "Старые KPI-колонки",
                "Старые и технические KPI-поля отсутствуют",
            )

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
                column_name = str(column).lower()
                if any(marker in column_name for marker in PERCENT_DELTA_MARKERS):
                    lower, upper = -1.0, 1.0
                elif "факт %" in column_name:
                    lower, upper = 0.0, 2.0
                else:
                    lower, upper = 0.0, 1.0
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
            ("page8_learning_course_summary", "Статус адаптации", ALLOWED_STATUSES["page8_learning_course_summary"]),
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
        if self.published_mode:
            return
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
        if self.published_mode:
            return
        frame = self.tables.get("page3_merch_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        kpi_metrics = self.tables.get("kpi_employee_monthly_metrics")
        if kpi_metrics is not None and not kpi_metrics.empty:
            weight_columns = [
                column
                for column in p3.KPI_SCORE_WEIGHT_COLUMNS
                if column in kpi_metrics.columns
            ]
            work = work.merge(
                kpi_metrics[
                    ["MonthStart", "YearMonth", "ID сотрудника", *weight_columns]
                ].rename(columns={"ID сотрудника": "ID мерчендайзера"}),
                on=["MonthStart", "YearMonth", "ID мерчендайзера"],
                how="left",
            )
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
                tolerance = 0.0051 if "эффективност" in column.lower() else 0.0005
                mismatches = [
                    idx
                    for idx, (a, e) in enumerate(zip(actual, expected, strict=False))
                    if not _is_close(a, e, tolerance=tolerance)
                ]
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
        if self.published_mode:
            return
        frame = self.tables.get("page5_sv_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        work["Доступность метрик СВ %"] = work.apply(p5._available_weight_from_row, axis=1)
        work["Индекс эффективности СВ %"] = work.apply(p5._weighted_score_from_row, axis=1)
        work["Доступность личных метрик %"] = work.apply(
            p5._personal_available_weight_from_row, axis=1
        )
        if "QuarterLabel аттестации клиента" not in work.columns:
            q1 = pd.to_numeric(
                work["Аттестация клиента Q1 2026 %"]
                if "Аттестация клиента Q1 2026 %" in work.columns
                else pd.Series(np.nan, index=work.index),
                errors="coerce",
            )
            q4 = pd.to_numeric(
                work["Аттестация клиента Q4 2025 %"]
                if "Аттестация клиента Q4 2025 %" in work.columns
                else pd.Series(np.nan, index=work.index),
                errors="coerce",
            )
            work["QuarterLabel аттестации клиента"] = pd.Series(pd.NA, index=work.index, dtype="object")
            work.loc[q4.notna(), "QuarterLabel аттестации клиента"] = "Q4 2025"
            work.loc[q1.notna(), "QuarterLabel аттестации клиента"] = "Q1 2026"
        work["Личная эффективность СВ %"] = work.apply(p5._personal_score_from_row, axis=1)
        checks = [
            ("Доступность метрик СВ %", p5._available_weight_from_row, True),
            (
                "Балл эффективности",
                lambda row: np.floor(row.get("Индекс эффективности СВ %") * 100 + 1e-9)
                if pd.notna(row.get("Индекс эффективности СВ %"))
                else pd.NA,
                True,
            ),
            ("Статус эффективности СВ", p5._status_from_row, False),
            ("Причина статуса СВ", p5._status_reason_from_row, False),
            ("Доступность личных метрик %", p5._personal_available_weight_from_row, True),
            (
                "Балл личной эффективности",
                lambda row: np.floor(row.get("Личная эффективность СВ %") * 100 + 1e-9)
                if pd.notna(row.get("Личная эффективность СВ %"))
                else pd.NA,
                True,
            ),
            ("Статус личной эффективности", p5._personal_status_from_row, False),
            ("Причина личной эффективности", p5._personal_reason_from_row, False),
        ]
        for column, fn, numeric in checks:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if numeric:
                tolerance = 0.0051 if "эффективност" in column.lower() else 0.0005
                mismatches = [
                    idx
                    for idx, (a, e) in enumerate(zip(actual, expected, strict=False))
                    if not _is_close(a, e, tolerance=tolerance)
                ]
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
        if self.published_mode:
            return
        frame = self.tables.get("page7_tm_monthly_snapshot")
        if frame is None:
            return
        work = frame.copy()
        work["Балл эффективности %"] = work.apply(p7._tm_effectiveness_score_from_row, axis=1)
        if "Статус" not in work.columns and "Статус ТМ" in work.columns:
            work["Статус"] = work["Статус ТМ"]
        checks = [
            ("Доступность индекса ТМ %", p7._tm_available_weight_from_row, True),
            (
                "Балл эффективности",
                lambda row: round(row.get("Балл эффективности %") * 100)
                if pd.notna(row.get("Балл эффективности %"))
                else pd.NA,
                True,
            ),
            ("Статус ТМ", p7._status_from_row, False),
            ("Причина статуса ТМ", p7._status_reason_from_row, False),
        ]
        for column, fn, numeric in checks:
            if column not in work.columns:
                continue
            expected = work.apply(fn, axis=1)
            actual = work[column]
            if numeric:
                tolerance = 0.0051 if "эффективност" in column.lower() else 0.0005
                mismatches = [
                    idx
                    for idx, (a, e) in enumerate(zip(actual, expected, strict=False))
                    if not _is_close(a, e, tolerance=tolerance)
                ]
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

    def check_page8_fallback(self) -> None:
        frame = self.tables.get("page8_learning_course_summary")
        if frame is None or {"Статус адаптации", "Готовность новичка %"}.difference(frame.columns):
            return
        insufficient = frame["Статус адаптации"].astype(str).eq("Мало данных")
        bad_insufficient = frame[insufficient & frame["Готовность новичка %"].notna()]
        bad_scored = frame[~insufficient & frame["Готовность новичка %"].isna()]
        if not bad_insufficient.empty:
            self.add("ERROR", "page8_learning_course_summary", "Fallback адаптации", "Мало данных с рассчитанной готовностью", len(bad_insufficient))
        else:
            self.add("OK", "page8_learning_course_summary", "Fallback адаптации", "Мало данных без готовности")
        if not bad_scored.empty:
            self.add("ERROR", "page8_learning_course_summary", "Fallback адаптации", "Рабочий статус без готовности", len(bad_scored))
        else:
            self.add("OK", "page8_learning_course_summary", "Fallback адаптации", "Рабочие статусы имеют готовность")

        if "Обязательное обучение закрыто %" in frame.columns:
            readiness = pd.to_numeric(frame["Готовность новичка %"], errors="coerce")
            learning = pd.to_numeric(frame["Обязательное обучение закрыто %"], errors="coerce")
            bad_readiness = frame[readiness.notna() & learning.notna() & (readiness > learning + 0.00001)]
            if not bad_readiness.empty:
                self.add(
                    "ERROR",
                    "page8_learning_course_summary",
                    "Готовность новичка",
                    "Готовность выше закрытого обучения",
                    len(bad_readiness),
                )
            else:
                self.add(
                    "OK",
                    "page8_learning_course_summary",
                    "Готовность новичка",
                    "Не выше закрытого обучения",
                )

    def check_page8_hire_date(self) -> None:
        if self.published_mode:
            return
        frame = self.tables.get("page8_learning_course_summary")
        required = {"MonthStart", "Дата приёма"}
        if frame is None or required.difference(frame.columns):
            return
        hire_date = pd.to_datetime(frame["Дата приёма"], errors="coerce")
        learning_month_end = (
            pd.to_datetime(frame["MonthStart"], errors="coerce")
            - pd.DateOffset(months=1)
            + pd.offsets.MonthEnd(0)
        )
        bad = frame[hire_date.notna() & learning_month_end.lt(hire_date)]
        if not bad.empty:
            self.add("ERROR", "page8_learning_course_summary", "Дата обучения и приёма", "Есть обучение до даты приёма", len(bad))
        else:
            self.add("OK", "page8_learning_course_summary", "Дата обучения и приёма", "Обучение не раньше даты приёма")

        learning_path = self.out_dir / "learning_fact.parquet"
        if not learning_path.exists():
            return
        learning = pd.read_parquet(learning_path)
        learning_required = {"ID сотрудника", "MonthStart", "Дата завершения", "Обязательный", "Пройдено"}
        if learning_required.difference(learning.columns):
            return

        summary = frame[["ID сотрудника", "MonthStart", "Дата приёма"]].drop_duplicates().copy()
        summary["Месяц обучения"] = pd.to_datetime(summary["MonthStart"], errors="coerce") - pd.DateOffset(months=1)
        summary["Дата приёма"] = pd.to_datetime(summary["Дата приёма"], errors="coerce")

        learning_work = p8._prepare_learning_with_competency(learning)
        learning_work["Месяц обучения"] = pd.to_datetime(learning_work["MonthStart"], errors="coerce")
        learning_work["Дата завершения"] = pd.to_datetime(learning_work["Дата завершения"], errors="coerce")
        success_mask = p8._required_course_success_mask(learning_work)
        learning_work = learning_work[
            learning_work["Обязательный курс"].fillna(False).eq(True)
            & learning_work["Дата завершения"].notna()
            & success_mask
        ].copy()

        joined = summary.merge(
            learning_work[["ID сотрудника", "Месяц обучения", "Дата завершения"]],
            on=["ID сотрудника", "Месяц обучения"],
            how="left",
        )
        valid = joined[
            joined["Дата завершения"].notna()
            & (
                joined["Дата приёма"].isna()
                | joined["Дата завершения"].ge(joined["Дата приёма"])
            )
        ]
        valid_keys = valid[["ID сотрудника", "Месяц обучения"]].drop_duplicates()
        invalid = summary.merge(valid_keys, on=["ID сотрудника", "Месяц обучения"], how="left", indicator=True)
        invalid = invalid[invalid["_merge"].eq("left_only")]
        if not invalid.empty:
            self.add(
                "ERROR",
                "page8_learning_course_summary",
                "Текущее обучение новичка",
                "Нет валидного обязательного обучения после даты приёма",
                len(invalid),
            )
        else:
            self.add(
                "OK",
                "page8_learning_course_summary",
                "Текущее обучение новичка",
                "Есть валидное обязательное обучение после даты приёма",
            )

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
        self.check_published_output_contract()
        self.check_percent_types_and_ranges()
        self.check_status_values()
        self.check_region_scope()
        self.check_page1_formulas()
        self.check_page3_formulas()
        self.check_page4_fallback()
        self.check_page5_formulas()
        self.check_page7_formulas()
        self.check_page8_fallback()
        self.check_page8_hire_date()
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
    if not errors.empty:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
