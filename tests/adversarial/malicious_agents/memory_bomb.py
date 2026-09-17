#!/usr/bin/env python3
"""Malicious test agent: tries to allocate unbounded memory."""
import sys

sys.stdin.readline()
blocks = []
while True:
    blocks.append(bytearray(10 * 1024 * 1024))  # 10MB at a time
