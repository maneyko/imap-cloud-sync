import tomllib
from pathlib import Path
from types import SimpleNamespace

file = Path(__file__).parent.parent.joinpath("config.toml")

Settings = SimpleNamespace(**tomllib.loads(file.read_text()))
