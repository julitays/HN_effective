import json
from pathlib import Path

import pandas as pd

from scripts.tools.audit_vitrines import (
    Audit,
    KPI_COMPONENT_COLUMNS,
    PUBLISHED_REQUIRED_COLUMNS,
)


def _published_page3(tmp_path: Path, extra: dict | None = None) -> Audit:
    row = {
        column: 0.95 if "%" in column else 1
        for column in PUBLISHED_REQUIRED_COLUMNS["page3_merch_monthly_snapshot"]
    }
    row.update(
        {
            "YearMonth": 202607,
            "Мерчендайзер": "Сотрудник 1",
            "Регион BI": "Москва",
            "Супервайзер": "Супервайзер 1",
            "Территориальный менеджер": "ТМ 1",
            "Статус личной эффективности": "Соответствует роли",
            "Причина личной эффективности": pd.NA,
        }
    )
    for column in KPI_COMPONENT_COLUMNS:
        row[column] = 0.95
    if extra:
        row.update(extra)

    pd.DataFrame([row]).to_parquet(
        tmp_path / "page3_merch_monthly_snapshot.parquet",
        index=False,
    )
    (tmp_path / "etl_run_manifest.json").write_text("{}", encoding="utf-8")
    cleanup_path = tmp_path / "cleanup.json"
    cleanup_path.write_text(
        json.dumps(
            {
                "columns": {
                    "page3_merch_monthly_snapshot": ["technical"]
                }
            }
        ),
        encoding="utf-8",
    )

    audit = Audit(tmp_path)
    audit.settings = {
        "reporting": {
            "publish_tables": ["page3_merch_monthly_snapshot.parquet"],
            "powerbi_column_contract": str(cleanup_path),
        }
    }
    return audit


def test_published_audit_accepts_current_kpi_schema(tmp_path: Path):
    audit = _published_page3(tmp_path)

    audit.run()

    errors = [row for row in audit.rows if row["Уровень"] == "ERROR"]
    assert errors == []


def test_published_audit_rejects_old_and_technical_columns(tmp_path: Path):
    audit = _published_page3(
        tmp_path,
        extra={"PICOS план %": 0.95, "technical": "old"},
    )

    audit.run()

    error_checks = {
        row["Проверка"]
        for row in audit.rows
        if row["Уровень"] == "ERROR"
    }
    assert "Технические колонки" in error_checks
    assert "Старые KPI-колонки" in error_checks

