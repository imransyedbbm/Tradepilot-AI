"""SQLAlchemy persistence: order intents, audit events, persistent halt latch, mode history.

SQLite by default for local work; set TP_DATABASE_URL to PostgreSQL for deployment.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from trading.domain import OrderIntent, OrderState


class Base(DeclarativeBase):
    pass


class IntentRow(Base):
    __tablename__ = "order_intents"
    intent_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    proposal_id: Mapped[str] = mapped_column(String(40))
    token: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(40))
    strategy_id: Mapped[str] = mapped_column(String(10))
    strategy_version: Mapped[str] = mapped_column(String(20))
    style: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    stop_price: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[str | None] = mapped_column(String(20), nullable=True)
    simulated: Mapped[int] = mapped_column(Integer, default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str] = mapped_column(Text)


class KVRow(Base):
    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class DuplicateIntent(Exception):
    pass


class Store:
    def __init__(self, url: str = "sqlite:///:memory:"):
        kw = {}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool
                kw["poolclass"] = StaticPool
        self.engine = create_engine(url, **kw)
        Base.metadata.create_all(self.engine)

    # audit ---------------------------------------------------------------
    def audit(self, at: datetime, actor: str, kind: str, detail: dict | str) -> None:
        text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        with Session(self.engine) as s, s.begin():
            s.add(AuditRow(at=at, actor=actor, kind=kind, detail=text))

    def audit_events(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)).all()
            return [{"id": r.id, "at": r.at.isoformat(), "actor": r.actor, "kind": r.kind, "detail": r.detail} for r in rows]

    # kv ------------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        with Session(self.engine) as s:
            r = s.get(KVRow, key)
            return r.value if r else default

    def put(self, key: str, value: str) -> None:
        with Session(self.engine) as s, s.begin():
            s.merge(KVRow(key=key, value=value))

    # intents -------------------------------------------------------------
    def insert_intent(self, i: OrderIntent) -> None:
        with Session(self.engine) as s:
            if s.scalar(select(IntentRow).where(IntentRow.dedupe_key == i.dedupe_key)):
                raise DuplicateIntent(i.dedupe_key)
        with Session(self.engine) as s, s.begin():
            s.add(self._row(i))

    def save_intent(self, i: OrderIntent) -> None:
        with Session(self.engine) as s, s.begin():
            s.merge(self._row(i))

    def intents(self, states: set[OrderState] | None = None) -> list[dict]:
        with Session(self.engine) as s:
            q = select(IntentRow)
            if states:
                q = q.where(IntentRow.state.in_([x.value for x in states]))
            return [self._dict(r) for r in s.scalars(q).all()]

    @staticmethod
    def _row(i: OrderIntent) -> IntentRow:
        return IntentRow(
            intent_id=i.intent_id, dedupe_key=i.dedupe_key, proposal_id=i.proposal_id,
            token=i.instrument.token, symbol=i.instrument.symbol, strategy_id=i.strategy_id,
            strategy_version=i.strategy_version, style=i.style.value, side=i.side, quantity=i.quantity,
            stop_price=str(i.stop_price), state=i.state.value, broker_order_id=i.broker_order_id,
            filled_qty=i.filled_qty, avg_fill_price=str(i.avg_fill_price) if i.avg_fill_price else None,
            simulated=int(i.simulated),
            history=[[st.value, at.isoformat(), note] for st, at, note in i.history],
        )

    @staticmethod
    def _dict(r: IntentRow) -> dict:
        return {c.name: getattr(r, c.name) for c in IntentRow.__table__.columns}


def D(v) -> Decimal | None:
    return Decimal(v) if v is not None else None
