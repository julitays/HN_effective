import json

from scripts.tools.audit_powerbi_vitrine_fields import _layout_usage


def test_layout_usage_resolves_powerbi_query_aliases():
    measure = ("_Меры", "m Обязательное обучение тренд %")
    layout = {
        "config": json.dumps(
            {
                "singleVisual": {
                    "prototypeQuery": {
                        "From": [{"Name": "_", "Entity": "_Меры", "Type": 0}],
                        "Select": [
                            {
                                "Measure": {
                                    "Expression": {"SourceRef": {"Source": "_"}},
                                    "Property": measure[1],
                                }
                            }
                        ],
                    }
                }
            },
            ensure_ascii=False,
        )
    }

    used_columns, used_measures = _layout_usage(layout, set(), {measure})

    assert used_columns == set()
    assert used_measures == {measure}
