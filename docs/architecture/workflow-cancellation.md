# Workflow cancellation and concurrent runs

AI-started workflows execute in the MCP bridge; the Workflows editor executes in
the panel process. The old Stop implementation only inspected an in-process
dictionary. The other process returned stopped=false, which the UI ignored.
Stopping an active tool also returned immediately while its terminal command kept
running. One chat card and one workflow activity ID overwrote concurrent runs.

Every run now has a separate run ID and a local record under AppData/workflow-runs.
Stop writes a request for the selected run; its owner polls while executing each
step and between steps. Nested runs share cancellation. Active terminal calls
register cleanup that closes the terminal and kills its child process tree.
Terminals opened by a run remain owned until that run finishes, including commands
started in the background. Closing a terminal also rejects outstanding approval
requests. Stopped runs cannot execute subsequent tool calls.

Run records include the owner's PID and process creation time. A missing owner or
reused PID becomes stopped when queried. The UI hydrates records when a tab opens
and periodically reconciles missed events. Run snapshots never grant approval,
change caller identity or re-execute workflow steps.

Each chat shows every concurrent run with its own Stop control. The Workflows
toolbar keeps Test available while runs are active and provides a run selector and
Stop for the selected active run. Activity IDs include run IDs. Late events cannot
resurrect a stopped run, and failed Stop requests show an error and permit retry.

Regression checks cover cancellation from another process, stopping one of two
concurrent runs, real Windows terminal child termination, dead-owner recovery,
chat card separation, and reopening the editor with active run controls.
An already-issued external operation cannot be rolled back by cancelling Python;
cooperative operations stop and further workflow tool calls are rejected.
