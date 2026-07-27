import html
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import load_settings


OUTPUT_DOCX = Path("docs") / "HN_BI_методология_расчетов.docx"


def _xml_escape(value: str) -> str:
    return html.escape(str(value), quote=False)


def _paragraph(text: str = "", style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}<w:r><w:t>{_xml_escape(text)}</w:t></w:r></w:p>"


def _bullet(text: str) -> str:
    return _paragraph(f"• {text}")


def _table(rows: list[list[str]]) -> str:
    table_rows = []
    for row in rows:
        cells = []
        for cell in row:
            cells.append(
                "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>"
                f"{_paragraph(cell)}</w:tc>"
            )
        table_rows.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return (
        "<w:tbl>"
        "<w:tblPr><w:tblBorders>"
        "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "<w:left w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "<w:right w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "<w:insideV w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"D9E2EC\"/>"
        "</w:tblBorders></w:tblPr>"
        f"{''.join(table_rows)}</w:tbl>"
    )


def _section(parts: list[str], title: str) -> None:
    parts.append(_paragraph(title, "Heading1"))


def _subsection(parts: list[str], title: str) -> None:
    parts.append(_paragraph(title, "Heading2"))


def _safe_status_counts(path: Path, column: str) -> str:
    if not path.exists():
        return "файл не найден"
    frame = pd.read_parquet(path)
    if column not in frame.columns:
        return "колонка не найдена"
    return "; ".join(f"{key}: {value}" for key, value in frame[column].value_counts(dropna=False).items())


