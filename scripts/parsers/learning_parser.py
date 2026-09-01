import pandas as pd
from pathlib import Path
from scripts.corporate_university import read_sql
from scripts.utils import (
    get_active_users_scope,
    load_settings,
    normalize_dim,
    normalize_employee_id,
    save_parquet,
)
from scripts.parsers.oed_parser import _build_lookups, _match_row_with_method


# ── Константы ────────────────────────────────────────────────────────────────

_norm_id = normalize_employee_id

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

def _norm_bool(val) -> bool | None:
    if pd.isna(val):
        return None
    return str(val).strip().lower() in ("да", "yes", "true", "1")


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


def _epoch_to_moscow(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    result = pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
    return result.dt.tz_convert("Europe/Moscow").dt.tz_localize(None)


def _load_learning_database(
    settings: dict,
    roi: pd.DataFrame,
    *,
    active_only: bool = True,
) -> pd.DataFrame:
    if roi.empty or "course_id" not in roi.columns:
        raise ValueError("Нельзя загрузить обучение из БД без согласованного каталога курсов")

    course_ids = sorted(
        {
            int(str(value).strip())
            for value in roi["course_id"].dropna()
            if str(value).strip().isdigit()
        }
    )
    if not course_ids:
        raise ValueError("В согласованном каталоге нет корректных номеров курсов")
    course_list = ",".join(str(value) for value in course_ids)
    active_filter = "AND u.active = 1" if active_only else ""

    query = f"""
        WITH scoped_users AS (
            SELECT
                u.id AS user_id,
                COALESCE(NULLIF(TRIM(u.external_idx), ''), NULLIF(TRIM(e.extId), '')) AS employee_id
            FROM users u
            LEFT JOIN employees e ON e.person_id = u.person_id
            LEFT JOIN org_structure_level o ON o.id = u.org_structure_level_id
            WHERE (UPPER(TRIM(u.project_name)) = 'H&N' OR UPPER(TRIM(o.name)) = 'H&N')
              {active_filter}
        ),
        detail_totals AS (
            SELECT curs_id, COUNT(*) AS total_elements
            FROM curs_details
            WHERE curs_id IN ({course_list})
            GROUP BY curs_id
        ),
        progress AS (
            SELECT
                sci.user_id,
                sci.curs_id,
                COUNT(DISTINCT CASE WHEN sci.status = 'finish' THEN sci.curs_detail_id END) AS finished_elements,
                MIN(NULLIF(sci.last_start, 0)) AS detail_start_epoch
            FROM students_curs_info sci
            JOIN scoped_users su ON su.user_id = sci.user_id
            WHERE sci.curs_id IN ({course_list})
            GROUP BY sci.user_id, sci.curs_id
        ),
        ranked_test_details AS (
            SELECT
                curs_id,
                curs_detail_id,
                ROW_NUMBER() OVER (
                    PARTITION BY curs_id
                    ORDER BY sort_id DESC, curs_detail_id DESC
                ) AS row_number
            FROM curs_details
            WHERE curs_id IN ({course_list}) AND type = 'test'
        ),
        final_test_details AS (
            SELECT curs_id, curs_detail_id
            FROM ranked_test_details
            WHERE row_number = 1
        ),
        test_scores AS (
            SELECT
                sci.user_id,
                sci.curs_id,
                MAX(sci.test_points_procent) AS final_test_score
            FROM students_curs_info sci
            JOIN scoped_users su ON su.user_id = sci.user_id
            JOIN final_test_details test_detail
              ON test_detail.curs_id = sci.curs_id
             AND test_detail.curs_detail_id = sci.curs_detail_id
            GROUP BY sci.user_id, sci.curs_id
        )
        SELECT
            su.employee_id AS employee_id_raw,
            sc.curs_id AS course_id,
            c.curs_name AS course_name_src,
            sc.status AS assignment_status,
            COALESCE(NULLIF(sc.first_recording_time, 0), progress.detail_start_epoch) AS start_epoch,
            NULLIF(sc.time_end, 0) AS completion_epoch,
            COALESCE(progress.finished_elements, 0) AS finished_elements,
            detail_totals.total_elements,
            test_scores.final_test_score,
            c.hours,
            c.minutes
        FROM students_curses sc
        JOIN scoped_users su ON su.user_id = sc.user_id
        JOIN courses c ON c.curs_id = sc.curs_id
        LEFT JOIN detail_totals ON detail_totals.curs_id = sc.curs_id
        LEFT JOIN progress
          ON progress.user_id = sc.user_id
         AND progress.curs_id = sc.curs_id
        LEFT JOIN test_scores
          ON test_scores.user_id = sc.user_id
         AND test_scores.curs_id = sc.curs_id
        WHERE sc.curs_id IN ({course_list})
    """
    raw = read_sql(settings, query)
    if raw.empty:
        raise ValueError("Корпоративный университет не вернул назначений согласованных курсов")

    fact = pd.DataFrame(index=raw.index)
    fact["employee_id_raw"] = raw["employee_id_raw"].map(_norm_id)
    fact["course_id"] = raw["course_id"].astype("Int64").astype("string")
    fact["course_name_src"] = raw["course_name_src"].astype("string").str.strip()

    total = pd.to_numeric(raw["total_elements"], errors="coerce")
    finished = pd.to_numeric(raw["finished_elements"], errors="coerce").fillna(0)
    fact["completion_pct"] = (finished / total.where(total.gt(0))).clip(upper=1).round(4)

    status = raw["assignment_status"].astype("string").str.strip().str.lower()
    started = pd.to_numeric(raw["start_epoch"], errors="coerce").gt(0) | finished.gt(0)
    fact["status"] = "available"
    fact.loc[started, "status"] = "in_progress"
    fact.loc[status.eq("failed"), "status"] = "failed"
    fact.loc[status.eq("archive"), "status"] = "passed"

    fact["test_score"] = (
        pd.to_numeric(raw["final_test_score"], errors="coerce") / 100
    ).round(4)
    fact["start_date"] = _epoch_to_moscow(raw["start_epoch"])
    fact["completion_date"] = _epoch_to_moscow(raw["completion_epoch"])
    fact["hours"] = (
        pd.to_numeric(raw["hours"], errors="coerce").fillna(0)
        + pd.to_numeric(raw["minutes"], errors="coerce").fillna(0) / 60
    )

    fact = fact[fact["employee_id_raw"].ne("")].copy()
    print(
        f"  Обучение DB: {len(fact)} назначений, "
        f"{fact['employee_id_raw'].nunique()} сотрудников, "
        f"{fact['course_id'].nunique()} курсов из согласованного каталога"
    )
    return fact


# ── Основная функция ──────────────────────────────────────────────────────────

def parse_learning(dim: pd.DataFrame = None) -> None:
    settings = load_settings()
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

    source = str(settings["sources"]["learning"].get("source", "")).strip().lower()
    if source != "corporate_university":
        raise ValueError(f"Неизвестный источник обучения: {source}")
    fact = _load_learning_database(settings, roi)

    # Дедупликация: employee_id + course_id → берём лучший результат
    before = len(fact)
    fact = (fact
            .sort_values(["completion_pct", "completion_date"],
                         ascending=[False, False], na_position="last")
            .drop_duplicates(subset=["employee_id_raw", "course_id"], keep="first")
            .reset_index(drop=True))
    print(f"  Обучение: дедупликация {before} -> {len(fact)} строк")

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

    # Когда курс стартовал относительно официальной даты найма.
    # Отрицательные значения сохраняем как факт источника; в витринах они фильтруются по бизнес-правилам.
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
        print(f"  Обучение: фильтр по активным USERS {before} -> {len(fact)} строк")

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
