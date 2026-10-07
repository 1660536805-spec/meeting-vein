# 空会议基准

- Case ID: `empty-meeting-v1`
- Title: `空会议基准`
- Input utterances: 0
- Expected board nodes: 0 content nodes; if the application requires an issue root, it must be marked as an empty placeholder and excluded from content counts.
- Expected semantic edges: 0
- Expected evidence references: 0
- Expected open questions, conflicts, and actions: 0
- Expected UI state: explicit empty-state guidance to add an agenda or start input; never a fabricated “waiting for discussion” conclusion.
- Expected export: a valid empty-meeting document with title and no invented decisions.

This is a product-level gold case, not yet an automated fixture. The meeting-creation task should encode it as an API/UI regression case.
