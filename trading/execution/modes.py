"""Execution-mode state machine with evidence-based live gates.

No configuration value, restart or strategy can move the system into LIVE_ENABLED.
Only `authorize_live` with an operator identity AND every gate evidenced can.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading.domain import ExecutionMode as M

LIVE_GATES = (
    "operator_authorization",
    "broker_credentials_verified",
    "static_ip_registered",
    "regulatory_algo_approval",
    "position_reconciliation",
    "market_data_fresh",
    "risk_limits_and_halt_tested",
    "critical_tests_passing",
    "monitoring_active",
    "rollback_runbook",
)

TRANSITIONS: dict[M, set[M]] = {
    M.RESEARCH: {M.PAPER, M.EMERGENCY_HALT},
    M.PAPER: {M.RESEARCH, M.LIVE_PENDING_VALIDATION, M.EMERGENCY_HALT},
    M.LIVE_PENDING_VALIDATION: {M.PAPER, M.LIVE_DISABLED, M.EMERGENCY_HALT},
    M.LIVE_DISABLED: {M.PAPER, M.LIVE_PENDING_VALIDATION, M.EMERGENCY_HALT},
    M.LIVE_ENABLED: {M.LIVE_DISABLED, M.EMERGENCY_HALT},
    M.EMERGENCY_HALT: {M.PAPER, M.RESEARCH, M.LIVE_DISABLED},
}


class ModeError(Exception):
    pass


@dataclass
class GateEvidence:
    gate: str
    passed: bool
    evidence: str
    recorded_by: str
    at: datetime


@dataclass
class ModeController:
    mode: M = M.PAPER
    evidence: dict[str, GateEvidence] = field(default_factory=dict)
    history: list[tuple[M, M, str, datetime, str]] = field(default_factory=list)

    def record_gate(self, gate: str, passed: bool, evidence: str, by: str, at: datetime) -> None:
        if gate not in LIVE_GATES:
            raise ModeError(f"unknown gate {gate}")
        if passed and not evidence.strip():
            raise ModeError("a passing gate requires evidence")
        self.evidence[gate] = GateEvidence(gate, passed, evidence, by, at)

    def gate_status(self) -> dict[str, bool]:
        return {g: bool(self.evidence.get(g) and self.evidence[g].passed) for g in LIVE_GATES}

    def missing_gates(self) -> list[str]:
        return [g for g, ok in self.gate_status().items() if not ok]

    def transition(self, new: M, by: str, at: datetime, reason: str = "") -> None:
        if not by:
            raise ModeError("mode transitions require an operator identity")
        if new == M.LIVE_ENABLED:
            raise ModeError("use authorize_live() to enable live execution")
        if new not in TRANSITIONS[self.mode]:
            raise ModeError(f"{self.mode} -> {new} not permitted")
        self.history.append((self.mode, new, by, at, reason))
        self.mode = new

    def authorize_live(self, by: str, at: datetime, confirmation: str) -> None:
        if self.mode != M.LIVE_DISABLED:
            raise ModeError("live can only be authorized from LIVE_DISABLED")
        if confirmation != "I AUTHORIZE LIVE TRADING":
            raise ModeError("explicit confirmation phrase required")
        missing = self.missing_gates()
        if missing:
            raise ModeError(f"live gates missing evidence: {', '.join(missing)}")
        self.history.append((self.mode, M.LIVE_ENABLED, by, at, "live authorized"))
        self.mode = M.LIVE_ENABLED

    def halt(self, by: str, at: datetime, reason: str) -> None:
        if self.mode != M.EMERGENCY_HALT:
            self.history.append((self.mode, M.EMERGENCY_HALT, by, at, reason))
            self.mode = M.EMERGENCY_HALT

    @property
    def can_submit_real_orders(self) -> bool:
        return self.mode == M.LIVE_ENABLED and not self.missing_gates()

    @property
    def can_submit_paper_orders(self) -> bool:
        return self.mode == M.PAPER
