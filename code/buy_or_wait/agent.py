"""Small observable orchestration layer for the Buy or Wait? agent.

The agent delegates arithmetic and safety decisions to deterministic domain
components. Its value is the explicit evidence -> state -> simulation ->
alternatives -> selection workflow and trace, not probabilistic financial advice.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Mapping

from buy_or_wait.candidates import Candidate, generate_candidates, rank_candidates
from buy_or_wait.capacity import CapacityResult, calculate_capacity
from buy_or_wait.data import DatasetRepository
from buy_or_wait.evidence import ExtractionRun
from buy_or_wait.forecast import ReconstructedState, reconstruct_state
from buy_or_wait.models import RequestBundle


@dataclass(frozen=True, slots=True)
class AgentDecision:
    request_id: str
    state: ReconstructedState
    capacity: CapacityResult
    candidates: tuple[Candidate, ...]
    ranked_candidates: tuple[Candidate, ...]
    selected: Candidate | None
    trace: tuple[Mapping[str, object], ...]


def decide_request(
    bundle: RequestBundle,
    repository: DatasetRepository,
    evidence: ExtractionRun,
) -> AgentDecision:
    """Run one complete deterministic agent trajectory with stage trace."""
    trace: list[Mapping[str, object]] = []
    started = perf_counter()
    state = reconstruct_state(bundle, repository, evidence.facts, evidence.resolved_amounts)
    trace.append({"stage": "evidence_to_state", "facts": len(evidence.facts), "recurrence_rules": len(state.recurrence_rules)})
    capacity = calculate_capacity(state, bundle.request.requested_amount)
    trace.append({"stage": "capacity", "amount_safe_to_pay": str(capacity.amount_safe_to_pay), "earliest_date": capacity.earliest_date_for_full_payment.isoformat() if capacity.earliest_date_for_full_payment else None})
    candidates = generate_candidates(state, bundle, capacity)
    trace.append({"stage": "evaluate_alternatives", "candidate_count": len(candidates), "rejected": sum(1 for item in candidates if not item.eligible)})
    ranked = rank_candidates(candidates)
    selected = ranked[0] if ranked else None
    trace.append({"stage": "choose_action", "method": selected.method if selected else "not_recommended", "option_id": selected.option_id if selected else None})
    trace.append({"stage": "trace_complete", "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
    return AgentDecision(bundle.request.request_id, state, capacity, candidates, ranked, selected, tuple(trace))
