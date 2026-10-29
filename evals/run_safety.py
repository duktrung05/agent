"""Run the explicit fixture safety catalog, independently of live models or sources."""

import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from medical_agent.core import utc_now
from medical_agent.corpus import write_json_new


def main():
    parser = argparse.ArgumentParser(description="Chạy 12 ca lỗi/phạm vi bằng fixture; không chứng nhận an toàn y khoa.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    catalog = json.loads((ROOT / "evals/safety_cases.json").read_text(encoding="utf-8"))
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(case["test"]) for case in catalog["cases"])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = {test.id() for test, _ in result.failures + result.errors}
    skipped = {test.id() for test, _ in result.skipped}
    payload = {"created_at": utc_now(), "execution": "deterministic_fixtures_not_live_model",
               "clinical_validation": "not_performed", "total": result.testsRun,
               "passed": result.testsRun - len(failed) - len(skipped),
               "cases": [{"id": case["id"], "test": case["test"],
                          "status": "failed" if case["test"] in failed else "skipped" if case["test"] in skipped else "passed"} for case in catalog["cases"]]}
    write_json_new(args.output, payload)
    return 0 if result.wasSuccessful() and not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
