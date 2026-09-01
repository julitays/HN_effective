import pandas as pd

from scripts.parsers.okk_parser import _build_unified_falsification_flag


def test_explicit_falsification_flag_overrides_descriptive_fields():
    frame = pd.DataFrame(
        {
            "has_falsification": pd.Series([False, True], dtype="boolean"),
            "_has_explicit_falsification_flag": [True, True],
            "_has_falsification_count": [True, True],
            "falsification_count": [1, 0],
            "falsification_notes": ["comment", ""],
            "фальс_отсутствует": [1, 0],
        }
    )

    result = _build_unified_falsification_flag(frame)

    assert result.tolist() == [False, True]


def test_old_files_use_final_falsification_count():
    frame = pd.DataFrame(
        {
            "has_falsification": pd.Series([False, False], dtype="boolean"),
            "_has_explicit_falsification_flag": [False, False],
            "_has_falsification_count": [True, True],
            "falsification_count": [0, 2],
        }
    )

    result = _build_unified_falsification_flag(frame)

    assert result.tolist() == [False, True]


def test_comments_and_reasons_do_not_create_fraud_without_final_source_result():
    frame = pd.DataFrame(
        {
            "has_falsification": pd.Series([False], dtype="boolean"),
            "_has_explicit_falsification_flag": [False],
            "_has_falsification_count": [False],
            "falsification_count": [0],
            "falsification_notes": ["comment"],
            "фальс_отсутствует": [1],
        }
    )

    result = _build_unified_falsification_flag(frame)

    assert result.tolist() == [False]
