from pathlib import Path

import pandas as pd
import pytest

from scripts.production import (
    etl_lock,
    prune_output_columns,
    prune_output_tables,
    publish_staging,
    validate_freshness,
)
from scripts.utils import load_settings, save_parquet


def test_output_override_rewrites_all_configured_outputs(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HN_OUT_DIR", str(tmp_path / "staging"))

    settings = load_settings()

    assert Path(settings["paths"]["out"]) == tmp_path / "staging"
    assert Path(settings["sources"]["users"]["output"]).parent == tmp_path / "staging"
    assert Path(settings["sources"]["teams"]["output"]).parent == tmp_path / "staging"


def test_save_parquet_replaces_file_without_temporary_tail(tmp_path: Path):
    output = tmp_path / "sample.parquet"
    save_parquet(pd.DataFrame({"value": [1]}), str(output))
    save_parquet(pd.DataFrame({"value": [2, 3]}), str(output))

    assert pd.read_parquet(output)["value"].tolist() == [2, 3]
    assert list(tmp_path.glob("*.tmp")) == []


def test_lock_rejects_parallel_run(tmp_path: Path):
    lock_path = tmp_path / ".etl.lock"
    with etl_lock(lock_path):
        with pytest.raises(RuntimeError, match="ETL уже запущен"):
            with etl_lock(lock_path):
                pass
    assert not lock_path.exists()


def test_freshness_rejects_stale_table(tmp_path: Path):
    save_parquet(
        pd.DataFrame({"YearMonth": [202606], "value": [1]}),
        str(tmp_path / "table.parquet"),
    )

    with pytest.raises(RuntimeError, match="ожидался минимум 202607"):
        validate_freshness(
            tmp_path,
            ["table.parquet"],
            expected_yearmonth=202607,
            as_of_date=pd.Timestamp("2026-08-25"),
        )


def test_publish_staging_replaces_complete_file_set(tmp_path: Path):
    target = tmp_path / "out"
    staging = tmp_path / ".etl_staging" / "run" / "out"
    target.mkdir()
    staging.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    (staging / "new.txt").write_text("new", encoding="utf-8")

    publish_staging(staging, target, "run")

    assert not (target / "old.txt").exists()
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"


def test_publish_staging_rolls_back_after_replace_error(monkeypatch, tmp_path: Path):
    target = tmp_path / "out"
    staging = tmp_path / ".etl_staging" / "run" / "out"
    target.mkdir()
    staging.mkdir(parents=True)
    (target / "a.txt").write_text("original-a", encoding="utf-8")
    (target / "b.txt").write_text("original-b", encoding="utf-8")
    (staging / "a.txt").write_text("new-a", encoding="utf-8")
    (staging / "b.txt").write_text("new-b", encoding="utf-8")

    from scripts import production

    real_replace = production.os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if not failed and Path(source).name == "b.txt":
            failed = True
            raise PermissionError("locked")
        return real_replace(source, destination)

    monkeypatch.setattr(production.os, "replace", fail_once)
    with pytest.raises(PermissionError, match="locked"):
        publish_staging(staging, target, "run")

    assert (target / "a.txt").read_text(encoding="utf-8") == "original-a"
    assert (target / "b.txt").read_text(encoding="utf-8") == "original-b"


def test_publish_staging_stops_before_changes_when_target_is_locked(monkeypatch, tmp_path: Path):
    target = tmp_path / "out"
    staging = tmp_path / ".etl_staging" / "run" / "out"
    target.mkdir()
    staging.mkdir(parents=True)
    (target / "existing.txt").write_text("original", encoding="utf-8")
    (staging / "existing.txt").write_text("new", encoding="utf-8")

    from scripts import production

    def locked(_path):
        raise PermissionError("locked")

    monkeypatch.setattr(production, "_probe_exclusive_access", locked)
    with pytest.raises(RuntimeError, match="Публикация не начата"):
        publish_staging(staging, target, "run")

    assert (target / "existing.txt").read_text(encoding="utf-8") == "original"
    assert not (target / ".publishing").exists()
    assert not (tmp_path / ".etl_backup" / "run").exists()


def test_prune_output_tables_keeps_only_powerbi_contract(tmp_path: Path):
    (tmp_path / "page.parquet").touch()
    (tmp_path / "technical.parquet").touch()
    (tmp_path / "qa_vitrines_report.xlsx").touch()

    removed = prune_output_tables(tmp_path, ["page.parquet"])

    assert removed == ["technical.parquet"]
    assert (tmp_path / "page.parquet").exists()
    assert (tmp_path / "qa_vitrines_report.xlsx").exists()


def test_prune_output_tables_rejects_missing_contract_table(tmp_path: Path):
    with pytest.raises(RuntimeError, match="отсутствуют missing.parquet"):
        prune_output_tables(tmp_path, ["missing.parquet"])


def test_prune_output_columns_applies_powerbi_contract(tmp_path: Path):
    output = tmp_path / "page.parquet"
    save_parquet(
        pd.DataFrame({"used": [1, 2], "technical": [3, 4]}),
        str(output),
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        '{"columns": {"page": ["technical", "already absent"]}}',
        encoding="utf-8",
    )

    removed = prune_output_columns(tmp_path, plan)

    assert removed == {"page": 1}
    assert pd.read_parquet(output).columns.tolist() == ["used"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_prune_output_columns_rejects_empty_table(tmp_path: Path):
    output = tmp_path / "page.parquet"
    save_parquet(pd.DataFrame({"only": [1]}), str(output))
    plan = tmp_path / "plan.json"
    plan.write_text('{"columns": {"page": ["only"]}}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="удаляет все колонки"):
        prune_output_columns(tmp_path, plan)


def test_powerbi_column_contract_is_versioned_and_available():
    settings = load_settings()
    contract = Path(settings["reporting"]["powerbi_column_contract"])

    assert contract == Path("reports/powerbi_cleanup_plan.json")
    assert contract.exists()
