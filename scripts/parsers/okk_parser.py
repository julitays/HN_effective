import sys
import re
import pandas as pd
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import get_active_users_scope, get_as_of_date, load_settings, normalize_dim, save_parquet
from scripts.parsers.oed_parser import _build_lookups, _match_row_with_method


# ── Константы ────────────────────────────────────────────────────────────────

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "май": 5,   "июн": 6,   "июл": 7,  "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# Маппинг: очищенный substring заголовка → целевое имя
# Порядок важен — более специфичные паттерны ПЕРВЫМИ
COL_MAP = [
    # СВ: 2025 (SV ФИ / ФИО СВ), Dec/Jan-формат (SV — латиница)
    (r"фио св",        "sv_name_raw"),
    (r"^sv фи$",       "sv_name_raw"),
    (r"sv фи",         "sv_name_raw"),
    (r"^sv$",          "sv_name_raw"),   # Dec/Jan: колонка "SV"
    # ТМ: 2025 (ФИ РМ), 2026 (ТМ кириллица), Dec/Jan (TM латиница)
    (r"^фи рм$",       "tm_name_raw"),
    (r"^тм$",          "tm_name_raw"),   # кириллица ТМ
    (r"^tm$",          "tm_name_raw"),   # латиница TM
    # МЕ (мерчендайзер — кого проверяют)
    (r"фио ме",        "me_name_raw"),
    (r"^me$",          "me_name_raw"),   # латиница ME
    # Даты
    (r"дата фотоауд",  "audit_date"),
    (r"дата провед",   "audit_date"),
    (r"дата визита",   "audit_date"),
    (r"дата",          "audit_date"),
    # Место
    (r"^bu$",          "region"),
    (r"sap тт",        "store_sap_id"),
    (r"sap tt",        "store_sap_id"),   # латиница TT
    (r"sap ко",        "store_sap_id"),
    (r"sap id",        "store_sap_id"),
    (r"^сеть$",        "store_format"),
    (r"торговая",      "store_format"),
    (r"^адрес$",       "address"),
    (r"номер продажи", "visit_id"),
    (r"номер площадки","visit_id"),
    # Метрики % (специфичные — первыми)
    # PICOS числовой (26.84 = % наличия из системы) — системный показатель
    (r"^picos$",                       "pct_picos"),
    # Специфичные паттерны — РАНЬШЕ широкого, иначе перехватит
    (r"% качества picos по хп",        "pct_picos_хп"),      # холодная полка
    (r"% качества picos по тп",        "pct_picos_тп"),      # тёплая полка
    (r"общий.*% качества picos",       "pct_picos_качество"), # общий итог = аудиторский
    # Широкий паттерн — после специфичных
    (r"% качества picos",              "pct_picos_качество"), # % что проверяют аудиторы
    (r"% наличие picos",               "pct_picos"),          # наличие из старого формата
    (r"^osa$",               "pct_osa"),
    # "% качества фото" (2026 format) = только качество фотографирования
    (r"% качества фото",     "pct_фото_правила"),
    (r"% наличие по кат",    "pct_category"),
    (r"% наличие",           "pct_availability"),
    # Специфичные метрики качества фото должны идти РАНЬШЕ общего "средний % качества",
    # иначе март/апрель 2025 ошибочно маппятся в итоговое качество визита.
    (r"средний % качества 1 аудита",            "pct_фото_качество_1"),
    (r"средний % качества выполнения.*1фотоаудита", "pct_фото_качество_1"),
    # Итоговые метрики — всё что называется "средний % качества" без уточнения = финал
    (r"качество визита",     "pct_overall"),
    (r"^средний % качества$","pct_overall"),   # финальная метрика, не только фото!
    (r"средний % качества",  "pct_overall"),
    (r"резолюция наличие %", "pct_overall"),
    # Фальсификация
    (r"^фальса$",              "falsification_flag"),
    (r"наличие фальсиф",       "falsification_flag"),
    (r"^фальсификация$",       "falsification_flag"),
    (r"кол-во фальсифик",      "falsification_count"),
    (r"кол-во нарушен",        "falsification_count"),
    (r"комментари.{0,10}фальс","falsification_notes"),
    (r"примечание.{0,10}наруш","falsification_notes"),

    # === Блок 1: Качество фотоаудита ===
    # 1-й фотоаудит
    (r"1.й фотоаудит выполнен качественно",    "фото_качество_1"),
    (r"1 фотоаудит выполнен качественно",       "фото_качество_1"),
    (r"1-ый фотоаудит выполнен качественно",    "фото_качество_1"),
    (r"фото прямое.*не более 45.*\n",           "фото_прямое_1"),
    (r"^фото прямое$",                          "фото_прямое_1"),
    (r"^фото прямое \(не более 45\)$",          "фото_прямое_1"),
    (r"обзорное с расстояния.*не обрезано.*снизу.*сверху\)$", "фото_обзорное_1"),
    (r"фото четкие.*не размытые.*\n",           "фото_чёткое_1"),
    (r"^фото четкие, не размытые$",             "фото_чёткое_1"),
    (r"отсутствуют посторонние предметы$",      "фото_без_предметов_1"),
    (r"% качества выполнения фотоаудитов",      "pct_фото_качество_1"),
    (r"% качества доп",                         "pct_фото_доп"),
    (r"% качества по правилам фотографирования","pct_фото_правила"),
    # 2-й фотоаудит
    (r"2.й фотоаудит выполнен качественно",     "фото_качество_2"),
    (r"2 фотоаудит выполнен качественно",        "фото_качество_2"),
    (r"фото прямое \(не более 45\)\.1",         "фото_прямое_2"),
    (r"обзорное с расстояния.*фото не обрезано", "фото_обзорное_2"),
    (r"фото четкие, не размытые\.1",            "фото_чёткое_2"),
    (r"отсутствуют посторонние предметы\.1",    "фото_без_предметов_2"),
    (r"средний % качества 2 аудита",            "pct_фото_качество_2"),
    # Доп. фото и сценарий
    (r"наличие доп",                            "фото_доп_наличие"),
    (r"доп\. фото.*товар считан",               "фото_доп_товар"),
    (r"доп\. фото.*3 полки",                    "фото_доп_3полки"),
    (r"фото соотв.*сценарию",                   "фото_сценарий"),
    (r"дверцы хо открыты",                      "фото_хо_открыты"),
    (r"фирменные хо выполнены качественно",     "хо_фирменные_качество"),
    (r"соблюдены правила фотографирования",     "фото_правила_регал"),
    (r"фото прямое.*обзорное",                   "фото_прямое_обзорное"),
    (r"в первом фотоаудите столько же",         "фото_кол_одинаково"),
    (r"нет критической разницы.*количеств",     "фото_кол_одинаково"),

    # === Блок 2: PICoS / Выкладка ===
    (r"топ 16 размещены согласно планам в sfa", "picos_топ16_по_плану"),
    (r"топ 16 размещены на золотых полках",     "picos_топ16_золотые_полки"),
    (r"sku из топ 16.*мультифейсинг",           "picos_топ16_мультифейсинг"),
    (r"выкладка брендов.*блоками",              "picos_бренд_блок"),
    (r"собран минимум 1 бренд-блок",            "picos_бренд_блок"),
    (r"новинки расположены мин",                "picos_новинки_фейсинг"),
    (r"сметана и творог расположены рядом",     "picos_сметана_творог"),
    (r"про греческий расположен в блоке",       "picos_про_греческий"),
    (r"продукт тема расположен.*верхн",         "picos_тема_верхняя_полка"),
    (r"продукт растишка расположен.*нижн",      "picos_растишка_нижняя_полка"),
    (r"ценники присутствуют на каждом",         "picos_ценники"),
    (r"наличие ценников",                       "picos_ценники"),
    (r"ndп.*тема.*жб.*золотых полках",          "picos_ндп_тема"),
    (r"ндп.*тема.*жб.*золотых полках",          "picos_ндп_тема"),
    (r"греческий стоит в блоке",                "picos_про_греческий"),
    (r"раст.*продукты.*планто.*золотых",        "picos_планто"),
    # (дублирующие паттерны убраны — перенесены выше в правильном порядке)

    # === Блок 3: Признаки фальсификации (check-колонки) ===
    (r"дубли одного и того же регала",          "фальс_дубли_регала"),
    (r"работа с полкой не проведена тотально",  "фальс_полка_не_поправлена"),
    (r"подставленные фейсы",                    "фальс_подставные_фейсы"),
    (r"фото с другого устройства",              "фальс_другое_устройство"),
    # СКЮ (Cyrillic Ю, не У!) — специфичный .1 вариант первым
    (r"скю выставлены на.+\.1",                "фальс_конкурент_ску_2"),
    (r"скю выставлены на.*конкурента",         "фальс_конкурент_ску"),
    (r"фото до и после одинаковые",             "фальс_фото_одинаковые"),
    (r"фальсификация отсутствует",              "фальс_отсутствует"),

    # === Дополнительные паттерны ===
    (r"доп.*фото соттветс|доп.*фото.*качеств",   "фото_доп_качество"),
    (r"количество фотоаудитов",                   "фото_кол_аудитов"),
    (r"на фото отсутствуют посторонние.*четк",    "фото_без_предметов_чёткое"),
    (r"анкета.*холодн",                           "анкета_холодная_полка"),
    (r"анкета.*тепл",                             "анкета_теплая_полка"),
    # ТОП 16 мультифейсинг (в файлах "P" может быть латинским — "ТОP")
    (r"sku из то[пp] 16.*мультифейсинг",          "picos_топ16_мультифейсинг"),
    # ТОП 16 по плану (повтор с латинским p)
    (r"то[пp] 16 размещены согласно планам",      "picos_топ16_по_плану"),
    # НДП Тема ЖБ — скобки теперь остаются, "(тема жб)" в строке
    (r"ндп.*\(тема жб\)",                         "picos_ндп_тема"),
    (r"ндп.*стоит в сценарии ндп",                "picos_ндп_тема"),
    # Тема/Растишка (варианты без "продукт")
    (r"продукты бренда тема.*верхних",            "picos_тема_верхняя_полка"),
    (r"продукты бренда растишка.*нижних",         "picos_растишка_нижняя_полка"),
    # Планто
    (r"планто стоят вместе|раст.*продукты.*планто","picos_планто"),
    # Средний % (несколько вариантов)
    (r"средний общий %",                           "pct_средний_общий"),
    # (pct_picos_хп и pct_picos_качество — паттерны перенесены в начало COL_MAP)
    # Фото с экрана другого устройства (вариант)
    (r"фото с экрана другого устройства",          "фальс_другое_устройство"),
    (r"фото из другой тт",                         "фальс_фото_из_другой_тт"),
    # Есть фото всех категорий
    (r"есть фото всех категорий",                  "фото_все_категории"),
    # Работа с полкой (вариант без "тотально")
    (r"работа с полкой не проведена.*бардак",      "фальс_полка_не_поправлена"),
    # Комментарии о причинах
    (r"наличие комментариев причин",               "picos_причины_отсутствия"),
    # Обзорное 2-й аудит (вариант с "1,5 -2м" с пробелом)
    (r"обзорное с расстояния 1,5 -2м.*не обрезано","фото_обзорное_2"),
    # 2-й фотоаудит с инструкцией (Да-1, нет-0) — отдельные проверки
    (r"фото прямое.*да-1.*нет",                    "фото_прямое_2"),
    (r"фото четкие.*да-1.*нет",                    "фото_чёткое_2"),
    (r"на фото отсутствуют посторонние.*да-1",     "фото_без_предметов_2"),
    # Второй экземпляр «есть фото всех категорий»
    (r"есть фото всех категорий.+\.1",             "фото_все_категории_2"),
    # Дубли одинаковых колонок (после дедупликации получают суффикс .1)
    (r"фото прямое.+\.1",                          "фото_прямое_2"),
    (r"фото четкие.+\.1",                          "фото_чёткое_2"),
]

