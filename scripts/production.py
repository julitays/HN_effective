from __future__ import annotations

import hashlib
import json
import os
import shutil
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from scripts.tools.audit_vitrines import Audit


def _probe_exclusive_access(path: Path) -> None:
    if os.name != "nt":
        with path.open("r+b"):
            return

    generic_read = 0x80000000
    generic_write = 0x40000000
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle_value = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(path),
        generic_read | generic_write,
        0,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle_value:
        error_code = ctypes.get_last_error()
        raise PermissionError(error_code, os.strerror(error_code), str(path))
    kernel32.CloseHandle(handle)


def validate_publish_target(target_out: Path) -> None:
    if not target_out.exists():
        return
    locked_files: list[str] = []
    for path in sorted(target_out.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        try:
            _probe_exclusive_access(path)
        except OSError:
            locked_files.append(path.name)
    if locked_files:
        sample = ", ".join(locked_files[:5])
        suffix = "" if len(locked_files) <= 5 else f" и ещё {len(locked_files) - 5}"
        raise RuntimeError(
            "Публикация не начата: выходные файлы используются другим процессом "
            f"({sample}{suffix}). Закройте Power BI Desktop и повторите запуск."
        )


@contextmanager
def etl_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        details = lock_path.read_text(encoding="utf-8", errors="replace") if lock_path.exists() else ""
        raise RuntimeError(
            f"ETL уже запущен или прошлый запуск завершился аварийно: {lock_path}. {details}"
        ) from exc

    try:
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)


def prepare_staging_directory(data_dir: Path, run_id: str) -> Path:
    staging_root = data_dir / ".etl_staging" / run_id
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_out = staging_root / "out"
    staging_out.mkdir(parents=True)
    (staging_out / ".gitkeep").touch()
    return staging_out


def run_output_qa(out_dir: Path) -> Path:
    audit = Audit(out_dir)
    report_path = audit.run()
    result = pd.DataFrame(audit.rows)
    errors = result[result["Уровень"].eq("ERROR")]
    if not errors.empty:
        details = errors[["Таблица", "Проверка", "Количество", "Детали"]].to_dict("records")
        raise RuntimeError(f"QA витрин не пройден: {details}")
    return report_path


def validate_freshness(
    out_dir: Path,
    table_names: list[str],
    expected_yearmonth: int,
    as_of_date: pd.Timestamp,
) -> dict[str, int]:
    latest_by_table: dict[str, int] = {}
    as_of_yearmonth = int(as_of_date.year * 100 + as_of_date.month)
    for table_name in table_names:
        path = out_dir / table_name
        if not path.exists():
            raise RuntimeError(f"Контроль свежести: отсутствует {table_name}")
        schema_names = set(pq.read_schema(path).names)
        if "YearMonth" in schema_names:
            values = pd.read_parquet(path, columns=["YearMonth"])["YearMonth"]
            yearmonths = pd.to_numeric(values, errors="coerce").dropna().astype(int)
        elif "MonthStart" in schema_names:
            values = pd.to_datetime(
                pd.read_parquet(path, columns=["MonthStart"])["MonthStart"],
                errors="coerce",
            ).dropna()
            yearmonths = (values.dt.year * 100 + values.dt.month).astype(int)
        else:
            raise RuntimeError(f"Контроль свежести: в {table_name} нет периода")
        if yearmonths.empty:
            raise RuntimeError(f"Контроль свежести: в {table_name} нет валидных периодов")
        latest = int(yearmonths.max())
        if latest < expected_yearmonth:
            raise RuntimeError(
                f"Контроль свежести: {table_name} заканчивается {latest}, ожидался минимум {expected_yearmonth}"
            )
        if latest > as_of_yearmonth:
            raise RuntimeError(
                f"Контроль свежести: {table_name} содержит будущий период {latest} при дате расчёта {as_of_yearmonth}"
            )
        latest_by_table[table_name] = latest
    return latest_by_table


