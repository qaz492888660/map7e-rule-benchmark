import unittest

from normalize import normalize_name


class NormalizeTests(unittest.TestCase):
    def test_trims_and_lowercases(self):
        self.assertEqual(normalize_name("  Alice Smith  "), "alice smith")


if __name__ == "__main__":
    unittest.main()
