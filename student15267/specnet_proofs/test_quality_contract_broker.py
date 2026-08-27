import unittest

from .quality_contract_broker import (
    QualityTier,
    QualityContract,
    ShadowPrices,
    VirtualByteDebtLedger,
    select_minimum_byte_portfolio,
    select_quality_tier,
)


class QualityContractBrokerTests(unittest.TestCase):
    def test_exact_portfolio_uses_minimum_bytes(self):
        contracts = [
            QualityContract(1, 8.0, 0.6),
            QualityContract(2, 5.0, 0.4),
            QualityContract(3, 4.0, 0.4),
        ]
        result = select_minimum_byte_portfolio(contracts, 0.8, retain_limit=2)
        self.assertTrue(result.feasible)
        self.assertEqual([item.contract_id for item in result.contracts], [2, 3])
        self.assertEqual(result.total_bytes, 9.0)

    def test_equal_cost_portfolios_use_stable_id_tie_break(self):
        contracts = [
            QualityContract(1, 5.0, 0.4),
            QualityContract(2, 5.0, 0.4),
        ]
        result = select_minimum_byte_portfolio(contracts, 0.4, retain_limit=1)
        self.assertTrue(result.feasible)
        self.assertEqual([item.contract_id for item in result.contracts], [1])

    def test_uncertainty_can_make_point_feasible_portfolio_infeasible(self):
        contracts = [QualityContract(1, 5.0, 0.5, 0.5)]
        point = select_minimum_byte_portfolio(contracts, 0.5, 1, 0.0)
        robust = select_minimum_byte_portfolio(contracts, 0.5, 1, 1.0)
        self.assertTrue(point.feasible)
        self.assertFalse(robust.feasible)

    def test_infeasible_fallback_utility_respects_retain_limit(self):
        contracts = [
            QualityContract(1, 1.0, 0.5),
            QualityContract(2, 1.0, 0.4),
            QualityContract(3, 1.0, 0.3),
        ]
        result = select_minimum_byte_portfolio(
            contracts,
            required_utility=1.0,
            retain_limit=2,
        )
        self.assertFalse(result.feasible)
        self.assertEqual(len(result.contracts), 3)
        self.assertAlmostEqual(result.achieved_lower_utility, 0.9)

    def test_tier_negotiation_grants_lower_feasible_contract(self):
        contracts = [QualityContract(1, 5.0, 1.0, selection_probability=0.8)]
        result = select_quality_tier(
            contracts,
            [QualityTier("primary", 0.9), QualityTier("degraded", 0.75)],
            retain_limit=1,
            uncertainty_penalty=1.0,
        )
        self.assertTrue(result.feasible)
        self.assertTrue(result.degraded)
        self.assertEqual(result.granted_tier, "degraded")

    def test_tier_negotiation_rejects_when_every_tier_is_infeasible(self):
        contracts = [QualityContract(1, 5.0, 0.5, selection_probability=0.5)]
        result = select_quality_tier(
            contracts,
            [QualityTier("primary", 0.5), QualityTier("degraded", 0.3)],
            retain_limit=1,
            uncertainty_penalty=1.0,
        )
        self.assertFalse(result.feasible)
        self.assertFalse(result.degraded)
        self.assertIsNone(result.granted_tier)

    def test_tier_ladder_must_be_descending(self):
        with self.assertRaises(ValueError):
            select_quality_tier(
                [],
                [QualityTier("low", 0.2), QualityTier("high", 0.3)],
                retain_limit=0,
            )

    def test_byte_debt_conserves_budget_charges_and_expiry(self):
        ledger = VirtualByteDebtLedger()
        ledger.allocate(7, 10.0)
        ledger.charge(7, 8.0, "optional")
        ledger.charge(7, 5.0, "background")
        self.assertEqual(ledger.global_outstanding, 3.0)
        ledger.expire(7, 1.0)
        self.assertEqual(ledger.global_outstanding, 2.0)
        ledger.assert_conservation()

    def test_later_budget_does_not_reopen_expired_debt(self):
        ledger = VirtualByteDebtLedger()
        ledger.allocate(7, 10.0)
        ledger.charge(7, 13.0, "optional")
        ledger.expire(7, 3.0)
        ledger.allocate(7, 1.0)
        account = ledger.account(7)
        self.assertEqual(account.outstanding, 0.0)
        self.assertEqual(account.unused_budget, 1.0)
        ledger.assert_conservation()

    def test_shadow_byte_price_rises_after_budget_violation(self):
        prices = ShadowPrices(learning_rate=0.5)
        prices.update(0.95, 0.95, served_bytes=12.0, byte_budget=10.0)
        self.assertEqual(prices.byte, 1.0)
        costly = QualityContract(1, 10.0, 0.8)
        cheap = QualityContract(2, 2.0, 0.7)
        self.assertGreater(
            prices.score(cheap, 0.0, 0.0),
            prices.score(costly, 0.0, 0.0),
        )

    def test_fairness_debt_increases_contract_priority(self):
        prices = ShadowPrices(fairness=2.0)
        contract = QualityContract(1, 5.0, 0.5)
        self.assertGreater(
            prices.score(contract, 0.0, 0.0, workflow_fairness_debt=1.0),
            prices.score(contract, 0.0, 0.0, workflow_fairness_debt=0.0),
        )

    def test_shadow_score_can_use_robust_utility(self):
        prices = ShadowPrices(quality=1.0)
        uncertain = QualityContract(1, 5.0, 1.0, selection_probability=0.5)
        point_score = prices.score(uncertain, 0.0, 0.0)
        robust_score = prices.score(
            uncertain,
            0.0,
            0.0,
            uncertainty_penalty=1.0,
        )
        self.assertLess(robust_score, point_score)


if __name__ == "__main__":
    unittest.main()
