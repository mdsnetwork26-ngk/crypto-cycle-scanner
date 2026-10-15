import os
import tempfile

# Harus diset SEBELUM modul app diimpor: pakai DB sementara + mode demo
# (data sintetis) supaya tes tidak menyentuh jaringan maupun data asli.
_tmp = tempfile.mkdtemp(prefix="ccs-test-")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "test.db"))
os.environ["DEMO_MODE"] = "1"
