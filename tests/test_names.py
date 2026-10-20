"""What the diary will write down as somebody's name, and what it will not.

The rule refuses what a name cannot be rather than describing what one is, so
the names here are the ones a careless rule would refuse: hyphens, apostrophes,
accents, a surname that starts with a letter and an apostrophe, a first name
that is also an English word.
"""

from __future__ import annotations

import unittest

from booking_agent.clinic.names import plausible_name, why_not_a_name


class WhatIsAName(unittest.TestCase):
    def test_names_as_people_have_them(self) -> None:
        for name in (
            "Mario Rossi",
            "Anna",
            "Anne-Marie O'Neill",
            "Giulia D'Amico",
            "Don Ricci",
            "Grace",
            "J. Smith",
            "Niccolò Bianchi",
            "Rossi, Mario",
            "Maria De Luca",
        ):
            with self.subTest(name=name):
                self.assertTrue(plausible_name(name), why_not_a_name(name))


class WhatIsNot(unittest.TestCase):
    """Every one of the first four was once taken for a name, and "yes" booked under."""

    def test_what_was_said_instead_of_a_name(self) -> None:
        for said in (
            "yes",
            "RSSMRA85T10A562S",
            "Ann4 B1anchi",
            "I'd rather not say",
            "no wait, no contrast",
            "the doctor didn't say",
            "left",
            "I'm not telling you",
        ):
            with self.subTest(said=said):
                self.assertFalse(plausible_name(said))

    def test_nothing_much(self) -> None:
        for said in ("", "   ", ".", "J", "--"):
            with self.subTest(said=said):
                self.assertFalse(plausible_name(said))

    def test_and_a_sentence_however_short_its_words(self) -> None:
        self.assertFalse(plausible_name("Anna Maria Rosa Laura Bianchi"))

    def test_the_refusal_says_why(self) -> None:
        # The server hands this to whatever asked, so it has to be a sentence.
        self.assertIn("digits", why_not_a_name("RSSMRA85T10A562S"))
        self.assertIn("'yes'", why_not_a_name("yes"))
        self.assertEqual(why_not_a_name("Mario Rossi"), "")


if __name__ == "__main__":
    unittest.main()
