from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd


def source_set_digest(
    sources: Iterable[Path],
    root: Path,
    version: str,
    extra_key: str = "",
) -> str:
    digest = hashlib.sha256()
    digest.update(version.encode("utf-8"))
    digest.update(extra_key.encode("utf-8"))
    for source in sorted(sources):
        digest.update(source.relative_to(root).as_posix().encode("utf-8"))
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def load_or_build_parquet_cache(
    cache_dir: Path | None,
    cache_key: str,
    builder: Callable[[], pd.DataFrame],
) -> tuple[pd.DataFrame, bool]:
    if cache_dir is None:
        return builder(), False

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key}.parquet"
    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path), True
        except Exception:
            cache_path.unlink(missing_ok=True)

    frame = builder()
    temporary = cache_path.with_suffix(".tmp.parquet")
    try:
        frame.to_parquet(temporary, index=False, compression="snappy")
        temporary.replace(cache_path)
        for stale in cache_dir.glob("*.parquet"):
            if stale != cache_path:
                stale.unlink(missing_ok=True)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        print(f"  Кеш {cache_dir.name} не записан — {exc}")
    return frame, False


def load_or_build_parquet_bundle(
    cache_dir: Path | None,
    cache_key: str,
    names: Iterable[str],
    builder: Callable[[], dict[str, pd.DataFrame]],
) -> tuple[dict[str, pd.DataFrame], bool]:
    expected = tuple(names)
    if cache_dir is None:
        return builder(), False

    cache_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = cache_dir / cache_key
    paths = {name: bundle_dir / f"{name}.parquet" for name in expected}
    if bundle_dir.exists() and all(path.exists() for path in paths.values()):
        try:
            return {name: pd.read_parquet(path) for name, path in paths.items()}, True
        except Exception:
            shutil.rmtree(bundle_dir, ignore_errors=True)

    frames = builder()
    temporary_dir = cache_dir / f"{cache_key}.tmp"
    shutil.rmtree(temporary_dir, ignore_errors=True)
    try:
        temporary_dir.mkdir(parents=True)
        for name in expected:
            frames[name].to_parquet(
                temporary_dir / f"{name}.parquet",
                index=False,
                compression="snappy",
            )
        shutil.rmtree(bundle_dir, ignore_errors=True)
        temporary_dir.replace(bundle_dir)
        for stale in cache_dir.iterdir():
            if stale.is_dir() and stale != bundle_dir:
                shutil.rmtree(stale, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        print(f"  Кеш {cache_dir.name} не записан — {exc}")
    return frames, False
