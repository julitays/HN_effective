import sys
import traceback
from pathlib import Path

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

TOTAL_STEPS = 24


def _run_step(number: int, label: str, func, **kwargs):
    """Выполняет один шаг пайплайна. При ошибке — печатает причину и не
    прерывает остальные шаги (см. решение по обработке ошибок в плане)."""
    print(f"\n[{number}/{TOTAL_STEPS}] {label}...")
    try:
        result = func(**kwargs)
        _RESULTS.append((number, label, "OK", ""))
        return result
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        print(f"  !! ШАГ ПРОВАЛЕН: {label} — {detail}")
        traceback.print_exc()
        _RESULTS.append((number, label, "ОШИБКА", detail))
        return None


_RESULTS: list[tuple[int, str, str, str]] = []


def run_all() -> None:
    print("=== Запуск всех парсеров ===")
    _RESULTS.clear()

    dim = _run_step(1, "USERS → dim_employees", parse_users)
    _run_step(2, "ОЭД → fact_oed", parse_oed, dim=dim)
    _run_step(3, "Справочник команд → dim_teams", build_teams, dim=dim)
    _run_step(4, "Открытые вакансии", parse_open_vacancies)
    _run_step(5, "Закрытые вакансии", parse_closed_vacancies)
    _run_step(6, "Кадровый реестр", parse_hr_registry)
    _run_step(7, "Кадровая витрина (org staffing)", build_org_staffing_monthly_snapshot)
    _run_step(8, "ОКК", parse_okk)
    _run_step(9, "Обучение", parse_learning)
    _run_step(10, "Месячная витрина обучения", build_learning_monthly)
    _run_step(11, "KPI", parse_kpi)
    _run_step(12, "ENPS", parse_enps)
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

    ok = [r for r in _RESULTS if r[2] == "OK"]
    failed = [r for r in _RESULTS if r[2] != "OK"]

    print(f"\n=== Сводка: {len(ok)}/{len(_RESULTS)} шагов успешно ===")
    if failed:
        print("Провалившиеся шаги (данные по ним НЕ обновлены, использованы старые файлы из data/out, если были):")
        for number, label, _, detail in failed:
            print(f"  [{number}/{TOTAL_STEPS}] {label}: {detail}")
    else:
        print("Все источники обработаны без ошибок.")

    print("\n=== Готово ===")


if __name__ == "__main__":
    run_all()
