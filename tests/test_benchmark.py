"""A/B runs use isolated stores and require explicit, unmodified human reviews."""

from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from medical_agent.benchmark import load_cases, run_suite, score_review, summarize
from medical_agent.core import Config
from medical_agent.corpus import freeze_manifest
from test_system import QUOTE, ScriptedModel, assessment, decision, report


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus.json"
        self.corpus.write_text(json.dumps({"records": [{"pmid": "101", "title": "Synthetic broad source", "abstract": QUOTE, "year": 2024}]}))
        freeze_manifest(self.corpus)
        self.case = {"id": "fixture", "split": "dev", "question": "Tóm tắt 1 bài nghiên cứu.", "required_outputs": ["Tóm tắt có nguồn."], "reference_source_ids": []}

    def tearDown(self):
        self.temp.cleanup()

    def run_fixture(self):
        def factory(config):
            decisions = [decision("search_local", "broad"), decision("read_source", source_id="pubmed:101"), decision("finish")]
            return ScriptedModel(decisions, [assessment()], [report()])
        output = self.root / "experiment"
        with patch("sys.stdout", new=io.StringIO()):
            records = run_suite([self.case], self.corpus, output, Config(offline=True, top_k=1), model_factory=factory)
        templates = json.loads((output / "reviews.template.json").read_text())["reviews"]
        return records, templates, output

    def test_ab_runs_have_separate_stores_and_no_automatic_semantic_score(self):
        records, reviews, output = self.run_fixture()
        self.assertEqual({r["variant"] for r in records}, {"fixed", "agent"})
        self.assertEqual(len({r["run_id"] for r in records}), 2)
        self.assertEqual(len({r["home"] for r in records}), 2)
        self.assertTrue(all(r["status"] == "completed" for r in records))
        for row in summarize(records, reviews):
            self.assertIsNone(row["mean_claim_support"])
            self.assertEqual(row["content_reviewed"], 0)
        self.assertEqual(json.loads((output / "experiment.json").read_text())["recorded_runs"], 2)
        self.assertTrue((output / "summary.csv").is_file())
        self.assertTrue(all(r["retrieval"]["precision_at_5"] is None for r in records))

    def test_review_unsupported_claim_is_not_treated_as_supported(self):
        records, reviews, _ = self.run_fixture()
        review = reviews[0]
        review.update({"reviewer": "fixture_reviewer", "content_complete": False})
        review["claims"][0]["label"] = "unsupported"
        review["criteria"][0]["label"] = "missing"
        score = score_review(records[0], review)
        self.assertEqual(score["claim_support"], 0)
        self.assertEqual(score["evidence_coverage"], 0)

    def test_review_cannot_drop_or_rewrite_claim(self):
        records, reviews, _ = self.run_fixture()
        altered = deepcopy(reviews[0])
        altered["claims"] = []
        with self.assertRaises(ValueError):
            score_review(records[0], altered)
        altered = deepcopy(reviews[0])
        altered["claims"][0]["text"] = "Different claim"
        with self.assertRaises(ValueError):
            score_review(records[0], altered)

    def test_review_requires_reviewer_and_all_criteria_for_complete(self):
        records, reviews, _ = self.run_fixture()
        review = reviews[0]
        review["content_complete"] = True
        with self.assertRaises(ValueError):
            score_review(records[0], review)
        review["reviewer"] = "fixture"
        with self.assertRaises(ValueError):
            score_review(records[0], review)
        review["claims"][0]["label"] = "supported"
        review["criteria"][0]["label"] = "covered"
        self.assertEqual(score_review(records[0], review)["claim_support"], 1)

    def test_review_unknown_run_is_rejected(self):
        records, reviews, _ = self.run_fixture()
        reviews[0]["run_id"] = "unknown"
        with self.assertRaises(ValueError):
            summarize(records, reviews)

    def test_case_id_traversal_and_split_leakage_are_rejected(self):
        path = self.root / "cases.json"
        path.write_text(json.dumps({"cases": [{**self.case, "id": "../outside"}]}))
        with self.assertRaises(ValueError):
            load_cases(path)
        path.write_text(json.dumps({"cases": [{**self.case, "group": "same"}, {**self.case, "id": "other", "split": "test", "group": "same"}]}))
        with self.assertRaises(ValueError):
            load_cases(path)

    def test_online_benchmark_is_rejected_before_model_calls(self):
        with self.assertRaises(ValueError):
            run_suite([self.case], self.corpus, self.root / "experiment", Config(offline=False))

    def test_question_bank_contains_30_questions_with_10_dev_and_20_test(self):
        root = Path(__file__).resolve().parents[1]
        cases = load_cases(root / "evals/questions.json", "all")
        self.assertEqual(len(cases), 30)
        self.assertEqual(sum(c["split"] == "dev" for c in cases), 10)
        self.assertEqual(sum(c["split"] == "test" for c in cases), 20)
        self.assertTrue(all(not c["references_reviewed"] for c in cases))


if __name__ == "__main__":
    unittest.main()
