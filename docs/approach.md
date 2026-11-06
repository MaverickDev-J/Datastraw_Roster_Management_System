# Approach note

Roster models client demand independently from employee availability. Date-effective primary and backup allocations establish eligibility; explicit combined duties represent the low-volume multi-process responsibility found in single-owner brands. Independent full-load duties occupy AM/PM blocks and cannot overlap.

A sparse integer optimisation model enforces client staffing, employee availability, daily hours and rolling rest. Preferences and primary ownership guide the choice among legal assignments. Repair minimises changes against a previous roster while retaining explicit locks. When the available resources cannot satisfy the rules, diagnostic coverage shortfalls remain visible and publication is blocked.

The implementation uses React/TypeScript, FastAPI, SQLAlchemy and SQLite. HiGHS through SciPy is the currently verified solver because OR-Tools installation was blocked in the implementation environment; an optional CP-SAT implementation is also supplied. A separate validator recomputes hard constraints after generation, editing, configuration changes and immediately before publication. Version snapshots, revision checks and isolated reviewer workspaces protect consistency and reproducibility.

The first implementation prioritises inspectable scheduling and realistic failure handling. Production identity, multi-instance job execution, schema migration management and more detailed shift/workload policies are documented follow-up work.
