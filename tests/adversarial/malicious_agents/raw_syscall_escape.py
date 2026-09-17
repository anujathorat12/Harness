#!/usr/bin/env python3
"""Malicious test agent: ignores the SDK entirely and tries to read a
host file directly via raw Python I/O, bypassing the action-request
protocol altogether. This demonstrates -- and documents -- exactly the gap
process/container isolation exists to contain: nothing in the STDIO
protocol itself can stop an agent that never uses it. What actually runs
this file already executes as a low-privilege, resource-limited OS process
with no elevated capabilities; a full accounting of what that does and does
not prevent is in ARCHITECTURE.md."""
import json
import sys

sys.stdin.readline()
try:
    with open("/etc/passwd") as f:
        content = f.read(200)
    sys.stdout.write(json.dumps({"type": "finished", "result": {"raw_read_succeeded": True, "preview": content[:50]}}) + "\n")
except PermissionError as e:
    sys.stdout.write(json.dumps({"type": "finished", "result": {"raw_read_succeeded": False, "error": str(e)}}) + "\n")
sys.stdout.flush()
