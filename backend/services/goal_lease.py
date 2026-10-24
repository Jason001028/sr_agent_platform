"""Worker ownership shared by the loop, tool boundary and durable writes."""

from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
import time

current_lease = ContextVar("goal_lease", default=None)
current_tool = ContextVar("goal_tool", default=None)


class LeaseLost(RuntimeError):
    pass


@dataclass
class GoalLease:
    goal_id: str
    token: str
    version: int
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def check(self, store):
        if self.stopped.is_set():
            raise LeaseLost("goal worker stopped")
        goal = store.get_goal(self.goal_id)
        if (
            self.stopped.is_set()
            or not goal
            or goal["lease_token"] != self.token
            or goal["version"] != self.version
            or (goal["lease_until"] or 0) <= time.time()
        ):
            raise LeaseLost("goal lease expired, cancelled or replaced")
        return goal
