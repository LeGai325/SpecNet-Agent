import unittest

from .eligible_window_paired_audit import paired_rows


class EligibleWindowPairedAuditTests(unittest.TestCase):
    def test_pairing_uses_matching_run_and_scenario(self):
        base = {
            "p99_latency": 1.0,
            "deadline_miss_ratio": 1.0,
            "waste": 1.0,
            "quality": 1.0,
            "normalized_latency": 1.0,
            "background_service_ratio": 1.0,
            "link_utilization": 1.0,
        }
        eligible = [dict(base, run=0, scenario=0, p99_latency=3.0)]
        reference = [dict(base, run=0, scenario=0, p99_latency=1.0)]
        row = next(item for item in paired_rows(eligible, reference) if item["metric"] == "p99_latency")
        self.assertEqual(row["paired_cells"], 1)
        self.assertEqual(row["mean_delta"], 2.0)


if __name__ == "__main__":
    unittest.main()
