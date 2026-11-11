"""Entry point of the bundled app (PyInstaller). Double-click = `whoosh-connect run`."""
import sys

from whoosh_connect.bundle import launch

if __name__ == "__main__":
    sys.exit(launch(sys.argv[1:]))
