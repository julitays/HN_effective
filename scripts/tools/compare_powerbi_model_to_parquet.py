from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "out"
PROFILE_PATH = ROOT / "reports" / "powerbi_model_table_profiles.csv"
REPORT_PATH = ROOT / "reports" / "powerbi_model_vs_parquet.xlsx"


def _normalize_month(value):
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return parsed.normalize() if pd.notna(parsed) else pd.NaT


def compare_powerbi_model_to_parquet() -> pd.DataFrame:
    model = pd.read_csv(PROFILE_PATH)
    rows = []
    for _, profile in model.iterrows():
        table = profile["Таблица"]
        path = OUT_DIR / f"{table}.parquet"
        if not path.exists():
            rows.append(
                {
                    "Таблица": table,
                    "Статус": "Нет parquet",
                    "Строк в модели": profile["Строк в модели"],
                    "Строк в parquet": pd.NA,
                    "Последний период модели": profile["Последний период модели"],
                    "Последний период parquet": pd.NaT,
                }
            )
            continue

        frame = pd.read_parquet(path)
        model_rows = int(profile["Строк в модели"])
        parquet_rows = len(frame)
        model_month = _normalize_month(profile["Последний период модели"])
        parquet_month = (
            pd.to_datetime(frame["MonthStart"], errors="coerce").max().normalize()
            if "MonthStart" in frame.columns and frame["MonthStart"].notna().any()
            else pd.NaT
        )
        row_match = model_rows == parquet_rows
        month_match = (pd.isna(model_month) and pd.isna(parquet_month)) or model_month == parquet_month
        rows.append(
            {
                "Таблица": table,
                "Статус": "Совпадает" if row_match and month_match else "Нужно обновить Power BI",
                "Строк в модели": model_rows,
                "Строк в parquet": parquet_rows,
                "Разница строк": parquet_rows - model_rows,
                "Последний период модели": model_month,
                "Последний период parquet": parquet_month,
            }
        )

    result = pd.DataFrame(rows).sort_values(["Статус", "Таблица"])
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_excel(REPORT_PATH, index=False)
    print(f"Сохранено: {REPORT_PATH}")
    print(result["Статус"].value_counts().to_string())
    return result


if __name__ == "__main__":
    compare_powerbi_model_to_parquet()