# Колонки, которые НЕ попадают в безымянные check_XX
# (либо уже именованы, либо текстовые/технические)
NOT_CHECK = {
    "sv_name_raw", "tm_name_raw", "me_name_raw", "audit_date", "region",
    "store_sap_id", "store_format", "address", "visit_id",
    "pct_picos", "pct_osa", "pct_category", "pct_availability", "pct_overall",
    "falsification_count", "falsification_notes", "falsification_flag",
    # Фотоаудит
    "фото_качество_1", "фото_качество_2",
    "фото_прямое_1", "фото_прямое_2", "фото_обзорное_1", "фото_обзорное_2",
    "фото_чёткое_1", "фото_чёткое_2", "фото_без_предметов_1", "фото_без_предметов_2",
    "фото_доп_наличие", "фото_доп_товар", "фото_доп_3полки",
    "фото_сценарий", "фото_хо_открыты", "фото_правила_регал",
    "фото_прямое_обзорное", "фото_кол_одинаково",
    "pct_фото_качество_1", "pct_фото_качество_2", "pct_фото_доп", "pct_фото_правила",
    # PICoS
    "picos_топ16_по_плану", "picos_топ16_золотые_полки", "picos_топ16_мультифейсинг",
    "picos_бренд_блок", "picos_новинки_фейсинг", "picos_сметана_творог",
    "picos_про_греческий", "picos_тема_верхняя_полка", "picos_растишка_нижняя_полка",
    "picos_ценники", "picos_ндп_тема", "picos_планто",
    "pct_picos_тп", "pct_picos_хп", "pct_средний_общий",
    "pct_picos_качество",
    "фото_правила_все_соблюдены",
    # Фальсификация (check-колонки)
    "фальс_дубли_регала", "фальс_полка_не_поправлена", "фальс_подставные_фейсы",
    "фальс_другое_устройство", "фальс_конкурент_ску",
    "фальс_фото_одинаковые", "фальс_отсутствует", "фальс_конкурент_ску",
    # Дополнительные
    "фото_доп_качество", "фото_кол_аудитов", "фото_без_предметов_чёткое",
    "анкета_холодная_полка", "анкета_теплая_полка",
    "picos_ндп_тема", "picos_планто", "picos_причины_отсутствия",
    "pct_picos_хп",
    "фото_все_категории", "фото_все_категории_2", "фальс_фото_из_другой_тт",
    "фото_прямое_2", "фото_чёткое_2", "фальс_конкурент_ску_2",
    "хо_фирменные_качество",
}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def detect_period(path: Path) -> tuple[str, int, int]:
    """Q: Сводная H&N МАЙ 02.06 Закрыт.xlsx в папке 2025/ → ("2025_05", 2025, 5)."""
    try:
        year = int(path.parent.name)
    except ValueError:
        year = get_as_of_date().year
        print(f"    ОКК: !! не удалось определить год из папки '{path.parent.name}' для {path.name}, использую текущий год {year} — проверьте файл")
    stem = path.stem.lower()
    for ru, num in MONTHS_RU.items():
        if ru in stem:
            return f"{year}_{num:02d}", year, num
    print(f"    ОКК: !! не удалось определить месяц из имени файла {path.name} — проверьте название")
    return f"{year}_00", year, 0


