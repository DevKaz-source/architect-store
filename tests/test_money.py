from __future__ import annotations

import unittest

from app.money import InvalidMoney, cents_to_api_amount, format_brl, parse_brl_to_cents


class MoneyTests(unittest.TestCase):
    def test_parses_brazilian_formats(self) -> None:
        self.assertEqual(parse_brl_to_cents("R$ 1.234,56"), 123456)
        self.assertEqual(parse_brl_to_cents("25,00"), 2500)
        self.assertEqual(parse_brl_to_cents("25.00"), 2500)
        self.assertEqual(parse_brl_to_cents("25"), 2500)

    def test_rejects_invalid_values(self) -> None:
        for value in ("", "abc", "0", "-1", "1,999"):
            with self.subTest(value=value), self.assertRaises(InvalidMoney):
                parse_brl_to_cents(value)

    def test_formats_brl(self) -> None:
        self.assertEqual(format_brl(123456), "R$ 1.234,56")
        self.assertEqual(format_brl(-50), "-R$ 0,50")
        self.assertEqual(cents_to_api_amount(509), "5.09")


if __name__ == "__main__":
    unittest.main()
