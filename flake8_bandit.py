"""Implementation of bandit security testing in Flake8."""
import ast
import configparser
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, NamedTuple, Set

import pycodestyle
from flake8.options.config import ConfigFileFinder
from flake8 import utils as stdin_utils

from bandit.core.config import BanditConfig
from bandit.core.meta_ast import BanditMetaAst
from bandit.core.metrics import Metrics
from bandit.core.node_visitor import BanditNodeVisitor
from bandit.core.test_set import BanditTestSet


__version__ = "2.1.2"


class Flake8BanditConfig(NamedTuple):
    profile: Dict
    target_paths: Set
    excluded_paths: Set

    @classmethod
    @lru_cache(maxsize=32)
    def from_config_file(cls) -> "Flake8BanditConfig":
        # set defaults
        profile = {}
        target_paths = set()
        excluded_paths = set()

        # populate config from `.bandit` configuration file
        ini_file = ConfigFileFinder("bandit", None, None).local_config_files()
        config = configparser.ConfigParser()
        try:
            config.read(ini_file)
            bandit_config = {k: v for k, v in config.items("bandit")}

            # test-set profile
            if bandit_config.get("skips"):
                profile["exclude"] = (
                    bandit_config.get("skips").replace("S", "B").split(",")
                )
            if bandit_config.get("tests"):
                profile["include"] = (
                    bandit_config.get("tests").replace("S", "B").split(",")
                )

            # file include/exclude
            if bandit_config.get("targets"):
                paths = bandit_config.get("targets").split(",")
                for path in paths:
                    # convert absolute to relative
                    if path.startswith("/"):
                        path = "." + path
                    target_paths.add(Path(path))

            if bandit_config.get("exclude"):
                paths = bandit_config.get("exclude").split(",")
                for path in paths:
                    # convert absolute to relative
                    if path.startswith("/"):
                        path = "." + path
                    excluded_paths.add(Path(path))

        except (configparser.Error, KeyError, TypeError) as e:
            profile = {}
            if str(e) != "No section: 'bandit'":
                sys.stderr.write(f"Unable to parse config file: {e}")

        return cls(profile, target_paths, excluded_paths)


class BanditTester(object):
    """Flake8 class for checking code for bandit test errors.

    This class is necessary and used by flake8 to check the python
    file or files that are being tested.

    """

    name = "flake8-bandit"
    version = __version__

    def __init__(self, tree, filename, lines):
        self.filename = filename
        self.tree = tree
        self.lines = lines

    def _check_source(self):
        config = Flake8BanditConfig.from_config_file()

        # potentially exit early if bandit config tells us to
        filepath = Path(self.filename)
        filepaths = set(filepath.parents)
        filepaths.add(filepath)
        if (
            config.excluded_paths and config.excluded_paths.intersection(filepaths)
        ) or (
            config.target_paths
            and len(config.target_paths.intersection(filepaths)) == 0
        ):
            return []

        bnv = BanditNodeVisitor(
            self.filename,
            BanditMetaAst(),
            BanditTestSet(BanditConfig(), profile=config.profile),
            False,
            [],
            Metrics(),
        )
        bnv.generic_visit(self.tree)
        return [
            {
                # flake8-bugbear uses bandit default prefix 'B'
                # so this plugin replaces the 'B' with an 'S' for Security
                # See https://github.com/PyCQA/flake8-bugbear/issues/37
                "test_id": item.test_id.replace("B", "S"),
                "issue_text": item.text,
                "line_number": item.lineno,
                "severity": item.severity,
                "confidence": item.confidence,
            }
            for item in bnv.tester.results
        ]

    def run(self):
        """run will check file source through the bandit code linter."""

        if not self.tree or not self.lines:
            self._load_source()
        for warn in self._check_source():
            message = "%s %s (CL: %s, risk: %s)" % (
                warn["test_id"],
                warn["issue_text"],
                warn["confidence"],
                warn["severity"],
            )
            yield (warn["line_number"], 0, message, type(self))

    def _load_source(self):
        """Loads the file in a way that auto-detects source encoding and deals
        with broken terminal encodings for stdin.

        Stolen from flake8_import_order because it's good.
        """

        if self.filename in ("stdin", "-", None):
            self.filename = "stdin"
            self.lines = stdin_utils.stdin_get_value().splitlines(True)
        else:
            self.lines = pycodestyle.readlines(self.filename)
        if not self.tree:
            self.tree = ast.parse("".join(self.lines))
