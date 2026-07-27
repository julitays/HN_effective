import re
import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet

# ── Путь к файлу методологии ─────────────────────────────────────────────────
METHODOLOGY_PATH = r"C:/Users/julit/Desktop/DASHBOARDS/ОПРОСeNPS/data/Структура/Единая методология.xlsx"

# ── Сегменты по файлам ────────────────────────────────────────────────────────
SEGMENT_MAP = {
    "КАМРМТМ": "SV+1",
    "КАМР":    "SV+1",
    "КАМ":     "SV+1",
    "СВ":      "SV",
    "SV":      "SV",
    "ПромоМЕ": "SV-1",
    "МЕ":      "SV-1",
}

# ── Ключевые паттерны вопросов (для поиска в заголовках) ─────────────────────
Q_PATTERNS = {
    # eNPS
    "enps_raw":          r"готовы порекомендовать компанию",
    # Лояльность
    "лояльность":        r"нравится работать",
    "гордость":          r"горжусь тем, что работаю",
    # Риск ухода
    "риск_ухода":        r"задумываетесь о смене работы",
    # Стресс
    "стресс":            r"уровень.{0,10}стресс|стресс.{0,10}уровень",
    "нагрузка":          r"перерабатываете|переработки|нагрузк",
    # Демография — ищем и с "Ваш/Ваша" и без (для архивных файлов)
    "регион":            r"регион",
    "должность":         r"должность",
    "стаж":              r"как давно вы работаете",
    "возраст":           r"возраст",
    # Метрики удовлетворённости (используем блок Оплата как прокси)
    "удовл_зарплата":    r"система мотивации|рассчитывается моя зарплата|удовлетвор.{0,10}зарплат|уровн.{0,15}оплат",
    "удовл_льготы":      r"социальный пакет|довольны.{0,15}льгот",
    "удовл_условия":     r"физическими условиями|условия.{0,15}работ",
    # Метрики вовлечённости
    "вклад_компания":    r"важный вклад в успех|работа дает ощущение",
    "смысл_работы":      r"профессиональные способности реализуются|реализоваться",
    "саморазвитие":      r"развиваться.{0,20}профессионально|профессиональный рост",
    # Блоки для графика
    "блок_руководитель_q":  [  # несколько вопросов → усредняем
        r"руководитель замечает",
        r"обратную связь.{0,20}руководит",
        r"руководитель.{0,20}обучать",
        r"регулярность общения с руководителем",
        r"руководитель.{0,20}доступен",
        r"руководитель.{0,20}помогает.{0,20}приоритет",
    ],
    "блок_нагрузка_q": [
        r"равномерность.{0,20}нагрузк",
        r"физическими условиями",
        r"уровень стресса",
    ],
    "блок_обучение_q": [
        r"качество.{0,20}обуч",
        r"доступность и актуальность обучающего",
        r"эффективность вебинаров",
        r"обучение помогает",
        r"обучение способствует",
    ],
    "блок_инструменты_q": [
        r"цифровых инструментов",
        r"ресурсов.{0,20}эффективно",
        r"информация доводится",
    ],
    "блок_рост_q": [
        r"профессиональные способности реализ",  # реализуются / реализовываются
        r"критерии.{0,20}повышения в должности",
        r"обучение способствует.{0,20}развитию",
    ],
    "блок_оплата_q": [
        r"система мотивации стимулирует",
        r"рассчитывается моя зарплата",
    ],
    "блок_команда_q": [
        r"комфортно работать с.{0,20}коллегами",
        r"качество.{0,20}скорости взаимодействия с коллег",
    ],
}


# ── Загрузка методологии ─────────────────────────────────────────────────────

def _load_methodology() -> dict:
    """Загружает справочник ответов и словарь вопросов из методологии."""
    if not Path(METHODOLOGY_PATH).exists():
        print(f"  ENPS: методология не найдена: {METHODOLOGY_PATH}")
        return {}

    xl = pd.ExcelFile(METHODOLOGY_PATH)

    if "Справочник_вариантов_ответов" not in xl.sheet_names:
        print(
            f"  ENPS: !! в файле методологии нет листа 'Справочник_вариантов_ответов' "
            f"(есть: {xl.sheet_names}) — расчёт баллов будет пустым"
        )
        return {}

    # Справочник вариантов ответов → {текст_ответа: числовое_значение}
    answers = xl.parse("Справочник_вариантов_ответов", dtype=str)
    score_map: dict[str, float] = {}
    for _, row in answers.iterrows():
        if str(row.get("Использовать в расчете", "")).strip() != "Да":
            continue
        val = row.get("Числовое значение", "")
        ans = str(row.get("Вариант ответа", "") or "").strip().lower()
        if ans and val and str(val).strip():
            try:
                score_map[ans] = float(str(val).strip())
            except ValueError:
                pass

    return {"score_map": score_map}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _find_col(df: pd.DataFrame, pattern: str) -> str | None:
    """Ищет колонку по паттерну в заголовке (case-insensitive)."""
    for col in df.columns:
        if re.search(pattern, str(col).lower()):
            return col
    return None


