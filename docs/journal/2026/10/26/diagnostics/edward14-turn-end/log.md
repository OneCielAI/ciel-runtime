# Investigation log

## 2026-10-26
- Read Edward u22 transcript and router state after user approval. No session restart, input injection, config or queue changes. Two diagnostic PowerShell scripts were copied to C:/Windows/Temp; they only read files, but copying them was an unnecessary remote filesystem write under a read-only plan. Subsequent probes executed read-only Python directly without copying files.
- Confirmed normal end_turn with Work unfinished, separately from API Error: terminated. No fix or deployment claimed for either issue.