def _clean_col_header(col) -> str:
    """Нормализует заголовок: убирает \\n, метаданные-скобки, лишние пробелы."""
    s = re.sub(r"\n+", " ", str(col))
    # Убираем только скобки с системными метаданными
    s = re.sub(
        r"\s*\((формула|внешний\s+код|клиент|раздел[^)]*|номер маршрута|информативная\s+колонка)\)\s*",
        " ", s, flags=re.IGNORECASE
    )
    return " ".join(s.lower().split())


def _find_detail_sheet(xl: pd.ExcelFile) -> tuple[str, int]:
    """Ищем лист с деталями и строку заголовка в первых четырёх строках."""
    MARKERS  = ["анкеты", "w0", "w1", "детал", "лист1", "база", "данные", "detail"]
    REQUIRED = ["sap", "bu", "sv", "дата", "фио", "фальс"]

    def find_in_sheets(sheet_names: list[str]) -> tuple[str, int] | None:
        for name in sheet_names:
            for header_row in range(4):
                try:
                    df_test = xl.parse(name, nrows=3, header=header_row)
                    hdr = " ".join(_clean_col_header(c) for c in df_test.columns)
                    if sum(r in hdr for r in REQUIRED) >= 2:
                        return name, header_row
                except Exception:
                    pass
        return None

    marked_sheets = [
        name
        for name in xl.sheet_names
        if any(marker in name.lower().strip() for marker in MARKERS)
    ]
    marked_match = find_in_sheets(marked_sheets)
    if marked_match is not None:
        return marked_match

    all_sheets_match = find_in_sheets(xl.sheet_names)
    if all_sheets_match is not None:
        return all_sheets_match

    return xl.sheet_names[0], 0


