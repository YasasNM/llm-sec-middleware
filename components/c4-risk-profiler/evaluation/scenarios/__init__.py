"""The 7 scenarios live inside the c4 package (c4.demo.scenarios) because the dashboard's
"Load demo data" button needs them too. This file keeps the old import path working for the
evaluation scripts."""

from c4.demo.scenarios import SCENARIOS, USER_FOR_SCENARIO, build_scenario

__all__ = ["SCENARIOS", "USER_FOR_SCENARIO", "build_scenario"]
