#!/usr/bin/env python3
"""GitHub HTTPS helper. Only returns a reference; never stores credentials."""
import os
import re
import sys

if sys.argv[1:] != ["get"]:
    sys.exit(0)
fields = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line)
ref = os.environ.get("AGENT_CONTAINER_GITHUB_SECRET", "")
if fields.get("protocol") != "https" or fields.get("host") != "github.com":
    sys.exit(0)
if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", ref):
    sys.exit(0)
print("username=x-access-token")
print("password=" + "secret" + "ref:" + ref)