def _clean_name(name) -> str:
    """Убирает суффиксы 'оглу'/'кызы', числовые суффиксы и лишние пробелы."""
    if pd.isna(name) or not str(name).strip():
        return ""
    s = re.sub(r"\s+(оглу|кызы)\s*$", "", str(name), flags=re.IGNORECASE)
    s = re.sub(r"\s+\d+\s*$", "", s)   # "Иванов Иван 2" → "Иванов Иван"
    return " ".join(s.strip().split()).title()


def _has_numeric_suffix(name) -> bool:
    """True если имя заканчивается цифрой → строку нужно удалить."""
    if pd.isna(name):
        return False
    return bool(re.search(r"\d+\s*$", str(name).strip()))


def _find_col(df: pd.DataFrame, patterns: list[str]) -> str | None:
    """Возвращает первую колонку df, в заголовке которой есть хотя бы один паттерн."""
    cols_lower = {c: str(c).lower() for c in df.columns}
    for pat in patterns:
        for col, low in cols_lower.items():
            if re.search(pat, low):
                return col
    return None


def _map_columns(df: pd.DataFrame) -> dict[str, str]:
    """Возвращает маппинг {исходная_колонка → целевое_имя} по COL_MAP."""
    result = {}
    used_targets = set()
    # Очищаем заголовки: убираем \n, скобочные пояснения, лишние пробелы
    cols_clean = [(c, _clean_col_header(c)) for c in df.columns]

    for pat, target in COL_MAP:
        if target in used_targets:
            continue
        for col, clean in cols_clean:
            if col in result:
                continue
            if re.search(pat, clean):
                result[col] = target
                used_targets.add(target)
                break
    return result


def _normalize_falsification(series: pd.Series) -> pd.Series:
    """100 или 1 → True (нарушение найдено). 0 → False."""
    num = pd.to_numeric(series, errors="coerce")
    mx  = num.max(skipna=True)
    if pd.notna(mx) and mx > 1:
        return (num == 100).astype("boolean")
    return (num == 1).astype("boolean")


