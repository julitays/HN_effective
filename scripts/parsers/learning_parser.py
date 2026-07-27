import re
import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet, normalize_dim, get_active_users_scope
from scripts.parsers.oed_parser import _build_lookups, _match_row_with_method


# ── Константы ────────────────────────────────────────────────────────────────

STATUS_MAP = {
    "завершено успешно":    "passed",
    "завершено неуспешно":  "failed",
    "в процессе обучения":  "in_progress",
    "доступно":             "available",
}

ROI_COLS = {
    "Номер курса в КУ":                                         "course_id",
    "Название курса в КУ":                                      "course_name_roi",
    "Цель курса":                                               "course_goal",
    "Какую компетенцию развивает":                              "competency",
    "Тип обучения\nТренинг / Курс":                            "training_type",
    "Обязательный курс":                                        "is_mandatory_raw",
    "Учавствует в адаптации":                                   "is_adaptation_raw",
    "Есть тестирование или считать по прогрессу (посещению)":  "count_method",
    "Балл тестирования, который считается успешной сдачей теста": "passing_threshold",
}


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _parse_test_score(val) -> float | None:
    """'Название курса: 93,33%' → 0.9333. Также обрабатывает просто '93%'."""
    if pd.isna(val) or str(val).strip() in ("-", "nan", ""):
        return None
    s = str(val)
    # Ищем последнее число в формате 'NN,NN%' или 'NN%'
    m = re.search(r"(\d+)[,.](\d+)%\s*$", s)
    if m:
        return round(float(f"{m.group(1)}.{m.group(2)}") / 100, 4)
    m = re.search(r"(\d+)%\s*$", s)
    if m:
        return round(int(m.group(1)) / 100, 4)
    return None


def _norm_bool(val) -> bool | None:
    if pd.isna(val):
        return None
    return str(val).strip().lower() in ("да", "yes", "true", "1")


