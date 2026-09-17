#!/usr/bin/env python3
"""Malicious test agent: tries to exhaust the process/PID table."""
import os
import sys

sys.stdin.readline()
children = []
try:
    while True:
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        children.append(pid)
except OSError:
    sys.stdout.write('{"type": "finished", "result": {"forked": %d}}\n' % len(children))
    sys.stdout.flush()
