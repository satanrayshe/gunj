"""Gunj: date Indian recordings from the hum of the national grid."""
import os as _os

# numpy and scipy each bundle OpenBLAS, which by default reserves memory for
# one buffer per CPU thread when imported. Gunj needs no multithreaded BLAS,
# so ask for one thread unless the caller chose otherwise. This only works if
# gunj is imported before numpy (true for the CLI and the repo scripts).
# Measured effect: results/mem_import_check.json.
_os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

__version__ = "0.1.0"
