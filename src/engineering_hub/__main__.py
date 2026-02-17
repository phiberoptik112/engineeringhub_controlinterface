"""Entry point for running as module: python -m engineering_hub."""

import sys

from engineering_hub.cli import main

if __name__ == "__main__":
    sys.exit(main())
