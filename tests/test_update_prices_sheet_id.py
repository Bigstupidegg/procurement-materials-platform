from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_prices_v6_3_C3_1_DRYRUN.py"


class UpdatePricesSheetIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_sheet_id_environment_default_is_empty(self):
        assignment = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SHEET_ID" for target in node.targets)
        )
        strip_call = assignment.value
        self.assertIsInstance(strip_call, ast.Call)
        self.assertEqual(ast.unparse(strip_call.func), "os.getenv('GOOGLE_SHEET_ID', '').strip")
        getenv_call = strip_call.func.value
        self.assertEqual(ast.unparse(getenv_call.func), "os.getenv")
        self.assertEqual(ast.literal_eval(getenv_call.args[0]), "GOOGLE_SHEET_ID")
        self.assertEqual(ast.literal_eval(getenv_call.args[1]), "")
        self.assertNotIn("DEFAULT_SHEET_ID", self.source)

    def test_missing_sheet_id_fails_closed_with_clear_error(self):
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "require_sheet_id"
        )
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), "exec"), namespace)
        require_sheet_id = namespace["require_sheet_id"]

        for missing in (None, "", "   "):
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(RuntimeError, "GOOGLE_SHEET_ID 未設定"):
                    require_sheet_id(missing)
        self.assertEqual(require_sheet_id(" sheet-from-environment "), "sheet-from-environment")

    def test_sheet_id_is_validated_before_market_or_sheet_access(self):
        main_guard = self.source.index("sheet_id = require_sheet_id(SHEET_ID)")
        first_market_fetch = self.source.index("results = {}", main_guard)
        sheet_open = self.source.index("book = gc.open_by_key(sheet_id)", main_guard)
        self.assertLess(main_guard, first_market_fetch)
        self.assertLess(main_guard, sheet_open)


if __name__ == "__main__":
    unittest.main()
