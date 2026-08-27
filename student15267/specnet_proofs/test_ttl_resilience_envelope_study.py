import unittest

from .ttl_resilience_envelope_study import (
    first_grid_ttl_at_least,
    select_ttl_from_horizons,
)


class TTLResilienceEnvelopeTests(unittest.TestCase):
    def test_selects_smallest_predeclared_grid_ttl(self):
        self.assertEqual(1280, first_grid_ttl_at_least(1200, (1024, 1280, 1536)))
        self.assertEqual(1024, first_grid_ttl_at_least(1024, (1024, 1280)))

    def test_rejects_horizon_above_grid(self):
        with self.assertRaises(ValueError):
            first_grid_ttl_at_least(1537, (1024, 1536))

    def test_horizon_selection_uses_maximum_not_average(self):
        selection = select_ttl_from_horizons(
            [
                {"target_lag_epochs": 21.0},
                {"target_lag_epochs": 100.0},
                {"target_lag_epochs": 1030.0},
            ],
            (1024, 1152, 1280),
        )
        self.assertEqual(1152, selection["selected_ttl_epochs"])
        self.assertEqual(1030.0, selection["required_ttl_epochs"])
        self.assertEqual(122.0, selection["selection_headroom_epochs"])