def _build_unified_falsification_flag(df: pd.DataFrame) -> pd.Series:
    """Return the source's final falsification result without inferred fallbacks.

    The explicit final flag has priority. Older files without that flag use the
    final falsification count. Comments and reason columns are descriptive and
    must not turn a non-fraud visit into fraud.
    """
    explicit_flag = (
        df["has_falsification"].fillna(False).astype("boolean")
        if "has_falsification" in df.columns
        else pd.Series(False, index=df.index, dtype="boolean")
    )
    has_explicit_source = (
        df["_has_explicit_falsification_flag"].fillna(False).astype(bool)
        if "_has_explicit_falsification_flag" in df.columns
        else pd.Series(False, index=df.index)
    )

    count_flag = (
        df["falsification_count"].fillna(0).gt(0)
        if "falsification_count" in df.columns
        else pd.Series(False, index=df.index)
    )
    has_count_source = (
        df["_has_falsification_count"].fillna(False).astype(bool)
        if "_has_falsification_count" in df.columns
        else pd.Series(False, index=df.index)
    )

    unified = pd.Series(False, index=df.index, dtype="boolean")
    unified.loc[has_explicit_source] = explicit_flag.loc[has_explicit_source]
    count_fallback = ~has_explicit_source & has_count_source
    unified.loc[count_fallback] = count_flag.loc[count_fallback]
    return unified.astype("boolean")


def _normalize_binary(series: pd.Series) -> pd.Series:
    """-1 и 1 → 1 (хорошо), 0 → 0 (плохо), NaN → NA."""
    num = pd.to_numeric(series, errors="coerce")
    result = num.abs().where(num.abs() <= 1)
    return pd.Series(
        [None if pd.isna(v) else int(v) for v in result],
        dtype="Int8",
    )


def _normalize_percent(series: pd.Series) -> pd.Series:
    """Приводим к шкале 0–1.

    Логика:
    - если колонка массово в шкале 0–100, делим весь столбец на 100
    - если в колонке почти всё уже 0–1, единичные выбросы > 1.5 не должны
      масштабировать весь столбец; такие значения просто ограничиваем 1.0
    """
    num = pd.to_numeric(series, errors="coerce")
    gt_mask = num > 1.5
    non_na = num.notna().sum()
    gt_share = float(gt_mask.sum()) / float(non_na) if non_na else 0.0

    if num.max(skipna=True) > 1.5 and gt_share >= 0.2:
        num = num / 100
    else:
        num = num.clip(upper=1.0)
    return num.round(4)


# ── Матчинг сотрудников ───────────────────────────────────────────────────────

def _match_names(names: pd.Series, id_lookup: dict, name_lookup: dict,
                 partial_lookup: dict) -> tuple[list, list]:
    ids, methods = [], []
    for name in names:
        cleaned = _clean_name(name)
        eid, method = _match_row_with_method("", cleaned, id_lookup, name_lookup, partial_lookup)
        ids.append(eid or None)
        methods.append(method)
    return ids, methods


# ── Загрузка одного файла ─────────────────────────────────────────────────────

