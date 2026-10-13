"""Dataset: PneumoniaMNIST (MedMNIST v2), 28x28 grayscale chest X-rays, normal vs pneumonia.

See docs/DECISIONS.md ADR-001. The official npz is downloaded once from Zenodo (no login)
and checked against a pinned sha256. The official train split is cut into 6 IID partitions
with a fixed seed and written as one CSV per site: header `label,x0..x783`, integer pixels 0-255.
The test split stays on the central VM for global evaluation.

CLI (used by the deploy scripts):
    python -m flhub_core.data materialize --sites nvf-h1 nvf-h2 [--out ~/flhub-data]
    python -m flhub_core.data info
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DATASET = "pneumoniamnist"
DATASET_URL = "https://zenodo.org/records/10519652/files/pneumoniamnist.npz?download=1"
DATASET_SHA256 = "e1792d3f03751cb101e99f19a63b3c1941436c988665f47853417b05be250cd8"
IMAGE_SHAPE = (1, 28, 28)
NUM_FEATURES = 28 * 28
NUM_CLASSES = 2
CLASS_NAMES = ("normal", "pneumonia")
COLUMNS = ["label"] + [f"x{i}" for i in range(NUM_FEATURES)]

PARTITION_SEED = 2026
NUM_PARTITIONS = 6
SITES = {"nvf-h1": 0, "nvf-h2": 1, "v6-h3": 2, "v6-h4": 3, "flw-h5": 4, "flw-h6": 5}

# sha256 of each materialized site CSV; materialize() refuses to write anything else.
PARTITION_SHA256 = {
    "nvf-h1": "9956007981524bd843c6bcd8c6424b6a3893e6216c82623c627346cbbbd071fe",
    "nvf-h2": "728c1ae41186210e6eb54a3fdf59d30e4705c4292140de15924e5a497aa36f22",
    "v6-h3": "93e2067524e750c9d728690c56b267d938472c5ccecfaa49146289d93c27cce0",
    "v6-h4": "3d14269d3abd1ff4b84183c58b241c0b33fa4e8e8ef25f4f34e077ede0798df3",
    "flw-h5": "44dc0771ad147dfcd346f28abf62e23799de61fd2d672a8c59265245181b7da5",
    "flw-h6": "9b4ba56df945e3fc41bcb53f6ad80498c563e76757ac98faa071d01c5206083a",
}


def data_dir() -> Path:
    """Root for downloaded and materialized data (`$FLHUB_DATA_DIR`, default `~/flhub-data`)."""
    return Path(os.environ.get("FLHUB_DATA_DIR", str(Path.home() / "flhub-data"))).expanduser()


def site_path(site_id: str, out_dir: str | Path | None = None) -> Path:
    return (Path(out_dir).expanduser() if out_dir else data_dir()) / f"{site_id}.csv"


def _sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(cache_dir: str | Path | None = None, url: str | None = None) -> Path:
    """Path of the verified npz; downloads it if missing or corrupt."""
    cache = Path(cache_dir).expanduser() if cache_dir else data_dir() / "raw"
    path = cache / f"{DATASET}.npz"
    if path.exists() and _sha256_file(path) == DATASET_SHA256:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    url = url or os.environ.get("FLHUB_DATASET_URL", DATASET_URL)
    last_error: Exception | None = None
    for attempt in range(4):
        tmp = None
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, tempfile.NamedTemporaryFile(
                dir=cache, delete=False
            ) as out:
                tmp = out.name
                shutil.copyfileobj(resp, out)
            got = _sha256_file(tmp)
            if got != DATASET_SHA256:
                raise ValueError(f"sha256 mismatch for {url}: got {got}, want {DATASET_SHA256}")
            os.replace(tmp, path)
            return path
        except Exception as exc:  # retried, then re-raised below
            last_error = exc
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            time.sleep(2**attempt)
    raise RuntimeError(f"could not download {DATASET} from {url}: {last_error}")


def load_split(split: str, cache_dir: str | Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Official split `train|val|test` as (pixels uint8 (n, 784), labels int64 (n,))."""
    with np.load(download(cache_dir)) as d:
        x = d[f"{split}_images"]
        y = d[f"{split}_labels"]
    return x.reshape(len(x), -1).astype(np.uint8), y.reshape(-1).astype(np.int64)


def partition_indices(n: int, num_partitions: int = NUM_PARTITIONS, seed: int = PARTITION_SEED) -> list[np.ndarray]:
    """IID split of range(n). Uses the legacy RandomState, whose stream numpy keeps frozen."""
    perm = np.random.RandomState(seed).permutation(n)
    return [np.sort(p) for p in np.array_split(perm, num_partitions)]


