from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "out"
REPORTS = ROOT / "reports"
METADATA = REPORTS / "powerbi_model_metadata"
PBIX = ROOT / "Логика молока - эффективность проекта.pbix"
TABLE_PROFILES = REPORTS / "powerbi_model_table_profiles.csv"


def _read_layout() -> dict:
    raw = zipfile.ZipFile(PBIX).read("Report/Layout")
    return json.loads(raw.decode("utf-16-le").lstrip("\ufeff"))


def _walk_json(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                return
            yield from _walk_json(parsed)


def _layout_usage(
    layout: dict,
    known_columns: set[tuple[str, str]],
    known_measures: set[tuple[str, str]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    used_columns: set[tuple[str, str]] = set()
    used_measures: set[tuple[str, str]] = set()
    known_artifacts = known_columns | known_measures
    tables = sorted({table for table, _ in known_artifacts}, key=len, reverse=True)
    fields_by_table = {
        table: sorted(
            (field for candidate_table, field in known_artifacts if candidate_table == table),
            key=len,
            reverse=True,
        )
        for table in tables
    }

    def register_text(value: str) -> None:
        for table in tables:
            marker = f"{table}."
            if marker not in value:
                continue
            for field in fields_by_table[table]:
                if f"{table}.{field}" not in value:
                    continue
                key = (table, field)
                if key in known_columns:
                    used_columns.add(key)
                if key in known_measures:
                    used_measures.add(key)

    def visit(node, inherited_aliases: dict[str, str] | None = None) -> None:
        aliases = dict(inherited_aliases or {})
        if isinstance(node, str):
            register_text(node)
            stripped = node.strip()
            if stripped.startswith(("{", "[")):
                try:
                    visit(json.loads(stripped), aliases)
                except (json.JSONDecodeError, TypeError):
                    pass
            return
        if isinstance(node, list):
            for child in node:
                visit(child, aliases)
            return
        if not isinstance(node, dict):
            return

        from_items = node.get("From")
        if isinstance(from_items, list):
            for item in from_items:
                if not isinstance(item, dict):
                    continue
                alias = item.get("Name")
                entity = item.get("Entity")
                if alias and entity:
                    aliases[str(alias)] = str(entity)

        for object_type, known, used in (
            ("Column", known_columns, used_columns),
            ("Measure", known_measures, used_measures),
        ):
            item = node.get(object_type)
            if not isinstance(item, dict):
                continue
            field = item.get("Property")
            expression = item.get("Expression", {})
            source_ref = expression.get("SourceRef", {}) if isinstance(expression, dict) else {}
            table = source_ref.get("Entity") or aliases.get(str(source_ref.get("Source", "")))
            key = (str(table), str(field))
            if table and field and key in known:
                used.add(key)

        for child in node.values():
            visit(child, aliases)

    visit(layout)
    return used_columns, used_measures


def _metadata() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tables = pd.read_csv(METADATA / "tables.csv")
    columns = pd.read_csv(METADATA / "columns.csv")
    measures = pd.read_csv(METADATA / "measures.csv")
    relationships = pd.read_csv(METADATA / "relationships.csv")
    table_names = tables.set_index("ID")["Name"].to_dict()
    columns["Таблица"] = columns["TableID"].map(table_names)
    columns["Поле"] = columns["ExplicitName"].combine_first(columns["InferredName"])
    measures["Таблица"] = measures["TableID"].map(table_names)
    return tables, columns, measures, relationships


def _relationship_fields(
    columns: pd.DataFrame, relationships: pd.DataFrame
) -> set[tuple[str, str]]:
    lookup = columns.set_index("ID")[["Таблица", "Поле"]].to_dict("index")
    result: set[tuple[str, str]] = set()
    for column_id in pd.concat(
        [relationships["FromColumnID"], relationships["ToColumnID"]], ignore_index=True
    ).dropna():
        row = lookup.get(int(column_id))
        if row:
            result.add((str(row["Таблица"]), str(row["Поле"])))
    return result


def _dependency_fields(
    used_measures: set[tuple[str, str]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    dependencies = pd.read_csv(METADATA / "calculation_dependencies.csv")
    columns: set[tuple[str, str]] = set()
    measures = set(used_measures)
    queue = list(used_measures)
    while queue:
        table, measure = queue.pop()
        source = dependencies[
            dependencies["OBJECT_TYPE"].astype("string").str.contains(
                "MEASURE", case=False, na=False
            )
            & dependencies["TABLE"].astype("string").eq(table).fillna(False)
            & dependencies["OBJECT"].astype("string").eq(measure).fillna(False)
        ]
        for _, row in source.iterrows():
            referenced_type = str(row.get("REFERENCED_OBJECT_TYPE", "")).upper()
            referenced_table = row.get("REFERENCED_TABLE")
            referenced_object = row.get("REFERENCED_OBJECT")
            if pd.isna(referenced_table) or pd.isna(referenced_object):
                continue
            key = (str(referenced_table), str(referenced_object))
            if "COLUMN" in referenced_type:
                columns.add(key)
            elif "MEASURE" in referenced_type and key not in measures:
                measures.add(key)
                queue.append(key)
    return columns, measures


def _parquet_tables() -> dict[str, Path]:
    return {path.stem: path for path in OUT.glob("*.parquet")}


def _storage_size_mb() -> pd.Series:
    segments_path = METADATA / "storage_column_segments.csv"
    dictionaries_path = METADATA / "storage_table_columns.csv"
    if not segments_path.exists() or not dictionaries_path.exists():
        return pd.Series(dtype="float64")
    segments = pd.read_csv(segments_path)
    dictionaries = pd.read_csv(dictionaries_path)
    segments["USED_SIZE"] = pd.to_numeric(segments["USED_SIZE"], errors="coerce").fillna(0)
    dictionaries["DICTIONARY_SIZE"] = pd.to_numeric(
        dictionaries["DICTIONARY_SIZE"], errors="coerce"
    ).fillna(0)
    segment_size = segments.groupby("MEASURE_GROUP_NAME")["USED_SIZE"].sum()
    dictionary_size = dictionaries.groupby("MEASURE_GROUP_NAME")["DICTIONARY_SIZE"].sum()
    return segment_size.add(dictionary_size, fill_value=0) / 1024 / 1024


def main() -> None:
    global METADATA, PBIX, TABLE_PROFILES
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbix", type=Path, default=PBIX)
    parser.add_argument("--metadata", type=Path, default=METADATA)
    parser.add_argument("--profiles", type=Path, default=TABLE_PROFILES)
    arguments = parser.parse_args()
    PBIX = arguments.pbix
    METADATA = arguments.metadata
    TABLE_PROFILES = arguments.profiles
    REPORTS.mkdir(parents=True, exist_ok=True)
    tables, model_columns, measures, relationships = _metadata()
    known_columns = set(
        zip(model_columns["Таблица"].dropna().astype(str), model_columns.loc[model_columns["Таблица"].notna(), "Поле"].astype(str))
    )
    known_measures = set(zip(measures["Таблица"].astype(str), measures["Name"].astype(str)))
    layout_used, layout_measures = _layout_usage(
        _read_layout(), known_columns, known_measures
    )
    missing_layout_columns = sorted(layout_used - known_columns)
    missing_layout_measures = sorted(layout_measures - known_measures)
    dependency_used, active_measures = _dependency_fields(layout_measures)
    relationship_used = _relationship_fields(model_columns, relationships)
    sort_lookup = model_columns.set_index("ID")[["Таблица", "Поле"]].to_dict("index")
    sort_used = {
        (str(sort_lookup[int(column_id)]["Таблица"]), str(sort_lookup[int(column_id)]["Поле"]))
        for column_id in model_columns["SortByColumnID"].dropna()
        if int(column_id) in sort_lookup
    }
    model_field_set = known_columns

    rows = []
    summary = []
    for table, path in sorted(_parquet_tables().items()):
        frame = pd.read_parquet(path)
        model_fields = {field for candidate_table, field in model_field_set if candidate_table == table}
        for field in frame.columns:
            key = (table, str(field))
            reasons = []
            if key in layout_used:
                reasons.append("визуал/фильтр PBIX")
            if key in dependency_used:
                reasons.append("мера/расчёт PBIX")
            if key in relationship_used:
                reasons.append("связь модели")
            if key in sort_used:
                reasons.append("сортировка модели")
            in_model = str(field) in model_fields
            if reasons:
                decision = "Оставить"
            elif in_model:
                decision = "Кандидат на удаление"
            else:
                decision = "Не загружено и не используется"
            rows.append(
                {
                    "Таблица": table,
                    "Поле": field,
                    "В модели PBIX": in_model,
                    "Используется в визуале": key in layout_used,
                    "Используется в мере": key in dependency_used,
                    "Используется в связи": key in relationship_used,
                    "Используется в сортировке": key in sort_used,
                    "Причина оставить": ", ".join(reasons),
                    "Решение": decision,
                }
            )
        missing = sorted(model_fields - set(map(str, frame.columns)))
        summary.append(
            {
                "Таблица": table,
                "Строк": len(frame),
                "Колонок parquet": len(frame.columns),
                "Колонок модели": len(model_fields),
                "Используется в визуалах": sum((table, field) in layout_used for field in model_fields),
                "Кандидатов на удаление": sum(
                    row["Таблица"] == table and row["Решение"] == "Кандидат на удаление"
                    for row in rows
                ),
                "Поля модели отсутствуют в parquet": ", ".join(missing),
            }
        )

    field_audit = pd.DataFrame(rows)
    summary_frame = pd.DataFrame(summary)
    measure_errors = measures[measures["ErrorMessage"].notna()][
        ["Таблица", "Name", "Expression", "ErrorMessage"]
    ].copy()
    measure_audit = measures[["Таблица", "Name", "Expression", "ErrorMessage"]].copy()
    measure_audit["Используется в PBIX"] = [
        (str(table), str(name)) in active_measures
        for table, name in zip(measure_audit["Таблица"], measure_audit["Name"])
    ]
    measure_audit["Решение"] = measure_audit["Используется в PBIX"].map(
        {True: "Оставить", False: "Удалить из модели"}
    )

    profiles = (
        pd.read_csv(TABLE_PROFILES)
        if TABLE_PROFILES.exists()
        else pd.DataFrame(columns=["Таблица", "Строк в модели"])
    )
    profile_rows = profiles.set_index("Таблица")["Строк в модели"].to_dict()
    storage_size = _storage_size_mb()
    visible_tables = tables[~tables["IsHidden"].fillna(False)].copy()
    table_audit_rows = []
    for table in visible_tables["Name"].astype(str):
        direct_columns = sum(candidate_table == table for candidate_table, _ in layout_used)
        measure_columns = sum(candidate_table == table for candidate_table, _ in dependency_used)
        active_table_measures = sum(candidate_table == table for candidate_table, _ in active_measures)
        is_used = bool(direct_columns or measure_columns or active_table_measures)
        if table == "_Меры":
            decision = "Оставить"
        elif is_used:
            decision = "Оставить"
        else:
            decision = "Отключить загрузку / удалить из модели"
        table_audit_rows.append(
            {
                "Таблица": table,
                "Строк в модели": profile_rows.get(table, pd.NA),
                "Размер модели, МБ": round(float(storage_size.get(table, 0)), 3),
                "Поля в визуалах": direct_columns,
                "Поля в используемых мерах": measure_columns,
                "Используемых мер таблицы": active_table_measures,
                "Решение": decision,
            }
        )
    table_audit = pd.DataFrame(table_audit_rows).sort_values(
        ["Решение", "Размер модели, МБ"], ascending=[True, False]
    )
    auto_date_tables = tables[
        tables["Name"].astype(str).str.startswith(("LocalDateTable_", "DateTableTemplate_"))
    ][["Name", "IsHidden"]].copy()
    auto_date_tables["Размер модели, МБ"] = auto_date_tables["Name"].map(storage_size).fillna(0).round(3)
    auto_date_tables["Решение"] = "Отключить Auto date/time"
    model_only = []
    parquet_map = {table: set(map(str, pd.read_parquet(path).columns)) for table, path in _parquet_tables().items()}
    for table, field in sorted(model_field_set):
        if table in parquet_map and field not in parquet_map[table]:
            model_only.append({"Таблица": table, "Поле модели без parquet": field})

    broken_visual_fields = pd.DataFrame(
        [
            {"Тип": "Столбец", "Таблица": table, "Поле": field}
            for table, field in missing_layout_columns
        ]
        + [
            {"Тип": "Мера", "Таблица": table, "Поле": field}
            for table, field in missing_layout_measures
        ]
    )

    output = REPORTS / "powerbi_vitrine_field_audit.xlsx"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary_frame.to_excel(writer, sheet_name="Сводка", index=False)
        field_audit.to_excel(writer, sheet_name="Поля витрин", index=False)
        pd.DataFrame(model_only).to_excel(writer, sheet_name="Модель без parquet", index=False)
        broken_visual_fields.to_excel(writer, sheet_name="Ошибки визуалов", index=False)
        measure_audit.to_excel(writer, sheet_name="Все меры", index=False)
        measure_errors.to_excel(writer, sheet_name="Ошибки мер", index=False)
        table_audit.to_excel(writer, sheet_name="Таблицы модели", index=False)
        auto_date_tables.to_excel(writer, sheet_name="Auto date time", index=False)
    print(f"Сохранено: {output}")
    print(summary_frame.to_string(index=False))
    if not measure_errors.empty:
        print("\nОшибки мер:")
        print(measure_errors[["Таблица", "Name", "ErrorMessage"]].to_string(index=False))
    if not broken_visual_fields.empty:
        print("\nПоля визуалов, отсутствующие в модели:")
        print(broken_visual_fields.to_string(index=False))
    unload = table_audit[table_audit["Решение"].str.startswith("Отключить")]
    if not unload.empty:
        print(
            "\nКандидаты на отключение загрузки: "
            f"{len(unload)} таблиц, {unload['Размер модели, МБ'].sum():.1f} МБ"
        )


if __name__ == "__main__":
    main()