def prune_output_tables(out_dir: Path, publish_tables: list[str]) -> list[str]:
    allowed = {
        name if name.endswith(".parquet") else f"{name}.parquet"
        for name in publish_tables
    }
    missing = sorted(name for name in allowed if not (out_dir / name).exists())
    if missing:
        raise RuntimeError(
            "Публикация запрещена: в согласованном наборе Power BI отсутствуют "
            + ", ".join(missing)
        )
    removed: list[str] = []
    for path in sorted(out_dir.glob("*.parquet"), key=lambda item: item.name):
        if path.name not in allowed:
            removed.append(path.name)
            path.unlink()
    return removed


def prune_output_columns(out_dir: Path, plan_path: Path) -> dict[str, int]:
    if not plan_path.exists():
        raise RuntimeError(f"Не найден контракт колонок Power BI: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    columns_to_drop = plan.get("columns")
    if not isinstance(columns_to_drop, dict):
        raise RuntimeError(
            f"В контракте Power BI отсутствует объект columns: {plan_path}"
        )

    removed_by_table: dict[str, int] = {}
    for table_name, configured_columns in columns_to_drop.items():
        path = out_dir / f"{table_name}.parquet"
        if not path.exists():
            continue
        source = pq.read_table(path)
        drop_set = {str(column) for column in configured_columns}
        keep_columns = [column for column in source.column_names if column not in drop_set]
        if not keep_columns:
            raise RuntimeError(
                f"Контракт Power BI удаляет все колонки из {path.name}"
            )
        removed_count = len(source.column_names) - len(keep_columns)
        if removed_count == 0:
            removed_by_table[table_name] = 0
            continue
        temporary = path.with_name(f".{path.name}.columns.tmp")
        try:
            pq.write_table(source.select(keep_columns), temporary, compression="snappy")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        removed_by_table[table_name] = removed_count
    return removed_by_table


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_run_manifest(
    out_dir: Path,
    raw_dir: Path,
    run_id: str,
    as_of_date: pd.Timestamp,
    latest_by_table: dict[str, int],
) -> Path:
    outputs = []
    for path in sorted(out_dir.glob("*.parquet")):
        metadata = pq.ParquetFile(path).metadata
        outputs.append(
            {
                "file": path.name,
                "rows": metadata.num_rows,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    sources = [
        {
            "file": path.relative_to(raw_dir).as_posix(),
            "bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }
        for path in sorted(raw_dir.rglob("*"))
        if path.is_file() and path.name != ".gitkeep"
    ]
    manifest = {
        "run_id": run_id,
        "as_of_date": as_of_date.date().isoformat(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "freshness": latest_by_table,
        "outputs": outputs,
        "sources": sources,
    }
    manifest_path = out_dir / "etl_run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def publish_staging(staging_out: Path, target_out: Path, run_id: str) -> None:
    data_dir = target_out.parent
    backup_root = data_dir / ".etl_backup" / run_id
    backup_out = backup_root / "out"
    target_out.mkdir(parents=True, exist_ok=True)
    validate_publish_target(target_out)
    backup_out.mkdir(parents=True, exist_ok=True)
    original_files = {path.name for path in target_out.iterdir() if path.is_file()}
    staged_files = {path.name for path in staging_out.iterdir() if path.is_file()}
    for path in target_out.iterdir():
        if path.is_file():
            shutil.copy2(path, backup_out / path.name)

    marker = target_out / ".publishing"
    marker.write_text(run_id, encoding="utf-8")
    try:
        for source in sorted(staging_out.iterdir(), key=lambda path: path.name):
            if source.is_file():
                os.replace(source, target_out / source.name)
        for stale_name in original_files - staged_files:
            (target_out / stale_name).unlink(missing_ok=True)
    except Exception:
        for current in target_out.iterdir():
            if current.is_file() and current.name not in original_files:
                current.unlink(missing_ok=True)
        for backup in backup_out.iterdir():
            if backup.is_file():
                temporary = target_out / f".{backup.name}.rollback"
                shutil.copy2(backup, temporary)
                os.replace(temporary, target_out / backup.name)
        raise
    else:
        shutil.rmtree(backup_root, ignore_errors=True)
        staging_parent = staging_out.parent
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)
    finally:
        marker.unlink(missing_ok=True)
