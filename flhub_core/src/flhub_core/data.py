"""Datasets, site data and data variants.

Stage 1 (ADR-001): PneumoniaMNIST, 6 IID partitions, one CSV per site (`label,x0..x783`, integer
pixels 0-255), materialized as `<dir>/<site>.csv`. Stage 2 (ADR-009) adds OrganAMNIST and the
partitions of flhub_core.partition. A *data variant* is `<dataset>-<pid>` (e.g. `organamnist-dir0.3`);
a site holds it as `<dir>/<variant>-<site>.csv`, in the same CSV layout. Each npz is downloaded once
from Zenodo (no login) and checked against a pinned sha256; test and validation splits stay on the
central VM.

CIFAR-10 (32x32 RGB, the dataset of NVFlare's hello-pt example) is offered as data variants
`cifar10-iid` and `cifar10-dir0.3` over the same six sites: the same CSV layout with 3072 pixel
columns (`label,x0..x3071`, channel-first R, G, B planes as in the official batches). It is
downloaded from the official python tarball (sha256-pinned) and is not part of `--variants all`.

load_site(path) returns a lazy SiteSource. train() resolves it with train_config["data_variant"]:
no variant -> the path itself (a `<site>.csv` file) or `pneumoniamnist-iid` (a directory); variant v
-> `<dir>/<v>-<site>.csv` next to a `<site>.csv` path, or the one `<v>-*.csv` in a directory.
load_site(DataFrame) returns concrete SiteData, used as given (a framework that selects data
itself, e.g. a vantage6 labelled database).

CLI (deploy scripts, data provisioning):
    python -m flhub_core.data materialize --sites nvf-h1 nvf-h2 [--out ~/flhub-data]       (stage 1)
    python -m flhub_core.data materialize --variants all --sites nvf-h1 nvf-h2 [--out DIR]
           [--manifests configs/stage2/partitions]     (stage 2; verified against the manifests)
    python -m flhub_core.data manifests --out configs/stage2/partitions                      (central)
    python -m flhub_core.data manifests --variants cifar10-iid --out configs/stage2/partitions
    python -m flhub_core.data info [--variant organamnist-dir0.3]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pickle
import re
import shutil
import tarfile
import tempfile
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import partition as P

DATASET = "pneumoniamnist"
DATASET_URL = "https://zenodo.org/records/10519652/files/pneumoniamnist.npz?download=1"
DATASET_SHA256 = "e1792d3f03751cb101e99f19a63b3c1941436c988665f47853417b05be250cd8"
IMAGE_SHAPE = (1, 28, 28)
NUM_FEATURES = 28 * 28
NUM_CLASSES = 2
CLASS_NAMES = ("normal", "pneumonia")
COLUMNS = ["label"] + [f"x{i}" for i in range(NUM_FEATURES)]
MAX_LABEL = 255

PARTITION_SEED = 2026
NUM_PARTITIONS = 6
SITES = {"nvf-h1": 0, "nvf-h2": 1, "v6-h3": 2, "v6-h4": 3, "flw-h5": 4, "flw-h6": 5}

# sha256 of each materialized stage-1 site CSV; materialize() refuses to write anything else.
PARTITION_SHA256 = {
    "nvf-h1": "9956007981524bd843c6bcd8c6424b6a3893e6216c82623c627346cbbbd071fe",
    "nvf-h2": "728c1ae41186210e6eb54a3fdf59d30e4705c4292140de15924e5a497aa36f22",
    "v6-h3": "93e2067524e750c9d728690c56b267d938472c5ccecfaa49146289d93c27cce0",
    "v6-h4": "3d14269d3abd1ff4b84183c58b241c0b33fa4e8e8ef25f4f34e077ede0798df3",
    "flw-h5": "44dc0771ad147dfcd346f28abf62e23799de61fd2d672a8c59265245181b7da5",
    "flw-h6": "9b4ba56df945e3fc41bcb53f6ad80498c563e76757ac98faa071d01c5206083a",
}


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    url: str  # public copy (official splits); "" = hospital-owned data: no manifest, no hub-side evaluation
    sha256: str
    num_classes: int
    class_names: tuple[str, ...]
    pids: tuple[str, ...]  # partitions offered as data variants
    image_shape: tuple[int, int, int] = IMAGE_SHAPE  # (channels, height, width)
    file_name: str | None = None  # the downloaded file in the cache (default <name>.npz)
    splits: tuple[str, ...] = ("train", "val", "test")
    in_all: bool = True  # part of `materialize --variants all` and `manifests` (the stage-2 variants)


DATASETS = {
    "pneumoniamnist": DatasetInfo(DATASET, DATASET_URL, DATASET_SHA256, NUM_CLASSES, CLASS_NAMES, ("iid",)),
    # OrganAMNIST (MedMNIST v2, 28 px; Zenodo md5 68e3f8846a6bd62f0c9bf841c0d9eacc), ADR-009
    "organamnist": DatasetInfo(
        "organamnist", "https://zenodo.org/records/10519652/files/organamnist.npz?download=1",
        "7c8c8b0753e2c0d7f70928817eac02be7cbb09f6beff4346e9d0ea79ecb598bd", 11,
        ("bladder", "femur-left", "femur-right", "heart", "kidney-left", "kidney-right", "liver", "lung-left",
         "lung-right", "pancreas", "spleen"), P.PIDS),
    # CIFAR-10 (Krizhevsky 2009), the data of NVFlare's hello-pt example: the official python tarball,
    # sha256 as pinned by Keras (torchvision pins md5 c58f30108f718f92721af3b95e74349a). No val split.
    "cifar10": DatasetInfo(
        "cifar10", "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz",
        "6d958be074577803d12ecdefd02955f39262c83c16fe9348329d7fe0b5c001ce", 10,
        ("airplane", "automobile", "bird", "cat", "deer", "dog", "frog", "horse", "ship", "truck"),
        ("iid", "dir0.3"), image_shape=(3, 32, 32), file_name="cifar-10-python.tar.gz",
        splits=("train", "test"), in_all=False),
}
DEFAULT_VARIANT = "pneumoniamnist-iid"  # = the stage-1 partitions (<site>.csv)
# pixel columns per row -> image shape; a site file's width tells which kind of image it holds
SHAPE_BY_FEATURES = {int(np.prod(i.image_shape)): tuple(i.image_shape) for i in DATASETS.values()}


def columns(num_features: int = NUM_FEATURES) -> list[str]:
    """CSV header of a site file with `num_features` pixel columns: label,x0..x<n-1>."""
    return ["label"] + [f"x{i}" for i in range(num_features)]


def image_shape(num_features: int) -> tuple[int, int, int]:
    if num_features not in SHAPE_BY_FEATURES:
        raise ValueError(f"{num_features} pixel columns match no dataset; known: "
                         + ", ".join(f"{n} ({'x'.join(map(str, s))})" for n, s in SHAPE_BY_FEATURES.items()))
    return SHAPE_BY_FEATURES[num_features]


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


def dataset_info(name: str) -> DatasetInfo:
    if name not in DATASETS:
        raise KeyError(f"unknown dataset {name!r}; known: {sorted(DATASETS)}")
    return DATASETS[name]


def download(cache_dir: str | Path | None = None, url: str | None = None, dataset: str = DATASET) -> Path:
    """Path of the verified dataset file (npz, or the CIFAR-10 tarball); downloads it if missing or corrupt.

    A mirror can be named with $FLHUB_<DATASET>_URL (e.g. FLHUB_CIFAR10_URL), or the file can be put
    into the cache by hand: either way it must have the pinned sha256.
    """
    info = dataset_info(dataset)
    cache = Path(cache_dir).expanduser() if cache_dir else data_dir() / "raw"
    path = cache / (info.file_name or f"{info.name}.npz")
    if path.exists() and _sha256_file(path) == info.sha256:
        return path
    cache.mkdir(parents=True, exist_ok=True)
    url = (url or os.environ.get(f"FLHUB_{info.name.upper()}_URL")
           or (os.environ.get("FLHUB_DATASET_URL") if dataset == DATASET else None) or info.url)
    last_error: Exception | None = None
    for attempt in range(4):
        tmp = None
        try:
            with urllib.request.urlopen(url, timeout=300) as resp, tempfile.NamedTemporaryFile(
                dir=cache, delete=False
            ) as out:
                tmp = out.name
                shutil.copyfileobj(resp, out)
            got = _sha256_file(tmp)
            if got != info.sha256:
                raise ValueError(f"sha256 mismatch for {url}: got {got}, want {info.sha256}")
            os.replace(tmp, path)
            return path
        except Exception as exc:  # retried, then re-raised below
            last_error = exc
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            time.sleep(2**attempt)
    raise RuntimeError(f"could not download {info.name} from {url}: {last_error}")


def load_split(split: str, cache_dir: str | Path | None = None,
               dataset: str = DATASET) -> tuple[np.ndarray, np.ndarray]:
    """Official split `train|val|test` as (pixels uint8 (n, C*H*W), labels int64 (n,))."""
    info = dataset_info(dataset)
    if split not in info.splits:
        raise KeyError(f"{dataset} has no {split} split; splits: {list(info.splits)}")
    path = download(cache_dir, dataset=dataset)
    if path.name.endswith(".tar.gz"):
        x, y = _cifar_split(path, split)
    else:
        with np.load(path) as d:
            x = d[f"{split}_images"]
            y = d[f"{split}_labels"]
    return x.reshape(len(x), -1).astype(np.uint8), y.reshape(-1).astype(np.int64)


class _BatchUnpickler(pickle.Unpickler):
    """Loads only what a CIFAR batch file holds: a dict of bytes, lists and numpy arrays."""

    _ALLOWED = {("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
                ("numpy", "ndarray"), ("numpy", "dtype"), ("_codecs", "encode")}  # encode: bytes in protocol 2

    def find_class(self, module: str, name: str):
        if (module, name) not in self._ALLOWED:
            raise pickle.UnpicklingError(f"unexpected object {module}.{name} in a CIFAR batch file")
        if hasattr(np, "_core"):  # numpy 2 moved numpy.core to numpy._core (the old name warns)
            module = module.replace("numpy.core.", "numpy._core.")
        return super().find_class(module, name)


_CIFAR_CACHE: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}


def _cifar_split(path: Path, split: str) -> tuple[np.ndarray, np.ndarray]:
    """(pixels uint8 (n, 3072), labels) of the official CIFAR python tarball, batches in file order.

    Each row holds the R, G and B planes of a 32x32 image (channel-first, row-major). The tarball is
    sha256-verified by download() before it is read; the unpickler admits only numpy arrays.
    """
    key = (str(path), split)
    if key not in _CIFAR_CACHE:
        names = [f"data_batch_{i}" for i in range(1, 6)] if split == "train" else ["test_batch"]
        xs, ys = [], []
        with tarfile.open(path, "r:gz") as tar:
            members = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
            for name in names:
                f = tar.extractfile(members[name])
                batch = _BatchUnpickler(f, encoding="bytes").load()
                xs.append(np.asarray(batch[b"data"], dtype=np.uint8).reshape(-1, 3 * 32 * 32))
                ys.append(np.asarray(batch[b"labels"], dtype=np.int64))
        x, y = np.concatenate(xs), np.concatenate(ys)
        x.flags.writeable = y.flags.writeable = False
        _CIFAR_CACHE[key] = (x, y)
    return _CIFAR_CACHE[key]


def partition_indices(n: int, num_partitions: int = NUM_PARTITIONS, seed: int = PARTITION_SEED) -> list[np.ndarray]:
    """Stage-1 IID split of range(n). Uses the legacy RandomState, whose stream numpy keeps frozen."""
    perm = np.random.RandomState(seed).permutation(n)
    return [np.sort(p) for p in np.array_split(perm, num_partitions)]


def partition(index: int, cache_dir: str | Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    pixels, labels = load_split("train", cache_dir)
    idx = partition_indices(len(labels))[index]
    return pixels[idx], labels[idx]


def to_csv_bytes(pixels: np.ndarray, labels: np.ndarray) -> bytes:
    lines = [",".join(columns(np.shape(pixels)[1]))]
    for label, row in zip(labels.tolist(), pixels.tolist()):
        lines.append(f"{label}," + ",".join(map(str, row)))
    return ("\n".join(lines) + "\n").encode("ascii")


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        out.write(payload)
    os.replace(out.name, path)


def materialize(site_id: str, out_dir: str | Path | None = None, cache_dir: str | Path | None = None) -> Path:
    """Write the site's stage-1 partition to `<out_dir>/<site_id>.csv` (verified, atomic)."""
    if site_id not in SITES:
        raise KeyError(f"unknown site {site_id!r}; known: {sorted(SITES)}")
    payload = to_csv_bytes(*partition(SITES[site_id], cache_dir))
    digest = hashlib.sha256(payload).hexdigest()
    expected = PARTITION_SHA256.get(site_id)
    if expected and digest != expected:
        raise ValueError(f"partition {site_id} has sha256 {digest}, expected {expected}")
    path = site_path(site_id, out_dir)
    _write_atomic(path, payload)
    return path


