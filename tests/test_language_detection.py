from pr_guardian.languages.detector import detect_languages, identify_language


class TestIdentifyLanguage:
    def test_python_extension(self):
        assert identify_language("src/main.py") == "python"

    def test_typescript_extension(self):
        assert identify_language("src/app.ts") == "typescript"

    def test_tsx_extension(self):
        assert identify_language("src/Component.tsx") == "typescript"

    def test_csharp_extension(self):
        assert identify_language("Program.cs") == "csharp"

    def test_go_extension(self):
        assert identify_language("main.go") == "go"

    def test_sql_extension(self):
        assert identify_language("migrations/001.sql") == "sql"

    def test_terraform_extension(self):
        assert identify_language("infra/main.tf") == "terraform"

    def test_dockerfile_name(self):
        assert identify_language("Dockerfile") == "dockerfile"

    def test_dockerfile_variant(self):
        assert identify_language("Dockerfile.prod") == "dockerfile"

    def test_unknown_extension(self):
        assert identify_language("file.xyz") == "unknown"

    def test_markdown(self):
        assert identify_language("README.md") == "markdown"

    def test_yaml(self):
        assert identify_language("config.yml") == "yaml"


class TestDetectLanguages:
    def test_single_language(self):
        result = detect_languages(["src/main.py", "src/utils.py"])
        assert result.primary_language == "python"
        assert result.language_count == 1
        assert not result.cross_stack

    def test_multi_language(self):
        result = detect_languages([
            "src/main.py", "src/app.ts", "migrations/001.sql"
        ])
        assert result.language_count == 3
        assert result.cross_stack  # python + typescript + sql

    def test_cross_stack_excludes_config(self):
        result = detect_languages(["src/main.py", "config.yml"])
        assert not result.cross_stack  # yaml is not a runtime language

    def test_empty_files(self):
        result = detect_languages([])
        assert result.primary_language == "unknown"
        assert result.language_count == 0

    def test_files_method(self):
        result = detect_languages(["src/a.py", "src/b.py", "src/c.ts"])
        assert result.files("python") == ["src/a.py", "src/b.py"]
        assert result.files("typescript") == ["src/c.ts"]
        assert result.files("go") == []
