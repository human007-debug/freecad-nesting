"""
setup_geometry_fast.py
-----------------------
One-time build step for the optional `geometry_fast` Cython accelerator
(see geometry_fast.pyx's module docstring for why it exists). Not needed to
run this project at all -- geometry.py falls back to its own pure-Python
implementation if the compiled extension isn't present. Build it with:

    .venv/bin/pip install cython
    .venv/bin/python setup_geometry_fast.py build_ext --inplace

which drops a `geometry_fast*.so` next to this file, importable as
`import geometry_fast`.
"""

from setuptools import setup
from Cython.Build import cythonize

setup(
    name="geometry_fast",
    ext_modules=cythonize("geometry_fast.pyx", compiler_directives={"language_level": "3"}),
    zip_safe=False,
)
