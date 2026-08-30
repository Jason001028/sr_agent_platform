"""Print the tool manifest as JSON.

Usage: python -m backend.tools
"""

import json

from . import manifest

if __name__ == "__main__":
    print(json.dumps(manifest(), indent=2, ensure_ascii=False))
