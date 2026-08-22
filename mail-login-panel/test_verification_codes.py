import unittest

from main import extract_verification_code


class VerificationCodeExtractionTests(unittest.TestCase):
    def test_chinese_labeled_code(self):
        self.assertEqual(
            extract_verification_code("登录验证", "您的验证码：482913，十分钟内有效。", ""),
            "482913",
        )

    def test_english_labeled_code(self):
        self.assertEqual(
            extract_verification_code("Sign in", "Your verification code is 731204.", ""),
            "731204",
        )

    def test_code_before_label(self):
        self.assertEqual(
            extract_verification_code("Security notice", "AB12CD is your security code.", ""),
            "AB12CD",
        )

    def test_subject_context_with_isolated_code(self):
        self.assertEqual(
            extract_verification_code("Your login code", "<div>846291</div>", ""),
            "846291",
        )

    def test_google_cloud_year_is_not_a_code(self):
        text = (
            "开始在 Google Cloud 上构建。领取 300 美元迎新赠金，体验 150 多款产品。 "
            "2026 Google LLC 1600 Amphitheatre Parkway, Mountain View, CA 94043。"
        )
        self.assertIsNone(extract_verification_code("了解 Google 账号的后续步骤", text, ""))

    def test_verification_subject_does_not_turn_year_into_code(self):
        text = "请验证您的邮箱地址。版权所有 2026 Google LLC，邮编 94043。"
        self.assertIsNone(extract_verification_code("Google Cloud 验证码", text, ""))

    def test_unlabeled_order_number_is_not_a_code(self):
        self.assertIsNone(
            extract_verification_code("订单已发货", "订单 837201 已于 2026-08-21 发出。", "")
        )

    def test_directly_labeled_four_digit_year_can_still_be_an_otp(self):
        self.assertEqual(
            extract_verification_code("登录", "验证码：2026", ""),
            "2026",
        )


if __name__ == "__main__":
    unittest.main()
