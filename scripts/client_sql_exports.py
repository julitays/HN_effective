from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.staffing_utils import normalize_name, short_name


ARCHIVE_PATTERN = re.compile(r"^HN_KPI_(\d{6})\.zip$", re.IGNORECASE)
REQUIRED_FILES = {
    "manifest.csv",
    "errors.csv",
    "warnings.csv",
    "visits.csv",
    "agents.csv",
    "stores.csv",
    "picos_by_visit.csv",
    "osa_by_visit.csv",
    "top16_by_visit.csv",
}


def _clean_code(series: pd.Series) -> pd.Series:
    result = series.astype("string").str.strip().str.replace(r"\.0+$", "", regex=True)
    return result.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def _to_numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.replace("\xa0", " ", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _read_csv(archive: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with archive.open(name) as source:
        with io.TextIOWrapper(source, encoding="utf-8-sig", newline="") as text:
            try:
                return pd.read_csv(text, dtype="string")
            except pd.errors.EmptyDataError:
                return pd.DataFrame()


def _unique_lookup(keys: pd.Series, employee_ids: pd.Series) -> dict[str, str]:
    work = pd.DataFrame({"key": keys, "employee_id": employee_ids}).dropna()
    work["key"] = work["key"].astype("string").str.strip()
    work["employee_id"] = work["employee_id"].astype("string").str.strip()
    work = work[work["key"].ne("") & work["employee_id"].ne("")]
    grouped = work.groupby("key")["employee_id"].agg(
        lambda values: tuple(sorted(set(values.astype(str))))
    )
    return {key: ids[0] for key, ids in grouped.items() if len(ids) == 1}


def _clean_agent_name(value) -> str | None:
    if pd.isna(value):
        return None
    text = re.sub(r"\s+\d+$", "", str(value).strip())
    return normalize_name(text)


def _validate_manifest(
    archive_path: Path,
    archive: zipfile.ZipFile,
    frames: dict[str, pd.DataFrame],
) -> None:
    members = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
    missing = sorted(REQUIRED_FILES - members)
    if missing:
        raise ValueError(f"SQL-пакет {archive_path.name}: отсутствуют файлы {missing}")

    manifest = frames["manifest.csv"].copy()
    if not {"file", "rows", "status"}.issubset(manifest.columns):
        raise ValueError(f"SQL-пакет {archive_path.name}: некорректный manifest.csv")
    manifest["rows"] = pd.to_numeric(manifest["rows"], errors="coerce").astype("Int64")
    manifest_rows = manifest.set_index("file")["rows"].to_dict()
    manifest_data_files = REQUIRED_FILES - {
        "manifest.csv",
        "errors.csv",
        "warnings.csv",
    }
    for name in manifest_data_files:
        expected = manifest_rows.get(name)
        if expected is None:
            raise ValueError(f"SQL-пакет {archive_path.name}: {name} отсутствует в manifest.csv")
        if int(expected) != len(frames[name]):
            raise ValueError(
                f"SQL-пакет {archive_path.name}: {name} — manifest={expected}, факт={len(frames[name])}"
            )
    if not frames["errors.csv"].empty:
        raise ValueError(f"SQL-пакет {archive_path.name}: errors.csv содержит ошибки")


def _load_month_archive(
    archive_path: Path,
    year_month: int,
    dim_employees: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, pd.DataFrame]:
    with zipfile.ZipFile(archive_path) as archive:
        members = {Path(name).name: name for name in archive.namelist() if not name.endswith("/")}
        missing = sorted(REQUIRED_FILES - set(members))
        if missing:
            raise ValueError(f"SQL-пакет {archive_path.name}: отсутствуют файлы {missing}")
        frames = {
            name: _read_csv(archive, members[name])
            for name in REQUIRED_FILES
        }
        _validate_manifest(archive_path, archive, frames)

    visits = frames["visits.csv"].copy()
    agents = frames["agents.csv"].copy()
    stores = frames["stores.csv"].copy()
    picos = frames["picos_by_visit.csv"].copy()

    expected_visit_columns = {
        "visit_id",
        "aggregate_visit_id",
        "visit_date",
        "store_id",
        "agent_master_id",
    }
    if not expected_visit_columns.issubset(visits.columns):
        missing = sorted(expected_visit_columns - set(visits.columns))
        raise ValueError(f"SQL-пакет {archive_path.name}: visits.csv без полей {missing}")
    if not {"agent_master_id", "agent_login", "agent_name", "is_active"}.issubset(
        agents.columns
    ):
        raise ValueError(f"SQL-пакет {archive_path.name}: некорректный agents.csv")
    if not {
        "store_id",
        "network_name",
        "store_format",
        "city",
        "business_unit",
        "sales_group",
    }.issubset(stores.columns):
        raise ValueError(f"SQL-пакет {archive_path.name}: некорректный stores.csv")

    visits["visit_id"] = _clean_code(visits["visit_id"])
    visits["aggregate_visit_id"] = _clean_code(visits["aggregate_visit_id"])
    visits["store_id"] = _clean_code(visits["store_id"])
    visits["agent_master_id"] = _clean_code(visits["agent_master_id"])
    visits["visit_date"] = pd.to_datetime(
        visits["visit_date"], errors="coerce", dayfirst=True
    ).dt.normalize()
    if visits[["visit_id", "visit_date", "store_id", "agent_master_id"]].isna().any().any():
        raise ValueError(f"SQL-пакет {archive_path.name}: обязательные поля визита содержат пустые значения")
    if visits["visit_id"].duplicated().any():
        raise ValueError(f"SQL-пакет {archive_path.name}: visit_id не уникален")
    observed_months = set(
        (visits["visit_date"].dt.year * 100 + visits["visit_date"].dt.month)
        .dropna()
        .astype(int)
    )
    if observed_months != {year_month}:
        raise ValueError(
            f"SQL-пакет {archive_path.name}: даты визитов относятся к месяцам {sorted(observed_months)}"
        )

    agents["agent_master_id"] = _clean_code(agents["agent_master_id"])
    stores["store_id"] = _clean_code(stores["store_id"])
    if agents["agent_master_id"].duplicated().any():
        raise ValueError(f"SQL-пакет {archive_path.name}: agent_master_id не уникален")
    if stores["store_id"].duplicated().any():
        raise ValueError(f"SQL-пакет {archive_path.name}: store_id не уникален")

    directory = dim_employees.copy()
    if not {"ID сотрудника", "ФИО"}.issubset(directory.columns):
        raise ValueError("Для SQL-визитов необходимы ID сотрудника и ФИО из USERS")
    directory["ID сотрудника"] = directory["ID сотрудника"].astype("string").str.strip()
    directory["ФИО norm"] = directory["ФИО"].map(normalize_name)
    directory["Короткое ФИО norm"] = directory["ФИО"].map(short_name)
    full_lookup = _unique_lookup(directory["ФИО norm"], directory["ID сотрудника"])
    short_lookup = _unique_lookup(
        directory["Короткое ФИО norm"], directory["ID сотрудника"]
    )
    canonical_name = (
        directory.dropna(subset=["ID сотрудника"])
        .drop_duplicates("ID сотрудника", keep="last")
        .set_index("ID сотрудника")["ФИО"]
        .astype("string")
        .to_dict()
    )

    agents["ФИО агента norm"] = agents["agent_name"].map(_clean_agent_name)
    agents["ID сотрудника exact"] = agents["ФИО агента norm"].map(full_lookup)
    agents["ID сотрудника short"] = (
        agents["ФИО агента norm"].map(short_name).map(short_lookup)
    )
    agents["ID сотрудника"] = agents["ID сотрудника exact"].combine_first(
        agents["ID сотрудника short"]
    )
    agents["Источник ID"] = pd.Series(pd.NA, index=agents.index, dtype="string")
    agents.loc[agents["ID сотрудника exact"].notna(), "Источник ID"] = (
        "SQL агент → точное ФИО USERS"
    )
    agents.loc[
        agents["ID сотрудника exact"].isna()
        & agents["ID сотрудника short"].notna(),
        "Источник ID",
    ] = "SQL агент → уникальные фамилия и имя USERS"
    agents["ФИО сотрудника"] = agents["ID сотрудника"].map(canonical_name)
    agents["ФИО сотрудника"] = agents["ФИО сотрудника"].combine_first(
        agents["agent_name"].astype("string").str.strip()
    )

    agent_audit = agents[
        [
            "agent_master_id",
            "agent_login",
            "agent_name",
            "is_active",
            "ID сотрудника",
            "ФИО сотрудника",
            "Источник ID",
        ]
    ].copy()
    agent_audit["YearMonth"] = year_month
    agent_audit["SQL-пакет"] = archive_path.name

    visits = visits.merge(
        agents[
            [
                "agent_master_id",
                "agent_login",
                "ID сотрудника",
                "ФИО сотрудника",
                "Источник ID",
            ]
        ],
        on="agent_master_id",
        how="left",
        validate="many_to_one",
    )
    visits = visits.merge(stores, on="store_id", how="left", validate="many_to_one")

    result = pd.DataFrame(index=visits.index)
    result["YearMonth"] = pd.Series(year_month, index=visits.index, dtype="Int64")
    result["Ключ визита RTM"] = visits["visit_id"]
    result["Дата визита"] = visits["visit_date"]
    result["Код RTM"] = visits["agent_master_id"]
    result["ТТ"] = visits["store_id"]
    result["Маршрут RTM"] = visits["aggregate_visit_id"]
    result["Регион RTM"] = visits["business_unit"]
    result["BU RTM"] = visits["business_unit"]
    result["SG RTM"] = visits["sales_group"]
    result["Город RTM"] = visits["city"]
    result["Визит выполнен"] = True
    result["Визит подтверждён"] = True
    result["Файл RTM"] = archive_path.name
    result["ID сотрудника"] = visits["ID сотрудника"].astype("string")
    result["ФИО из логинов"] = visits["ФИО сотрудника"].astype("string")
    result["Логин"] = visits["agent_login"].astype("string")
    result["Источник ID"] = visits["Источник ID"].astype("string")
    result["ID ТМ из логинов"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["ТМ из логинов"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["Код СВ из логинов"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["MonthStart"] = result["Дата визита"].dt.to_period("M").dt.to_timestamp()
    result["Источник визитов"] = "SQL клиента"
    result["Сеть SQL"] = visits["network_name"].astype("string")
    result["Формат ТТ SQL"] = visits["store_format"].astype("string")

    expected_picos_columns = {
        "visit_id",
        "store_id",
        "visit_date",
        "picos_potential",
        "picos_plan",
        "picos_fact",
        "picos_execution",
    }
    if picos.empty and not len(picos.columns):
        picos = pd.DataFrame(columns=sorted(expected_picos_columns))
    if not expected_picos_columns.issubset(picos.columns):
        missing = sorted(expected_picos_columns - set(picos.columns))
        raise ValueError(
            f"SQL-пакет {archive_path.name}: picos_by_visit.csv без полей {missing}"
        )
    picos["visit_id"] = _clean_code(picos["visit_id"])
    picos["store_id"] = _clean_code(picos["store_id"])
    picos["visit_date"] = pd.to_datetime(
        picos["visit_date"], errors="coerce", dayfirst=True
    ).dt.normalize()
    for column in ["picos_potential", "picos_plan", "picos_fact", "picos_execution"]:
        picos[column] = _to_numeric(picos[column])
    if not picos.empty:
        required_picos_values = [
            "visit_id",
            "store_id",
            "visit_date",
            "picos_potential",
            "picos_plan",
            "picos_fact",
        ]
        if picos[required_picos_values].isna().any().any():
            raise ValueError(
                f"SQL-пакет {archive_path.name}: обязательные поля PICOS содержат пустые значения"
            )
        if picos["visit_id"].duplicated().any():
            raise ValueError(
                f"SQL-пакет {archive_path.name}: PICOS содержит повторяющиеся visit_id"
            )
        picos_months = set(
            (picos["visit_date"].dt.year * 100 + picos["visit_date"].dt.month)
            .dropna()
            .astype(int)
        )
        if picos_months != {year_month}:
            raise ValueError(
                f"SQL-пакет {archive_path.name}: даты PICOS относятся к месяцам {sorted(picos_months)}"
            )
        if picos["picos_potential"].le(0).any() or picos["picos_plan"].le(0).any():
            raise ValueError(
                f"SQL-пакет {archive_path.name}: PICOS содержит неположительный потенциал или план"
            )

    picos_ratio = (picos["picos_fact"] / picos["picos_plan"]).round(10)
    picos_execution_pct = pd.Series(np.nan, index=picos.index, dtype="float64")
    valid_picos = picos_ratio.notna()
    picos_execution_pct.loc[valid_picos & picos_ratio.lt(0.75)] = 0.0
    picos_execution_pct.loc[valid_picos & picos_ratio.ge(0.75)] = picos_ratio.loc[
        valid_picos & picos_ratio.ge(0.75)
    ].clip(upper=1.0)
    picos_result = pd.DataFrame(
        {
            "YearMonth": pd.Series(year_month, index=picos.index, dtype="Int64"),
            "MonthStart": pd.Timestamp(
                year=year_month // 100,
                month=year_month % 100,
                day=1,
            ),
            "Ключ визита RTM": picos["visit_id"],
            "Дата визита PICOS": picos["visit_date"],
            "ТТ": picos["store_id"],
            "PICOS план SQL": picos["picos_plan"],
            "PICOS факт SQL": picos["picos_fact"],
            "PICOS выполнение SQL %": picos_execution_pct,
        }
    )

    audit = {
        "YearMonth": year_month,
        "Источник визитов": "SQL клиента",
        "SQL-пакет": archive_path.name,
        "Подтверждённых визитов": result["Ключ визита RTM"].nunique(),
        "Сопоставлено с сотрудником": int(result["ID сотрудника"].notna().sum()),
        "Покрытие сопоставления": float(result["ID сотрудника"].notna().mean()),
        "Уникальных кодов RTM": result["Код RTM"].nunique(),
        "Уникальных сотрудников": result["ID сотрудника"].nunique(),
        "Уникальных ТТ": result["ТТ"].nunique(),
        "Предупреждений пакета": len(frames["warnings.csv"]),
    }
    return result, agent_audit, audit, picos_result


def _discover_archives(export_root: Path) -> dict[int, Path]:
    archives: dict[int, Path] = {}
    for path in sorted(export_root.glob("HN_KPI_*.zip")):
        match = ARCHIVE_PATTERN.match(path.name)
        if not match:
            continue
        year_month = int(match.group(1))
        if year_month in archives:
            raise ValueError(f"Найдено несколько SQL-пакетов за {year_month}")
        archives[year_month] = path
    return archives


def load_client_sql_data(
    export_root: Path,
    dim_employees: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, set[int]]:
    if not export_root.exists():
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), set()

    archives = _discover_archives(export_root)
    if not archives:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), set()

    visit_frames = []
    agent_audits = []
    month_audits = []
    picos_frames = []
    for year_month, path in sorted(archives.items()):
        visits, agents, audit, picos = _load_month_archive(
            path, year_month, dim_employees
        )
        visit_frames.append(visits)
        agent_audits.append(agents)
        month_audits.append(audit)
        picos_frames.append(picos)

    all_visits = pd.concat(visit_frames, ignore_index=True)
    duplicate_keys = all_visits.duplicated(["YearMonth", "Ключ визита RTM"])
    if duplicate_keys.any():
        raise ValueError("SQL-пакеты содержат повторяющиеся visit_id между месячными разделами")
    all_picos = pd.concat(picos_frames, ignore_index=True)
    if not all_picos.empty and all_picos.duplicated(
        ["YearMonth", "Ключ визита RTM"]
    ).any():
        raise ValueError("SQL-пакеты содержат повторяющиеся PICOS visit_id между месяцами")
    return (
        all_visits,
        pd.concat(agent_audits, ignore_index=True),
        pd.DataFrame(month_audits),
        all_picos,
        set(archives),
    )


def load_client_sql_visits(
    export_root: Path,
    dim_employees: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, set[int]]:
    visits, agents, audit, _, months = load_client_sql_data(
        export_root, dim_employees
    )
    return visits, agents, audit, months