def _load_okk_file(
    path: Path,
    id_lookup: dict,
    name_lookup: dict,
    partial_lookup: dict,
) -> pd.DataFrame | None:
    period, year, month = detect_period(path)

    try:
        xl = pd.ExcelFile(path)
    except Exception as e:
        print(f"    ОШИБКА открытия {path.name}: {e}")
        return None

    sheet, header_row = _find_detail_sheet(xl)
    try:
        raw = xl.parse(sheet, header=header_row, dtype=str)
        # Дедупликация: одинаковые колонки получают суффикс .1, .2 и т.д.
        seen: dict[str, int] = {}
        dedup_cols = []
        for col in raw.columns:
            key = _clean_col_header(col)
            if key in seen:
                seen[key] += 1
                dedup_cols.append(f"{col}.{seen[key]}")
            else:
                seen[key] = 0
                dedup_cols.append(col)
        raw.columns = dedup_cols
    except Exception as e:
        print(f"    ОШИБКА чтения листа '{sheet}' в {path.name}: {e}")
        return None

    if raw.empty or len(raw.columns) < 5:
        print(f"    {path.name}: лист '{sheet}' пустой, пропускаем")
        return None

    # Маппинг колонок
    col_map = _map_columns(raw)
    raw = raw.rename(columns=col_map)

    # Фильтруем строки с числами в имени СВ, ТМ или МЕ (дублирующие/тестовые визиты)
    for name_col in ("sv_name_raw", "tm_name_raw", "me_name_raw"):
        if name_col in raw.columns:
            mask = raw[name_col].apply(_has_numeric_suffix)
            if mask.sum():
                raw = raw[~mask]

    if raw.empty:
        return None

    n = len(raw)
    # Инициализируем с нужным количеством строк — иначе скаляры не broadcast
    result = pd.DataFrame(index=range(n))
    result["period"]      = period
    result["year"]        = year
    result["month"]       = month
    result["file_source"] = path.name

    # Дата аудита
    if "audit_date" in raw.columns:
        result["audit_date"] = pd.to_datetime(
            raw["audit_date"], errors="coerce", dayfirst=True, format="mixed"
        )
    else:
        result["audit_date"] = None

    # Место — только если колонка реально есть в файле
    for col in ("region", "store_sap_id", "store_format", "address"):
        if col in raw.columns:
            result[col] = raw[col].str.strip()
    result["store_format"] = result.get("store_format", pd.Series(dtype="str"))

    # Матчинг СВ
    sv_raw = raw["sv_name_raw"] if "sv_name_raw" in raw.columns else pd.Series([""] * n)
    tm_raw = raw["tm_name_raw"] if "tm_name_raw" in raw.columns else pd.Series([""] * n)
    me_raw = raw["me_name_raw"] if "me_name_raw" in raw.columns else pd.Series([""] * n)

    sv_ids, sv_methods = _match_names(sv_raw, id_lookup, name_lookup, partial_lookup)
    tm_ids, tm_methods = _match_names(tm_raw, id_lookup, name_lookup, partial_lookup)
    me_ids, me_methods = _match_names(me_raw, id_lookup, name_lookup, partial_lookup)

    result["employee_id"]      = sv_ids
    result["tm_id"]            = tm_ids
    result["me_id"]            = me_ids
    result["sv_name_raw"]      = sv_raw.apply(_clean_name).values
    result["tm_name_raw"]      = tm_raw.apply(_clean_name).values
    result["me_name_raw"]      = me_raw.apply(_clean_name).values
    result["sv_match_method"]  = sv_methods
    result["tm_match_method"]  = tm_methods
    result["me_match_method"]  = me_methods

    # Процентные метрики — только если колонка есть в файле
    for col in ("pct_picos", "pct_osa", "pct_overall"):
        if col in raw.columns:
            result[col] = _normalize_percent(raw[col])

    # Фальсификация
    has_explicit_falsification_flag = "falsification_flag" in raw.columns
    has_falsification_count = "falsification_count" in raw.columns
    result["_has_explicit_falsification_flag"] = has_explicit_falsification_flag
    result["_has_falsification_count"] = has_falsification_count

    if has_explicit_falsification_flag:
        result["has_falsification"] = _normalize_falsification(raw["falsification_flag"])
    else:
        result["has_falsification"] = pd.array([False] * n, dtype="boolean")

    result["falsification_count"] = (
        pd.to_numeric(raw["falsification_count"], errors="coerce").astype("Int16")
        if has_falsification_count else pd.array([0] * n, dtype="Int16")
    )
    result["falsification_notes"] = (
        raw["falsification_notes"].str.strip()
        if "falsification_notes" in raw.columns else None
    )

    # Именованные колонки — разделяем pct_ (проценты) и остальные (бинарные)
    SYSTEM_COLS = {
        "sv_name_raw", "tm_name_raw", "me_name_raw", "audit_date", "region",
        "store_sap_id", "store_format", "address", "visit_id",
        "falsification_count", "falsification_notes", "falsification_flag",
    }
    for col in raw.columns:
        if col not in NOT_CHECK or col in SYSTEM_COLS:
            continue
        if col.startswith("pct_"):
            # Процентная метрика — нормализуем в 0–1
            result[col] = _normalize_percent(raw[col])
        else:
            # Бинарная проверка
            result[col] = _normalize_binary(raw[col])

    # Безымянные числовые проверки — не попавшие ни в один маппинг
    check_cols = [
        c for c in raw.columns
        if c not in NOT_CHECK
        and c not in ("sv_name_raw", "tm_name_raw", "me_name_raw", "audit_date")
        and pd.to_numeric(raw[c], errors="coerce").notna().sum() > n * 0.1
    ]
    for i, col in enumerate(check_cols, 1):
        result[f"check_{i:02d}"] = _normalize_binary(raw[col])

    matched_sv = sum(1 for m in sv_methods if m != "unmatched")
    print(
        f"    {path.name}[{sheet}, заголовок {header_row + 1}]: "
        f"{n} строк | СВ {matched_sv}/{n} | {len(check_cols)} проверок"
    )
    return result


# ── Применение маппинга из config ────────────────────────────────────────────

