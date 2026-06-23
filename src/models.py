
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from src.tool_logger import logger

# ============================================================
# Domain model
# ============================================================
class EventType:
    COCR = "COCR"
    COST = "COST"
    DTAC = "DTAC"
    DTDE = "DTDE"
    CODO = "CODO"
    COFI = "COFI"

@dataclass(frozen=True)
class SourceLocation:
    module_path: Optional[str] = None
    line_number: Optional[int] = None

@dataclass(frozen=True)
class StartFunction:
    qualified_name: str

@dataclass(frozen=True)
class AltstepFunction:
    qualified_name: str

@dataclass(frozen=True)
class LogEvent:
    timestamp: datetime
    type: str
    component_id: str
    source: Optional[SourceLocation] = None
    start_fn: Optional[StartFunction] = None
    activated_fn: Optional[AltstepFunction] = None
    deactivated_fns: Tuple[AltstepFunction, ...] = tuple()
    related_component: Optional[str] = None
    expectation: Optional[str] = None
    verdict: Optional[str] = None
    raw_line: Optional[str] = None
    message_name: Optional[str] = None
    port_name: Optional[str] = None 

@dataclass
class LifecycleState:
    created: bool = False
    started: bool = False
    start_origin: Optional[StartFunction] = None

@dataclass
class ExpectationState:
    status: Optional[str] = None
    related_component: Optional[str] = None

@dataclass
class ComponentStateSnapshot:
    timestamp: datetime
    lifecycle: LifecycleState
    altsteps_active: Set[str] = field(default_factory=set)
    expectation: ExpectationState = field(default_factory=ExpectationState)
    verdict: Optional[str] = None

    def clone(self) -> "ComponentStateSnapshot":
        return ComponentStateSnapshot(
            timestamp=self.timestamp,
            lifecycle=LifecycleState(
                created=self.lifecycle.created,
                started=self.lifecycle.started,
                start_origin=self.lifecycle.start_origin,
            ),
            altsteps_active=set(self.altsteps_active),
            expectation=ExpectationState(
                status=self.expectation.status,
                related_component=self.expectation.related_component,
            ),
            verdict=self.verdict,
        )

@dataclass
class ComponentStateHistory:
    component_id: str
    snapshots: List[ComponentStateSnapshot] = field(default_factory=list)
    events: List[LogEvent] = field(default_factory=list)

    @property
    def latest(self) -> Optional[ComponentStateSnapshot]:
        return self.snapshots[-1] if self.snapshots else None

    def append(self, ev: LogEvent, next_snapshot: ComponentStateSnapshot) -> None:
        self.events.append(ev)
        self.snapshots.append(next_snapshot)

@dataclass
class ComponentRegistry:
    by_id: Dict[str, ComponentStateHistory] = field(default_factory=dict)

    def ensure(self, component_id: str, ts: datetime) -> ComponentStateHistory:
        if component_id not in self.by_id:
            initial = ComponentStateSnapshot(timestamp=ts, lifecycle=LifecycleState())
            self.by_id[component_id] = ComponentStateHistory(component_id, [initial], [])
            logger.debug(
                "Created new ComponentStateHistory: component_id=%s, ts=%s",
                component_id,
                ts.isoformat(),
            )
        return self.by_id[component_id]

    def histories(self) -> Iterable[ComponentStateHistory]:
        return self.by_id.values()

@dataclass
class Frame:
    idx: int
    ts: datetime
    state: str
    active_altsteps: List[str] = field(default_factory=list)
    incoming: List[str] = field(default_factory=list)
    consumed: List[str] = field(default_factory=list)
    outgoing: List[str] = field(default_factory=list)

    def to_jsonable(self, cumulative_counts: Tuple[int, int, int]) -> Dict[str, Any]:
        in_total, consume_total, out_total = cumulative_counts
        return {
            "State": self.state,
            "Active_Altsteps": self.active_altsteps,
            "Incoming_messages": self.incoming,
            "Consumed_messages": self.consumed,
            "Outgoing_messages": self.outgoing,
            "ico_summary": {
                "in": in_total,
                "consume": consume_total,
                "out": out_total,
            },
        }

@dataclass
class QueueConsumeStats:
    ptqu_count: int = 0
    ptrx_count: int = 0
    ptqu_timestamps: List[datetime] = field(default_factory=list)
    ptrx_timestamps: List[datetime] = field(default_factory=list)

    @property
    def last_queued_at(self) -> Optional[datetime]:
        return self.ptqu_timestamps[-1] if self.ptqu_timestamps else None

    @property
    def last_consumed_at(self) -> Optional[datetime]:
        return self.ptrx_timestamps[-1] if self.ptrx_timestamps else None

# ============================================================
# Constants / classification
# ============================================================
# Heuristic thresholds; adjust if needed
LOW_ACTIVITY_MAX_FRAMES = 6          # e.g. components with ≤ 6 frames
LOW_ACTIVITY_MAX_TOTAL_MSGS = 4      # and total (in+consume+out) ≤ 4

# How many items to show in overview sections
TOP_COMPONENTS_FOR_DENSITY = 10
TOP_MESSAGES_FOR_REPETITION = 10

MESSAGE_INCOMING = {"PTQU"}
MESSAGE_CONSUMED = {"PTRX"}
MESSAGE_OUTGOING = {"PTSD"}

LIFECYCLE_TO_STATE: Dict[str, str] = {
    "COCR": "COMPONENT_CREATED",
    "COST": "COMPONENT_STARTED",
    "DTAC": "COMPONENT_ACTIVATING_DEFAULTS",
    "DTDE": "COMPONENT_DISABLING_DEFAULTS",
    "CODO": "COMPONENT_TASK_DONE",
    "COFI": "COMPONENT_CYCLE_FINISHED",
}

INTERESTING_MNEMONICS = (
    set(LIFECYCLE_TO_STATE.keys())
    | MESSAGE_INCOMING
    | MESSAGE_CONSUMED
    | MESSAGE_OUTGOING
)

# Mnemonics we want to drop early at pre-processing level
IGNORED_MNEMONICS: Set[str] = {
    "PLLG", "ALRP", "ALWT", "ALLV", "ALEN", "PTPU" # examples, add or remove what you want
}