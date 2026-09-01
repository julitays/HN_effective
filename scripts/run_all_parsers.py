import argparse
import csv
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.parsers.users_parser import parse_users
from scripts.parsers.oed_parser import parse_oed
from scripts.builders.teams_builder import build_teams
from scripts.parsers.parse_open_vacancies import parse_open_vacancies
from scripts.parsers.parse_closed_vacancies import parse_closed_vacancies
from scripts.parsers.parse_hr_registry import parse_hr_registry
from scripts.parsers.okk_parser import parse_okk
from scripts.parsers.learning_parser import parse_learning
from scripts.builders.build_learning_monthly import build_learning_monthly
from scripts.parsers.kpi_parser import parse_kpi
from scripts.parsers.enps_parser import parse_enps
from scripts.parsers.attestations_parser import parse_attestations
from scripts.builders.build_model_dimensions import build_model_dimensions
from scripts.builders.build_page1_monthly_snapshot import build_page1_monthly_snapshot
from scripts.builders.build_page2_data import build_page2_data
from scripts.builders.build_page3_data import build_page3_data
from scripts.builders.build_page4_tt_data import build_page4_tt_data
from scripts.builders.build_page5_sv_oed_data import build_page5_sv_oed_data
from scripts.builders.build_oed_quarterly_snapshot import build_oed_quarterly_snapshot
from scripts.builders.build_page6_okk_fraud_data import build_page6_okk_fraud_data
from scripts.builders.build_page7_tm_data import build_page7_tm_data
from scripts.builders.build_page8_learning_competencies_data import build_page8_learning_competencies_data
from scripts.builders.build_page9_climate_data import build_page9_climate_data
from scripts.builders.build_org_staffing_monthly_snapshot import build_org_staffing_monthly_snapshot
from scripts.builders.build_security_access import build_security_access
from scripts.production import (
    etl_lock,
    prepare_staging_directory,
    prune_output_columns,
    prune_output_tables,
    publish_staging,
    run_output_qa,
    validate_freshness,
    write_run_manifest,
)
from scripts.utils import get_as_of_date, load_settings

TOTAL_STEPS = 25


def _run_step(number: int, label: str, func, **kwargs):
    """Выполняет шаг и сохраняет ошибку для итогового аварийного завершения."""
    print(f"\n[{number}/{TOTAL_STEPS}] {label}...")
    started_at = datetime.now()
    started = time.perf_counter()
    try:
        result = func(**kwargs)
        elapsed = time.perf_counter() - started
        print(f"  Время шага: {elapsed:.2f} сек.")
        _RESULTS.append((number, label, "OK", "", elapsed, started_at))
        return result
    except Exception as exc:
        elapsed = time.perf_counter() - started
        detail = f"{exc.__class__.__name__}: {exc}"
        print(f"  !! ШАГ ПРОВАЛЕН: {label} — {detail}")
        traceback.print_exc()
        print(f"  Время до ошибки: {elapsed:.2f} сек.")
        _RESULTS.append((number, label, "ОШИБКА", detail, elapsed, started_at))
        return None


_RESULTS: list[tuple[int, str, str, str, float, datetime]] = []


def _write_performance_report(total_seconds: float) -> None:
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    latest_path = reports_dir / "etl_performance_latest.csv"
    history_path = reports_dir / "etl_performance_history.csv"
    run_id = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        {
            "run_id": run_id,
            "step": number,
            "label": label,
            "status": status,
            "seconds": round(elapsed, 3),
            "share_pct": round(elapsed / total_seconds * 100, 2) if total_seconds else 0,
            "started_at": started_at.isoformat(timespec="seconds"),
            "detail": detail,
        }
        for number, label, status, detail, elapsed, started_at in _RESULTS
    ]
    if not rows:
        return
    fieldnames = list(rows[0])
    with latest_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    append_header = not history_path.exists() or history_path.stat().st_size == 0
    with history_path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if append_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f"  Профиль ETL: {latest_path}")