# ---- site data ------------------------------------------------------------------------------
@dataclass(frozen=True)
class SiteData:
    """Training or test data in model format."""

    x: np.ndarray  # float32 (n, C, H, W), pixel / 255: (n, 1, 28, 28) MedMNIST, (n, 3, 32, 32) CIFAR-10
    y: np.ndarray  # int64 (n,)
    fingerprint: str  # sha256 of the canonical uint8 pixels + int64 labels; seeds the data order

    def __len__(self) -> int:
        return int(self.y.shape[0])

    def resolve(self, variant: str | None = None) -> SiteData:
        """Concrete data is used as given, whatever the variant (the framework selected it)."""
        return self

    def label_hist(self, num_classes: int) -> np.ndarray:
        return np.bincount(self.y, minlength=num_classes)


def from_arrays(pixels: np.ndarray, labels: np.ndarray, num_classes: int | None = None) -> SiteData:
    """SiteData from (n, F) integer pixels and (n,) labels; labels checked against num_classes if given.

    F gives the image shape: 784 = 1x28x28 (MedMNIST), 3072 = 3x32x32 (CIFAR-10, channel-first).
    """
    pixels = np.asarray(pixels)
    labels = np.asarray(labels).reshape(-1)
    if pixels.ndim != 2 or pixels.shape[1] not in SHAPE_BY_FEATURES or pixels.shape[0] != labels.shape[0]:
        widths = " or ".join(str(n) for n in SHAPE_BY_FEATURES)
        raise ValueError(f"expected pixels (n, {widths}) and labels (n,), got {pixels.shape} {labels.shape}")
    if pixels.size and (pixels.min() < 0 or pixels.max() > 255):
        raise ValueError("pixel values must be integers in 0..255")
    top = MAX_LABEL if num_classes is None else num_classes - 1
    if labels.size and (labels.min() < 0 or labels.max() > top):
        raise ValueError(f"labels must be in 0..{top}")
    px = np.ascontiguousarray(pixels, dtype=np.uint8)
    lab = np.ascontiguousarray(labels, dtype="<i8")
    fingerprint = hashlib.sha256(px.tobytes() + lab.tobytes()).hexdigest()
    x = (px.astype(np.float32) / np.float32(255.0)).reshape((-1,) + image_shape(px.shape[1]))
    return SiteData(x=x, y=lab.astype(np.int64), fingerprint=fingerprint)