def partition(index: int, cache_dir: str | Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    pixels, labels = load_split("train", cache_dir)
    idx = partition_indices(len(labels))[index]
    return pixels[idx], labels[idx]


def to_csv_bytes(pixels: np.ndarray, labels: np.ndarray) -> bytes:
    lines = [",".join(COLUMNS)]
    for label, row in zip(labels.tolist(), pixels.tolist()):
        lines.append(f"{label}," + ",".join(map(str, row)))
    return ("\n".join(lines) + "\n").encode("ascii")


def materialize(site_id: str, out_dir: str | Path | None = None, cache_dir: str | Path | None = None) -> Path:
    """Write the site's partition to `<out_dir>/<site_id>.csv` (verified, atomic)."""
    if site_id not in SITES:
        raise KeyError(f"unknown site {site_id!r}; known: {sorted(SITES)}")
    payload = to_csv_bytes(*partition(SITES[site_id], cache_dir))
    digest = hashlib.sha256(payload).hexdigest()
    expected = PARTITION_SHA256.get(site_id)
    if expected and digest != expected:
        raise ValueError(f"partition {site_id} has sha256 {digest}, expected {expected}")
    path = site_path(site_id, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write(payload)
    os.replace(out.name, path)
    return path


@dataclass(frozen=True)
class SiteData:
    """Training or test data in model format."""

    x: np.ndarray  # float32 (n, 1, 28, 28), pixel / 255
    y: np.ndarray  # int64 (n,)
    fingerprint: str  # sha256 of the canonical uint8 pixels + int64 labels; seeds the data order

    def __len__(self) -> int:
        return int(self.y.shape[0])


def from_arrays(pixels: np.ndarray, labels: np.ndarray) -> SiteData:
    pixels = np.asarray(pixels)
    labels = np.asarray(labels).reshape(-1)
    if pixels.ndim != 2 or pixels.shape[1] != NUM_FEATURES or pixels.shape[0] != labels.shape[0]:
        raise ValueError(f"expected pixels (n, {NUM_FEATURES}) and labels (n,), got {pixels.shape} {labels.shape}")
    if pixels.size and (pixels.min() < 0 or pixels.max() > 255):
        raise ValueError("pixel values must be integers in 0..255")
    if labels.size and (labels.min() < 0 or labels.max() >= NUM_CLASSES):
        raise ValueError(f"labels must be in 0..{NUM_CLASSES - 1}")
    px = np.ascontiguousarray(pixels, dtype=np.uint8)
    lab = np.ascontiguousarray(labels, dtype="<i8")
    fingerprint = hashlib.sha256(px.tobytes() + lab.tobytes()).hexdigest()
    x = (px.astype(np.float32) / np.float32(255.0)).reshape((-1,) + IMAGE_SHAPE)
    return SiteData(x=x, y=lab.astype(np.int64), fingerprint=fingerprint)


def load_site(source) -> SiteData:
    """Site data from a CSV path or from a pandas DataFrame with the same columns."""
    if hasattr(source, "columns"):  # DataFrame, e.g. handed over by a vantage6 node
        missing = [c for c in COLUMNS if c not in source.columns]
        if missing:
            raise ValueError(f"DataFrame lacks columns {missing[:5]}...")
        arr = source[COLUMNS].to_numpy(dtype=np.int64)
    else:
        path = Path(source).expanduser()
        with open(path, encoding="ascii") as f:
            header = f.readline().strip().split(",")
        if header != COLUMNS:
            raise ValueError(f"{path}: header must be label,x0..x{NUM_FEATURES - 1}")
        arr = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    return from_arrays(arr[:, 1:], arr[:, 0])


def load_test(cache_dir: str | Path | None = None) -> SiteData:
    return from_arrays(*load_split("test", cache_dir))


def load_train_pooled(cache_dir: str | Path | None = None) -> SiteData:
    """All 6 partitions together (= the official train split), for the centralized baseline."""
    return from_arrays(*load_split("train", cache_dir))


def _main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m flhub_core.data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("materialize", help="write site CSVs")
    m.add_argument("--sites", nargs="+", default=sorted(SITES, key=SITES.get))
    m.add_argument("--out", default=None, help="output dir (default $FLHUB_DATA_DIR or ~/flhub-data)")
    sub.add_parser("info", help="print dataset and partition summary")
    args = ap.parse_args(argv)
    if args.cmd == "materialize":
        for site in args.sites:
            path = materialize(site, args.out)
            print(f"{site}\t{path}\tsha256:{_sha256_file(path)}")
    else:
        print(f"dataset {DATASET} sha256:{DATASET_SHA256}")
        _, labels = load_split("train")
        for site, i in sorted(SITES.items(), key=lambda kv: kv[1]):
            part = labels[partition_indices(len(labels))[i]]
            print(f"{site}\tpartition {i}\tn={len(part)}\tpositives={int(part.sum())}")
        test = load_test()
        print(f"test\tn={len(test)}\tpositives={int(test.y.sum())}")


if __name__ == "__main__":
    _main()
