"""
Manifest Data Container.

The :any:`Manifest`, :any:`Project`, :any:`Remote` and :any:`Defaults` classes are pure data container.
They do not implement any business logic.
"""

from pathlib import Path
from typing import Callable, List, Optional

import yaml
from pydantic import BaseModel, Field, root_validator

from .exceptions import ManifestNotFoundError


class Remote(BaseModel, allow_population_by_field_name=True):
    """
    Remote Alias.

    :param name: Remote Name
    :param url_base: Base URL. Optional.
    """

    name: str
    url_base: Optional[str] = Field(None, alias="url-base")


class Defaults(BaseModel):
    """
    Default Values.

    These default values are used, if the project does not specify it.

    :param remote: Remote Name
    :param revision: Revision
    """

    remote: Optional[str] = None
    revision: Optional[str] = None


class Project(BaseModel, allow_population_by_field_name=True):
    """
    Project.

    A project specifies the reference to a repository.
    `remote` and `url` are mutually exclusive.
    `url` and `sub-url` are likewise mutually exclusive, but `sub-url` requires a `remote`.

    :param name (str): Unique name.
    :param remote (str): Remote Alias
    :param sub_url (str): URL relative to remote url_base.
    :param url (str): URL
    :param revision (str): Revision
    :param path (str): Project Filesystem Path. Relative to Workspace Directory.
    """

    name: str
    remote: Optional[str] = None
    sub_url: Optional[str] = Field(None, alias="sub-url")
    url: Optional[str] = None
    revision: Optional[str] = None
    path: Optional[str] = None

    @root_validator(allow_reuse=True)
    def _remote_or_url(cls, values):
        # pylint: disable=no-self-argument,no-self-use
        remote = values.get("remote", None)
        sub_url = values.get("sub_url", None)
        url = values.get("url", None)
        if remote and url:
            raise ValueError("'remote' and 'url' are mutually exclusive")
        if url and sub_url:
            raise ValueError("'url' and 'sub-url' are mutually exclusive")
        if sub_url and not remote:
            raise ValueError("'sub-url' requires 'remote'")
        return values


class Manifest(BaseModel, allow_population_by_field_name=True):

    """
    Manifest.

    :param main (Project): Main project.
    :param defaults (Defaults): Default settings.
    :param remotes (List[Remote]): Remote Aliases
    :param projects (List[Project]): Projects.
    """

    main: Project = Field(Project(name="main"), alias="self")
    defaults: Defaults = Defaults()
    remotes: List[Remote] = []
    projects: List[Project] = []

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load :any:`Manifest` from `path`."""
        try:
            content = path.read_text()
        except FileNotFoundError:
            raise ManifestNotFoundError(path) from None
        data = yaml.load(content, Loader=yaml.Loader)
        manifestdata = data.get("manifest", {})
        return cls(**manifestdata)

    def save(self, path: Path):
        """Save manifest within `project_path` at :any:`Manifest.path`."""
        data = {"manifest": self.dict(by_alias=True, exclude_none=True)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data))


class ResolvedProject(Project):

    """
    Project with resolved `defaults` and `remotes`.

    Only `name`, `url`, `revisìon`, `path` will be set.

    :param name (str): Unique name.
    :param url (str): URL.
    :param revision (str): Revision.
    :param path (str): Project Filesystem Path. Relative to Workspace Directory.
    :param manifest (Manifest): Project Manifest.
    """

    name: str
    path: str
    url: Optional[str] = None
    revision: Optional[str] = None
    manifest: Optional[Manifest] = None

    @staticmethod
    def from_project(defaults: Defaults, remotes: List[Remote], project: Project) -> "ResolvedProject":
        """
        Create :any:`ResolvedProject` from `manifest` and `project`.
        """
        url = project.url
        if not url:
            # URL assembly
            project_remote = project.remote or defaults.remote
            if project_remote:
                project_sub_url = project.sub_url or project.name
                for remote in remotes:
                    if remote.name == project_remote:
                        url = f"{remote.url_base}/{project_sub_url}"
                        break
                else:
                    raise ValueError(f"Unknown remote {project.remote} for project {project.name}")
        return ResolvedProject(
            name=project.name,
            path=project.path or project.name,
            url=url,
            revision=project.revision or defaults.revision,
        )


def create_project_filter(project_paths: Optional[List[Path]] = None) -> Callable[[Project], bool]:
    """Create filter function."""
    if project_paths:
        initialized_project_paths: List[Path] = project_paths
        return lambda project: project.path in initialized_project_paths
    return lambda _: True