def _write_docx(parts: list[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'mc:Ignorable="w14 wp14">'
        "<w:body>"
        f"{''.join(parts)}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="850" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )
    now = datetime.now().isoformat(timespec="seconds")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>HN BI: методология расчетов</dc:title>"
        "<dc:creator>Codex</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        "</cp:coreProperties>"
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>Codex</Application></Properties>"
    )
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/_rels/document.xml.rels", doc_rels)
        docx.writestr("docProps/core.xml", core)
        docx.writestr("docProps/app.xml", app)


def build_document() -> Path:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])
    qa_path = out_dir / "qa_vitrines_report.xlsx"
    qa_summary = "QA не запускался"
    if qa_path.exists():
        qa = pd.read_excel(qa_path, sheet_name="checks")
        qa_summary = (
            f"OK: {qa['Уровень'].eq('OK').sum()}, "
            f"WARN: {qa['Уровень'].eq('WARN').sum()}, "
            f"ERROR: {qa['Уровень'].eq('ERROR').sum()}"
        )

    parts: list[str] = []
    parts.append(_paragraph("HN BI: методология расчетов витрин", "Title"))
    parts.append(_paragraph(f"Дата формирования: {datetime.now():%d.%m.%Y %H:%M}"))
    parts.append(_paragraph(f"Контрольный QA: {qa_summary}. Отчет QA: data/out/qa_vitrines_report.xlsx"))

    _section(parts, "1. Общие правила данных")
    parts.extend(
        [
            _bullet("Структурные привязки сотрудников, СВ, ТМ и регионов берутся из актуального USERS-периметра; исторические KPI/OKK не переопределяют текущую оргструктуру."),
            _bullet("В отчетные витрины по сотрудникам попадают активные сотрудники проекта H&N; авторизация = Нет не исключает новичка, если сотрудник активен."),
            _bullet("Проценты в parquet хранятся числами 0..1; в Power BI их нужно форматировать как процент."),
            _bullet("Если метрика отсутствует, ETL не подставляет старое значение без явного правила. Для индексных витрин используется контроль доступного веса."),
            _bullet("Если доступно меньше 60% веса ключевых метрик или отсутствует обязательная ключевая метрика, ставится статус Недостаточно данных."),
        ]
    )

    _section(parts, "2. Страница 1: регионы")
    parts.append(_paragraph("Витрина: data/out/page1_region_monthly_snapshot.parquet. Строка = месяц + Регион BI."))
    parts.extend(
        [
            _bullet("KPI проекта %: средний KPI 1 из клиентских KPI TT-файлов, если они есть; старый KPI за прошлый месяц не подставляется."),
            _bullet("Качество визитов %: среднее Качество визита из okk_fact."),
            _bullet("Фрод %: доля визитов с Флаг фальсификации = True; Фрод кол-во = количество таких визитов."),
            _bullet("Обязательное обучение %: доля пройденных обязательных назначений из learning_monthly."),
            _bullet("Риск ухода структуры eNPS % и Оценка команды %: последний доступный квартальный показатель на дату месяца."),
            _bullet("Кадровая устойчивость % = 1 - 50% * доля вакансий к активным МЕ - 30% * текучесть - 20% * доля кадрового оттока."),
            _bullet("Индекс региона % = 65% операционный блок + 35% кадровая устойчивость; внутри операционного блока: KPI 30%, OKK 20%, обучение 15%, антифрод 15%, климат 10%, оценка команды 10%."),
            _bullet("Статус: Недостаточно данных при доступности <60%; Высокий риск при индексе <80%, двух красных сигналах или кадровом collapse; Контроль при индексе <88%, одном красном или двух мягких сигналах; иначе Стабильно."),
            _bullet("Приоритеты справа формируются только для Контроль/Высокий риск; Стабильно и Недостаточно данных в приоритеты не выводятся."),
        ]
    )

    _section(parts, "3. Страница 2: KPI и драйверы")
    parts.append(_paragraph("Витрины: page2_actions_monthly и page2_sv_monthly_snapshot."))
    parts.extend(
        [
            _bullet("page2_actions_monthly собирает действия только там, где есть реальный разрыв до цели."),
            _bullet("Первое действие СВ выбирается в ETL: если KPI ниже цели — Разобрать KPI; иначе берется максимальный разрыв среди фрода, ОКК, OSA, PICOS и обучения."),
            _bullet("Статус команды: 2+ нарушенных блока = Высокий риск; 1 нарушенный блок = Контроль; без нарушений = Стабильно."),
            _bullet("Технический ключ Page2 по СВ очищен: одна строка = месяц + ID супервайзера."),
        ]
    )

    _section(parts, "4. Страница 3: мерчендайзеры")
    parts.append(_paragraph("Витрина: page3_merch_monthly_snapshot. Строка = месяц + мерчендайзер."))
    parts.append(
        _table(
            [
                ["Метрика", "Вес", "Зеленая зона", "Желтая зона", "Красная зона"],
                ["KPI проекта %", "40%", ">=95%", ">90% и <95%", "<90%"],
                ["ОКК %", "15%", ">=60%", "40–60%", "<40%"],
                ["Обучение %", "20%", ">=95%", "90–95%", "<90%"],
                ["Аттестация клиента %", "25%", ">=95%", ">90% и <95%", "<90%"],
            ]
        )
    )
    parts.extend(
        [
            _bullet("Балл личной эффективности = сумма баллов четырех блоков. Если значение ниже красного порога, блок дает 0 баллов."),
            _bullet("Если значение достигло желтого порога, блок дает Вес * MIN(Значение / Зеленый порог, 1)."),
            _bullet("KPI проекта % и ОКК % — критичные метрики; если их нет, статус = Недостаточно данных."),
            _bullet("Если Аттестация клиента % отсутствует, блок не снижает балл; это нейтральный fallback для новичков/не участвовавших."),
            _bullet("Причина личной эффективности выводит просевшие блоки; аттестация клиента указывается с кварталом, если квартал определен."),
        ]
    )

    _section(parts, "5. Страница 4: сложность торговых точек")
    parts.append(_paragraph("Витрина: page4_tt_monthly_snapshot. Строка = месяц + ТТ."))
    parts.append(
        _table(
            [
                ["Компонента сложности", "Вес"],
                ["Повторяемая просадка KPI проекта на ТТ при разных МЕ", "35%"],
                ["Нестабильность OSA/PICOS по истории", "25%"],
                ["Повторяемость OKK-нарушений на ТТ", "20%"],
                ["Отклонение от похожих ТТ сети/города", "10%"],
                ["Частая смена МЕ / нестабильность маршрута", "10%"],
            ]
        )
    )
    parts.extend(
        [
            _bullet("Сложность % считается только если есть минимум 2 месяца истории и минимум 3 визита по ТТ; иначе Сложность % = пусто и Статус ТТ = Недостаточно данных."),
            _bullet("Эталон: сложность <=35%, KPI проекта >=80%, ОКК >=60%."),
            _bullet("Сложная ТТ: сложность >=65%, если основной вклад дают KPI/OSA/PICOS/OKK-компоненты."),
            _bullet("Не вина МЕ: сложность >=65%, если основной вклад дают похожие ТТ, сеть/город или частая смена МЕ."),
            _bullet("Контроль: данных достаточно, но точка не попала в Эталон или Сложная ТТ."),
        ]
    )

    _section(parts, "6. Страница 5: супервайзеры — работа с командой")
    parts.append(_paragraph("Витрина: page5_sv_monthly_snapshot. Управленческий балл не включает клиентскую аттестацию и личную эффективность."))
    parts.append(
        _table(
            [
                ["Метрика", "Вес", "Зеленая зона", "Желтая зона", "Красная зона"],
                ["KPI месяца %", "35%", ">=95%", "90–95%", "<90%"],
                ["ОКК команды %", "15%", ">=60%", "40–60%", "<40%"],
                ["Обучение команды %", "15%", ">=95%", "90–95%", "<90%"],
                ["Фрод %", "15%", "<=15%", "15–20%", ">20%"],
                ["Стабильность команды %", "15%", ">=95%", "90–95%", "<90%"],
                ["Текучесть команды %", "5%", "<=10%", "10–15%", ">15%"],
            ]
        )
    )
    parts.extend(
        [
            _bullet("KPI месяца % и ОКК команды % — обязательные ключевые метрики; без них статус = Недостаточно данных."),
            _bullet("Если доступно меньше 60% веса управленческих метрик, статус = Недостаточно данных."),
            _bullet("Красная зона метрики дает 0 за свой вес; желтая зона начисляется линейно до зеленого порога."),
            _bullet("Статус: Стабильно при балле >=90% и без желтых/красных управленческих флагов; Зона риска при балле <80%; остальные достаточные случаи = Контроль."),
            _bullet("Причина статуса СВ показывает только красные флаги через запятую; если все метрики зеленые — Все метрики выше целевого уровня; если красных нет — поле пустое."),
            _bullet("Стабильность команды % = 1 - 70% * доля вакансий от плановой команды - 20% * текучесть - 10% * чистый отток."),
        ]
    )

    _section(parts, "7. Супервайзеры — личная эффективность и ОЭД")
    parts.append(_paragraph("Личная эффективность находится в той же витрине page5_sv_monthly_snapshot; отдельная квартальная ОЭД-витрина — page5_sv_oed_quarterly и oed_quarterly_snapshot."))
    parts.append(
        _table(
            [
                ["Метрика", "Вес", "Зеленая зона", "Желтая зона", "Красная зона"],
                ["Аттестация клиента %", "40%", ">=95%", ">90% и <95%", "<90%"],
                ["Аттестация ОЭД %", "20%", ">=95%", ">90% и <95%", "<90%"],
                ["Продукт ОЭД %", "20%", ">=95%", ">90% и <95%", "<90%"],
                ["Управление ОЭД %", "20%", ">=95%", ">90% и <95%", "<90%"],
            ]
        )
    )
    parts.extend(
        [
            _bullet("KPI ОЭД % пока справочно и не входит в балл личной эффективности."),
            _bullet("Если сотрудник не участвовал в ОЭД как СВ, ОЭД-блоки не заполняются нулями; статус личной эффективности = Новичок."),
            _bullet("Высокая личная готовность возможна только при балле >=95%, всех зеленых личных метриках и Класс ОЭД = ТОП."),
            _bullet("Соответствует роли: балл >=90% и нет красных личных флагов."),
            _bullet("Зона развития: балл <90% или есть красный личный флаг."),
        ]
    )

    _section(parts, "8. Страница 6: ОКК и фрод")
    parts.append(_paragraph("Витрины: page6_okk_region_monthly и page6_okk_insights_monthly."))
    parts.extend(
        [
            _bullet("page6_okk_region_monthly: месяц + регион; ОКК %, Фрод %, OSA %, PICOS % считаются средними из okk_fact."),
            _bullet("% из проверок ОКК в блоках анкеты = среднее значение выбранного блока ОКК по худшей зоне месяца."),
            _bullet("KPI-разрыв % = KPI проекта % - целевой KPI 75%; отрицательное значение означает недовыполнение цели."),
            _bullet("Просадка ОКК % = среднее ОКК без нарушения - среднее ОКК с нарушением. Это доля, форматировать как %."),
            _bullet("Сигналы формируются автоматически: повторяемый фрод у СВ, падение ОКК раньше KPI, слабые PICOS/OSA, низкое фото при высоком фроде."),
        ]
    )

    _section(parts, "9. Страница 7: территориальные менеджеры")
    parts.append(_paragraph("Витрина: page7_tm_monthly_snapshot. Одна строка = месяц + ТМ; регионы ТМ выводятся списком, региональный слайсер на этой странице мягкий."))
    parts.append(
        _table(
            [
                ["Метрика", "Вес", "Зеленая зона", "Желтая зона", "Красная зона"],
                ["KPI месяца территории %", "30%", ">=95%", "90–95%", "<90%"],
                ["Качество команды %", "20%", ">=80%", "70–80%", "<70%"],
                ["Обучение команды %", "15%", ">=95%", "90–95%", "<90%"],
                ["Фрод %", "15%", "<=10%", "10–18%", ">18%"],
                ["Стабильность команды %", "15%", ">=95%", "90–95%", "<90%"],
                ["Текучесть %", "5%", "<=10%", "10–15%", ">15%"],
            ]
        )
    )
    parts.extend(
        [
            _bullet("KPI месяца территории % и Качество команды % — обязательные; без них статус = Недостаточно данных."),
            _bullet("Если второстепенная метрика отсутствует, ее вес перераспределяется между доступными метриками; если доступно меньше 60% веса, статус = Недостаточно данных."),
            _bullet("Статус ТМ: Стабильно при балле >=80% и достаточных данных; Зона риска при балле <80%; Недостаточно данных при нехватке данных."),
            _bullet("Причина статуса ТМ выводит только красные флаги через запятую; желтые флаги причину не заполняют."),
            _bullet("Личные метрики ТМ — обучение ТМ %, средний балл теста, стаж — справочные и не входят в управленческий балл."),
        ]
    )

    _section(parts, "10. Страница 8: обучение и компетенции")
    parts.append(_paragraph("Витрины: page8_learning_course_summary, page8_learning_effect_trend, page8_learning_employee_matrix."))
    parts.extend(
        [
            _bullet("Матрица компетенций строится по активным H&N МЕ из USERS; компетенция = 1, если закрыт курс из согласованного маппинга, иначе 0."),
            _bullet("% закрытых компетенций = Закрыто компетенций / 6."),
            _bullet("Эффект курса считается относительно месяца прохождения: до = предыдущий месяц, после 30 = текущий месяц, после 60 = следующий месяц."),
            _bullet("Комбинированный эффект = 65% * Эффект KPI % + 35% * Эффект ОКК %; если доступна только одна метрика, используется она."),
            _bullet("Статус курса: подтвержден >=5%; умеренный эффект >=2%; эффект не подтвержден от -2% до +2%; отрицательная динамика < -2%; в процессе замера если курс свежий."),
        ]
    )

    _section(parts, "11. Страница 9: анонимный климат")
    parts.append(_paragraph("Витрины: page9_climate_quarterly_region и page9_climate_blocks_region. Источник — enps_fact, только агрегированные срезы."))
    parts.extend(
        [
            _bullet("Статус региона по климату считается только при минимум 10 ответах."),
            _bullet("Высокий риск: риск ухода >=24% или удовлетворенность <55%."),
            _bullet("Контроль: риск ухода >=18% или удовлетворенность <63%."),
            _bullet("Стабильно: условия риска и контроля не выполнены."),
            _bullet("Предупреждения QA по Центру/Дальнему Востоку оставлены как исторический eNPS-периметр; в рабочем dRegion их нет."),
        ]
    )

    _section(parts, "12. Контроль QA")
    parts.extend(
        [
            _bullet("Скрипт проверки: scripts/audit_vitrines.py."),
            _bullet("Итоговый отчет: data/out/qa_vitrines_report.xlsx."),
            _bullet(f"Текущие статусы Page1: {_safe_status_counts(out_dir / 'page1_region_monthly_snapshot.parquet', 'Статус региона')}."),
            _bullet(f"Текущие статусы Page5: {_safe_status_counts(out_dir / 'page5_sv_monthly_snapshot.parquet', 'Статус эффективности СВ')}."),
            _bullet(f"Текущие статусы Page7: {_safe_status_counts(out_dir / 'page7_tm_monthly_snapshot.parquet', 'Статус ТМ')}."),
        ]
    )

    _write_docx(parts, OUTPUT_DOCX)
    return OUTPUT_DOCX


def main() -> None:
    output_path = build_document()
    print(f"Сформировано: {output_path}")


if __name__ == "__main__":
    main()
