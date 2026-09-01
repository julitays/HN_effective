from pathlib import Path

import pandas as pd

from scripts.tools.build_powerbi_cleanup_plan import build_plan


def test_cleanup_plan_preserves_existing_column_contract(tmp_path: Path):
    audit = tmp_path / "audit.xlsx"
    fields = pd.DataFrame(
        [
            [
                "page",
                "new_unused",
                True,
                False,
                False,
                False,
                False,
                "",
                "Кандидат на удаление",
            ]
        ]
    )
    with pd.ExcelWriter(audit) as writer:
        pd.DataFrame().to_excel(writer, sheet_name="summary", index=False)
        fields.to_excel(writer, sheet_name="fields", index=False)

    plan = build_plan(audit, {"columns": {"page": ["old_unused"]}})

    assert plan["columns"]["page"] == ["new_unused", "old_unused"]
