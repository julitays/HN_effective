import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, load_columns_map, save_parquet


def parse_attestations() -> None:
    settings = load_settings()
    col_map = load_columns_map().get("attestations", {})
    folder = Path(settings["sources"]["attestations"]["folder"])
    output = settings["sources"]["attestations"]["output"]

    files = list(folder.glob("*.xlsx")) + list(folder.glob("*.csv"))
    if not files:
        print("  АТТЕСТАЦИИ: файлы не найдены, пропускаем")
        return

    frames = []
    for f in files:
        df = pd.read_excel(f) if f.suffix == ".xlsx" else pd.read_csv(f)
        frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    if col_map:
        result = result.rename(columns=col_map)

    save_parquet(result, output)
