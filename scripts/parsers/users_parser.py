import pandas as pd
from scripts.corporate_university import read_sql
from scripts.utils import (
    get_as_of_date,
    load_settings,
    normalize_employee_id,
    save_parquet,
)

_normalize_id = normalize_employee_id


USER_OUTPUT_RENAME = {
    "employee_id": "ID сотрудника",
    "last_name": "Фамилия",
    "first_name": "Имя",
    "middle_name": "Отчество",
    "full_name": "ФИО",
    "position": "Должность",
    "org_unit": "Подразделение",
    "city": "Город",
    "region": "Регион",
    "attribute": "Атрибут",
    "project": "Проект",
    "email": "Электронная почта",
    "groups": "Группы",
    "authorization": "Авторизация",
    "manager_id": "ID руководителя",
    "hire_date": "Дата приёма",
    "tenure_days": "Стаж (дней)",
    "tenure_months": "Стаж (месяцев)",
    "is_active": "Активен",
    "manager_full_name": "ФИО руководителя",
}


def _title(series: pd.Series) -> pd.Series:
    return series.str.strip().str.title()


def _build_full_name(row) -> str:
    parts = []
    for column in ["last_name", "first_name", "middle_name"]:
        value = row.get(column, "")
        parts.append("" if pd.isna(value) else str(value).strip())
    # Пропускаем пустые значения и буквальные "nan"
    return " ".join(p for p in parts if p and p.lower() != "nan")


def _save_users(frame: pd.DataFrame, output: str) -> None:
    save_parquet(frame.rename(columns=USER_OUTPUT_RENAME), output)


def _load_database_users(settings: dict) -> pd.DataFrame:
    query = """
        SELECT
            u.id AS user_id,
            u.person_id,
            u.parent_id_ AS manager_person_id,
            COALESCE(NULLIF(TRIM(u.external_idx), ''), NULLIF(TRIM(e.extId), '')) AS employee_id,
            u.last_name,
            u.first_name,
            u.midle_name AS middle_name,
            u.position,
            u.subdivision,
            u.city,
            u.city_name,
            u.region_name,
            u.attribute,
            u.project_name,
            u.email,
            u.active,
            CAST(u.date_take_on_work AS CHAR) AS hire_date,
            u.last_updated_wp_at,
            o.name AS org_name
        FROM users u
        LEFT JOIN employees e ON e.person_id = u.person_id
        LEFT JOIN org_structure_level o ON o.id = u.org_structure_level_id
    """
    raw = read_sql(settings, query)
    if raw.empty:
        raise ValueError("Корпоративный университет вернул пустую таблицу пользователей")

    raw["employee_id"] = raw["employee_id"].map(_normalize_id)
    raw["person_id"] = pd.to_numeric(raw["person_id"], errors="coerce").astype("Int64")
    raw["manager_person_id"] = pd.to_numeric(
        raw["manager_person_id"], errors="coerce"
    ).astype("Int64")
    raw["active"] = pd.to_numeric(raw["active"], errors="coerce").fillna(0).astype(int)
    raw["last_updated_wp_at"] = pd.to_datetime(
        raw["last_updated_wp_at"], errors="coerce"
    )

    project = raw["project_name"].astype("string").str.strip().str.upper()
    org_name = raw["org_name"].astype("string").str.strip().str.upper()
    raw["_hn_scope"] = project.eq("H&N") | org_name.eq("H&N")

    selected_people = set(raw.loc[raw["_hn_scope"], "person_id"].dropna().astype(int))
    for _ in range(2):
        parent_people = set(
            raw.loc[raw["person_id"].isin(selected_people), "manager_person_id"]
            .dropna()
            .astype(int)
        )
        selected_people.update(parent_people)

    work = raw[raw["person_id"].isin(selected_people)].copy()
    work = work[work["employee_id"].ne("")].copy()
    work = work.sort_values(
        ["active", "last_updated_wp_at", "user_id"],
        ascending=[False, False, False],
        na_position="last",
    ).drop_duplicates(subset=["employee_id"], keep="first")

    person_to_employee = (
        raw[raw["employee_id"].ne("")]
        .sort_values(
            ["active", "last_updated_wp_at", "user_id"],
            ascending=[False, False, False],
            na_position="last",
        )
        .drop_duplicates(subset=["person_id"], keep="first")
        .set_index("person_id")["employee_id"]
        .to_dict()
    )

    work["last_name"] = _title(work["last_name"].astype("string"))
    work["first_name"] = _title(work["first_name"].astype("string"))
    work["middle_name"] = _title(work["middle_name"].astype("string"))
    work["full_name"] = work.apply(_build_full_name, axis=1)
    work["position"] = _title(work["position"].astype("string"))
    work["org_unit"] = (
        work["org_name"].astype("string").str.strip()
        .replace("", pd.NA)
        .fillna(work["subdivision"].astype("string").str.strip())
    )
    work["city"] = (
        work["city_name"].astype("string").str.strip()
        .replace("", pd.NA)
        .fillna(work["city"].astype("string").str.strip())
        .str.title()
    )
    work["region"] = work["region_name"].astype("string").str.strip().str.title()
    work["attribute"] = work["attribute"].astype("string").str.strip()
    work["project"] = work["project_name"].astype("string").str.strip()
    work.loc[work["_hn_scope"], "project"] = "H&N"
    work["groups"] = work["project"]
    work["authorization"] = work["active"].map({1: "Да", 0: "Нет"})
    work["manager_id"] = work["manager_person_id"].map(person_to_employee)
    work["manager_id"] = work["manager_id"].fillna("Вакансия")
    work["hire_date"] = pd.to_datetime(work["hire_date"], errors="coerce")
    work["tenure_days"] = (get_as_of_date() - work["hire_date"]).dt.days.clip(lower=0)
    work["tenure_months"] = (work["tenure_days"] / 30.44).round(1)
    work["is_active"] = work["active"].eq(1)

    id_to_name = work.set_index("employee_id")["full_name"].to_dict()
    work["manager_full_name"] = work["manager_id"].map(id_to_name).fillna(work["manager_id"])

    output_columns = list(USER_OUTPUT_RENAME)
    result = work[output_columns].reset_index(drop=True)
    active_hn = result[
        result["is_active"].eq(True) & result["project"].astype(str).eq("H&N")
    ]
    print(
        f"  USERS DB: {len(result)} строк, "
        f"активных H&N — {len(active_hn)}, "
        f"руководитель определён — {result['manager_id'].ne('Вакансия').sum()}"
    )
    return result


def parse_users() -> pd.DataFrame:
    settings = load_settings()
    source = str(settings["sources"]["users"].get("source", "")).strip().lower()
    if source != "corporate_university":
        raise ValueError(f"Неизвестный источник USERS: {source}")
    output = settings["sources"]["users"]["output"]
    df = _load_database_users(settings)
    _save_users(df, output)
    print(f"  USERS: {len(df)} записей сохранено в dim_employees")
    return df
