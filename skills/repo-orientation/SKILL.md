---
name: repo-orientation
description: Get oriented in an unfamiliar codebase quickly before changing anything.
---
1. List the top level and read the README, build files (pyproject.toml, package.json, Makefile) and CI config to learn how the project builds and tests.
2. Run the test suite once to get a baseline. Write down in your notes which tests already fail.
3. Find the entry points and the modules that relate to your task: `grep -rn` for the names in the task description.
4. Read the tests for those modules. They show intended behavior faster than the code does.
5. Save a short map in memory_write: key files, how to run tests, the baseline failures.
