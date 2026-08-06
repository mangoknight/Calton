"""``select(Task)`` must never be written directly.

The soft-delete filter is not automatic in SQLAlchemy the way xorm's ``deleted`` tag is
upstream. A bare ``select(Task)`` compiles, runs, returns deleted tasks, and fails
nothing — the damage shows up as an MCP client seeing tasks the user deleted. Until now
this was only a convention stated in a docstring, which is not a control.

Written as source inspection rather than a runtime check because the mistake is one of
authorship: there is no moment at runtime where a correct and an incorrect query are
distinguishable.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "calton"

#: Where the sanctioned wrapper is defined; it is the one place allowed to do this.
SANCTIONED = {SOURCE_ROOT / "models" / "task.py"}


def _python_files() -> list[Path]:
    return sorted(path for path in SOURCE_ROOT.rglob("*.py") if path not in SANCTIONED)


def _selects_task_directly(tree: ast.AST) -> list[int]:
    """Line numbers of any ``select(Task)`` call."""
    offences = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if _referenced_name(node.func) != "select":
            continue
        if any(_referenced_name(argument) == "Task" for argument in node.args):
            offences.append(node.lineno)

    return offences


def _referenced_name(node: ast.expr) -> str | None:
    """The trailing identifier of a name or attribute — ``Task`` for both forms."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_no_bare_select_of_task(path: Path) -> None:
    offences = _selects_task_directly(ast.parse(path.read_text()))

    assert not offences, (
        f"{path} calls select(Task) at line(s) {offences}. "
        "Use base_task_query() so the deleted_at filter is applied."
    )


def test_the_guard_catches_a_real_offence(tmp_path: Path) -> None:
    """A check that never fires is indistinguishable from one that cannot fire."""
    offending = "from sqlalchemy import select\nfrom calton.models import Task\nq = select(Task)\n"

    assert _selects_task_directly(ast.parse(offending)) == [3]


def test_the_guard_ignores_other_models() -> None:
    allowed = (
        "from sqlalchemy import select\nfrom calton.models import Project\nq = select(Project)\n"
    )

    assert _selects_task_directly(ast.parse(allowed)) == []


def test_base_task_query_is_exported() -> None:
    """The alternative has to be reachable, or the rule is unfollowable."""
    from calton.models import base_task_query

    assert callable(base_task_query)