def _to_score(val, score_map: dict) -> float | None:
    """Конвертирует текстовый/числовой ответ в балл 0–10."""
    if pd.isna(val) or str(val).strip() in ("-", "", "nan", "*"):
        return None
    s = str(val).strip()
    # Числовые значения 0-10
    try:
        num = float(s)
        if 0 <= num <= 10:
            return num
    except ValueError:
        pass
    # Текстовый маппинг
    return score_map.get(s.lower())


def _detect_segment(filepath: str) -> str:
    """Определяет сегмент по имени файла."""
    name = Path(filepath).stem.upper()
    for key, seg in SEGMENT_MAP.items():
        if key.upper() in name:
            return seg
    # Архивные файлы
    if "SV+1" in name or "АРХИВ" in name and "SV+1" in name:
        return "SV+1"
    if "SV-1" in name:
        return "SV-1"
    return "SV"


def _detect_period_from_date(date_str: str) -> str:
    """'2026-02-18 ...' → '2026_Q1'."""
    try:
        dt = pd.to_datetime(date_str)
        year = dt.year
        month = dt.month
        q = (month - 1) // 3 + 1
        return f"{year}_Q{q}"
    except Exception:
        return "Unknown"


def _normalize_period(raw_period: str) -> str:
    """Нормализует период: 'Q3_21' → '2021_Q3', '2026_Q1' → '2026_Q1'."""
    s = str(raw_period).strip()
    # Уже нормальный формат YYYY_Qn
    if re.match(r"\d{4}_Q\d", s):
        return s
    # Архивный формат Qn_YY
    m = re.match(r"Q(\d)_(\d{2})$", s, re.IGNORECASE)
    if m:
        q, yy = m.group(1), m.group(2)
        year = int(yy) + 2000
        return f"{year}_Q{q}"
    # Формат H1_2024 или 2024_H1
    m2 = re.match(r"(\d{4})_H(\d)", s, re.IGNORECASE)
    if m2:
        year, h = int(m2.group(1)), int(m2.group(2))
        q = 1 if h == 1 else 3
        return f"{year}_Q{q}"
    return s


def _avg(*values) -> float | None:
    """Среднее непустых значений."""
    v = [x for x in values if x is not None and not pd.isna(x)]
    return round(sum(v) / len(v), 3) if v else None


def _enps_category(score) -> str | None:
    """0-10 → Промоутер/Пассивный/Критик."""
    if score is None:
        return None
    s = float(score)
    if s >= 9:
        return "Промоутер"
    if s >= 7:
        return "Пассивный"
    return "Критик"


def _risk_level(score) -> str | None:
    """Балл риска → уровень (инвертированная шкала)."""
    if score is None:
        return None
    s = float(score)
    if s >= 8:
        return "Низкий"
    if s >= 6:
        return "Средний"
    return "Высокий"


# ── Загрузка одного файла ─────────────────────────────────────────────────────

