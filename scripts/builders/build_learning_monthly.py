import sys
from pathlib import Path

import pandas as pd

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.utils import enrich_for_output, load_settings, save_parquet


def build_learning_monthly() -> pd.DataFrame:
    settings = load_settings()
    learn_path = Path(settings["sources"]["learning"]["output"])
    if not learn_path.exists():
        print("  Learning monthly: источник learning_fact не найден")
        return pd.DataFrame()

    learn = pd.read_parquet(learn_path)
    learn = enrich_for_output(learn, output_path=str(learn_path))

    if "StartMonth" not in learn.columns:
        print("  Learning monthly: в learning_fact нет StartMonth")
        return pd.DataFrame()

    mandatory = learn[learn["Обязательный"] == True].copy()
    mandatory = mandatory[mandatory["StartMonth"].notna()].copy()
    mandatory = mandatory[mandatory["Регион BI"].notna()].copy()
    mandatory["Пройдено числом"] = mandatory["Пройдено"].eq(True).astype(int)

    monthly = (
        mandatory
        .groupby(["StartMonth", "StartYearMonth", "Регион BI", "Группа региона"], dropna=False)
        .agg(
            **{
                "Назначено обязательных курсов": ("ID сотрудника", "size"),
                "Пройдено обязательных курсов": ("Пройдено числом", "sum"),
                "Сотрудников с курсами": ("ID сотрудника", "nunique"),
            }
        )
        .reset_index()
    )

    monthly = monthly.rename(columns={
        "StartMonth": "MonthStart",
        "StartYearMonth": "YearMonth",
    })

    monthly["Обязательное обучение %"] = (
        monthly["Пройдено обязательных курсов"] / monthly["Назначено обязательных курсов"]
    ).round(4)

    output = learn_path.parent / "learning_monthly.parquet"
    save_parquet(monthly, str(output))

    print(f"\n  Learning monthly: {len(monthly)} строк")
    print(
        "  Периоды: "
        f"{monthly['MonthStart'].min():%Y-%m} -> {monthly['MonthStart'].max():%Y-%m}"
        if not monthly.empty else "  Периоды: нет данных"
    )
    return monthly


if __name__ == "__main__":
    build_learning_monthly()
