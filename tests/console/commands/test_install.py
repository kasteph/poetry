from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from poetry.core.masonry.utils.module import ModuleOrPackageNotFound
from poetry.core.packages.dependency_group import MAIN_GROUP


if TYPE_CHECKING:
    from cleo.testers.command_tester import CommandTester
    from pytest_mock import MockerFixture

    from poetry.poetry import Poetry
    from tests.types import CommandTesterFactory
    from tests.types import ProjectFactory


PYPROJECT_CONTENT = """\
[tool.poetry]
name = "simple-project"
version = "1.2.3"
description = "Some description."
authors = [
    "Python Poetry <tests@python-poetry.org>"
]
license = "MIT"
readme = "README.rst"

[tool.poetry.dependencies]
python = "~2.7 || ^3.4"

[tool.poetry.group.foo.dependencies]
foo = "^1.0"

[tool.poetry.group.bar.dependencies]
bar = "^1.1"

[tool.poetry.group.baz.dependencies]
baz = "^1.2"

[tool.poetry.group.bim.dependencies]
bim = "^1.3"

[tool.poetry.group.bam]
optional = true

[tool.poetry.group.bam.dependencies]
bam = "^1.4"
"""


@pytest.fixture
def poetry(project_factory: ProjectFactory) -> Poetry:
    return project_factory(name="export", pyproject_content=PYPROJECT_CONTENT)


@pytest.fixture
def tester(
    command_tester_factory: CommandTesterFactory, poetry: Poetry
) -> CommandTester:
    return command_tester_factory("install")


@pytest.mark.parametrize(
    ("options", "groups"),
    [
        ("", {MAIN_GROUP, "foo", "bar", "baz", "bim"}),
        (f"--only {MAIN_GROUP}", {MAIN_GROUP}),
        ("--only foo", {"foo"}),
        ("--only foo,bar", {"foo", "bar"}),
        ("--only bam", {"bam"}),
        ("--with bam", {MAIN_GROUP, "foo", "bar", "baz", "bim", "bam"}),
        ("--without foo,bar", {MAIN_GROUP, "baz", "bim"}),
        (f"--without {MAIN_GROUP}", {"foo", "bar", "baz", "bim"}),
        ("--with foo,bar --without baz --without bim --only bam", {"bam"}),
        # net result zero options
        ("--with foo", {MAIN_GROUP, "foo", "bar", "baz", "bim"}),
        ("--without bam", {MAIN_GROUP, "foo", "bar", "baz", "bim"}),
        ("--with bam --without bam", {MAIN_GROUP, "foo", "bar", "baz", "bim"}),
        ("--with foo --without foo", {MAIN_GROUP, "bar", "baz", "bim"}),
        # deprecated options
        ("--default", {MAIN_GROUP}),
        ("--no-dev", {MAIN_GROUP}),
        ("--dev-only", {"foo", "bar", "baz", "bim"}),
    ],
)
@pytest.mark.parametrize("with_root", [True, False])
def test_group_options_are_passed_to_the_installer(
    options: str,
    groups: set[str],
    with_root: bool,
    tester: CommandTester,
    mocker: MockerFixture,
):
    """
    Group options are passed properly to the installer.
    """
    mocker.patch.object(tester.command.installer, "run", return_value=0)
    editable_builder_mock = mocker.patch(
        "poetry.masonry.builders.editable.EditableBuilder",
        side_effect=ModuleOrPackageNotFound(),
    )

    if not with_root:
        options = f"--no-root {options}"

    tester.execute(options)

    package_groups = set(tester.command.poetry.package._dependency_groups.keys())
    installer_groups = set(tester.command.installer._groups)

    assert installer_groups <= package_groups
    assert set(installer_groups) == groups

    if with_root:
        assert editable_builder_mock.call_count == 1
        assert editable_builder_mock.call_args_list[0][0][0] == tester.command.poetry
    else:
        assert editable_builder_mock.call_count == 0


def test_sync_option_is_passed_to_the_installer(
    tester: CommandTester, mocker: MockerFixture
):
    """
    The --sync option is passed properly to the installer.
    """
    mocker.patch.object(tester.command.installer, "run", return_value=1)

    tester.execute("--sync")

    assert tester.command.installer._requires_synchronization