def _parse_file(filepath: str, score_map: dict, is_archive: bool = False) -> pd.DataFrame:
    """Читает один Excel-файл опроса и возвращает нормализованный DataFrame."""
    try:
        xl = pd.ExcelFile(filepath)
        # Архивные файлы: читаем лист "выгрузка" если он есть
        if "выгрузка" in xl.sheet_names:
            sheet = "выгрузка"
        else:
            sheet = xl.sheet_names[0]
            print(
                f"    ENPS: !! в {Path(filepath).name} нет листа 'выгрузка', "
                f"беру первый лист '{sheet}' (есть: {xl.sheet_names}) — проверьте, тот ли это лист"
            )
        raw = xl.parse(sheet, dtype=str)
    except Exception as e:
        print(f"    ОШИБКА {Path(filepath).name}: {e}")
        return pd.DataFrame()

    segment = _detect_segment(filepath)
    rows = []

    for _, row in raw.iterrows():
        r: dict = {}

        # ── Период ─────────────────────────────────────────────────────────
        if is_archive and "Цикл" in raw.columns:
            raw_period = str(row.get("Цикл", "")).strip()
            r["период"] = _normalize_period(raw_period)
        elif "Время создания" in raw.columns:
            r["период"] = _detect_period_from_date(str(row.get("Время создания", "")))
        else:
            r["период"] = "Unknown"

        # Год и квартал
        m = re.match(r"(\d{4})_Q(\d)", r["период"])
        if m:
            r["год"]     = int(m.group(1))
            r["квартал"] = f"Q{m.group(2)}"
        else:
            r["год"] = None
            r["квартал"] = None

        r["сегмент"] = segment

        # ── Демография ────────────────────────────────────────────────────
        r["регион"]      = _get_demo(row, raw, "регион")
        pos = _get_demo(row, raw, "должность")
        # Если должность не заполнена — берём из сегмента файла
        r["должность"]   = pos if pos else segment
        r["стаж_группа"] = _get_demo(row, raw, "стаж")

        # ── Ключевые метрики ─────────────────────────────────────────────
        enps_col  = _find_col(raw, Q_PATTERNS["enps_raw"])
        loy_col   = _find_col(raw, Q_PATTERNS["лояльность"])
        risk_col  = _find_col(raw, Q_PATTERNS["риск_ухода"])
        stress_col = _find_col(raw, Q_PATTERNS["стресс"])

        r["готов_рекомендовать"] = _to_score(row.get(enps_col) if enps_col else None, score_map)
        r["лояльность"]          = _to_score(row.get(loy_col) if loy_col else None, score_map)
        r["риск_ухода_балл"]     = _to_score(row.get(risk_col) if risk_col else None, score_map)
        r["стресс"]              = _to_score(row.get(stress_col) if stress_col else None, score_map)

        # ── Вовлечённость ─────────────────────────────────────────────────
        eng_cols = [
            _find_col(raw, Q_PATTERNS["вклад_компания"]),
            _find_col(raw, Q_PATTERNS["смысл_работы"]),
            _find_col(raw, Q_PATTERNS["саморазвитие"]),
        ]
        eng_scores = [_to_score(row.get(c) if c else None, score_map) for c in eng_cols]
        r["вовлечённость"] = _avg(*eng_scores)

        # ── Удовлетворённость базовая ─────────────────────────────────────
        sat_cols = [
            _find_col(raw, Q_PATTERNS["удовл_зарплата"]),
            _find_col(raw, Q_PATTERNS["удовл_льготы"]),
            _find_col(raw, Q_PATTERNS["удовл_условия"]),
        ]
        sat_scores = [_to_score(row.get(c) if c else None, score_map) for c in sat_cols]
        r["удовлетворённость"] = _avg(*sat_scores)

        # ── Блоки (среднее по нескольким вопросам) ───────────────────────
        r["блок_руководство"]  = _block_avg(row, raw, score_map, "блок_руководитель_q")
        r["блок_нагрузка"]     = _block_avg(row, raw, score_map, "блок_нагрузка_q")
        r["блок_обучение"]     = _block_avg(row, raw, score_map, "блок_обучение_q")
        r["блок_инструменты"]  = _block_avg(row, raw, score_map, "блок_инструменты_q")
        r["блок_рост"]         = _block_avg(row, raw, score_map, "блок_рост_q")
        r["блок_оплата"]       = _block_avg(row, raw, score_map, "блок_оплата_q")
        r["блок_команда"]      = _block_avg(row, raw, score_map, "блок_команда_q")

        # ── Классификации ─────────────────────────────────────────────────
        r["категория_enps"]    = _enps_category(r["готов_рекомендовать"])
        r["риск_ухода_уровень"] = _risk_level(r["риск_ухода_балл"])

        # ── Сигналы риска ─────────────────────────────────────────────────
        r["думает_об_уходе"]        = r["риск_ухода_уровень"] == "Высокий"
        r["критик_enps"]            = r["категория_enps"] == "Критик"
        r["низкая_лояльность"]      = (r["лояльность"] is not None and r["лояльность"] <= 4)
        r["высокий_стресс"]         = (r["стресс"] is not None and r["стресс"] <= 4)

        # Составной риск ухода: ≥2 из 3 косвенных сигналов (критик + лояльность + стресс)
        signals = [r["критик_enps"], r["низкая_лояльность"], r["высокий_стресс"]]
        r["составной_риск_ухода"] = sum(1 for s in signals if s) >= 2

        rows.append(r)

    return pd.DataFrame(rows)


def _get_demo(row, df: pd.DataFrame, key: str) -> str | None:
    col = _find_col(df, Q_PATTERNS.get(key, key))
    if col and pd.notna(row.get(col)):
        return str(row.get(col)).strip()
    return None


def _block_avg(row, df: pd.DataFrame, score_map: dict, pattern_key: str) -> float | None:
    """Считает средний балл блока по нескольким вопросам."""
    patterns = Q_PATTERNS.get(pattern_key)
    if patterns is None:
        return None
    # Список паттернов → несколько вопросов → усредняем
    if isinstance(patterns, list):
        scores = []
        for pat in patterns:
            col = _find_col(df, pat)
            if col:
                s = _to_score(row.get(col), score_map)
                if s is not None:
                    scores.append(s)
        return _avg(*scores)
    # Один паттерн (строка)
    col = _find_col(df, patterns)
    return _to_score(row.get(col), score_map) if col else None


