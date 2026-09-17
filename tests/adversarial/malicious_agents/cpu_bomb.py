#!/usr/bin/env python3
"""Malicious test agent: busy-loops to try to exhaust CPU. Used by
tests/adversarial/test_sandbox_escape.py to verify RLIMIT_CPU actually kills
it. Deliberately self-contained (no agent_sdk import) since it never needs
to negotiate an action -- it just needs to run.
"""
import sys

sys.stdin.readline()  # consume task_input line, ignore it
x = 0
while True:
    x += 1  # unbounded CPU burn
