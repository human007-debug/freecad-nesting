"""
tests/test_geometry_fast.py
----------------------------
Correctness of the optional Cython accelerator (geometry_fast.pyx) against
geometry.py's own pure-Python reference implementation, plus the fallback
path when the compiled extension isn't built. See geometry.py's
PERFORMANCE docstring and README's "Performance" section for why this
module exists and what it replaced (a numpy attempt that was slower, and a
kerf-offset-reuse approximation that was outright rejected for
misclassifying real cases).

Skipped entirely if geometry_fast hasn't been built (`pip install cython
setuptools && python3 setup_geometry_fast.py build_ext --inplace`) --
it's an optional accelerator, not a hard dependency, so its absence isn't
a test failure.
"""

import builtins
import importlib
import random

import pytest

import geometry

geometry_fast = pytest.importorskip(
    "geometry_fast",
    reason="geometry_fast not built -- see README's Performance section to build it",
)

from tests.conftest import rand_polygon  # noqa: E402


def test_geometry_module_resolves_to_compiled_backend():
    assert geometry.polygons_overlap.__module__ == "geometry_fast"
    assert geometry.polygons_min_distance.__module__ == "geometry_fast"


@pytest.mark.parametrize("seed", range(20))
def test_polygons_overlap_matches_pure_python(seed):
    rng = random.Random(seed)
    for _ in range(500):
        a = rand_polygon(rng, scale=rng.uniform(20, 80))
        b = rand_polygon(rng, scale=rng.uniform(20, 80),
                          cx=rng.uniform(-150, 150), cy=rng.uniform(-150, 150))
        assert geometry_fast.polygons_overlap(a, b) == geometry._polygons_overlap_py(a, b)


@pytest.mark.parametrize("seed", range(20))
def test_polygons_min_distance_matches_pure_python(seed):
    rng = random.Random(seed + 10_000)
    for _ in range(500):
        a = rand_polygon(rng, scale=rng.uniform(20, 80))
        b = rand_polygon(rng, scale=rng.uniform(20, 80),
                          cx=rng.uniform(-150, 150), cy=rng.uniform(-150, 150))
        d_py = geometry._polygons_min_distance_py(a, b)
        d_fast = geometry_fast.polygons_min_distance(a, b)
        assert d_fast == pytest.approx(d_py, abs=1e-6)


def test_falls_back_to_pure_python_when_extension_unavailable(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "geometry_fast":
            raise ImportError("simulated: no compiled extension")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    importlib.reload(geometry)
    try:
        assert geometry.polygons_overlap.__name__ == "_polygons_overlap_py"
        assert geometry.polygons_overlap(
            [(0, 0), (2, 0), (2, 2), (0, 2)], [(1, 1), (3, 1), (3, 3), (1, 3)],
        ) is True
    finally:
        monkeypatch.undo()
        importlib.reload(geometry)  # restore the real (compiled-backed) module state
