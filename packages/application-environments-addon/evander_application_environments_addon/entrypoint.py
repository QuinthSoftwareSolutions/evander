"""Wait for a host-projected resource binding before starting the application."""

import json
import os
from pathlib import Path
import sys
import time

path = Path(os.environ["EVANDER_APPLICATION_RESOURCE_FILE"])
while not path.is_file():
    time.sleep(0.2)
connections = json.loads(path.read_text())
connection = connections[os.environ["EVANDER_APPLICATION_DATABASE_ALIAS"]]
os.environ["EVANDER_PREVIEW_DATABASE"] = connection["connection_string"]
os.execvp(sys.argv[1], sys.argv[1:])
