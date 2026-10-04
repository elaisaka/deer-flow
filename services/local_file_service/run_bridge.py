"""File entrypoint usable by both production and development Compose mounts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.local_file_service.bridge import main

if __name__ == "__main__":
    main()
