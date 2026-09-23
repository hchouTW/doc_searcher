"""Platform facts shared by tests."""

import os

# Root bypasses file permission checks, so permission-denied scenarios cannot be simulated.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
