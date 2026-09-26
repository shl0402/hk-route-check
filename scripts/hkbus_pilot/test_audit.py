import unittest
from audit import terminal_evidence


class IdentityTests(unittest.TestCase):
	def test_exact_endpoints_allow_punctuation_and_reverse_direction(self):
		a = {'infobox': [{'label': '起訖點', 'value': '紅磡（紅鸞道） ↔ 廣播道 ●'}]}
		self.assertTrue(
			terminal_evidence(
				a, [{'locStartNameC': '廣播道', 'locEndNameC': '紅磡(紅鸞道)'}]
			)[0]
		)

	def test_substring_is_not_proof(self):
		a = {'infobox': [{'label': '起訖點', 'value': '紅磡 ↔ 廣播道 ●'}]}
		self.assertFalse(
			terminal_evidence(
				a, [{'locStartNameC': '紅磡(紅鸞道)', 'locEndNameC': '九龍塘(廣播道)'}]
			)[0]
		)

	def test_different_terminal_and_missing_values_not_proof(self):
		a = {'infobox': [{'label': '起訖點', 'value': '黃大仙站 ↔ 沙田坳邨 ●'}]}
		m = [{'locStartNameC': '黃大仙站', 'locEndNameC': '慈雲山(北)'}]
		self.assertFalse(terminal_evidence(a, m)[0])
		self.assertFalse(terminal_evidence({'infobox': []}, m)[0])


if __name__ == '__main__':
	unittest.main()
