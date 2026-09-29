"""Download the public DWI, template and atlas files into a local cache."""

from __future__ import annotations

import time
from pathlib import Path

import requests

from .sources import all_files


def fetch(url: str, dest: Path, retries: int = 4) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries + 1):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            return dest
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt + 1))
    return dest


def download_all(data_dir: str | Path, subject: str = "01", session: str = "test",
                 verbose: bool = True) -> Path:
    data_dir = Path(data_dir)
    for rel, url in all_files(subject, session).items():
        dest = data_dir / rel
        existed = dest.exists()
        fetch(url, dest)
        if verbose:
            state = "cached" if existed else "downloaded"
            print(f"[{state}] {rel} ({dest.stat().st_size / 1e6:.1f} MB)")
    return data_dir
