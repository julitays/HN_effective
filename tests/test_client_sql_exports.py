import io
import zipfile

import pandas as pd

from scripts.client_sql_exports import load_client_sql_visits


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    return buffer.getvalue().encode("utf-8-sig")


def _write_package(path) -> None:
    frames = {
        "visits.csv": pd.DataFrame(
            {
                "visit_id": ["V1", "V2"],
                "aggregate_visit_id": ["A1", "A2"],
                "visit_date": ["10.07.2026 0:00:00", "11.07.2026 0:00:00"],
                "store_id": ["100", "100"],
                "agent_master_id": ["3001", "3002"],
            }
        ),
        "agents.csv": pd.DataFrame(
            {
                "agent_master_id": ["3001", "3002"],
                "agent_login": ["agent_1", "agent_2"],
                "agent_name": ["Иванов Иван Иванович 2", "Петров Петр"],
                "is_active": [1, 1],
            }
        ),
        "stores.csv": pd.DataFrame(
            {
                "store_id": ["100"],
                "network_code": ["CHAIN"],
                "network_name": ["Сеть"],
                "store_format": ["Супермаркет"],
                "city": ["Москва"],
                "business_unit": ["MOSCOW"],
                "sales_group": ["MOSCOW"],
            }
        ),
        "picos_by_visit.csv": pd.DataFrame(
            columns=[
                "visit_id",
                "store_id",
                "visit_date",
                "picos_plan",
                "picos_fact",
                "picos_execution",
            ]
        ),
        "osa_by_visit.csv": pd.DataFrame(
            columns=[
                "visit_id",
                "store_id",
                "visit_date",
                "must_products",
                "must_products_in_stock",
                "osa_fact_must",
                "all_matrix_products",
                "all_matrix_products_in_stock",
                "osa_fact_all_matrix",
            ]
        ),
        "top16_by_visit.csv": pd.DataFrame(
            columns=[
                "visit_id",
                "store_id",
                "visit_date",
                "observed_top16_products",
                "top16_facings",
                "all_facings",
                "group_facings_by_scene",
                "top16_share_all_facings",
                "top16_share_group_facings",
            ]
        ),
        "errors.csv": pd.DataFrame(columns=["error"]),
        "warnings.csv": pd.DataFrame(columns=["warning"]),
    }
    manifest = pd.DataFrame(
        [
            {"file": name, "rows": len(frame), "status": "validated"}
            for name, frame in frames.items()
        ]
    )
    frames["manifest.csv"] = manifest
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, frame in frames.items():
            archive.writestr(name, _csv_bytes(frame))


def test_sql_visits_use_only_controlled_users_name_rules(tmp_path):
    package = tmp_path / "HN_KPI_202607.zip"
    _write_package(package)
    dim = pd.DataFrame(
        {
            "ID сотрудника": ["E1", "E2"],
            "ФИО": ["Иванов Иван Иванович", "Петров Петр Петрович"],
        }
    )

    visits, agents, audit, months = load_client_sql_visits(tmp_path, dim)

    assert months == {202607}
    assert visits["ID сотрудника"].tolist() == ["E1", "E2"]
    assert agents["Источник ID"].tolist() == [
        "SQL агент → точное ФИО USERS",
        "SQL агент → уникальные фамилия и имя USERS",
    ]
    assert visits["Источник визитов"].unique().tolist() == ["SQL клиента"]
    assert audit.loc[0, "Покрытие сопоставления"] == 1.0
