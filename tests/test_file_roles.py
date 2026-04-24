from pr_guardian.config.schema import FileRolesConfig
from pr_guardian.discovery.file_roles import classify_file_roles
from pr_guardian.models.context import FileRole


class TestFileRolesGlobPatterns:
    """Verify fnmatch patterns for root-level files that ** alone would miss."""

    def test_root_md_file_is_docs(self):
        # "**/*.md" requires a slash, so "README.md" needs the "*.md" fallback pattern
        roles = classify_file_roles(["README.md"], FileRolesConfig())
        assert FileRole.DOCS in roles["README.md"]

    def test_nested_md_file_is_docs(self):
        roles = classify_file_roles(["docs/guide.md"], FileRolesConfig())
        assert FileRole.DOCS in roles["docs/guide.md"]

    def test_root_migrations_file_is_generated(self):
        # "**/migrations/**" won't match "migrations/001.sql"; needs "migrations/**"
        roles = classify_file_roles(["migrations/001_auto.sql"], FileRolesConfig())
        assert FileRole.GENERATED in roles["migrations/001_auto.sql"]

    def test_nested_migrations_file_is_generated(self):
        roles = classify_file_roles(["src/db/migrations/002.py"], FileRolesConfig())
        assert FileRole.GENERATED in roles["src/db/migrations/002.py"]

    def test_unrelated_file_is_production(self):
        roles = classify_file_roles(["src/handler.py"], FileRolesConfig())
        assert FileRole.PRODUCTION in roles["src/handler.py"]
        assert FileRole.DOCS not in roles["src/handler.py"]
        assert FileRole.GENERATED not in roles["src/handler.py"]
