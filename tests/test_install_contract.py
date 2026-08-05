from __future__ import annotations

from pathlib import Path
import unittest

from scripts.check_runtime_compatibility import SUPPORTED_PYTHON


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_BLOOMBERG_INDEX = (
    "https://blpapi.bloomberg.com/repository/releases/python/simple"
)


class InstallContractTests(unittest.TestCase):
    def test_owner_runtime_supports_python_312_and_313(self) -> None:
        self.assertEqual(SUPPORTED_PYTHON, ((3, 12), (3, 13)))

    def test_windows_installer_uses_the_dedicated_bloomberg_index(self) -> None:
        bootstrap = (PROJECT_ROOT / "scripts" / "run_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(OFFICIAL_BLOOMBERG_INDEX + "/", bootstrap)
        self.assertIn(
            '& $VenvPython -m pip install "--index-url=$BloombergIndexUrl" blpapi',
            bootstrap,
        )
        self.assertIn("python -m pip install --index-url=", bootstrap)
        self.assertNotIn("--index-url=https://bloomberg.com ", bootstrap)

    def test_windows_environment_is_user_local_and_never_repo_local(self) -> None:
        bootstrap = (PROJECT_ROOT / "scripts" / "run_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('Join-Path $env:USERPROFILE "Pyenvs"', bootstrap)
        self.assertIn('$VenvPath = Join-Path $VenvRoot "trade_builder"', bootstrap)
        self.assertIn('. $ActivateScript', bootstrap)
        self.assertIn('$env:VIRTUAL_ENV = $VenvPath', bootstrap)
        self.assertNotIn('Join-Path $RepoRoot ".venv"', bootstrap)

    def test_launcher_repairs_a_missing_or_incomplete_environment(self) -> None:
        launcher = (PROJECT_ROOT / "UPDATE_AND_OPEN.bat").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "INSTALL_BLOOMBERG.bat").read_text(encoding="utf-8")
        bootstrap = (PROJECT_ROOT / "scripts" / "run_windows.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts\\run_windows.ps1", launcher)
        self.assertIn("scripts\\run_windows.ps1", installer)
        self.assertIn("-InstallOnly", installer)
        self.assertIn("-m ensurepip --upgrade", bootstrap)
        self.assertIn("Test-ManagedDependencies", bootstrap)
        self.assertIn("function Ensure-EmbeddedDashboardData", bootstrap)
        self.assertIn("& $VenvPython $BuildDashboardPath", bootstrap)
        self.assertIn("Ensure-EmbeddedDashboardData", bootstrap)
        self.assertNotIn(".venv", launcher)
        self.assertNotIn("py -3", launcher)
        self.assertIn(
            '& $VenvPython (Join-Path $RepoRoot "scripts\\run_dashboard.py") --open',
            bootstrap,
        )

    def test_requirement_ranges_cover_verified_packages(self) -> None:
        base = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        bloomberg = (PROJECT_ROOT / "requirements-bloomberg.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("polars>=1.38,<2", base)
        self.assertIn("blpapi>=3.25,<4", bloomberg)
        self.assertIn(OFFICIAL_BLOOMBERG_INDEX, bloomberg)


if __name__ == "__main__":
    unittest.main()
