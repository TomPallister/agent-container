#!/usr/bin/env python3
import os
import sys
from client_shims import cx
try:
    cx(sys.argv[1:],dict(os.environ))
except Exception:
    print('cx: tokenless proxy configuration or sealed response invalid; no local credential login attempted',file=sys.stderr)
    sys.exit(2)
