import yaml
import pandas as pd
from pathlib import Path


def load_settings(path: str = "config/settings.yml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_columns_map(path: str = "config/columns_map.yml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_parquet(df: pd.DataFrame, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    print(f"  Сохранено: {output_path} ({len(df)} строк)")
