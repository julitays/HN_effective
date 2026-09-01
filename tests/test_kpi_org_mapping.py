from pathlib import Path

import pandas as pd

from scripts.kpi_org_mapping import (
    CURRENT_SV_SOURCE,
    CURRENT_TT_SOURCE,
    UNRESOLVED_SOURCE,
    attach_kpi_rtm_org,
    load_current_tm_assignments,
)


def _visit(tt: str = "850000001", employee_id: str | None = "E1") -> dict:
    return {
        "MonthStart": pd.Timestamp("2026-06-01"),
        "YearMonth": 202606,
        "Ключ визита RTM": "V1",
        "Дата визита": pd.Timestamp("2026-06-15"),
        "ТТ": tt,
        "Код RTM": "3000001",
        "ID сотрудника": employee_id,
        "ФИО из логинов": "Employee One",
    }


def _assignment(tt: str = "850000001") -> dict:
    return {
        "ТТ": tt,
        "ID территориального менеджера": "TM1",
        "Территориальный менеджер": "Current TM",
        "ID супервайзера текущий": "SV1",
        "Супервайзер текущий": "Current SV",
        "Код СВ текущий": "SV_ROUTE_1",
        "Регион BI текущий": "Волга",
        "Город текущий": "КАЗАНЬ",
        "Сеть текущая": "CHAIN",
        "Источник привязки ТМ": CURRENT_TT_SOURCE,
        "Файл текущей привязки": "current.xlsx",
    }


def test_exact_current_tt_assignment_is_used_for_every_period():
    visits = pd.DataFrame([_visit()])
    assignments = pd.DataFrame([_assignment()])

    result, audit = attach_kpi_rtm_org(visits, assignments)

    assert result.loc[0, "Территориальный менеджер"] == "Current TM"
    assert result.loc[0, "ID территориального менеджера"] == "TM1"
    assert result.loc[0, "Супервайзер"] == "Current SV"
    assert result.loc[0, "Регион BI"] == "Волга"
    assert result.loc[0, "Источник привязки ТМ"] == CURRENT_TT_SOURCE
    assert audit["Визитов"].sum() == 1


def test_tt_assignment_does_not_require_historical_employee():
    visits = pd.DataFrame([_visit(employee_id=None)])
    assignments = pd.DataFrame([_assignment()])

    result, _ = attach_kpi_rtm_org(visits, assignments)

    assert result.loc[0, "Территориальный менеджер"] == "Current TM"
    assert not bool(result.loc[0, "Сотрудник подтверждён активным USERS"])
    assert (
        result.loc[0, "Статус цепочки привязки"]
        == "RTM → текущая привязка ТТ → активный USERS"
    )


def test_unknown_tt_stays_unassigned_without_geographic_recovery():
    visits = pd.DataFrame([_visit(tt="UNKNOWN")])
    assignments = pd.DataFrame([_assignment()])

    result, audit = attach_kpi_rtm_org(visits, assignments)

    assert result.empty
    assert audit.loc[0, "Статус цепочки привязки"] == "Исключён: нет активного ТМ"


def test_current_users_restores_active_supervisor_and_tm_when_tt_assignment_is_invalid():
    visits = pd.DataFrame([_visit()])
    assignment = _assignment()
    assignment["ID супервайзера текущий"] = pd.NA
    assignment["Супервайзер текущий"] = pd.NA
    assignment["Код СВ текущий"] = pd.NA
    assignment["ID территориального менеджера"] = pd.NA
    assignment["Территориальный менеджер"] = pd.NA
    assignment["Источник привязки ТМ"] = UNRESOLVED_SOURCE
    teams = pd.DataFrame(
        [
            {
                "ID мерчендайзера": "E1",
                "ID супервайзера": "SV_USERS",
                "Супервайзер": "Supervisor From Users",
                "ID территориального менеджера": "TM_USERS",
                "Территориальный менеджер": "TM From Users",
                "Регион BI": "Волга",
            }
        ]
    )

    result, _ = attach_kpi_rtm_org(pd.DataFrame([_visit()]), pd.DataFrame([assignment]), teams)

    assert result.loc[0, "Территориальный менеджер"] == "TM From Users"
    assert result.loc[0, "ID территориального менеджера"] == "TM_USERS"
    assert result.loc[0, "Супервайзер"] == "Supervisor From Users"


