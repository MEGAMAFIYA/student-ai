"""
🧾 receipt_check.py (chekni bot tomonidan tekshirish) uchun testlar.

Bu modul Telegram/AI/tarmoqqa bog'liq EMAS — sof funksiyalar, shuning uchun
hech qanday stub kerak emas.

Ishga tushirish: python3 -m unittest tests.test_receipt_check -v
"""

import unittest

import receipt_check as rc


class ParseAmountTests(unittest.TestCase):
    def test_plain_int_and_float(self):
        self.assertEqual(rc.parse_amount(10000), 10000)
        self.assertEqual(rc.parse_amount(10000.0), 10000)

    def test_space_separated(self):
        self.assertEqual(rc.parse_amount("10 000"), 10000)

    def test_comma_thousands(self):
        self.assertEqual(rc.parse_amount("10,000"), 10000)

    def test_dot_thousands(self):
        self.assertEqual(rc.parse_amount("1.000.000"), 1000000)
        self.assertEqual(rc.parse_amount("1,000,000"), 1000000)

    def test_decimal_with_currency_suffix(self):
        self.assertEqual(rc.parse_amount("10 000,00 UZS"), 10000)
        self.assertEqual(rc.parse_amount("10000.00"), 10000)

    def test_none_and_garbage(self):
        self.assertIsNone(rc.parse_amount(None))
        self.assertIsNone(rc.parse_amount("hech narsa"))
        self.assertIsNone(rc.parse_amount(""))
        self.assertIsNone(rc.parse_amount(True))  # bool int emas deb hisoblanadi


class CardMatchTests(unittest.TestCase):
    CARD = "8600123456783456"

    def test_full_number_matches(self):
        self.assertTrue(rc.card_matches(self.CARD, "8600123456783456"))

    def test_full_number_with_separators(self):
        self.assertTrue(rc.card_matches(self.CARD, "8600 1234 5678 3456"))
        self.assertTrue(rc.card_matches(self.CARD, "8600-1234-5678-3456"))

    def test_masked_middle_digits_matches(self):
        self.assertTrue(rc.card_matches(self.CARD, "8600 12** **** 3456"))
        self.assertTrue(rc.card_matches(self.CARD, "8600 12•• •••• 3456"))

    def test_only_last_four_digits_matches(self):
        self.assertTrue(rc.card_matches(self.CARD, "**** 3456"))
        self.assertTrue(rc.card_matches(self.CARD, "3456"))

    def test_only_first_digits_matches(self):
        self.assertTrue(rc.card_matches(self.CARD, "8600 12** **** ****"))

    def test_wrong_last_digits_rejected(self):
        self.assertFalse(rc.card_matches(self.CARD, "**** 1111"))

    def test_too_few_visible_digits_rejected(self):
        self.assertFalse(rc.card_matches(self.CARD, "**** **56"))
        self.assertFalse(rc.card_matches(self.CARD, "**** ****"))

    def test_empty_inputs_rejected(self):
        self.assertFalse(rc.card_matches("", "3456"))
        self.assertFalse(rc.card_matches(self.CARD, ""))
        self.assertFalse(rc.card_matches(self.CARD, None))


class NameMatchTests(unittest.TestCase):
    HOLDER = "Alisher Karimov"

    def test_exact_match(self):
        self.assertTrue(rc.name_matches(self.HOLDER, "Alisher Karimov"))

    def test_reversed_order(self):
        self.assertTrue(rc.name_matches(self.HOLDER, "Karimov Alisher"))

    def test_initial_plus_surname(self):
        self.assertTrue(rc.name_matches(self.HOLDER, "A. Karimov"))
        self.assertTrue(rc.name_matches(self.HOLDER, "ALISHER K."))

    def test_cyrillic_variant(self):
        self.assertTrue(rc.name_matches(self.HOLDER, "Алишер Каримов"))

    def test_minor_ocr_typo_still_matches(self):
        self.assertTrue(rc.name_matches(self.HOLDER, "Alisher Karimoy"))

    def test_different_person_rejected(self):
        self.assertFalse(rc.name_matches(self.HOLDER, "Bekzod Yusupov"))

    def test_both_initials_only_rejected(self):
        # Xavfsizlik uchun: kamida bitta TO'LIQ so'z mos kelishi shart.
        self.assertFalse(rc.name_matches(self.HOLDER, "A. K."))

    def test_empty_inputs_rejected(self):
        self.assertFalse(rc.name_matches("", "Alisher Karimov"))
        self.assertFalse(rc.name_matches(self.HOLDER, ""))


class VerifyReceiptTests(unittest.TestCase):
    def test_all_correct_is_ok(self):
        extracted = {
            "amount": "10 000", "receiver_card": "8600 12** **** 3456",
            "receiver": "ALISHER K.", "confidence": 0.9,
        }
        result = rc.verify_receipt(
            extracted, expected_amount=10000, card_number="8600123456783456",
            card_holder="Alisher Karimov", min_confidence=0.6,
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.card and result.name and result.amount and result.confidence_ok)

    def test_card_mismatch_fails(self):
        extracted = {
            "amount": "10000", "receiver_card": "**** 1111",
            "receiver": "Alisher Karimov", "confidence": 0.9,
        }
        result = rc.verify_receipt(
            extracted, expected_amount=10000, card_number="8600123456783456",
            card_holder="Alisher Karimov", min_confidence=0.6,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.card)
        self.assertTrue(result.notes)

    def test_amount_mismatch_fails(self):
        extracted = {
            "amount": "5000", "receiver_card": "3456",
            "receiver": "Alisher Karimov", "confidence": 0.9,
        }
        result = rc.verify_receipt(
            extracted, expected_amount=10000, card_number="8600123456783456",
            card_holder="Alisher Karimov", min_confidence=0.6,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.amount)

    def test_low_confidence_fails_even_if_everything_else_matches(self):
        extracted = {
            "amount": "10000", "receiver_card": "3456",
            "receiver": "Alisher Karimov", "confidence": 0.2,
        }
        result = rc.verify_receipt(
            extracted, expected_amount=10000, card_number="8600123456783456",
            card_holder="Alisher Karimov", min_confidence=0.6,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.confidence_ok)

    def test_empty_extraction_fails_safely(self):
        result = rc.verify_receipt(
            None, expected_amount=10000, card_number="8600123456783456",
            card_holder="Alisher Karimov",
        )
        self.assertFalse(result.ok)

    def test_unconfigured_card_or_holder_is_skipped_not_crashed(self):
        # PAYMENT_CARD_NUMBER/HOLDER sozlanmagan bo'lsa — bot ularni tekshirmasdan
        # o'tkazib yuboradi (crash bo'lmaydi), lekin baribir 'ok' faqat summa+ishonch
        # to'g'ri bo'lganda ham bo'lmaydi, chunki card/name None (tasdiqlanmagan) qoladi.
        extracted = {"amount": "10000", "confidence": 0.9}
        result = rc.verify_receipt(
            extracted, expected_amount=10000, card_number="", card_holder="",
        )
        self.assertIsNone(result.card)
        self.assertIsNone(result.name)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
