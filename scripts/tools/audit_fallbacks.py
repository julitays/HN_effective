from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
OUT = ROOT / "data" / "out"
REPORTS = ROOT / "reports"

FALLBACK_MARKERS = (
    "fallback",
    "combine_first",
    "fillna",
    "недостаточно данных",
    "мало данных",
    "вакансия / нет тм",
    "вакансия / нет св",
    "нет привязки",
    "no_tm",
    "no_sv",
)

DATA_MARKERS = (
    "Недостаточно данных",
    "Мало данных",
    "Вакансия / нет ТМ",
    "Вакансия / нет СВ",
    "Нет привязки",
    "Нет подтверждённой привязки",
    "ТМ не указан в текущей привязке",
    "NO_TM",
    "NO_SV",
)


BUSINESS_RULES = [
    {
        "Контур": "Структура сотрудников",
        "Когда не хватает данных": "У СВ нет действующего ТМ в актуальном USERS",
        "Текущее правило": "ID ТМ = NO_TM, название = Вакансия / нет ТМ",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/staffing_utils.py",
    },
    {
        "Контур": "KPI / RTM — привязка ТМ",
        "Когда не хватает данных": "В текущем файле нет активного ТМ у торговой точки",
        "Текущее правило": "ТМ берётся по точному Ship To и подтверждается активным USERS. Если ТМ неактивен, допускается только текущая цепочка конкретного сотрудника RTM → логины → USERS. Невосстановленный визит исключается и считается в аудите",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/kpi_org_mapping.py",
    },
    {
        "Контур": "KPI / RTM — привязка сотрудника",
        "Когда не хватает данных": "Логин RTM не найден в справочнике логинов",
        "Текущее правило": "Сотрудник не назначается. Территория сохраняется только при точной привязке ТТ к активному ТМ; иначе визит исключается. Синтетические ID запрещены",
        "Подстановка старых данных": "Нет",
        "Статус": "Контролируется",
        "Источник": "scripts/kpi_org_mapping.py",
    },
    {
        "Контур": "KPI торговой точки",
        "Когда не хватает данных": "Нет клиентского KPI по ТТ и месяцу",
        "Текущее правило": "KPI остаётся пустым. Расчётный KPI из ОКК, МЕ или других источников запрещён",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page4_tt_data.py",
    },
    {
        "Контур": "Кадровая численность",
        "Когда не хватает данных": "Прошлый месяц отсутствует в текущем USERS",
        "Текущее правило": "Численность на конец месяца восстанавливается по дате приёма и увольнения; текущий месяц берётся из USERS",
        "Подстановка старых данных": "Нет",
        "Статус": "Исправлено",
        "Источник": "scripts/builders/build_org_staffing_monthly_snapshot.py",
    },
    {
        "Контур": "Сводная — статус региона",
        "Когда не хватает данных": "Отсутствует одна или несколько метрик",
        "Текущее правило": "Вес распределяется только между доступными метриками; при доступности менее 60% — Недостаточно данных и регион не попадает в приоритеты",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page1_monthly_snapshot.py",
    },
    {
        "Контур": "Мерчендайзеры — личный балл",
        "Когда не хватает данных": "Нет клиентской аттестации",
        "Текущее правило": "Аттестация исключается из доступного веса; отсутствие аттестации само по себе не снижает балл и не выводится в причины",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page3_data.py",
    },
    {
        "Контур": "Мерчендайзеры — личный балл",
        "Когда не хватает данных": "Доступно менее 60% веса или нет KPI",
        "Текущее правило": "Статус Недостаточно данных; отсутствие ОКК даёт 0 баллов и причину (нет проверок ОКК), но само по себе не блокирует расчёт",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page3_data.py",
    },
    {
        "Контур": "Торговые точки — сложность",
        "Когда не хватает данных": "Нет ОКК по точке",
        "Текущее правило": "Блок ОКК исключается из знаменателя; оценка не занижается",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page4_tt_data.py",
    },
    {
        "Контур": "Торговые точки — сложность",
        "Когда не хватает данных": "Доступно менее 60% веса или мало истории",
        "Текущее правило": "Сложность пустая, статус Недостаточно данных",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page4_tt_data.py",
    },
    {
        "Контур": "СВ — управление командой",
        "Когда не хватает данных": "Отсутствует часть командных метрик",
        "Текущее правило": "Балл считается по фиксированным весам; отсутствующая метрика даёт 0 баллов. Менее 60% доступного веса или нет KPI — Недостаточно данных; отсутствие ОКК не блокирует расчёт",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page5_sv_oed_data.py",
    },
    {
        "Контур": "СВ — личная эффективность",
        "Когда не хватает данных": "СВ не проходил ОЭД",
        "Текущее правило": "ОЭД не заменяется нулём; сотрудник считается новичком, балл не занижается отсутствующими блоками",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page5_sv_oed_data.py",
    },
    {
        "Контур": "ТМ — эффективность",
        "Когда не хватает данных": "Отсутствует часть метрик территории",
        "Текущее правило": "Балл считается по фиксированным весам; отсутствующая метрика даёт 0 баллов. Менее 60% доступного веса или нет KPI — Недостаточно данных; отсутствие качества/ОКК не блокирует расчёт",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page7_tm_data.py",
    },
    {
        "Контур": "Обучение новичков",
        "Когда не хватает данных": "Нет обучения либо ОКК первого/второго месяца",
        "Текущее правило": "Готовность не рассчитывается, статус Мало данных",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/builders/build_page8_learning_competencies_data.py",
    },
    {
        "Контур": "ОЭД и аттестации",
        "Когда не хватает данных": "Нет даты аттестации в основном поле",
        "Текущее правило": "Дата берётся из другого явно заданного столбца того же файла аттестации",
        "Подстановка старых данных": "Нет",
        "Статус": "Техническое сопоставление полей",
        "Источник": "scripts/builders/build_page3_data.py; scripts/builders/build_page5_sv_oed_data.py",
    },
    {
        "Контур": "Кадровый реестр",
        "Когда не хватает данных": "Нет точного ID сотрудника или руководителя",
        "Текущее правило": "Допускается только точное ID или точное полное ФИО. Короткое ФИО/ФИ без отчества запрещено",
        "Подстановка старых данных": "Нет",
        "Статус": "Согласовано",
        "Источник": "scripts/staffing_utils.py",
    },
]


class FallbackVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.function = "<module>"
        self.rows: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def _add(self, node: ast.AST, operation: str) -> None:
        text = ast.get_source_segment(self.source, node) or ""
        self.rows.append(
            {
                "Файл": self.path.relative_to(ROOT).as_posix(),
                "Строка": getattr(node, "lineno", pd.NA),
                "Функция": self.function,
                "Операция": operation,
                "Код": " ".join(text.split())[:500],
            }
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id
        if name in {"fillna", "combine_first"} or "fallback" in name.casefold():
            self._add(node, name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if "fallback" in node.id.casefold():
            self._add(node, "fallback variable")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and any(marker in node.value.casefold() for marker in FALLBACK_MARKERS):
            self._add(node, "fallback label")


def _scan_code() -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        if "tools" in path.parts or "_archive" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        visitor = FallbackVisitor(path, source)
        visitor.visit(tree)
        rows.extend(visitor.rows)
    result = pd.DataFrame(rows).drop_duplicates()
    return result.sort_values(["Файл", "Строка", "Операция"]).reset_index(drop=True)


def _scan_outputs() -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted(OUT.glob("*.parquet")):
        if not path.stem.startswith(("page", "dSupervisor", "dTM")):
            continue
        frame = pd.read_parquet(path)
        for column in frame.select_dtypes(include=["object", "string"]).columns:
            values = frame[column].astype("string")
            counts = Counter(value for value in values.dropna().astype(str) if value in DATA_MARKERS)
            for value, count in counts.items():
                rows.append(
                    {
                        "Таблица": path.stem,
                        "Поле": column,
                        "Fallback-значение": value,
                        "Строк": count,
                        "Доля строк %": count / len(frame) if len(frame) else pd.NA,
                    }
                )
    return pd.DataFrame(rows).sort_values(["Таблица", "Поле", "Fallback-значение"]).reset_index(drop=True)


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    code = _scan_code()
    data = _scan_outputs()
    rules = pd.DataFrame(BUSINESS_RULES)
    summary = (
        code.groupby(["Файл", "Операция"], dropna=False)
        .size()
        .reset_index(name="Количество")
        .sort_values(["Файл", "Операция"])
    )
    output = REPORTS / "fallback_registry.xlsx"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        rules.to_excel(writer, sheet_name="Бизнес-правила", index=False)
        data.to_excel(writer, sheet_name="Fallback в данных", index=False)
        summary.to_excel(writer, sheet_name="Сводка кода", index=False)
        code.to_excel(writer, sheet_name="Все места в коде", index=False)
    print(f"Сохранено: {output}")
    print(f"Бизнес-правил: {len(rules)}")
    print(f"Мест в коде: {len(code)}")
    print(f"Fallback-значений в витринах: {len(data)}")


if __name__ == "__main__":
    main()
