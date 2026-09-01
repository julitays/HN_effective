from datetime import datetime

from openpyxl import Workbook

from scripts.rtm_utils import _load_rtm_source


def _write_rtm(path, visit_id: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Фильтр тестовой выгрузки"])
    sheet.append([])
    sheet.append(
        [
            "route_date",
            "route_name",
            "employee_id",
            "shop_code",
            "agg_visit_id",
            "visit_date",
            "visit_id",
            "is_complete",
            "is_confirmed",
            "region",
        ]
    )
    sheet.append(
        [
            datetime(2026, 7, 1),
            "R-1",
            "101",
            "5001",
            visit_id,
            datetime(2026, 7, 2),
            visit_id,
            True,
            True,
            "VOLGA",
        ]
    )
    workbook.save(path)


def test_rtm_cache_reuses_only_exact_source_content(tmp_path):
    rtm_root = tmp_path / "rtm"
    cache_root = tmp_path / "cache"
    rtm_root.mkdir()
    source = rtm_root / "july.xlsx"
    _write_rtm(source, "VISIT-1")

    first, first_audit, first_cache = _load_rtm_source(source, rtm_root, cache_root)
    second, second_audit, second_cache = _load_rtm_source(source, rtm_root, cache_root)

    assert first_audit["Режим чтения"] == "Excel → кеш"
    assert second_audit["Режим чтения"] == "Кеш parquet"
    assert first_cache == second_cache
    assert second["Ключ визита RTM"].tolist() == first["Ключ визита RTM"].tolist()

    _write_rtm(source, "VISIT-2")
    changed, changed_audit, changed_cache = _load_rtm_source(source, rtm_root, cache_root)

    assert changed_audit["Режим чтения"] == "Excel → кеш"
    assert changed_cache != first_cache
    assert changed["Ключ визита RTM"].tolist() == ["VISIT-2"]