def _apply_column_map(df: pd.DataFrame, map_path: str = "config/okk_columns_map.xlsx") -> pd.DataFrame:
    """
    Читает config/okk_columns_map.xlsx:
      - Берём в витрину или нет = "Да"  → оставляем
      - Необходимое название в файле    → переименовываем
    Строки без сокращения и дубли по сокращению пропускаются.
    """
    if not Path(map_path).exists():
        print(f"  ОКК: файл маппинга не найден ({map_path}), пропускаем переименование")
        return df

    mapping = pd.read_excel(map_path, dtype=str)
    # Убираем строки без сокращения
    mapping = mapping[mapping["Сокращение в файле"].notna()].copy()
    mapping["Сокращение в файле"] = mapping["Сокращение в файле"].str.strip()
    mapping["Необходимое название в файле"] = mapping["Необходимое название в файле"].str.strip()
    mapping["Берем в витрину или нет"] = mapping["Берем в витрину или нет"].str.strip().str.lower()

    # Дедупликация — берём первую запись для каждого сокращения
    mapping = mapping.drop_duplicates(subset=["Сокращение в файле"], keep="first")

    # Только колонки "Да"
    keep = mapping[mapping["Берем в витрину или нет"] == "да"]

    # Колонки для удаления (не "Да") — только те, что есть в df
    drop_cols = [
        row["Сокращение в файле"]
        for _, row in mapping.iterrows()
        if row["Берем в витрину или нет"] != "да"
        and row["Сокращение в файле"] in df.columns
    ]
    df = df.drop(columns=drop_cols, errors="ignore")

    # Переименование — только если есть целевое название
    rename_map = {}
    seen_targets: set[str] = set()
    for _, row in keep.iterrows():
        short   = row["Сокращение в файле"]
        target  = row.get("Необходимое название в файле", "")
        if not target or pd.isna(target) or short not in df.columns:
            continue
        # Если такое целевое имя уже занято — добавляем суффикс _2
        final = target
        if final in seen_targets:
            final = f"{target} 2"
        seen_targets.add(final)
        rename_map[short] = final

    df = df.rename(columns=rename_map)
    return df


# ── Основная функция ──────────────────────────────────────────────────────────

