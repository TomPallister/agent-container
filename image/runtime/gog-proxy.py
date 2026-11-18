#!/usr/bin/env python3
import os
import sys
from client_shims import gog
try:
    gog(sys.argv[1:],dict(os.environ))
except Exception:
    print('gog: tokenless proxy configuration or sealed response invalid; no local credential login attempted',file=sys.stderr)
    sys.exit(2)
