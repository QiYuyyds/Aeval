"""Unit tests for agent_eval suite YAML loading and validation (task 2.2)."""

import pytest

from agent_eval.core.suite import SuiteLoadError, load_suite
from agent_eval.core.types import EvalSuite

VALID_SUITE_YAML = """
name: demo-suite
description: A valid suite
version: 1.2.0
tasks:
  - id: task-1
    description: Say hello
    prompt: "Hello"
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "hello"
              target: transcript
"""


def _write(tmp_path, content: str):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(content, encoding="utf-8")
    return suite_file


def test_valid_yaml_loads(tmp_path):
    suite = load_suite(_write(tmp_path, VALID_SUITE_YAML))
    assert isinstance(suite, EvalSuite)
    assert suite.name == "demo-suite"
    assert suite.version == "1.2.0"
    assert len(suite.tasks) == 1
    assert suite.tasks[0].graders[0].name == "code_based"


def test_from_yaml_delegates_to_loader(tmp_path):
    suite = EvalSuite.from_yaml(str(_write(tmp_path, VALID_SUITE_YAML)))
    assert suite.name == "demo-suite"


def test_missing_file_raises_with_path(tmp_path):
    with pytest.raises(SuiteLoadError, match="not found"):
        load_suite(tmp_path / "nope.yaml")


def test_directory_as_suite_file_raises(tmp_path):
    with pytest.raises(SuiteLoadError, match="Cannot read"):
        load_suite(tmp_path)


def test_invalid_yaml_syntax(tmp_path):
    suite_file = _write(tmp_path, "name: [unclosed\n  bad indent: :")
    with pytest.raises(SuiteLoadError, match="Invalid YAML"):
        load_suite(suite_file)


def test_non_mapping_yaml(tmp_path):
    suite_file = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(SuiteLoadError, match="YAML mapping"):
        load_suite(suite_file)


def test_duplicate_task_ids(tmp_path):
    yaml_text = """
name: dup
tasks:
  - id: same-id
    prompt: a
    graders:
      - type: code
        name: code_based
  - id: same-id
    prompt: b
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="Duplicate task IDs"):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-beta", "a.b.c", ""])
def test_invalid_version_format(tmp_path, version):
    yaml_text = f"""
name: bad-version
version: "{version}"
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="version"):
        load_suite(_write(tmp_path, yaml_text))


def test_empty_graders_rejected(tmp_path):
    yaml_text = """
name: no-graders
tasks:
  - id: t1
    prompt: a
    graders: []
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


def test_missing_graders_rejected(tmp_path):
    yaml_text = """
name: no-graders
tasks:
  - id: t1
    prompt: a
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize(
    "bad_name", ["", "1starts_with_digit", "has space", "dot.name", "中文"]
)
def test_invalid_grader_name(tmp_path, bad_name):
    yaml_text = f"""
name: bad-grader-name
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: "{bad_name}"
"""
    with pytest.raises(SuiteLoadError, match="name"):
        load_suite(_write(tmp_path, yaml_text))


def test_empty_suite_name_rejected(tmp_path):
    yaml_text = """
name: "   "
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="name"):
        load_suite(_write(tmp_path, yaml_text))


def test_suite_name_over_128_chars_rejected(tmp_path):
    yaml_text = """
name: {name}
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
""".format(name="x" * 129)
    with pytest.raises(SuiteLoadError, match="128"):
        load_suite(_write(tmp_path, yaml_text))


def test_no_tasks_rejected(tmp_path):
    yaml_text = """
name: empty
tasks: []
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


def test_negative_weight_rejected(tmp_path):
    yaml_text = """
name: bad-weight
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
        weight: -1.0
"""
    with pytest.raises(SuiteLoadError, match="weight"):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize("sample_count", [0, 11])
def test_sample_count_out_of_range_rejected(tmp_path, sample_count):
    yaml_text = f"""
name: bad-sample-count
tasks:
  - id: t1
    prompt: a
    graders:
      - type: model
        name: model_based
        sample_count: {sample_count}
"""
    with pytest.raises(SuiteLoadError, match="sample_count"):
        load_suite(_write(tmp_path, yaml_text))


