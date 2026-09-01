from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


TABLES_TO_HIDE = {
    "dim_employees",
    "dim_teams",
    "dTM",
    "org_staffing_report_snapshot",
    "region_map",
    "learning_monthly",
}

MEASURES_TO_REMOVE = {
    "m Фрод кол-во",
    "m Карточка мерча",
    "dRadarAxis",
    "m Фрод %",
}

REQUIRED_MEASURES = {
    "_Меры": [
        {
            "name": "m Обязательное обучение тренд %",
            "expression": "DIVIDE(SUM('learning_monthly'[Пройдено обязательных курсов]), SUM('learning_monthly'[Назначено обязательных курсов]))",
            "format_string": "0.0%",
        }
    ]
}


def build_plan(audit_path: Path, existing_plan: dict | None = None) -> dict:
    fields = pd.read_excel(audit_path, sheet_name=1)
    fields.columns = [
        "table",
        "field",
        "in_pbix",
        "visual",
        "measure",
        "relationship",
        "sort",
        "reason",
        "decision",
    ]
    candidates = fields[
        fields["in_pbix"].eq(True)
        & fields["decision"].eq("Кандидат на удаление")
        & ~fields["table"].isin(TABLES_TO_HIDE)
    ]
    columns = {
        table: sorted(group["field"].dropna().astype(str).unique().tolist())
        for table, group in candidates.groupby("table")
    }
    for table, fields in (existing_plan or {}).get("columns", {}).items():
        columns[table] = sorted(set(columns.get(table, [])) | set(map(str, fields)))
    return {
        "hide_tables": sorted(TABLES_TO_HIDE),
        "measures": sorted(MEASURES_TO_REMOVE),
        "required_measures": REQUIRED_MEASURES,
        "columns": columns,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, default=Path("reports/powerbi_vitrine_field_audit.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("reports/powerbi_cleanup_plan.json"))
    arguments = parser.parse_args()
    existing_plan = (
        json.loads(arguments.output.read_text(encoding="utf-8"))
        if arguments.output.exists()
        else None
    )
    plan = build_plan(arguments.audit, existing_plan)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"План: {len(plan['hide_tables'])} скрываемых таблиц, {len(plan['measures'])} мер, "
        f"{sum(len(items) for items in plan['columns'].values())} колонок"
    )


if __name__ == "__main__":
    main()
