#!/usr/bin/env python3
"""Core primitives for a quality-contract, resource-priced controller.

The module is simulator-independent on purpose.  It turns optional branches
into auditable contracts, selects an exact minimum-byte portfolio, maintains a
conservative byte-debt ledger, and updates shadow prices for constrained
control.  Integration with the event simulator is a later evidence stage.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class QualityContract:
    contract_id: int
    byte_cost: float
    expected_utility: float
    selection_probability: float = 1.0

    def __post_init__(self) -> None:
        if self.byte_cost < 0.0 or self.expected_utility < 0.0:
            raise ValueError("contract cost and utility must be non-negative")
        if not 0.0 <= self.selection_probability <= 1.0:
            raise ValueError("selection probability must lie in [0, 1]")

    def lower_utility(self, uncertainty_penalty: float) -> float:
        if not 0.0 <= uncertainty_penalty <= 1.0:
            raise ValueError("uncertainty penalty must lie in [0, 1]")
        discount = 1.0 - uncertainty_penalty * (1.0 - self.selection_probability)
        return self.expected_utility * max(0.0, discount)


@dataclass(frozen=True)
class ContractPortfolio:
    contracts: Tuple[QualityContract, ...]
    required_utility: float
    achieved_lower_utility: float
    total_bytes: float
    feasible: bool


@dataclass(frozen=True)
class QualityTier:
    tier_name: str
    required_utility: float

    def __post_init__(self) -> None:
        if not self.tier_name:
            raise ValueError("quality tier name cannot be empty")
        if self.required_utility < 0.0:
            raise ValueError("quality tier utility must be non-negative")


@dataclass(frozen=True)
class NegotiatedPortfolio:
    requested_tier: str
    granted_tier: str | None
    portfolio: ContractPortfolio

    @property
    def feasible(self) -> bool:
        return self.granted_tier is not None

    @property
    def degraded(self) -> bool:
        return self.granted_tier is not None and self.granted_tier != self.requested_tier


def select_minimum_byte_portfolio(
    contracts: Sequence[QualityContract],
    required_utility: float,
    retain_limit: int,
    uncertainty_penalty: float = 0.0,
) -> ContractPortfolio:
    """Return the exact minimum-byte portfolio meeting a lower-bound target."""
    if required_utility < 0.0:
        raise ValueError("required utility must be non-negative")
    if retain_limit < 0:
        raise ValueError("retain limit must be non-negative")
    source = list(contracts)
    candidates: List[Tuple[float, int, Tuple[int, ...], Tuple[QualityContract, ...], float]] = []
    for count in range(min(retain_limit, len(source)) + 1):
        for subset in itertools.combinations(source, count):
            utility = sum(item.lower_utility(uncertainty_penalty) for item in subset)
            if utility + 1e-12 < required_utility:
                continue
            candidates.append(
                (
                    sum(item.byte_cost for item in subset),
                    count,
                    tuple(sorted(item.contract_id for item in subset)),
                    subset,
                    utility,
                )
            )
    if candidates:
        byte_cost, _, _, selected, utility = min(candidates, key=lambda item: item[:3])
        return ContractPortfolio(tuple(selected), required_utility, utility, byte_cost, True)
    fallback = tuple(source)
    retained_lower_utilities = sorted(
        (item.lower_utility(uncertainty_penalty) for item in fallback),
        reverse=True,
    )[:retain_limit]
    return ContractPortfolio(
        fallback,
        required_utility,
        sum(retained_lower_utilities),
        sum(item.byte_cost for item in fallback),
        False,
    )


def select_quality_tier(
    contracts: Sequence[QualityContract],
    tiers: Sequence[QualityTier],
    retain_limit: int,
    uncertainty_penalty: float = 0.0,
) -> NegotiatedPortfolio:
    """Grant the highest feasible tier from a descending contract ladder."""
    ladder = list(tiers)
    if not ladder:
        raise ValueError("quality tier ladder cannot be empty")
    if len({tier.tier_name for tier in ladder}) != len(ladder):
        raise ValueError("quality tier names must be unique")
    for higher, lower in zip(ladder, ladder[1:]):
        if lower.required_utility > higher.required_utility + 1e-12:
            raise ValueError("quality tiers must be ordered by descending utility")

    last_portfolio: ContractPortfolio | None = None
    for tier in ladder:
        portfolio = select_minimum_byte_portfolio(
            contracts,
            tier.required_utility,
            retain_limit,
            uncertainty_penalty,
        )
        last_portfolio = portfolio
        if portfolio.feasible:
            return NegotiatedPortfolio(
                requested_tier=ladder[0].tier_name,
                granted_tier=tier.tier_name,
                portfolio=portfolio,
            )
    assert last_portfolio is not None
    return NegotiatedPortfolio(
        requested_tier=ladder[0].tier_name,
        granted_tier=None,
        portfolio=last_portfolio,
    )


@dataclass
class DebtAccount:
    allocated_budget: float = 0.0
    optional_charged: float = 0.0
    background_charged: float = 0.0
    expired_debt: float = 0.0

    @property
    def charged(self) -> float:
        return self.optional_charged + self.background_charged

    @property
    def outstanding(self) -> float:
        return max(0.0, self.charged - self.allocated_budget - self.expired_debt)

    @property
    def unused_budget(self) -> float:
        # Expired debt no longer consumes budget allocated in a later window.
        return max(
            0.0,
            self.allocated_budget + self.expired_debt - self.charged,
        )

    def assert_conservation(self, tolerance: float = 1e-9) -> None:
        left = self.charged + self.unused_budget
        right = self.allocated_budget + self.expired_debt + self.outstanding
        if abs(left - right) > tolerance:
            raise AssertionError(f"byte debt does not conserve: {left} != {right}")


@dataclass
class VirtualByteDebtLedger:
    accounts: Dict[int, DebtAccount] = field(default_factory=dict)

    def account(self, workflow_id: int) -> DebtAccount:
        return self.accounts.setdefault(workflow_id, DebtAccount())

    def allocate(self, workflow_id: int, byte_budget: float) -> None:
        if byte_budget < 0.0:
            raise ValueError("byte budget must be non-negative")
        self.account(workflow_id).allocated_budget += byte_budget

    def charge(self, workflow_id: int, byte_count: float, kind: str) -> None:
        if byte_count < 0.0:
            raise ValueError("byte charge must be non-negative")
        target = self.account(workflow_id)
        if kind == "optional":
            target.optional_charged += byte_count
        elif kind == "background":
            target.background_charged += byte_count
        else:
            raise ValueError("kind must be optional or background")

    def expire(self, workflow_id: int, byte_count: float) -> None:
        target = self.account(workflow_id)
        if byte_count < 0.0 or byte_count > target.outstanding + 1e-9:
            raise ValueError("expired debt must be within outstanding debt")
        target.expired_debt += byte_count

    @property
    def global_outstanding(self) -> float:
        return sum(account.outstanding for account in self.accounts.values())

    def assert_conservation(self) -> None:
        for account in self.accounts.values():
            account.assert_conservation()


@dataclass
class ShadowPrices:
    quality: float = 1.0
    byte: float = 0.0
    congestion: float = 0.0
    fairness: float = 0.0
    learning_rate: float = 0.1
    maximum: float = 100.0

    def update(
        self,
        quality_target: float,
        achieved_quality: float,
        served_bytes: float,
        byte_budget: float,
        congestion_cost: float = 0.0,
        congestion_budget: float = 0.0,
        fairness_debt: float = 0.0,
    ) -> None:
        self.quality = min(self.maximum, max(0.0, self.quality + self.learning_rate * (quality_target - achieved_quality)))
        self.byte = min(self.maximum, max(0.0, self.byte + self.learning_rate * (served_bytes - byte_budget)))
        self.congestion = min(self.maximum, max(0.0, self.congestion + self.learning_rate * (congestion_cost - congestion_budget)))
        self.fairness = min(self.maximum, max(0.0, self.fairness + self.learning_rate * fairness_debt))

    def score(
        self,
        contract: QualityContract,
        deadline_urgency: float,
        expected_congestion_cost: float,
        workflow_fairness_debt: float = 0.0,
        uncertainty_penalty: float = 0.0,
    ) -> float:
        return (
            self.quality * contract.lower_utility(uncertainty_penalty)
            + deadline_urgency
            - self.byte * contract.byte_cost
            - self.congestion * expected_congestion_cost
            + self.fairness * workflow_fairness_debt
        )
