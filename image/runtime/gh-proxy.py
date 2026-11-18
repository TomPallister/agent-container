#!/usr/bin/env python3
import sys
from github_auth import gh
try:
    gh(sys.argv[1:])
except Exception:
    print('gh: invalid reference routing configuration',file=sys.stderr)
    sys.exit(2)