def _norm_id(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip().upper()


def _load_roi_catalog(config_folder: Path) -> pd.DataFrame:
    """Читает ROI-каталог курсов из config/."""
    roi_files = list(config_folder.glob("*ROI*.xlsx")) + list(config_folder.glob("*roi*.xlsx"))
    if not roi_files:
        print("  Обучение: ROI-каталог не найден в config/, пропускаем")
        return pd.DataFrame()

    roi = pd.read_excel(roi_files[0], dtype=str)
    roi = roi.rename(columns={k: v for k, v in ROI_COLS.items() if k in roi.columns})

    # Нормализуем course_id
    if "course_id" in roi.columns:
        roi["course_id"] = roi["course_id"].str.strip().str.upper()

    # Булевы поля
    if "is_mandatory_raw" in roi.columns:
        roi["is_mandatory"] = roi["is_mandatory_raw"].apply(_norm_bool)
    if "is_adaptation_raw" in roi.columns:
        roi["is_adaptation"] = roi["is_adaptation_raw"].apply(_norm_bool)

    # Порог прохождения (0.9 → float)
    if "passing_threshold" in roi.columns:
        roi["passing_threshold"] = pd.to_numeric(roi["passing_threshold"], errors="coerce")

    keep = ["course_id", "course_name_roi", "course_goal", "competency",
            "is_mandatory", "is_adaptation"]
    roi = roi[[c for c in keep if c in roi.columns]]
    print(f"  Обучение: ROI-каталог загружен ({len(roi)} курсов)")
    return roi


def _load_course_file(xlsx_path: Path, course_id: str) -> pd.DataFrame | None:
    """Загружает один Excel-отчёт по обучению."""
    try:
        raw = pd.read_excel(xlsx_path, dtype=str)
    except Exception as e:
        print(f"    ОШИБКА {xlsx_path.name}: {e}")
        return None

    if "extId" not in raw.columns:
        return None

    n = len(raw)
    result = pd.DataFrame()
    result["employee_id_raw"] = raw["extId"].apply(_norm_id)
    result["course_id"]       = course_id

    # Название (из файла как fallback)
    result["course_name_src"] = raw.get("Название обучения", pd.Series([""] * n)).str.strip()

    # Прогресс 0-100 → 0.0-1.0
    prog = pd.to_numeric(raw.get("Прогресс обучения, %", pd.Series([None]*n)), errors="coerce")
    result["completion_pct"]  = (prog / 100).round(4)

    # Статус → нормализованный
    status_raw = raw.get("Статус обучения", pd.Series([""] * n)).str.strip().str.lower()
    result["status"] = status_raw.map(STATUS_MAP).fillna("unknown")

    # Балл теста — парсим из текстового поля
    result["test_score"] = raw.get("Результат тестирования", pd.Series([None]*n)).apply(_parse_test_score)

    # Даты — ищем по подстроке чтобы не зависеть от кодировки
    def _find_col(df, keyword):
        found = [c for c in df.columns if keyword.lower() in str(c).lower()]
        return df[found[0]] if found else pd.Series([None]*len(df))

    result["start_date"]      = pd.to_datetime(_find_col(raw, "начало обучения"), errors="coerce")
    result["completion_date"] = pd.to_datetime(_find_col(raw, "завершение обучения"), errors="coerce")

    # Часы
    result["hours"] = pd.to_numeric(raw.get("Кол-во часов обучения", pd.Series([None]*n)), errors="coerce").astype("Int16")

    return result


# ── Основная функция ──────────────────────────────────────────────────────────

def parse_learning(dim: pd.DataFrame = None) -> None:
    settings = load_settings()
    learn_root   = Path(settings["sources"]["learning"]["folder"])
    output       = settings["sources"]["learning"]["output"]
    config_folder = Path("config")

    # Матчинг dim_employees
    if dim is None or dim.empty:
        dim_path = Path(settings["sources"]["users"]["output"])
        if dim_path.exists():
            dim = normalize_dim(pd.read_parquet(dim_path))
            print(f"  Обучение: загружен dim_employees ({len(dim)} записей)")
        else:
            dim = pd.DataFrame()

    id_lookup, name_lookup, partial_lookup = (
        _build_lookups(dim) if not dim.empty else ({}, {}, {})
    )

    # ROI-каталог
    roi = _load_roi_catalog(config_folder)

    # Читаем все курсы из пронумерованных папок
    all_frames = []
    course_folders = [f for f in learn_root.iterdir() if f.is_dir() and f.name.isdigit()]

    for folder in sorted(course_folders):
        course_id = folder.name.upper()
        xlsx_files = sorted(folder.glob("*.xlsx"))
        if not xlsx_files:
            continue

        course_frames = []
        for xlsx in xlsx_files:
            df = _load_course_file(xlsx, course_id)
            if df is not None and not df.empty:
                course_frames.append(df)

        if not course_frames:
            continue

        course_df = pd.concat(course_frames, ignore_index=True)
        all_frames.append(course_df)
        print(f"    Курс {course_id}: {len(course_df)} строк из {len(xlsx_files)} файлов")

    if not all_frames:
        print("  Обучение: нет данных для обработки")
        return

    fact = pd.concat(all_frames, ignore_index=True)

    # Дедупликация: employee_id + course_id → берём лучший результат
    before = len(fact)
    fact = (fact
            .sort_values(["completion_pct", "completion_date"],
                         ascending=[False, False], na_position="last")
            .drop_duplicates(subset=["employee_id_raw", "course_id"], keep="first")
            .reset_index(drop=True))
    print(f"  Обучение: дедупликация {before} → {len(fact)} строк")

    # Матчинг по employee_id
    eids, methods = [], []
    for eid_raw in fact["employee_id_raw"]:
        eid, method = _match_row_with_method(eid_raw, "", id_lookup, name_lookup, partial_lookup)
        eids.append(eid or None)
        methods.append(method)

    fact["employee_id"]  = eids
    fact["match_method"] = methods

    # Присоединяем ROI-каталог
    if not roi.empty:
        fact = fact.merge(roi, on="course_id", how="left")

    # is_passed: логика зависит от count_method из ROI-файла
    by_status = fact["status"] == "passed"

    if "passing_threshold" in fact.columns and "count_method" in fact.columns:
        thr    = pd.to_numeric(fact["passing_threshold"], errors="coerce")
        method = fact["count_method"].str.strip().str.lower().fillna("")

        is_test     = method.str.contains("тест")
        is_progress = method.str.contains("прогресс")
        is_visit    = method.str.contains("посещ|был/не был")

        # Тест: балл первой попытки >= порога
        by_test = fact["test_score"].ge(thr, fill_value=2) & is_test

        # Прогресс: completion_pct = 100% (порог 1.0)
        by_progress = fact["completion_pct"].ge(thr, fill_value=2) & is_progress

        # Посещение: просто статус = passed
        by_visit = by_status & is_visit

        # Fallback: статус для тех у кого не определён метод
        is_passed = by_test | by_progress | by_visit | (by_status & ~(is_test | is_progress | is_visit))
    else:
        is_passed = by_status

    fact["is_passed"] = is_passed.astype("boolean")

    # ── ROI-аналитика ─────────────────────────────────────────────────────────

    # Маппинг компетенции → связанная бизнес-метрика (для ROI-анализа в PBI)
    COMPETENCY_METRIC = {
        "знание стандартов работы с категорией":          "PICoS / ТОП-16",
        "управление эффективностью и работа с отчетностью":"PICoS / Отчётность SFA",
        "честность в отчетности и качество исполнения":   "Фальсификации ОКК",
        "техническая грамотность":                        "Работа с ТСД",
        "стандартизация рабочего процесса":               "ОЭД Стандарты",
        "соблюдение норм охраны труда и техники безопасности":"Безопасность",
        "ориентация на развитие":                         "Адаптация",
        "управление качеством и товарными потерями":      "Качество продукта",
    }
    if "competency" in fact.columns:
        fact["linked_metric"] = (
            fact["competency"].str.lower().str.strip().map(COMPETENCY_METRIC)
        )

    # Скорость прохождения (дней от начала до завершения)
    if "start_date" in fact.columns and "completion_date" in fact.columns:
        delta = (fact["completion_date"] - fact["start_date"]).dt.days
        fact["days_to_complete"] = delta.clip(lower=0).astype("Int16")

    # Когда прошёл курс относительно официальной даты найма
    # Отрицательные значения = доступ выдан до официального выхода (нормально)
    if not dim.empty and "employee_id" in fact.columns and "hire_date" in dim.columns:
        hire_map   = dim.set_index("employee_id")["hire_date"].to_dict()
        hire_dates = fact["employee_id"].map(hire_map)
        if "start_date" in fact.columns:
            days_since_hire = (fact["start_date"] - hire_dates).dt.days
            fact["дней_после_найма"] = pd.array(
                [None if pd.isna(v) else int(v) for v in days_since_hire], dtype="Int32"
            )
            # Ученический договор: обучение начато до официальной даты найма
            fact["ученический_договор"] = (days_since_hire < 0).astype("boolean")
            # Адаптация: первые 90 дней после официальной даты найма
            fact["в_период_адаптации"] = (
                (days_since_hire >= 0) & (days_since_hire <= 90)
            ).astype("boolean")

    # Убираем технические и дублирующие колонки
    fact = fact.drop(columns=["course_name_src", "match_method",
                               "employee_id_raw"], errors="ignore")

    # Итоговый порядок и переименование
    fact = fact.rename(columns={
        "employee_id":        "ID сотрудника",
        "course_id":          "Номер курса",
        "course_name_roi":    "Название курса",
        "course_goal":        "Цель курса",
        "competency":         "Развиваемая компетенция",
        "linked_metric":      "Связанная метрика",
        "is_mandatory":       "Обязательный",
        "is_adaptation":      "В программе адаптации",
        "is_passed":          "Пройдено",
        "completion_pct":     "Прогресс",
        "test_score":         "Балл теста",
        "start_date":         "Дата начала",
        "completion_date":    "Дата завершения",
        "days_to_complete":    "Дней до завершения",
        "дней_после_найма":    "Дней после найма",
        "ученический_договор": "Ученический договор",
        "в_период_адаптации":  "В период адаптации",
    })

    col_order = [
        "ID сотрудника", "Номер курса", "Название курса", "Цель курса",
        "Развиваемая компетенция", "Связанная метрика",
        "Обязательный", "В программе адаптации",
        "Пройдено", "Прогресс", "Балл теста",
        "Дата начала", "Дата завершения",
        "Дней до завершения", "Дней после найма",
        "Ученический договор", "В период адаптации",
    ]
    fact = fact[[c for c in col_order if c in fact.columns]]

    for col in ["Прогресс", "Балл теста"]:
        if col in fact.columns:
            fact[col] = pd.to_numeric(fact[col], errors="coerce")
    for col in ["Дней до завершения", "Дней после найма"]:
        if col in fact.columns:
            fact[col] = pd.to_numeric(fact[col], errors="coerce").astype("Int64")
    for col in ["Пройдено", "Обязательный", "В программе адаптации", "Ученический договор", "В период адаптации"]:
        if col in fact.columns:
            fact[col] = fact[col].astype("boolean")

    if dim is not None and not dim.empty and "ID сотрудника" in fact.columns:
        scope = get_active_users_scope(dim)
        before = len(fact)
        fact = fact[fact["ID сотрудника"].astype(str).isin(scope["all_ids"])].copy()
        print(f"  Обучение: фильтр по активным USERS {before} → {len(fact)} строк")

    save_parquet(fact, output)

    total   = len(fact)
    id_col  = "ID сотрудника" if "ID сотрудника" in fact.columns else "employee_id"
    matched = fact[id_col].notna().sum() if id_col in fact.columns else 0
    passed  = fact["Пройдено"].eq(True).sum() if "Пройдено" in fact.columns else 0
    cid_col = "Номер курса" if "Номер курса" in fact.columns else "course_id"
    print(f"\n  Обучение итого: {total} строк")
    print(f"  Матчинг: {matched}/{total} ({matched/total*100:.1f}%)")
    print(f"  Сдали курс (Пройдено=True): {passed} ({passed/total*100:.1f}%)")
    print(f"  Уникальных курсов: {fact[cid_col].nunique() if cid_col in fact.columns else '?'}")