# ── Основная функция ──────────────────────────────────────────────────────────

def parse_enps(dim: pd.DataFrame = None) -> None:
    settings = load_settings()
    enps_root = Path(settings["sources"]["enps"]["folder"])
    output    = settings["sources"]["enps"]["output"]

    if not enps_root.exists():
        print("  ENPS: папка не найдена, пропускаем")
        return

    # Загружаем методологию
    methodology = _load_methodology()
    score_map   = methodology.get("score_map", {})
    if not score_map:
        print("  ENPS: методология не загружена, расчёт без маппинга")

    all_frames = []

    # ── Основные файлы (новая методология 2026) ───────────────────────────────
    main_files = sorted(enps_root.glob("*.xlsx"))
    print(f"  ENPS: основных файлов: {len(main_files)}")
    for f in main_files:
        df = _parse_file(str(f), score_map, is_archive=False)
        if not df.empty:
            all_frames.append(df)
            print(f"    {f.name}: {len(df)} строк | сегмент={df['сегмент'].iloc[0]}")

    # ── Архивные файлы ────────────────────────────────────────────────────────
    archive_folder = enps_root / "Архив"
    if archive_folder.exists():
        archive_files = sorted(archive_folder.glob("*.xlsx"))
        print(f"  ENPS: архивных файлов: {len(archive_files)}")
        for f in archive_files:
            df = _parse_file(str(f), score_map, is_archive=True)
            if not df.empty:
                all_frames.append(df)
                periods = df["период"].nunique()
                print(f"    {f.name}: {len(df)} строк | {periods} периодов")

    if not all_frames:
        print("  ENPS: нет данных для обработки")
        return

    fact = pd.concat(all_frames, ignore_index=True)

    # Типизация
    for col in ["готов_рекомендовать","лояльность","риск_ухода_балл","стресс",
                "вовлечённость","удовлетворённость",
                "блок_руководитель","блок_команда","блок_обучение","блок_карьера"]:
        if col in fact.columns:
            fact[col] = pd.to_numeric(fact[col], errors="coerce").round(2)

    # Булевы → 1/0 для удобного расчёта среднего в Power BI
    for col in ["думает_об_уходе","критик_enps",
                "низкая_лояльность","высокий_стресс","составной_риск_ухода"]:
        if col in fact.columns:
            fact[col] = fact[col].map({True: 1, False: 0}).astype("Int8")

    # Финальное переименование в русские названия
    fact = fact.rename(columns={
        "сегмент":        "Группа опроса",     # SV / SV+1 / SV-1
        "период":         "Период",
        "год":            "Год",
        "квартал":        "Квартал",
        "регион":         "Регион",
        "должность":      "Должность",
        "стаж_группа":    "Стаж",
        "готов_рекомендовать": "Балл eNPS",
        "лояльность":     "Лояльность",
        "риск_ухода_балл":"Балл риска ухода",
        "стресс":         "Стресс",
        "вовлечённость":  "Вовлечённость",
        "удовлетворённость":"Удовлетворённость",
        "блок_руководство": "Блок: Руководство",
        "блок_нагрузка":    "Блок: Нагрузка",
        "блок_обучение":    "Блок: Обучение",
        "блок_инструменты": "Блок: Инструменты",
        "блок_рост":        "Блок: Рост",
        "блок_оплата":      "Блок: Оплата",
        "блок_команда":     "Блок: Команда",
        "категория_enps": "Категория eNPS",
        "риск_ухода_уровень":"Уровень риска ухода",
        "думает_об_уходе":"Думает об уходе",
        "критик_enps":    "Критик eNPS",
        "низкая_лояльность":"Низкая лояльность",
        "высокий_стресс": "Высокий стресс",
        "составной_риск_ухода":"Составной риск ухода",
    })

    save_parquet(fact, output)

    # Итог
    total = len(fact)
    periods = fact["Период"].nunique()
    high_risk = fact["Думает об уходе"].eq(True).sum()
    composite = fact["Составной риск ухода"].eq(True).sum()
    promoters = (fact["Категория eNPS"] == "Промоутер").sum()
    detractors = (fact["Категория eNPS"] == "Критик").sum()
    n_enps = fact["Категория eNPS"].notna().sum()

    print(f"\n  ENPS итого: {total} ответов | {periods} периодов")
    print(f"  Риск ухода (прямой): {high_risk} ({high_risk/total*100:.1f}%)")
    print(f"  Composite risk:      {composite} ({composite/total*100:.1f}%)")
    if n_enps:
        enps = round((promoters - detractors) / n_enps * 100, 1)
        print(f"  eNPS: {enps} (промоутеры {promoters}, критики {detractors})")
    print(f"  Колонок: {len(fact.columns)}")
