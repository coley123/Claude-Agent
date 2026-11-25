---
name: debug-failing-test
description: Systematic loop for making a failing test pass without breaking others.
---
1. Run only the failing test (e.g. `python -m pytest path::test_name -x -q`) and read the whole error.
2. State one hypothesis about the cause in your notes before editing anything.
3. Read the code under test along the failing path. Check inputs, edge cases, off-by-one errors and types.
4. Make the smallest change that tests the hypothesis. Re-run the single test.
5. If it still fails, record the hypothesis as disproven and form a new one. Do not repeat a fix that already failed.
6. Once it passes, run the full suite to check for regressions before submitting.
7. If two hypotheses fail, post what you learned to the blackboard and request_help from a teammate.
