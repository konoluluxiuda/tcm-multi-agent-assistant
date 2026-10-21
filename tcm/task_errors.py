"""Shared local task failures, independent of controller and tool implementations."""


class LocalSnapshotChanged(RuntimeError):pass
class LocalUnavailable(RuntimeError):pass
class LocalBudget(RuntimeError):pass
