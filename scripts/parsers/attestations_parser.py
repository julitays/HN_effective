import re

import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, save_parquet


STATUS_MAP = {
    "завершено успешно": "Завершено успешно",
    "завершено неуспешно": "Завершено неуспешно",
    "в процессе обучения": "В процессе обучения",
    "доступно": "Доступно",
}


def _norm_id(value) -> str | None:
    if pd.isna(value) or value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _parse_test_score(value) -> float | None:
    if pd.isna(value) or str(value).strip() in {"", "-", "nan"}:
        return None
    text = str(value)
    match = re.search(r"(\d+)[,.](\d+)%\s*$", text)
    if match:
        return round(float(f"{match.group(1)}.{match.group(2)}") / 100, 4)
    match = re.search(r"(\d+)%\s*$", text)
    if match:
        return round(float(match.group(1)) / 100, 4)
    return None


def _parse_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", format="mixed", dayfirst=True)


def _extract_period(value, fallback_name: str):
    text = f"{value or ''} {fallback_name or ''}"
    match = re.search(r"Q\s*([1-4])\s*([12]\d{3})", text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"([12]\d{3})\s*Q\s*([1-4])", text, flags=re.IGNORECASE)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
        else:
            return pd.NaT, pd.NA, pd.NA
    else:
        quarter = int(match.group(1))
        year = int(match.group(2))

    month = (quarter - 1) * 3 + 1
    quarter_start = pd.Timestamp(year=year, month=month, day=1)
    return quarter_start, year * 10 + quarter, f"Q{quarter} {year}"


def _full_name(raw: pd.DataFrame) -> pd.Series:
    parts = []
    for column in ["Фамилия", "Имя", "Отчество"]:
        if column in raw.columns:
            parts.append(raw[column].fillna("").astype(str).str.strip())
        else:
            parts.append(pd.Series([""] * len(raw), index=raw.index))
    return (parts[0] + " " + parts[1] + " " + parts[2]).str.replace(r"\s+", " ", regex=True).str.strip()


def parse_attestations() -> None:
    settings = load_settings()
    folder = Path(settings["sources"]["attestations"]["folder"])
    output = settings["sources"]["attestations"]["output"]

    files = list(folder.glob("*.xlsx")) + list(folder.glob("*.csv"))
    if not files:
        print("  АТТЕСТАЦИИ: файлы не найдены, пропускаем")
        return

    frames = []
    for f in files:
        try:
            raw = pd.read_excel(f, dtype=str) if f.suffix == ".xlsx" else pd.read_csv(f, dtype=str)
        except Exception as exc:
            print(f"  АТТЕСТАЦИИ: пропущен файл {f.name} — {exc.__class__.__name__}: {exc}")
            continue
        if raw.empty or "extId" not in raw.columns:
            continue

        result = pd.DataFrame(index=raw.index)
        result["ID сотрудника"] = raw["extId"].map(_norm_id)
        result["Сотрудник"] = _full_name(raw)
        result["Должность"] = raw.get("Название должности", pd.Series([""] * len(raw), index=raw.index)).fillna("").astype(str).str.strip()
        result["Регион"] = raw.get("Регион", pd.Series([pd.NA] * len(raw), index=raw.index))
        result["Город"] = raw.get("Город", pd.Series([pd.NA] * len(raw), index=raw.index))
        result["Код маршрута"] = raw.get("Атрибут", pd.Series([pd.NA] * len(raw), index=raw.index))
        result["Руководитель"] = raw.get("Руководитель", pd.Series([pd.NA] * len(raw), index=raw.index))
        result["Название обучения"] = raw.get("Название обучения", pd.Series([pd.NA] * len(raw), index=raw.index))
        result["Статус обучения"] = (
            raw.get("Статус обучения", pd.Series([""] * len(raw), index=raw.index))
            .fillna("")
            .astype(str)
            .str.strip()
        )
        result["Статус аттестации клиента"] = (
            result["Статус обучения"].str.lower().map(STATUS_MAP).fillna(result["Статус обучения"])
        )
        result["Прогресс аттестации клиента %"] = (
            pd.to_numeric(raw.get("Прогресс обучения, %", pd.Series([pd.NA] * len(raw), index=raw.index)), errors="coerce")
            / 100
        )
        result["Тест аттестации клиента % raw"] = raw.get(
            "Результат тестирования",
            pd.Series([pd.NA] * len(raw), index=raw.index),
        ).map(_parse_test_score)
        result["Дата заявки"] = _parse_date_series(
            raw.get("Дата заявки", pd.Series([pd.NaT] * len(raw), index=raw.index))
        )
        result["Дата начала"] = _parse_date_series(
            raw.get("Начало обучения", pd.Series([pd.NaT] * len(raw), index=raw.index))
        )
        result["Дата завершения"] = _parse_date_series(
            raw.get("Завершение обучения", pd.Series([pd.NaT] * len(raw), index=raw.index))
        )
        result["Файл источник"] = f.name

        period_values = result["Название обучения"].map(lambda value: _extract_period(value, f.stem))
        result["QuarterStart"] = [item[0] for item in period_values]
        result["YearQuarter"] = [item[1] for item in period_values]
        result["QuarterLabel"] = [item[2] for item in period_values]

        completed = result["Статус аттестации клиента"].isin(["Завершено успешно", "Завершено неуспешно"])
        attempted = result["Прогресс аттестации клиента %"].fillna(0).gt(0)
        valid_score = result["Тест аттестации клиента % raw"].notna() & result["Тест аттестации клиента % raw"].gt(0)
        valid_result = completed & attempted & valid_score
        result["Аттестация клиента %"] = result["Тест аттестации клиента % raw"].where(valid_result)
        result["Есть результат аттестации клиента"] = valid_result
        result["Пройдена аттестация клиента"] = result["Статус аттестации клиента"].eq("Завершено успешно") & valid_result
        result["Уровень сотрудника"] = result["Должность"].str.lower().map(
            lambda value: "СВ" if "супервайзер" in value else "ТМ" if value == "tm" else "МЕ" if "мерч" in value else "Другое"
        )
        frames.append(result)

    if not frames:
        print("  АТТЕСТАЦИИ: в файлах нет пригодных строк")
        return

    result = pd.concat(frames, ignore_index=True)
    result = result.dropna(subset=["ID сотрудника", "QuarterStart"])

    numeric_columns = [
        "YearQuarter",
        "Прогресс аттестации клиента %",
        "Тест аттестации клиента % raw",
        "Аттестация клиента %",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    save_parquet(result, output)
    print(f"  АТТЕСТАЦИИ: {len(result)} строк, {result['ID сотрудника'].nunique()} сотрудников")