def read_csv(path: str | Path) -> SiteData:
    """Parse a site CSV (header label,x0..x783 or label,x0..x3071, integer values)."""
    path = Path(path).expanduser()
    with open(path, encoding="ascii") as f:
        header = f.readline().strip().split(",")
    if len(header) - 1 not in SHAPE_BY_FEATURES or header != columns(len(header) - 1):
        widths = " or ".join(f"x{n - 1}" for n in SHAPE_BY_FEATURES)
        raise ValueError(f"{path}: header must be label,x0..{widths}")
    arr = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    if arr.size == 0:
        arr = np.zeros((0, len(header)), np.int64)
    return from_arrays(arr[:, 1:], arr[:, 0])


def count_rows(path: Path) -> int:
    with open(path, "rb") as f:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))
    return max(lines - 1, 0)


def parse_variant(variant: str) -> tuple[str, str]:
    """`<dataset>-<pid>` -> (dataset, pid); the dataset must be known and offer the pid."""
    dataset, _, pid = variant.partition("-")
    info = dataset_info(dataset)
    if pid not in info.pids:
        raise KeyError(f"dataset {dataset} has no partition {pid!r}; known: {list(info.pids)}")
    return dataset, pid


def variant_file_name(variant: str, site: str) -> str:
    return f"{variant}-{site}.csv"


