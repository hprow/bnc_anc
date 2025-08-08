"""bnc_anc_pkg package initialization.

This module configures logging to ensure that messages emitted by modules in
the package are displayed on the console. The configuration is minimal and
only applies if no other logging handlers have been set up.
"""

import logging
import sys


if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