def test_loader_restores_blank_tm_only_by_unambiguous_current_sv(tmp_path: Path):
    source_dir = tmp_path / "Привязки ТМ"
    source_dir.mkdir()
    raw = pd.DataFrame(
        [
            {
                "Ship To": "850000001",
                "Cluster BU": "VOLGA",
                "Cluster SG": "KAZAN",
                "Chain": "CHAIN",
                "Агентство": "OPEN",
                "SV Name": "SV_ROUTE_1",
                "TM Name": "Manager Maria",
            },
            {
                "Ship To": "850000002",
                "Cluster BU": "VOLGA",
                "Cluster SG": "KAZAN",
                "Chain": "CHAIN",
                "Агентство": "OPEN",
                "SV Name": "SV_ROUTE_1",
                "TM Name": pd.NA,
            },
            {
                "Ship To": "850000003",
                "Cluster BU": "VOLGA",
                "Cluster SG": "KAZAN",
                "Chain": "CHAIN",
                "Агентство": "OPEN",
                "SV Name": pd.NA,
                "TM Name": pd.NA,
            },
        ]
    )
    raw.to_excel(source_dir / "current.xlsx", index=False)
    dim = pd.DataFrame(
        [
            {
                "ID сотрудника": "TM1",
                "ФИО": "Manager Maria Full",
                "Атрибут": pd.NA,
                "ID руководителя": pd.NA,
                "Активен": True,
            },
            {
                "ID сотрудника": "SV1",
                "ФИО": "Supervisor One",
                "Атрибут": "SV_ROUTE_1",
                "ID руководителя": "TM1",
                "Активен": True,
            },
        ]
    )

    mapping, audit = load_current_tm_assignments(source_dir, dim)
    direct = mapping[mapping["ТТ"].eq("850000001")].iloc[0]
    restored = mapping[mapping["ТТ"].eq("850000002")].iloc[0]
    unresolved = mapping[mapping["ТТ"].eq("850000003")].iloc[0]

    assert direct["Источник привязки ТМ"] == CURRENT_TT_SOURCE
    assert restored["Источник привязки ТМ"] == CURRENT_SV_SOURCE
    assert restored["Территориальный менеджер"] == "Manager Maria Full"
    assert restored["ID территориального менеджера"] == "TM1"
    assert unresolved["Источник привязки ТМ"] == UNRESOLVED_SOURCE
    assert pd.isna(unresolved["Территориальный менеджер"])
    assert audit["Количество"].sum() > 0


def test_loader_does_not_publish_inactive_tm_from_current_assignment(tmp_path: Path):
    source_dir = tmp_path / "Привязки ТМ"
    source_dir.mkdir()
    pd.DataFrame(
        [
            {
                "Ship To": "850000001",
                "Cluster BU": "WEST",
                "Агентство": "OPEN",
                "SV Name": "OLD_ROUTE",
                "TM Name": "Former Manager",
            }
        ]
    ).to_excel(source_dir / "current.xlsx", index=False)
    dim = pd.DataFrame(
        [
            {
                "ID сотрудника": "TM_OLD",
                "ФИО": "Former Manager Full",
                "Должность": "Tm",
                "Атрибут": pd.NA,
                "ID руководителя": pd.NA,
                "Активен": False,
            }
        ]
    )

    mapping, _ = load_current_tm_assignments(source_dir, dim)

    assert pd.isna(mapping.loc[0, "ID территориального менеджера"])
    assert pd.isna(mapping.loc[0, "Территориальный менеджер"])
    assert mapping.loc[0, "Источник привязки ТМ"] == UNRESOLVED_SOURCE
