import pandas as pd
from pathlib import Path
from scripts.utils import load_settings, load_columns_map, save_parquet


def parse_users() -> None:
    settings = load_settings()
    col_map = load_columns_map().get("users", {})
    folder = Path(settings["sources"]["users"]["folder"])
    output = settings["sources"]["users"]["output"]

    files = list(folder.glob("*.xlsx")) + list(folder.glob("*.csv"))
    if not files:
        print("  USERS: файлы не найдены, пропускаем")
        return

    frames = []
    for f in files:
        df = pd.read_excel(f) if f.suffix == ".xlsx" else pd.read_csv(f)
        frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    if col_map:
        result = result.rename(columns=col_map)

    save_parquet(result, output)
