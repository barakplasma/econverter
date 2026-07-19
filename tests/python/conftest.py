import os
import sys

APP_PYTHON = os.path.join(
    os.path.dirname(__file__), '..', '..', 'app', 'src', 'main', 'python')
sys.path.insert(0, os.path.abspath(APP_PYTHON))
