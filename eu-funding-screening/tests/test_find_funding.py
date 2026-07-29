import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "find_funding.py"
SPEC = importlib.util.spec_from_file_location("find_funding", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FundingFinderTests(unittest.TestCase):
    def setUp(self):
        self.project = MODULE.ProjectProfile(
            name="LUMINA",
            summary="Evidence intelligence for research infrastructures",
            keywords=["evidence intelligence", "research infrastructure"],
            technologies=["large language models", "knowledge graph"],
            applicant_types=["SME", "university"],
            personnel_roles=["postdoctoral researcher"],
            preferred_funding_types=["fellowship", "research grant"],
        )
        self.source = MODULE.Source(
            domain="example.eu",
            website_url="https://example.eu/",
            organization="Example Funder",
            country="European Union",
            language="English",
        )

    def test_lumina_msca_match_and_manpower_classification(self):
        parsed = MODULE.ParsedPage(
            title="MSCA Postdoctoral Fellowships 2027 — Open Call",
            text=(
                "Applications open. This postdoctoral fellowship supports research "
                "in large language models, knowledge graph and evidence intelligence. "
                "The researcher receives a salary and mobility allowance. "
                "Deadline: 9 September 2027. Up to EUR 250,000."
            ),
            links=[],
        )
        match = MODULE.score_page(
            self.project, parsed, self.source,
            "https://example.eu/calls/msca-postdoctoral", "sitemap",
            "2026-07-29T09:00:00+00:00",
        )
        self.assertIsNotNone(match)
        assert match
        self.assertEqual(match.programme_family, "MSCA Postdoctoral Fellowships")
        self.assertEqual(match.personnel_support, "direct_salary_or_fellowship")
        self.assertEqual(match.deadline, "2027-09-09")
        self.assertGreaterEqual(match.relevance_score, 60)

    def test_hungarian_call_link_is_discovered(self):
        self.assertTrue(
            MODULE.likely_call_link(
                "https://example.hu/palyazatok/ai-kutatas",
                "Nyitott kutatási pályázat",
            )
        )

    def test_cost_is_not_presented_as_salary_funding(self):
        family, mechanism, explanation = MODULE.classify_scheme(
            "COST Open Call for a new COST Action"
        )
        self.assertEqual(family, "COST Actions")
        self.assertEqual(mechanism, "networking_and_short_term_mobility_only")
        self.assertIn("rather than research salaries", explanation)

    def test_closed_call_can_be_identified(self):
        self.assertEqual(
            MODULE.status_from("Applications closed. Deadline 1 January 2025.", "2025-01-01"),
            "closed",
        )

    def test_generic_funder_page_is_not_a_lumina_match(self):
        parsed = MODULE.ParsedPage(
            title="Our Grants and Competitions",
            text=(
                "A funding programme for emerging filmmakers, cultural events, "
                "writers and youth exchanges. See our grant recipients."
            ),
            links=[],
        )
        match = MODULE.score_page(
            self.project, parsed, self.source,
            "https://example.eu/grants", "homepage_link",
            "2026-07-29T09:00:00+00:00",
        )
        self.assertIsNone(match)

    def test_tracking_parameters_are_removed(self):
        canonical = MODULE.canonical_url(
            "https://Example.eu/call/?utm_source=x&id=4#section"
        )
        self.assertEqual(canonical, "https://example.eu/call?id=4")


if __name__ == "__main__":
    unittest.main()