class SiteSource:
    """What load_site(path) returns: a site CSV or a directory of variant files, loaded lazily.

    Behaves like the SiteData of its default data (len, x, y, fingerprint); train() calls
    resolve(train_config data_variant). Parsed data is cached per variant (two entries).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._cache: OrderedDict[str, SiteData] = OrderedDict()

    def __repr__(self) -> str:
        return f"SiteSource({str(self.path)!r})"

    def file_for(self, variant: str | None) -> Path:
        if self.path.is_dir():
            variant = variant or DEFAULT_VARIANT
            pattern = f"{glob.escape(variant)}-*.csv"
            matches = sorted(self.path.glob(pattern))
            if len(matches) != 1:
                raise FileNotFoundError(f"data variant {variant}: {len(matches)} files match {self.path}/{pattern}"
                                        + ("" if matches else " (not provisioned)"))
            return matches[0]
        if not variant or self.path.name.startswith(f"{variant}-"):
            return self.path
        path = self.path.with_name(variant_file_name(variant, self.path.stem))
        if not path.exists():
            raise FileNotFoundError(f"data variant {variant} not provisioned: {path}")
        return path

    def resolve(self, variant: str | None = None) -> SiteData:
        key = variant or ""
        if key not in self._cache:
            self._cache[key] = read_csv(self.file_for(variant))
            while len(self._cache) > 2:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return self._cache[key]

    def __len__(self) -> int:
        try:
            return count_rows(self.file_for(None))
        except OSError:
            return 0

    @property
    def x(self) -> np.ndarray:
        return self.resolve().x

    @property
    def y(self) -> np.ndarray:
        return self.resolve().y

    @property
    def fingerprint(self) -> str:
        return self.resolve().fingerprint


def load_site(source) -> SiteData | SiteSource:
    """A pandas DataFrame (same columns) -> SiteData; a CSV file or a directory -> lazy SiteSource."""
    if hasattr(source, "columns"):  # DataFrame, e.g. handed over by a vantage6 node
        width = sum(1 for c in source.columns if re.fullmatch(r"x\d+", str(c)))
        cols = columns(width if width in SHAPE_BY_FEATURES else NUM_FEATURES)
        missing = [c for c in cols if c not in source.columns]
        if missing:
            raise ValueError(f"DataFrame lacks columns {missing[:5]}...")
        arr = source[cols].to_numpy(dtype=np.int64)
        return from_arrays(arr[:, 1:], arr[:, 0])
    return SiteSource(source)


def resolve_data(data, variant: str | None) -> SiteData:
    """The concrete SiteData that train() uses for `variant` (None = default data)."""
    if hasattr(data, "resolve"):
        return data.resolve(variant or None)
    raise TypeError(f"expected SiteData or SiteSource, got {type(data).__name__}")


def load_test(cache_dir: str | Path | None = None, dataset: str = DATASET) -> SiteData:
    return from_arrays(*load_split("test", cache_dir, dataset))


def load_val(cache_dir: str | Path | None = None, dataset: str = DATASET) -> SiteData:
    return from_arrays(*load_split("val", cache_dir, dataset))


def load_train_pooled(cache_dir: str | Path | None = None, dataset: str = DATASET) -> SiteData:
    """The whole official train split (= all partitions together), for the centralized baseline."""
    return from_arrays(*load_split("train", cache_dir, dataset))


# ---- data variants (stage 2) --------------------------------------------------------------------
def variant_sites(variant: str) -> list[str]:
    dataset, pid = parse_variant(variant)
    return list(SITES) if dataset == DATASET else P.sites_of(pid)


def variant_indices(variant: str, cache_dir: str | Path | None = None) -> dict[str, np.ndarray]:
    """{site: sorted indices into the official train split} of a data variant."""
    dataset, pid = parse_variant(variant)
    _, labels = load_split("train", cache_dir, dataset)
    if dataset == DATASET:  # the stage-1 partitions, unchanged
        parts = partition_indices(len(labels))
        return {s: parts[i] for s, i in SITES.items()}
    return P.make_partition(pid, labels, dataset_info(dataset).num_classes, PARTITION_SEED)


def variant_arrays(variant: str, cache_dir: str | Path | None = None) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    dataset, _ = parse_variant(variant)
    pixels, labels = load_split("train", cache_dir, dataset)
    return {s: (pixels[idx], labels[idx]) for s, idx in variant_indices(variant, cache_dir).items()}


def variant_site_data(variant: str, sites: list[str] | None = None,
                      cache_dir: str | Path | None = None) -> dict[str, SiteData]:
    """In-memory SiteData per site (hub side: same arrays as the materialized CSVs)."""
    arrays = variant_arrays(variant, cache_dir)
    return {s: from_arrays(*arrays[s]) for s in (sites or list(arrays))}


def all_variants() -> list[str]:
    """The stage-2 variants (`--variants all`); CIFAR-10's are named explicitly (cifar10-iid, ...)."""
    return [f"{d}-{p}" for d, info in DATASETS.items() if info.in_all for p in info.pids]


