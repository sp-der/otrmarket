import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.research.historical.databento import (
    DatabentoImportError, _instrument_map, raw_symbol_contract, verify_package,
)


class _Metadata:
    mappings = {
        "ESU6": [{"start_date": __import__("datetime").date(2026, 5, 1),
                   "end_date": __import__("datetime").date(2026, 8, 19), "symbol": "123"}],
        "ESM6-ESU6": [{"start_date": __import__("datetime").date(2026, 5, 1),
                        "end_date": __import__("datetime").date(2026, 8, 19), "symbol": "456"}],
    }


class DatabentoAcquisitionTests(unittest.TestCase):
    def test_exact_contract_resolution_preserves_mnq(self):
        self.assertEqual(raw_symbol_contract("MNQU6"), ("MNQ", "MNQ SEP26"))
        self.assertEqual(raw_symbol_contract("ESM6"), ("ES", "ES JUN26"))
        self.assertEqual(raw_symbol_contract("GCQ6"), ("GC", "GC AUG26"))

    def test_spreads_are_not_treated_as_outright_contracts(self):
        mapping = _instrument_map(_Metadata())
        self.assertEqual(set(mapping), {123})
        self.assertEqual(mapping[123]["raw_symbol"], "ESU6")

    def test_manifest_verifies_every_member(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "batch.zip"
            metadata = json.dumps({"dataset":"GLBX.MDP3"}).encode()
            conditions = json.dumps([{"date":"2026-07-30","condition":"DEGRADED"}]).encode()
            dbn = b"dbn"
            files = {"metadata.json":metadata,"condition.json":conditions,"data.dbn.zst":dbn}
            manifest = {"job_id":"JOB", "files":[{"filename":name,"size":len(value),
                "hash":"sha256:"+hashlib.sha256(value).hexdigest()} for name,value in files.items()]}
            with zipfile.ZipFile(package,"w") as archive:
                for name,value in files.items(): archive.writestr(name,value)
                archive.writestr("manifest.json",json.dumps(manifest))
            result=verify_package(package)
            self.assertTrue(result["manifest_verified"])
            self.assertEqual(result["degraded_dates"],["2026-07-30"])

    def test_manifest_hash_failure_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/"bad.zip"
            files={"metadata.json":b"{}","condition.json":b"[]","data.dbn.zst":b"x"}
            manifest={"job_id":"JOB","files":[{"filename":name,"size":len(value),"hash":"sha256:"+"0"*64} for name,value in files.items()]}
            with zipfile.ZipFile(package,"w") as archive:
                for name,value in files.items(): archive.writestr(name,value)
                archive.writestr("manifest.json",json.dumps(manifest))
            with self.assertRaises(DatabentoImportError): verify_package(package)


if __name__ == "__main__":
    unittest.main()