def parse_okk(dim: pd.DataFrame = None) -> None:
    settings = load_settings()
    okk_root = Path(settings["sources"]["okk"]["folder"])
    output   = settings["sources"]["okk"]["output"]

    if dim is None or dim.empty:
        dim_path = Path(settings["sources"]["users"]["output"])
        if dim_path.exists():
            dim = normalize_dim(pd.read_parquet(dim_path))
            print(f"  ОКК: загружен dim_employees ({len(dim)} записей)")
        else:
            print("  ОКК: dim_employees не найден, матчинг будет пропущен")
            dim = pd.DataFrame()

    if dim.empty:
        id_lookup = name_lookup = partial_lookup = {}
    else:
        id_lookup, name_lookup, partial_lookup = _build_lookups(dim)

    all_files = sorted(
        [
            path
            for path in okk_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".xlsx", ".xlsm"}
        ]
    )
    if not all_files:
        print("  ОКК: файлы не найдены, пропускаем")
        return

    print(f"  ОКК: найдено {len(all_files)} файлов")
    frames = []
    for f in all_files:
        df = _load_okk_file(f, id_lookup, name_lookup, partial_lookup)
        if df is not None and not df.empty:
            frames.append(df)

    if not frames:
        print("  ОКК: нет данных для обработки")
        return

    fact_okk = pd.concat(frames, ignore_index=True)

    # Убираем полностью пустые колонки (Power BI не может определить их тип)
    empty_cols = [c for c in fact_okk.columns if fact_okk[c].isna().all()]
    if empty_cols:
        print(f"  ОКК: удалено {len(empty_cols)} пустых колонок: {empty_cols}")
        fact_okk = fact_okk.drop(columns=empty_cols)

    # Object-колонки с нулевым заполнением → тоже удаляем
    obj_empty = [c for c in fact_okk.columns
                 if fact_okk[c].dtype == object and fact_okk[c].notna().sum() == 0]
    if obj_empty:
        fact_okk = fact_okk.drop(columns=obj_empty)

    # ── Фильтрация невалидных строк ────────────────────────────────────────────
    before = len(fact_okk)
    # Строки без даты аудита — невозможны, удаляем
    fact_okk = fact_okk[fact_okk["audit_date"].notna()]
    # Строки без SAP торговой точки — невозможны, удаляем
    if "store_sap_id" in fact_okk.columns:
        fact_okk = fact_okk[fact_okk["store_sap_id"].notna() & (fact_okk["store_sap_id"] != "")]
    removed = before - len(fact_okk)
    if removed:
        print(f"  Удалено невалидных строк (без даты/SAP): {removed}")

    # ── Единый флаг фальсификации ──────────────────────────────────────────────
    # Используем только итоговый признак источника:
    # - явный финальный флаг в новых файлах;
    # - финальный числовой счётчик в старых файлах без явного флага.
    # Комментарии и причины являются детализацией и не меняют итоговый флаг.
    fact_okk["has_falsification"] = _build_unified_falsification_flag(fact_okk)

    # ── Объединяем pct_overall и pct_средний_общий (одна метрика) ─────────────
    if "pct_средний_общий" in fact_okk.columns:
        fact_okk["pct_overall"] = fact_okk["pct_overall"].fillna(fact_okk["pct_средний_общий"])
        fact_okk = fact_okk.drop(columns=["pct_средний_общий"])

    # ── Удаляем технические колонки ────────────────────────────────────────────
    tech = [
        "file_source", "sv_match_method", "tm_match_method", "me_match_method",
        "_has_explicit_falsification_flag", "_has_falsification_count",
    ]
    fact_okk = fact_okk.drop(columns=[c for c in tech if c in fact_okk.columns])

    # Ещё раз чистим полностью пустые (после фильтрации строк)
    empty_after = [c for c in fact_okk.columns if fact_okk[c].isna().all()]
    if empty_after:
        print(f"  Удалено пустых колонок после фильтрации: {empty_after}")
        fact_okk = fact_okk.drop(columns=empty_after)

    # ── Вычисляемые сводные колонки ───────────────────────────────────────────

    # 1. Сводный PICoS (системный % наличия): если нет pct_picos, берём из ХП+ТП
    picos = fact_okk["pct_picos"].copy() if "pct_picos" in fact_okk.columns \
            else pd.Series(pd.array([pd.NA] * len(fact_okk), dtype="Float64"))
    хп_тп_cols = [c for c in ("pct_picos_хп", "pct_picos_тп") if c in fact_okk.columns]
    if хп_тп_cols:
        computed_avg = fact_okk[хп_тп_cols].mean(axis=1)
        picos = picos.where(picos.notna(), computed_avg)
    fact_okk["pct_picos"] = picos

    # Сводный PICoS качество (аудиторский): если нет pct_picos_качество, берём из ХП
    if "pct_picos_качество" in fact_okk.columns and хп_тп_cols:
        qual = fact_okk["pct_picos_качество"].copy()
        fact_okk["pct_picos_качество"] = qual.where(qual.notna(), computed_avg)
    elif хп_тп_cols and "pct_picos_качество" not in fact_okk.columns:
        fact_okk["pct_picos_качество"] = computed_avg

    # 2. Сводная колонка правил фотографирования (все соблюдены = 1)
    # Новый формат: фото_качество_2 — уже сводная
    # Старый формат: вычисляем как min(прямое, обзорное, чёткое, без_предметов)
    photo_rule_cols = [c for c in (
        "фото_прямое_2", "фото_обзорное_2", "фото_чёткое_2", "фото_без_предметов_2"
    ) if c in fact_okk.columns]

    if "фото_качество_2" in fact_okk.columns and photo_rule_cols:
        combined = fact_okk["фото_качество_2"].copy().astype("float")
        null_mask = combined.isna()
        if photo_rule_cols:
            computed = fact_okk[photo_rule_cols].min(axis=1).astype("float")
            combined = combined.where(~null_mask, computed)
        fact_okk["фото_правила_все_соблюдены"] = pd.array(
            [None if pd.isna(v) else int(v) for v in combined], dtype="Int8"
        )
    elif "фото_качество_2" in fact_okk.columns:
        fact_okk["фото_правила_все_соблюдены"] = fact_okk["фото_качество_2"]
    elif photo_rule_cols:
        computed = fact_okk[photo_rule_cols].min(axis=1)
        fact_okk["фото_правила_все_соблюдены"] = pd.array(
            [None if pd.isna(v) else int(v) for v in computed], dtype="Int8"
        )

    # Применяем маппинг: оставляем нужные колонки и переименовываем
    fact_okk = _apply_column_map(fact_okk)

    if dim is not None and not dim.empty and "ID мерчендайзера" in fact_okk.columns:
        scope = get_active_users_scope(dim)
        before = len(fact_okk)
        fact_okk = fact_okk[fact_okk["ID мерчендайзера"].astype(str).isin(scope["merch_ids"])].copy()
        print(f"  ОКК: фильтр по активным USERS {before} -> {len(fact_okk)} строк")

    save_parquet(fact_okk, output)

    total    = len(fact_okk)
    fals_col = "Флаг фальсификации" if "Флаг фальсификации" in fact_okk.columns else "has_falsification"
    fals_pct = fact_okk[fals_col].eq(True).sum() if fals_col in fact_okk.columns else 0
    period_col = next((c for c in fact_okk.columns if "период" in c.lower() or c == "period"), None)
    n_periods  = fact_okk[period_col].nunique() if period_col else "?"
    print(f"\n  ОКК итого: {total} строк | периодов: {n_periods}")
    print(f"  Фальсификации: {fals_pct} ({fals_pct/total*100:.1f}%)")
    print(f"  Колонок check_*: {sum(1 for c in fact_okk.columns if c.startswith('check_'))}")