def manifest(variant: str, cache_dir: str | Path | None = None) -> dict:
    """Sizes, label histograms, CSV sha256 and data fingerprints of a variant (committed as JSON)."""
    dataset, pid = parse_variant(variant)
    info = dataset_info(dataset)
    sites = {}
    for site, (px, lab) in variant_arrays(variant, cache_dir).items():
        payload = to_csv_bytes(px, lab)
        sites[site] = {"n": int(len(lab)), "label_hist": np.bincount(lab, minlength=info.num_classes).tolist(),
                       "sha256": hashlib.sha256(payload).hexdigest(), "fingerprint": from_arrays(px, lab).fingerprint,
                       "file": variant_file_name(variant, site)}
    return {"variant": variant, "dataset": dataset, "pid": pid, "definition": P.DEFINITIONS.get(pid, "stage-1 IID"),
            "partition_seed": PARTITION_SEED, "dataset_sha256": info.sha256, "num_classes": info.num_classes,
            "class_names": list(info.class_names), "total": sum(s["n"] for s in sites.values()), "sites": sites}


def manifest_json(m: dict) -> str:
    """Compact, diff-friendly JSON: one line per top-level key and per site."""
    head = [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in m.items() if k != "sites"]
    sites = [f"  {json.dumps(s)}: {json.dumps(v)}" for s, v in m["sites"].items()]
    return "\n".join(["{", *head, ' "sites": {', ",\n".join(sites), " }", "}"]) + "\n"


