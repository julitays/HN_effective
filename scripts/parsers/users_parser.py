import pandas as pd
from datetime import date
from pathlib import Path
from scripts.utils import load_settings, save_parquet

TODAY = pd.Timestamp(date.today())


def _normalize_id(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip().upper()


def _title(series: pd.Series) -> pd.Series:
    return series.str.strip().str.title()


def _build_full_name(row) -> str:
    parts = [
        str(row.get("last_name",   "") or "").strip(),
        str(row.get("first_name",  "") or "").strip(),
        str(row.get("middle_name", "") or "").strip(),
    ]
    # Пропускаем пустые значения и буквальные "nan"
    return " ".join(p for p in parts if p and p.lower() != "nan")


def _parse_csv(path: Path) -> pd.DataFrame:
    """Читает USERS-CSV и возвращает нормализованный DataFrame."""
    try:
        raw = pd.read_csv(path, sep=";", encoding="utf-8", dtype=str)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"не удалось прочитать {path.name} как UTF-8 (проверьте кодировку файла): {exc}"
        ) from exc

    required_cols = [
        "Внешний идентификатор", "Фамилия", "Имя", "Отчество", "Должность",
        "Элемент оргструктуры", "Город", "Регион", "Проект", "Email",
        "Группы", "Руководитель сотрудника", "дата приема",
    ]
    missing = [c for c in required_cols if c not in raw.columns]
    if missing:
        raise ValueError(f"в {path.name} отсутствуют ожидаемые колонки: {missing}")

    df = pd.DataFrame()
    df["employee_id"]  = raw["Внешний идентификатор"].apply(_normalize_id)
    df["last_name"]    = _title(raw["Фамилия"])
    df["first_name"]   = _title(raw["Имя"])
    df["middle_name"]  = _title(raw["Отчество"])
    df["full_name"]    = df.apply(_build_full_name, axis=1)
    df["position"]     = _title(raw["Должность"])
    df["org_unit"]     = raw["Элемент оргструктуры"].str.strip()
    df["city"]         = _title(raw["Город"])
    df["region"]       = _title(raw["Регион"])
    df["project"]      = raw["Проект"].str.strip()
    df["email"]        = raw["Email"].str.strip()
    df["groups"]       = raw["Группы"].str.strip()
    df["authorization"] = raw.get("Авторизация", pd.Series([""] * len(raw))).astype(str).str.strip()
    df["manager_id"]   = raw["Руководитель сотрудника"].apply(_normalize_id)
    df["manager_id"]   = df["manager_id"].replace("", "Вакансия")
    df["hire_date"]    = pd.to_datetime(
        raw["дата приема"], dayfirst=True, errors="coerce"
    )
    df["tenure_days"]   = (TODAY - df["hire_date"]).dt.days.clip(lower=0)
    df["tenure_months"] = (df["tenure_days"] / 30.44).round(1)
    return df


