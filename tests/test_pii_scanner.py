import asyncio
import tempfile
from pathlib import Path

from pr_guardian.mechanical.pii_scanner import run_pii_scanner


class TestPIIScanner:
    def _run(self, files_content: dict[str, str]) -> object:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            changed_files = []
            for name, content in files_content.items():
                file_path = repo_path / name
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)
                changed_files.append(name)
            return asyncio.get_event_loop().run_until_complete(
                run_pii_scanner(repo_path, changed_files)
            )

    def test_clean_file_passes(self):
        result = self._run({"src/main.py": "print('hello')\n"})
        assert result.passed

    def test_password_in_log_fails(self):
        result = self._run({"src/main.py": "logger.info(f'user password: {pwd}')\n"})
        assert not result.passed
        assert any("password" in f.message.lower() for f in result.findings)

    def test_email_in_log_warns(self):
        result = self._run({"src/main.py": "logger.info(f'user email: {email}')\n"})
        assert result.passed  # email is warning, not error
        assert len(result.findings) > 0

    def test_ssn_in_test_data_fails(self):
        result = self._run({"tests/test_user.py": 'ssn = "123-45-6789"\n'})
        assert not result.passed

    def test_no_findings_for_non_matching(self):
        result = self._run({"src/main.py": "x = 1 + 2\n"})
        assert result.passed
        assert len(result.findings) == 0
