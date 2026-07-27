import sys
import pandas as pd
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.builders.build_model_dimensions import build_model_dimensions
from scripts.builders.build_learning_monthly import build_learning_monthly
from scripts.builders.build_page1_monthly_snapshot import build_page1_monthly_snapshot
from scripts.utils import load_settings, save_parquet


def enrich_out_tables() -> None:
    settings = load_settings()
    out_dir = Path(settings["paths"]["out"])

    print("=== Обогащение parquet в data/out ===")
    for path in sorted(out_dir.glob("*.parquet")):
        if path.name in {"dRegion.parquet", "dMonth.parquet", "dQuarter.parquet", "learning_monthly.parquet", "page1_region_monthly_snapshot.parquet"}:
            continue
        print(f"  Обработка: {path.name}")
        df = pd.read_parquet(path)
        save_parquet(df, str(path))

    print("\n=== Сборка месячной витрины обучения ===")
    build_learning_monthly()

    print("\n=== Сборка snapshot первой страницы ===")
    build_page1_monthly_snapshot()

    print("\n=== Сборка размерностей модели ===")
    build_model_dimensions()


if __name__ == "__main__":
    enrich_out_tables()
