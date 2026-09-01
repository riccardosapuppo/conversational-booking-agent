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


if __name__ == "__main__":
    unittest.main()