def materialize_variant(variant: str, sites: list[str], out_dir: str | Path | None = None,
                        manifest_dir: str | Path | None = None, cache_dir: str | Path | None = None) -> list[Path]:
    """Write `<out_dir>/<variant>-<site>.csv` for the given sites; verified against the manifest if given."""
    expected = None
    if manifest_dir:
        mpath = Path(manifest_dir).expanduser() / f"{variant}.json"
        expected = json.loads(mpath.read_text())["sites"]
    arrays = variant_arrays(variant, cache_dir)
    out = Path(out_dir).expanduser() if out_dir else data_dir()
    paths = []
    for site in sites:
        if site not in arrays:
            raise KeyError(f"{variant} has no site {site!r}; sites: {list(arrays)}")
        payload = to_csv_bytes(*arrays[site])
        digest = hashlib.sha256(payload).hexdigest()
        if expected is not None and digest != expected[site]["sha256"]:
            raise ValueError(f"{variant}/{site}: sha256 {digest} != manifest {expected[site]['sha256']}")
        path = out / variant_file_name(variant, site)
        _write_atomic(path, payload)
        paths.append(path)
    return paths


def _main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m flhub_core.data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("materialize", help="write site CSVs")
    m.add_argument("--sites", nargs="+", default=None)
    m.add_argument("--variants", nargs="+", default=None,
                   help="data variants (<dataset>-<pid>) or 'all' (default: the stage-1 <site>.csv files)")
    m.add_argument("--out", default=None, help="output dir (default $FLHUB_DATA_DIR or ~/flhub-data)")
    m.add_argument("--manifests", default=None, help="verify against <dir>/<variant>.json")
    mf = sub.add_parser("manifests", help="write <out>/<variant>.json for every variant")
    mf.add_argument("--out", required=True)
    mf.add_argument("--variants", nargs="+", default=None, help="only these variants (default: all)")
    i = sub.add_parser("info", help="print dataset and partition summary")
    i.add_argument("--variant", default=DEFAULT_VARIANT)
    args = ap.parse_args(argv)
    if args.cmd == "materialize":
        if not args.variants:
            for site in args.sites or sorted(SITES, key=SITES.get):
                path = materialize(site, args.out)
                print(f"{site}\t{path}\tsha256:{_sha256_file(path)}")
            return
        variants = all_variants() if args.variants == ["all"] else args.variants
        for v in variants:
            sites = [s for s in (args.sites or variant_sites(v)) if s in variant_sites(v)]
            for path in materialize_variant(v, sites, args.out, args.manifests):
                print(f"{v}\t{path}\tsha256:{_sha256_file(path)}")
    elif args.cmd == "manifests":
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for v in args.variants or all_variants():
            (out / f"{v}.json").write_text(manifest_json(manifest(v)))
            print(f"{v}\t{out / (v + '.json')}")
    else:
        mf = manifest(args.variant)
        print(f"{mf['variant']}: {mf['definition']} (total {mf['total']})")
        for site, s in mf["sites"].items():
            print(f"{site}\tn={s['n']}\t{s['label_hist']}")


if __name__ == "__main__":
    _main()
