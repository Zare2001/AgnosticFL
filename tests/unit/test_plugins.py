"""Plugins: own models and datasets in flhub_core/plugins/ (not in git), loaded like built-in ones."""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from flhub_core import data as D
from flhub_core import model as M

PKG = Path(M.__file__).resolve().parent
EXAMPLE = PKG / "plugins" / "_example_my_net.py"
WARDS = textwrap.dedent("""
    from flhub_core.data import DatasetInfo, register_dataset
    register_dataset(DatasetInfo("wards", "", "", 3, ("low", "medium", "high"), ("own",),
                                 image_shape=(1, 64, 64), splits=()))
""")


def test_register_model_and_dataset_checks(monkeypatch):
    monkeypatch.setattr(M, "_ZOO", dict(M._ZOO))
    monkeypatch.setattr(D, "DATASETS", dict(D.DATASETS))
    monkeypatch.setattr(D, "SHAPE_BY_FEATURES", dict(D.SHAPE_BY_FEATURES))
    M.register_model("tiny", M.Net)
    assert M.check_model("tiny") == "tiny"
    M.register_model("tiny", M.Net)  # the same class again: fine
    with pytest.raises(ValueError):
        M.register_model("tiny", M.CNNMedium)
    with pytest.raises(ValueError):
        M.register_model("payload-4", M.Net)
    info = D.DatasetInfo("scans", "", "", 2, ("a", "b"), ("own",), image_shape=(1, 64, 64), splits=())
    D.register_dataset(info)
    assert D.dataset_info("scans").in_all is False and D.image_shape(4096) == (1, 64, 64)
    assert "scans-own" not in D.all_variants()
    with pytest.raises(ValueError):
        D.register_dataset(D.DatasetInfo("bad-name", "", "", 2, ("a", "b"), ("own",)))
    with pytest.raises(ValueError):  # 784 pixel columns already mean 1x28x28
        D.register_dataset(D.DatasetInfo("odd", "", "", 2, ("a", "b"), ("own",), image_shape=(4, 14, 14)))


def run_py(src: Path, code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=src,
                         env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture
def pkg_copy(tmp_path):
    """A copy of the flhub_core package, as the frameworks ship it, to add plugin files to."""
    src = tmp_path / "src"
    shutil.copytree(PKG, src / "flhub_core", ignore=shutil.ignore_patterns("__pycache__"))
    for f in (src / "flhub_core" / "plugins").glob("[!_]*.py"):  # the developer's own plugins, if any
        f.unlink()
    return src


def test_plugins_load_ship_and_change_the_code_version(pkg_copy):
    plugins = pkg_copy / "flhub_core" / "plugins"
    before = run_py(pkg_copy, "import flhub_core; print(flhub_core.code_version())")
    shutil.copy(EXAMPLE, plugins / "my_net.py")
    (plugins / "wards.py").write_text(WARDS)
    (plugins / "broken.py").write_text("import not_a_real_module_xyz\n")
    out = run_py(pkg_copy, textwrap.dedent("""
        import flhub_core, torch
        from flhub_core import data as D, plugins as P
        from flhub_core.model import build_model, input_shape
        m = build_model("my-net", 10)
        print(tuple(m(torch.zeros(2, 3, 32, 32)).shape), input_shape("my-net"))
        print(D.dataset_info("wards").num_classes, D.image_shape(64 * 64), sorted(P.failed))
        print(flhub_core.code_version())
    """)).splitlines()
    assert out[0] == "(2, 10) (3, 32, 32)"
    assert out[1] == "3 (1, 64, 64) ['broken']"
    assert out[2] != before  # the hub's strict_code_version check covers the plugins


def test_dataset_plugins_load_without_torch(pkg_copy):
    """The hospitals' data tool runs with numpy only: dataset plugins load, model plugins are skipped."""
    plugins = pkg_copy / "flhub_core" / "plugins"
    shutil.copy(EXAMPLE, plugins / "my_net.py")
    (plugins / "wards.py").write_text(WARDS)
    out = run_py(pkg_copy, textwrap.dedent("""
        import sys
        sys.modules["torch"] = None  # no torch here
        from flhub_core import data as D, plugins as P
        print(D.parse_variant("wards-own"), sorted(P.failed))
    """))
    assert out == "('wards', 'own') ['my_net']"
