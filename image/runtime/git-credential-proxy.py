#!/usr/bin/env python3
"""GitHub HTTPS helper. Only returns a reference; never stores credentials."""
import sys
from github_auth import select, repo_owner

if sys.argv[1:] != ["get"]:
    sys.exit(0)
fields = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line)
if fields.get("protocol") != "https" or fields.get("host") != "github.com":
    sys.exit(0)
ref = select(repo_owner(fields.get('path','')))
if not ref:
    sys.exit(0)
print("username=x-access-token")
print("password=" + ref)
