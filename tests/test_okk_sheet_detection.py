from pathlib import Path

import pandas as pd

from scripts.parsers.okk_parser import _find_detail_sheet, _load_okk_file


def _detail_row(date_value: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "BU (формула)": ["VOLGA"],
            "SV ФИО (формула)": ["Иванов Иван Иванович"],
            "ФИО МЕ": ["Петров Пётр Петрович"],
            "Дата фотоаудита": [date_value],
            "SAP ТТ (Внешний код)": ["850000001"],
            "PICOS": ["90"],
            "OSA": ["80"],
        }
    )


def test_okk_prefers_current_questionnaire_sheet_with_header_on_second_row(tmp_path: Path):
    source_dir = tmp_path / "2026"
    source_dir.mkdir()
    source = source_dir / "Сводная АВГУСТ.xlsx"

    with pd.ExcelWriter(source, engine="openpyxl") as writer:
        _detail_row("07.08.2026").to_excel(
            writer,
            sheet_name="Анкеты",
            startrow=1,
            index=False,
        )
        _detail_row("01.07.2026").to_excel(writer, sheet_name="W0", index=False)

    with pd.ExcelFile(source) as workbook:
        assert _find_detail_sheet(workbook) == ("Анкеты", 1)

    result = _load_okk_file(source, {}, {}, {})

    assert result is not None
    assert result["audit_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-07"]
    assert result["period"].tolist() == ["2026_08"]