def _run_pipeline() -> None:
    print("=== Запуск всех парсеров ===")
    _RESULTS.clear()
    run_started = time.perf_counter()

    dim = _run_step(1, "USERS -> dim_employees", parse_users)
    _run_step(2, "ОЭД -> fact_oed", parse_oed, dim=dim)
    _run_step(3, "Справочник команд -> dim_teams", build_teams, dim=dim)
    _run_step(4, "Открытые вакансии", parse_open_vacancies)
    _run_step(5, "Закрытые вакансии", parse_closed_vacancies)
    _run_step(6, "Кадровый реестр", parse_hr_registry)
    _run_step(7, "Кадровая витрина (org staffing)", build_org_staffing_monthly_snapshot)
    _run_step(8, "ОКК", parse_okk, dim=dim)
    _run_step(9, "Обучение", parse_learning, dim=dim)
    _run_step(10, "Месячная витрина обучения", build_learning_monthly)
    _run_step(11, "KPI", parse_kpi, dim=dim)
    _run_step(12, "ENPS", parse_enps, dim=dim)
    _run_step(13, "АТТЕСТАЦИИ", parse_attestations)
    _run_step(14, "Витрина 1 страницы (региональный snapshot)", build_page1_monthly_snapshot)
    _run_step(15, "Витрины 2 страницы", build_page2_data)
    _run_step(16, "Витрина 3 страницы", build_page3_data)
    _run_step(17, "Витрина 4 страницы", build_page4_tt_data)
    _run_step(18, "Витрина 5 страницы (+ dSupervisor)", build_page5_sv_oed_data)
    _run_step(19, "Витрина 6 страницы", build_page6_okk_fraud_data)
    _run_step(20, "Витрина 7 страницы (+ dTM)", build_page7_tm_data)
    # Обязательно после шага 20: читает dTM.parquet, который создаётся именно там.
    _run_step(21, "Отдельная витрина ОЭД (зависит от dTM)", build_oed_quarterly_snapshot)
    _run_step(22, "Витрина 8 страницы", build_page8_learning_competencies_data)
    _run_step(23, "Витрина 9 страницы", build_page9_climate_data)
    # Обязательно последним: считывает все уже готовые файлы из data/out.
    _run_step(24, "Размерности модели (dRegion/dMonth/dQuarter)", build_model_dimensions)
    _run_step(25, "Контур региональной безопасности", build_security_access, dim=dim)

    total_seconds = time.perf_counter() - run_started
    _write_performance_report(total_seconds)
    ok = [r for r in _RESULTS if r[2] == "OK"]
    failed = [r for r in _RESULTS if r[2] != "OK"]

    print(f"\n=== Сводка: {len(ok)}/{len(_RESULTS)} шагов успешно ===")
    print(f"Общее время ETL: {total_seconds:.2f} сек. ({total_seconds / 60:.2f} мин.)")
    print("Самые долгие шаги:")
    for number, label, _, _, elapsed, _ in sorted(_RESULTS, key=lambda row: row[4], reverse=True)[:5]:
        print(f"  [{number}/{TOTAL_STEPS}] {elapsed:8.2f} сек. — {label}")
    if failed:
        print("Провалившиеся шаги (их данные не обновлены; публикация результата запрещена):")
        for number, label, _, detail, _, _ in failed:
            print(f"  [{number}/{TOTAL_STEPS}] {label}: {detail}")
        failed_labels = ", ".join(label for _, label, _, _, _, _ in failed)
        raise RuntimeError(f"ETL завершён с ошибками: {failed_labels}")
    else:
        print("Все источники обработаны без ошибок.")

        print("\n=== Готово ===")


def run_all(*, transactional: bool = True, as_of_date: str | None = None) -> None:
    if not transactional or os.environ.get("HN_OUT_DIR"):
        if as_of_date:
            os.environ["HN_AS_OF_DATE"] = as_of_date
        _run_pipeline()
        return

    settings = load_settings()
    target_out = Path(settings["paths"]["out"])
    raw_dir = Path(settings["paths"]["raw"])
    data_dir = target_out.parent
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    lock_path = data_dir / ".etl.lock"
    staging_out = prepare_staging_directory(data_dir, run_id)
    previous_out = os.environ.get("HN_OUT_DIR")
    previous_as_of = os.environ.get("HN_AS_OF_DATE")
    as_of_value = pd.Timestamp(as_of_date).normalize() if as_of_date else get_as_of_date()

    try:
        with etl_lock(lock_path):
            os.environ["HN_OUT_DIR"] = str(staging_out)
            os.environ["HN_AS_OF_DATE"] = as_of_value.date().isoformat()
            try:
                _run_pipeline()
                qa_path = run_output_qa(staging_out)
                qa_path.unlink(missing_ok=True)
                reporting = settings["reporting"]
                latest_by_table = validate_freshness(
                    staging_out,
                    list(reporting["freshness_tables"]),
                    int(reporting["expected_latest_yearmonth"]),
                    as_of_value,
                )
                removed_outputs = prune_output_tables(
                    staging_out,
                    list(reporting["publish_tables"]),
                )
                pruned_columns = prune_output_columns(
                    staging_out,
                    Path(reporting["powerbi_column_contract"]),
                )
                manifest_path = write_run_manifest(
                    staging_out,
                    raw_dir,
                    run_id,
                    as_of_value,
                    latest_by_table,
                )
                print("  QA staging: проверка пройдена")
                print(f"  Исключено технических parquet из публикации: {len(removed_outputs)}")
                print(
                    "  Исключено технических колонок из Power BI: "
                    f"{sum(pruned_columns.values())}"
                )
                print(f"  Манифест запуска: {manifest_path}")
            finally:
                if previous_out is None:
                    os.environ.pop("HN_OUT_DIR", None)
                else:
                    os.environ["HN_OUT_DIR"] = previous_out
                if previous_as_of is None:
                    os.environ.pop("HN_AS_OF_DATE", None)
                else:
                    os.environ["HN_AS_OF_DATE"] = previous_as_of

            publish_staging(staging_out, target_out, run_id)
            print(f"  Опубликован согласованный набор витрин: {target_out}")
    except Exception:
        shutil.rmtree(staging_out.parent, ignore_errors=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Полная сборка H&N ETL")
    parser.add_argument("--direct", action="store_true", help="Запись напрямую без staging; только для локальной диагностики")
    parser.add_argument("--as-of-date", help="Фиксированная дата расчёта YYYY-MM-DD")
    arguments = parser.parse_args()
    run_all(transactional=not arguments.direct, as_of_date=arguments.as_of_date)