def parse_users() -> pd.DataFrame:
    settings = load_settings()
    folder   = Path(settings["sources"]["users"]["folder"])
    output   = settings["sources"]["users"]["output"]

    # ── Основной файл (H&N полевой персонал) ─────────────────────────────────
    main_files = sorted(folder.glob("*.csv"))
    if not main_files:
        print("  USERS: файлы не найдены, пропускаем")
        return pd.DataFrame()

    src = main_files[-1]
    print(f"  USERS: читаем {src.name}")
    df = _parse_csv(src)
    df["is_active"] = True

    # Дубли и пустые ID
    df = df.drop_duplicates(subset=["employee_id"], keep="first")
    df = df[df["employee_id"] != ""]

    # Демо-аккаунты
    demo_mask = df["full_name"].str.contains("demo", case=False, na=False)
    if demo_mask.sum():
        print(f"  USERS: отфильтровано {demo_mask.sum()} демо-аккаунтов")
    df = df[~demo_mask]

    # ── Подтягиваем недостающих менеджеров из all/ или active/ ──────────────────
    all_folder = next(
        (folder / name for name in ("active", "all") if (folder / name).exists()),
        None
    )
    all_files = sorted(all_folder.glob("*.csv")) if all_folder else []

    if all_files:
        all_raw = _parse_csv(all_files[-1])
        all_raw = all_raw[all_raw["employee_id"] != ""]
        all_raw = all_raw[~all_raw["full_name"].str.contains("demo", case=False, na=False)]

        # Итеративно подтягиваем менеджеров: 2 уровня = ТМ + Менеджер над ТМ
        MAX_LEVELS = 2
        for level in range(1, MAX_LEVELS + 1):
            known_ids   = set(df["employee_id"])
            missing_ids = set(df["manager_id"]) - known_ids - {"Вакансия"}
            if not missing_ids:
                break

            found = all_raw[all_raw["employee_id"].isin(missing_ids)]
            if found.empty:
                break

            depts = found["org_unit"].unique().tolist()
            print(f"  USERS: уровень {level} — найдено {len(found)} менеджеров "
                  f"из all/ (отделы: {depts})")
            df = pd.concat([df, found], ignore_index=True)
            df = df.drop_duplicates(subset=["employee_id"], keep="first")

        # Итоговые не найденные
        known_ids   = set(df["employee_id"])
        still_missing = set(df["manager_id"]) - known_ids - {"Вакансия"}
        if still_missing:
            print(f"  USERS: {len(still_missing)} менеджеров не найдено "
                  f"ни в одном файле: {still_missing}")

    # ── Добавляем заблокированных H&N из active/ (Авторизация=Нет) ──────────────
    # active/ содержит полный список включая заблокированных, которых нет в block/
    if all_files:
        all_raw_blocked = _parse_csv(all_files[-1])
        all_raw_blocked = all_raw_blocked[all_raw_blocked["employee_id"] != ""]
        all_raw_blocked = all_raw_blocked[
            ~all_raw_blocked["full_name"].str.contains("demo", case=False, na=False)
        ]
        # Берём H&N с Авторизация=Нет которых ещё нет в основном текущем срезе.
        # В главном текущем файле Авторизация=Нет может быть у новых активных сотрудников,
        # поэтому текущий файл для нас приоритетнее признака авторизации.
        active_raw = _parse_csv(all_files[-1])  # сырой файл для проверки Авторизации
        # Читаем Авторизацию напрямую из CSV (до _parse_csv нормализации)
        raw_active_csv = pd.read_csv(all_files[-1], sep=";", encoding="utf-8", dtype=str)
        raw_active_csv["_id"] = raw_active_csv["Внешний идентификатор"].str.strip().str.upper()
        raw_active_csv["_авт"] = raw_active_csv.get("Авторизация", pd.Series(["Да"]*len(raw_active_csv))).str.strip().str.lower()
        raw_active_csv["_proj"] = raw_active_csv.get("Проект", pd.Series([""]*len(raw_active_csv))).str.strip().str.upper()
        raw_active_csv["_grp"]  = raw_active_csv.get("Группы", pd.Series([""]*len(raw_active_csv))).fillna("")

        blocked_hn_ids = set(
            raw_active_csv[
                (raw_active_csv["_авт"] == "нет") &
                ((raw_active_csv["_proj"] == "H&N") |
                 raw_active_csv["_grp"].str.contains("H&N", na=False, case=False))
            ]["_id"]
        )
        known_now = set(df["employee_id"])
        new_blocked_from_active = all_raw_blocked[
            all_raw_blocked["employee_id"].isin(blocked_hn_ids - known_now)
        ].copy()
        if not new_blocked_from_active.empty:
            new_blocked_from_active["is_active"] = False
            print(f"  USERS: +{len(new_blocked_from_active)} заблокированных H&N из active/ "
                  f"(Авторизация=Нет, нет в block/)")
            if "is_active" not in df.columns:
                df["is_active"] = True
            df = pd.concat([df, new_blocked_from_active], ignore_index=True)
            df = df.drop_duplicates(subset=["employee_id"], keep="first")

    # ── Добавляем заблокированных (исторических) H&N сотрудников из block/ ───
    block_folder = folder / "block"
    block_files  = sorted(block_folder.glob("*.csv")) if block_folder.exists() else []

    if block_files:
        block_raw = _parse_csv(block_files[-1])
        block_raw = block_raw[block_raw["employee_id"] != ""]
        block_raw = block_raw[~block_raw["full_name"].str.contains("demo", case=False, na=False)]

        # Фильтруем: H&N по проекту ИЛИ те кто был в H&N по группам
        # (сотрудники могли перейти в другой проект, сохранив H&N в истории)
        is_hn_project = block_raw["project"].str.strip().str.upper() == "H&N"
        is_hn_group   = block_raw["groups"].str.contains("H&N", na=False, case=False)
        block_raw = block_raw[is_hn_project | is_hn_group]

        # Помечаем как неактивные
        block_raw = block_raw.copy()
        block_raw["is_active"] = False

        # Берём только тех, кого нет в основном файле
        known_ids   = set(df["employee_id"])
        new_blocked = block_raw[~block_raw["employee_id"].isin(known_ids)]

        if not new_blocked.empty:
            # У активных is_active = True (если колонки ещё нет)
            if "is_active" not in df.columns:
                df["is_active"] = True
            df = pd.concat([df, new_blocked], ignore_index=True)
            df = df.drop_duplicates(subset=["employee_id"], keep="first")
            print(f"  USERS: добавлено {len(new_blocked)} исторических H&N сотрудников "
                  f"из block/ (is_active=False)")

    # Проставляем is_active=True тем, у кого флаг не установлен
    if "is_active" not in df.columns:
        df["is_active"] = True
    df["is_active"] = df["is_active"].fillna(True)

    # Точечная нормализация региона: Москва = MOSCOW-семейство для дальнейшего маппинга.
    df["region"] = df["region"].replace({"Мск": "Москва"})

    # ── Целевой поиск по ID из ОЭД/ОКК-источников ───────────────────────────
    # Ищем ID которые есть в сырых файлах, но не попали в dim (перешли в другой проект)
    known_ids = set(df["employee_id"])
    oed_okk_ids: set[str] = set()
    for pattern in ("data/raw/oed/**/*.xlsx",
                    "data/raw/okk/**/*.xlsx",
                    "data/raw/learning/**/*.xlsx"):
        for p in Path(".").glob(pattern):
            try:
                hdr = pd.ExcelFile(p).parse(pd.ExcelFile(p).sheet_names[0],
                                            nrows=0).columns.tolist()
                # OED/OKK используют "ID", learning использует "extId"
                id_col = "ID" if "ID" in hdr else ("extId" if "extId" in hdr else None)
                if id_col:
                    raw_src = pd.ExcelFile(p).parse(
                        pd.ExcelFile(p).sheet_names[0],
                        usecols=[id_col], nrows=5000, dtype=str)
                    oed_okk_ids |= set(raw_src[id_col].dropna().str.strip().str.upper())
            except Exception:
                pass

    missing_from_src = oed_okk_ids - known_ids
    # Ищем в active/ и block/ по конкретным ID (без фильтра проекта)
    for src_name, src_files in [("active/", all_files),
                                 ("block/", sorted(block_folder.glob("*.csv")) if block_folder.exists() else [])]:
        if not src_files or not missing_from_src:
            break
        src_raw  = _parse_csv(src_files[-1])
        targeted = src_raw[src_raw["employee_id"].isin(missing_from_src)]
        targeted = targeted[~targeted["full_name"].str.contains("demo", case=False, na=False)]
        if not targeted.empty:
            targeted = targeted.copy()
            targeted["is_active"] = False
            new_t = targeted[~targeted["employee_id"].isin(set(df["employee_id"]))]
            if not new_t.empty:
                print(f"  USERS: +{len(new_t)} из {src_name} (другой проект, is_active=False)")
                df = pd.concat([df, new_t], ignore_index=True)
                df = df.drop_duplicates(subset=["employee_id"], keep="first")
                missing_from_src -= set(new_t["employee_id"])

    # ── Имя менеджера — lookup внутри итоговой таблицы ───────────────────────
    id_to_name = df.set_index("employee_id")["full_name"].to_dict()
    df["manager_full_name"] = df["manager_id"].map(id_to_name).fillna(df["manager_id"])

    # Сохраняем с русскими названиями, возвращаем оригинал для downstream-парсеров
    save_parquet(df.rename(columns={
        "employee_id":      "ID сотрудника",
        "last_name":        "Фамилия",
        "first_name":       "Имя",
        "middle_name":      "Отчество",
        "full_name":        "ФИО",
        "position":         "Должность",
        "org_unit":         "Подразделение",
        "city":             "Город",
        "region":           "Регион",
        "project":          "Проект",
        "email":            "Электронная почта",
        "groups":           "Группы",
        "authorization":    "Авторизация",
        "manager_id":       "ID руководителя",
        "hire_date":        "Дата приёма",
        "tenure_days":      "Стаж (дней)",
        "tenure_months":    "Стаж (месяцев)",
        "is_active":        "Активен",
        "manager_full_name":"ФИО руководителя",
    }), output)
    print(f"  USERS: {len(df)} записей сохранено в dim_employees")
    return df  # оригинальные имена для матчинга в других парсерах
