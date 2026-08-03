"""eyry — one CLI to run the Eyry recon suite together.

The suite is a set of small, independent tools:

    Foretop  (discover)  →  Purser  (queue)  →  Vedette  (probe)  →  Rutt  (store)

Each is useful on its own. ``eyry`` wires them into one pipeline: it starts each
stage, connects them through Redis and Postgres, and records the whole host
lifecycle (discovered → probed → reviewed) in Rutt as it goes.
"""

from .config import Config

__version__ = "0.1.0"
__all__ = ["Config", "__version__"]