# ─── 证据与预算声明 (specs/suite-format) ──────────────────────────────────────


def _task_yaml(extra: str) -> str:
    return f"""
name: evidence-suite
tasks:
  - id: t1
    prompt: a
{extra}    graders:
      - type: code
        name: code_based
"""


class TestEvidenceAndBudgetFields:
    def test_all_new_fields_omissible_with_unchanged_behaviour(self, tmp_path):
        """Scenario: 全部新字段可缺省 → 加载成功且既有行为不变。"""
        suite = load_suite(_write(tmp_path, VALID_SUITE_YAML))
        task = suite.tasks[0]
        assert task.tags == []
        assert (task.category, task.difficulty) == (None, None)
        assert (task.optimal_steps, task.step_budget, task.token_budget) == (None,) * 3
        assert task.cost_budget is None
        assert task.capture_tool_arguments is None  # 继承 suite
        assert suite.capture_tool_arguments is False
        assert suite.capture_for(task) is False

    def test_new_fields_round_trip_and_task_tightens_suite(self, tmp_path):
        yaml_text = """
name: evidence-suite
capture_tool_arguments: true
tasks:
  - id: t1
    prompt: a
    tags: [filesystem, regression]
    category: tools
    difficulty: hard
    optimal_steps: 3
    step_budget: 10
    token_budget: 20000
    cost_budget: 0.25
    capture_tool_arguments: false
    graders:
      - type: code
        name: code_based
"""
        suite = load_suite(_write(tmp_path, yaml_text))
        task = suite.tasks[0]
        assert task.tags == ["filesystem", "regression"]
        assert task.category == "tools"
        assert task.difficulty == "hard"
        assert (task.optimal_steps, task.step_budget, task.token_budget) == (3, 10, 20000)
        assert task.cost_budget == 0.25
        # Scenario: 单任务收紧 —— task 级声明覆盖 suite 级
        assert suite.capture_tool_arguments is True
        assert suite.capture_for(task) is False

    @pytest.mark.parametrize("field,value", [
        ("step_budget", 0),
        ("step_budget", -3),
        ("token_budget", 0),
        ("cost_budget", 0),
        ("cost_budget", -1.0),
        ("optimal_steps", 0),
    ])
    def test_illegal_budget_rejected_with_file_and_field_path(self, tmp_path, field, value):
        """Scenario: 非法预算值 → 指出该 task 的该字段, 含文件路径与字段路径。"""
        path = _write(tmp_path, _task_yaml(f"    {field}: {value}\n"))
        with pytest.raises(SuiteLoadError) as excinfo:
            load_suite(path)
        message = str(excinfo.value)
        assert str(path) in message
        assert f"tasks.0.{field}" in message

    @pytest.mark.parametrize("difficulty", ["trivial", "HARD"])
    def test_unknown_difficulty_rejected(self, tmp_path, difficulty):
        with pytest.raises(SuiteLoadError, match="difficulty"):
            load_suite(_write(tmp_path, _task_yaml(f'    difficulty: "{difficulty}"\n')))

    def test_blank_tag_rejected(self, tmp_path):
        with pytest.raises(SuiteLoadError, match="tags"):
            load_suite(_write(tmp_path, _task_yaml('    tags: [ok, "  "]\n')))

    def test_duplicate_tags_rejected(self, tmp_path):
        with pytest.raises(SuiteLoadError, match="重复"):
            load_suite(_write(tmp_path, _task_yaml("    tags: [a, a]\n")))

    def test_blank_category_rejected(self, tmp_path):
        with pytest.raises(SuiteLoadError, match="category"):
            load_suite(_write(tmp_path, _task_yaml('    category: "   "\n')))

    def test_api_json_creation_validates_identically(self):
        """校验挂在模型上: YAML 加载与 API JSON 创建行为一致。"""
        from pydantic import ValidationError

        from agent_eval.core.types import EvalTask

        with pytest.raises(ValidationError, match="step_budget"):
            EvalTask(
                id="t1",
                prompt="a",
                step_budget=0,
                graders=[{"type": "code", "name": "code_based"}],
            )
