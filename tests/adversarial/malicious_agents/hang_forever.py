#!/usr/bin/env python3
"""Malicious/broken test agent: never responds, never finishes -- tests the
wall-clock timeout watchdog kills it even though it isn't burning CPU."""
import sys
import time

sys.stdin.readline()
while True:
    time.sleep(3600)
