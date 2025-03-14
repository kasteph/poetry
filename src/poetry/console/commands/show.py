from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

from cleo.helpers import argument
from cleo.helpers import option
from poetry.core.packages.directory_dependency import DirectoryDependency
from poetry.core.packages.file_dependency import FileDependency
from poetry.core.packages.vcs_dependency import VCSDependency

from poetry.console.commands.group_command import GroupCommand
from poetry.utils.helpers import canonicalize_name


if TYPE_CHECKING:
    from cleo.io.io import IO
    from poetry.core.packages.dependency import Dependency
    from poetry.core.packages.package import Package
    from poetry.core.packages.project_package import ProjectPackage

    from poetry.repositories.installed_repository import InstalledRepository
    from poetry.repositories.repository import Repository


def reverse_deps(pkg: Package, repo: Repository) -> dict[str, str]:
    required_by = {}
    for locked in repo.packages:
        dependencies = {d.name: d.pretty_constraint for d in locked.requires}

        if pkg.name in dependencies:
            required_by[locked.pretty_name] = dependencies[pkg.name]

    return required_by


class ShowCommand(GroupCommand):

    name = "show"
    description = "Shows information about packages."

    arguments = [argument("package", "The package to inspect", optional=True)]
    options = [
        *GroupCommand._group_dependency_options(),
        option(
            "no-dev",
            None,
            "Do not list the development dependencies. (<warning>Deprecated</warning>)",
        ),
        option("tree", "t", "List the dependencies as a tree."),
        option(
            "why",
            None,
            "When showing the full list, or a <info>--tree</info> for a single package,"
            " also display why it's included.",
        ),
        option("latest", "l", "Show the latest version."),
        option(
            "outdated",
            "o",
            "Show the latest version but only for packages that are outdated.",
        ),
        option(
            "all",
            "a",
            "Show all packages (even those not compatible with current system).",
        ),
    ]

    help = """The show command displays detailed information about a package, or
lists all packages available."""

    colors = ["cyan", "yellow", "green", "magenta", "blue"]

    def handle(self) -> int | None:
        from cleo.io.null_io import NullIO
        from cleo.terminal import Terminal

        from poetry.puzzle.solver import Solver
        from poetry.repositories.installed_repository import InstalledRepository
        from poetry.repositories.pool import Pool
        from poetry.repositories.repository import Repository
        from poetry.utils.helpers import get_package_version_display_string

        package = self.argument("package")

        if self.option("tree"):
            self.init_styles(self.io)

        if self.option("why"):
            if self.option("tree") and package is None:
                self.line_error(
                    "<error>Error: --why requires a package when combined with"
                    " --tree.</error>"
                )

                return 1

            if not self.option("tree") and package:
                self.line_error(
                    "<error>Error: --why cannot be used without --tree when displaying"
                    " a single package.</error>"
                )

                return 1

        if self.option("outdated"):
            self._io.input.set_option("latest", True)

        if not self.poetry.locker.is_locked():
            self.line_error(
                "<error>Error: poetry.lock not found. Run `poetry lock` to create"
                " it.</error>"
            )
            return 1

        locked_repo = self.poetry.locker.locked_repository()
        root = self.project_with_activated_groups_only()

        # Show tree view if requested
        if self.option("tree") and package is None:
            requires = root.all_requires
            packages = locked_repo.packages
            for p in packages:
                for require in requires:
                    if p.name == require.name:
                        self.display_package_tree(self._io, p, locked_repo)
                        break

            return 0

        table = self.table(style="compact")
        locked_packages = locked_repo.packages
        pool = Pool(ignore_repository_names=True)
        pool.add_repository(locked_repo)
        solver = Solver(
            root,
            pool=pool,
            installed=Repository(),
            locked=locked_repo,
            io=NullIO(),
        )
        solver.provider.load_deferred(False)
        with solver.use_environment(self.env):
            ops = solver.solve().calculate_operations()

        required_locked_packages = {op.package for op in ops if not op.skipped}

        if package:
            pkg = None
            for locked in locked_packages:
                if canonicalize_name(package) == locked.name:
                    pkg = locked
                    break

            if not pkg:
                raise ValueError(f"Package {package} not found")

            required_by = reverse_deps(pkg, locked_repo)

            if self.option("tree"):
                if self.option("why"):
                    # The default case if there's no reverse dependencies is to query
                    # the subtree for pkg but if any rev-deps exist we'll query for each
                    # of them in turn
                    packages = [pkg]
                    if required_by:
                        packages = [
                            p
                            for p in locked_packages
                            for r in required_by.keys()
                            if p.name == r
                        ]
                    else:
                        # if no rev-deps exist we'll make this clear as it can otherwise
                        # look very odd for packages that also have no or few direct
                        # dependencies
                        self._io.write_line(
                            f"Package {package} is a direct dependency."
                        )

                    for p in packages:
                        self.display_package_tree(
                            self._io, p, locked_repo, why_package=pkg
                        )

                else:
                    self.display_package_tree(self._io, pkg, locked_repo)

                return 0

            rows = [
                ["<info>name</>", f" : <c1>{pkg.pretty_name}</>"],
                ["<info>version</>", f" : <b>{pkg.pretty_version}</b>"],
                ["<info>description</>", f" : {pkg.description}"],
            ]

            table.add_rows(rows)
            table.render()

            if pkg.requires:
                self.line("")
                self.line("<info>dependencies</info>")
                for dependency in pkg.requires:
                    self.line(
                        f" - <c1>{dependency.pretty_name}</c1>"
                        f" <b>{dependency.pretty_constraint}</b>"
                    )

            if required_by:
                self.line("")
                self.line("<info>required by</info>")
                for parent, requires_version in required_by.items():
                    self.line(f" - <c1>{parent}</c1> <b>{requires_version}</b>")

            return 0

        show_latest = self.option("latest")
        show_all = self.option("all")
        terminal = Terminal()
        width = terminal.width
        name_length = version_length = latest_length = required_by_length = 0
        latest_packages = {}
        latest_statuses = {}
        installed_repo = InstalledRepository.load(self.env)

        # Computing widths
        for locked in locked_packages:
            if locked not in required_locked_packages and not show_all:
                continue

            current_length = len(locked.pretty_name)
            if not self._io.output.is_decorated():
                installed_status = self.get_installed_status(locked, installed_repo)

                if installed_status == "not-installed":
                    current_length += 4

            if show_latest:
                latest = self.find_latest_package(locked, root)
                if not latest:
                    latest = locked

                latest_packages[locked.pretty_name] = latest
                update_status = latest_statuses[
                    locked.pretty_name
                ] = self.get_update_status(latest, locked)

                if not self.option("outdated") or update_status != "up-to-date":
                    name_length = max(name_length, current_length)
                    version_length = max(
                        version_length,
                        len(
                            get_package_version_display_string(
                                locked, root=self.poetry.file.parent
                            )
                        ),
                    )
                    latest_length = max(
                        latest_length,
                        len(
                            get_package_version_display_string(
                                latest, root=self.poetry.file.parent
                            )
                        ),
                    )

                    if self.option("why"):
                        required_by = reverse_deps(locked, locked_repo)
                        required_by_length = max(
                            required_by_length,
                            len(" from " + ",".join(required_by.keys())),
                        )
            else:
                name_length = max(name_length, current_length)
                version_length = max(
                    version_length,
                    len(
                        get_package_version_display_string(
                            locked, root=self.poetry.file.parent
                        )
                    ),
                )

                if self.option("why"):
                    required_by = reverse_deps(locked, locked_repo)
                    required_by_length = max(
                        required_by_length, len(" from " + ",".join(required_by.keys()))
                    )

        write_version = name_length + version_length + 3 <= width
        write_latest = name_length + version_length + latest_length + 3 <= width

        why_end_column = (
            name_length + version_length + latest_length + required_by_length
        )
        write_why = self.option("why") and (why_end_column + 3) <= width
        write_description = (why_end_column + 24) <= width

        for locked in locked_packages:
            color = "cyan"
            name = locked.pretty_name
            install_marker = ""
            if locked not in required_locked_packages:
                if not show_all:
                    continue

                color = "black;options=bold"
            else:
                installed_status = self.get_installed_status(locked, installed_repo)
                if installed_status == "not-installed":
                    color = "red"

                    if not self._io.output.is_decorated():
                        # Non installed in non decorated mode
                        install_marker = " (!)"

            if (
                show_latest
                and self.option("outdated")
                and latest_statuses[locked.pretty_name] == "up-to-date"
            ):
                continue

            line = (
                f"<fg={color}>"
                f"{name:{name_length - len(install_marker)}}{install_marker}</>"
            )
            if write_version:
                version = get_package_version_display_string(
                    locked, root=self.poetry.file.parent
                )
                line += f" <b>{version:{version_length}}</b>"
            if show_latest:
                latest = latest_packages[locked.pretty_name]
                update_status = latest_statuses[locked.pretty_name]

                if write_latest:
                    color = "green"
                    if update_status == "semver-safe-update":
                        color = "red"
                    elif update_status == "update-possible":
                        color = "yellow"

                    version = get_package_version_display_string(
                        latest, root=self.poetry.file.parent
                    )
                    line += f" <fg={color}>{version:{latest_length}}</>"

            if write_why:
                required_by = reverse_deps(locked, locked_repo)
                if required_by:
                    content = ",".join(required_by.keys())
                    # subtract 6 for ' from '
                    line += f" from {content:{required_by_length - 6}}"
                else:
                    line += " " * required_by_length

            if write_description:
                description = locked.description
                remaining = (
                    width - name_length - version_length - required_by_length - 4
                )

                if show_latest:
                    remaining -= latest_length

                if len(locked.description) > remaining:
                    description = description[: remaining - 3] + "..."

                line += " " + description

            self.line(line)

        return None

    def display_package_tree(
        self,
        io: IO,
        package: Package,
        installed_repo: Repository,
        why_package: Package | None = None,
    ) -> None:
        io.write(f"<c1>{package.pretty_name}</c1>")
        description = ""
        if package.description:
            description = " " + package.description

        io.write_line(f" <b>{package.pretty_version}</b>{description}")

        if why_package is not None:
            dependencies = [p for p in package.requires if p.name == why_package.name]
        else:
            dependencies = package.requires
            dependencies = sorted(
                dependencies,
                key=lambda x: x.name,
            )

        tree_bar = "├"
        total = len(dependencies)
        for i, dependency in enumerate(dependencies, 1):
            if i == total:
                tree_bar = "└"

            level = 1
            color = self.colors[level]
            info = (
                f"{tree_bar}── <{color}>{dependency.name}</{color}>"
                f" {dependency.pretty_constraint}"
            )
            self._write_tree_line(io, info)

            tree_bar = tree_bar.replace("└", " ")
            packages_in_tree = [package.name, dependency.name]

            self._display_tree(
                io, dependency, installed_repo, packages_in_tree, tree_bar, level + 1
            )

    def _display_tree(
        self,
        io: IO,
        dependency: Dependency,
        installed_repo: Repository,
        packages_in_tree: list[str],
        previous_tree_bar: str = "├",
        level: int = 1,
    ) -> None:
        previous_tree_bar = previous_tree_bar.replace("├", "│")

        dependencies = []
        for package in installed_repo.packages:
            if package.name == dependency.name:
                dependencies = package.requires

                break

        dependencies = sorted(
            dependencies,
            key=lambda x: x.name,
        )
        tree_bar = previous_tree_bar + "   ├"
        total = len(dependencies)
        for i, dependency in enumerate(dependencies, 1):
            current_tree = packages_in_tree
            if i == total:
                tree_bar = previous_tree_bar + "   └"

            color_ident = level % len(self.colors)
            color = self.colors[color_ident]

            circular_warn = ""
            if dependency.name in current_tree:
                circular_warn = "(circular dependency aborted here)"

            info = (
                f"{tree_bar}── <{color}>{dependency.name}</{color}>"
                f" {dependency.pretty_constraint} {circular_warn}"
            )
            self._write_tree_line(io, info)

            tree_bar = tree_bar.replace("└", " ")

            if dependency.name not in current_tree:
                current_tree.append(dependency.name)

                self._display_tree(
                    io, dependency, installed_repo, current_tree, tree_bar, level + 1
                )

    def _write_tree_line(self, io: IO, line: str) -> None:
        if not io.output.supports_utf8():
            line = line.replace("└", "`-")
            line = line.replace("├", "|-")
            line = line.replace("──", "-")
            line = line.replace("│", "|")

        io.write_line(line)

    def init_styles(self, io: IO) -> None:
        from cleo.formatters.style import Style

        for color in self.colors:
            style = Style(color)
            io.output.formatter.set_style(color, style)
            io.error_output.formatter.set_style(color, style)

    def find_latest_package(
        self, package: Package, root: ProjectPackage
    ) -> Package | None:
        from cleo.io.null_io import NullIO

        from poetry.puzzle.provider import Provider
        from poetry.version.version_selector import VersionSelector

        # find the latest version allowed in this pool
        if package.source_type in ("git", "file", "directory"):
            requires = root.all_requires

            for dep in requires:
                if dep.name == package.name:
                    provider = Provider(root, self.poetry.pool, NullIO())

                    if dep.is_vcs():
                        dep = cast(VCSDependency, dep)
                        return provider.search_for_vcs(dep)[0]
                    if dep.is_file():
                        dep = cast(FileDependency, dep)
                        return provider.search_for_file(dep)[0]
                    if dep.is_directory():
                        dep = cast(DirectoryDependency, dep)
                        return provider.search_for_directory(dep)[0]

        name = package.name
        selector = VersionSelector(self.poetry.pool)

        return selector.find_best_candidate(name, f">={package.pretty_version}")

    def get_update_status(self, latest: Package, package: Package) -> str:
        from poetry.core.semver.helpers import parse_constraint

        if latest.full_pretty_version == package.full_pretty_version:
            return "up-to-date"

        constraint = parse_constraint("^" + package.pretty_version)

        if constraint.allows(latest.version):
            # It needs an immediate semver-compliant upgrade
            return "semver-safe-update"

        # it needs an upgrade but has potential BC breaks so is not urgent
        return "update-possible"

    def get_installed_status(
        self, locked: Package, installed_repo: InstalledRepository
    ) -> str:
        for package in installed_repo.packages:
            if locked.name == package.name:
                return "installed"

        return "not-installed"
