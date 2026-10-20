"""The picture in the README, against the graph it claims to be a picture of.

A diagram drawn by hand is accurate the day it is drawn. This one is generated
from the compiled graph, and these tests fail if the two drift apart — a node
added to the conversation and forgotten in the documentation should break the
build, not quietly make the README wrong.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.diagram import nodes


def readme() -> str:
    return (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def drawn_in_readme() -> str:
    found = re.search(r"```mermaid\n(.*?)```", readme(), re.DOTALL)
    assert found is not None, "the README has no mermaid diagram in it"
    return found.group(1)


class ThePictureAndTheGraph(unittest.TestCase):
    def test_every_node_in_the_graph_is_in_the_picture(self) -> None:
        picture = drawn_in_readme()

        for node in nodes():
            with self.subTest(node=node):
                self.assertIn(f"{node}({node})", picture)

    def test_and_the_picture_invents_none(self) -> None:
        picture = drawn_in_readme()
        shown = set(re.findall(r"^\t(\w+)\(\w+\)$", picture, re.MULTILINE))

        self.assertEqual(shown, set(nodes()))

    def test_every_node_ends_the_turn(self) -> None:
        # The graph is entered once per message rather than run to completion.
        # If a node ever stops going to __end__, the sentence in the README
        # explaining that is no longer true.
        picture = drawn_in_readme()

        for node in nodes():
            if node == "understand":
                continue
            with self.subTest(node=node):
                self.assertIn(f"{node} --> __end__;", picture)


class TheFilesTheReadmePointsAt(unittest.TestCase):
    def test_they_all_exist(self) -> None:
        # Links written by hand to files that get moved. Checking them costs
        # four lines and stops the first thing a reader clicks being a 404.
        here = Path(__file__).resolve().parents[1]

        for link in re.findall(r"\]\((?!https?:)([^)#]+)\)", readme()):
            with self.subTest(link=link):
                self.assertTrue((here / link).exists(), f"README points at missing {link}")

    def test_the_commands_it_tells_you_to_run_exist(self) -> None:
        here = Path(__file__).resolve().parents[1]

        # The ones belonging to this repository. `python -m unittest` is the
        # standard library and is not going anywhere.
        for module in re.findall(r"python -m ((?:booking_agent|tools)[\w.]*)", readme()):
            with self.subTest(module=module):
                path = here / Path(*module.split("."))
                self.assertTrue(
                    path.with_suffix(".py").exists() or (path / "__main__.py").exists(),
                    f"README tells you to run {module}, which is not here",
                )


def counted(module: str = "") -> int:
    """How many checks there are, all of them or one file's worth."""
    here = Path(__file__).resolve().parents[1]
    loader = unittest.TestLoader()

    if module:
        return loader.loadTestsFromName(module).countTestCases()

    return loader.discover(start_dir=str(here / "tests"), top_level_dir=str(here)).countTestCases()


class TheCountItQuotes(unittest.TestCase):
    def test_there_are_as_many_tests_as_it_says(self) -> None:
        # "# 144 tests" is a measurement, and a measurement typed into prose
        # stops being one the moment somebody adds a file. Counting them back
        # costs four lines and leaves the number either right or failing, which
        # are the only two states worth having: the alternative is a README
        # that sounds precise about a repository it stopped reading.
        said = re.search(r"# (\d+) tests", readme())
        assert said is not None, "the README no longer says how many tests there are"

        found = counted()

        self.assertEqual(
            int(said.group(1)),
            found,
            f"the README says {said.group(1)} tests and this run found {found}",
        )

    def test_and_as_many_of_them_need_a_browser_as_it_says(self) -> None:
        """Three numbers about one suite, and not one of them typed twice.

        "CI runs 174 of the 189" is the sentence that matters here, because it
        is the one admitting that some of these checks are not run where it
        counts. A figure like that going quietly stale is worse than never
        having been quoted: it turns an honest disclosure into a wrong one, in
        the paragraph whose whole purpose was to be trusted.
        """
        prose = readme()

        needs = re.search(r"\*\*(\d+) of those tests need a browser", prose)
        assert needs is not None, "the README no longer says how many tests need a browser"

        split = re.search(r"\*\*CI runs (\d+) of the (\d+)\*\*", prose)
        assert split is not None, "the README no longer says how many of them CI runs"

        found = counted()
        in_a_browser = counted("tests.test_console")

        self.assertEqual(int(needs.group(1)), in_a_browser, "the browser count has drifted")
        self.assertEqual(int(split.group(1)), found - in_a_browser, "CI runs a different number")
        self.assertEqual(int(split.group(2)), found, "and out of a different total")


#: Number words as the README writes them.
_WORDS = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


class WhatTheReadmeSaysAboutItself(unittest.TestCase):
    """Sentences in the README that a run of something can contradict, and do."""

    def test_as_many_packages_are_pinned_as_it_says(self) -> None:
        # "It said four until somebody counted the file." Now the counting is
        # done here, so the next package added is a failure, not a paragraph.
        said = re.search(r"\b([A-Z][a-z]+) packages are installed, all of them pinned", readme())
        assert said is not None, "the README no longer says how many packages are installed"

        here = Path(__file__).resolve().parents[1]
        pinned = [
            line
            for line in (here / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if re.match(r"^[A-Za-z0-9_.-]+==\S+$", line.strip())
        ]

        self.assertEqual(_WORDS.get(said.group(1).lower()), len(pinned), f"pinned: {pinned}")
        self.assertIn(f"the {said.group(1).lower()} pinned packages", readme())

    def test_the_golden_figures_are_the_ones_a_run_gives(self) -> None:
        """The figures for now, against a run made now.

        The figures for the first runs are history and are not checked here:
        they are commits, and the README says which, so they can be run again.
        """
        from tools.evals import load, run_all, summarise

        prose = " ".join(readme().split())
        now = re.search(
            r"\*\*(\d+) of (\d+) pass, (\d+) miss and (\w+) do harm\*\*; \*\*(\d+) of the (\d+) critical\*\* pass; "
            r"and \*\*(\d+) of the (\d+) still held out\*\* pass",
            prose,
        )
        assert now is not None, "the README no longer says how the golden calls do now"

        golden = load()
        summary = summarise(run_all(golden), golden)
        harm = 0 if now.group(4) == "none" else int(now.group(4))

        self.assertEqual(
            (int(now.group(1)), int(now.group(2)), int(now.group(3)), harm),
            (summary.all.passed, summary.all.calls, summary.all.missed, summary.all.harmed),
        )
        self.assertEqual((int(now.group(5)), int(now.group(6))), (summary.critical.passed, summary.critical.calls))
        self.assertEqual((int(now.group(7)), int(now.group(8))), (summary.held_out.passed, summary.held_out.calls))

    def test_the_production_reconstruction_section_is_word_for_word_what_it_was(self) -> None:
        # Kept identical on purpose, whatever is added around it: it is the
        # statement of what this repository is, and it is not edited in
        # passing. Its fingerprint is the one it had at d7f436c.
        import hashlib

        found = re.search(r"## Production reconstruction\n(.*?)\n---\n", readme(), re.DOTALL)
        assert found is not None, "the README has no Production reconstruction section"

        self.assertEqual(
            hashlib.sha256(found.group(1).encode("utf-8")).hexdigest(),
            "0e4476c0706c7bc47639c1de81270d19baecfbf18ea5492f1d9726035aa13ab5",
        )


if __name__ == "__main__":
    unittest.main()
