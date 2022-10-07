"""
Application Configuration Handling.

This module implements the needed classes to handle persistent application settings. The core part of
the configuration system is the :class:`AppConfig` class, which allows easy access to options spread
across several configuration files. In addition, that class also allows to read and write dedicated
config files.
"""

from contextlib import contextmanager
from enum import Enum, auto
from pathlib import Path
from typing import Generator, Optional

import tomlkit
import tomlkit.exceptions
from pydantic import ValidationError

from anyrepo.exceptions import InvalidConfigurationFileError, InvalidConfigurationLocationError, UninitializedError

from .const import ANYREPO_PATH, CONFIG_FILE_NAME, SYSTEM_CONFIG_DIR, USER_CONFIG_DIR
from .datamodel import AppConfigData
from .workspacefinder import find_workspace


class _EnvAppConfigData(AppConfigData, env_prefix="anyrepo_", case_sensitive=False):
    """
    Configuration data read from the environment.

    This class is used to read the configuration values from the user's environment variables.
    """


class AppConfigLocation(Enum):
    """
    The location where configuration options are stored.

    This enum encodes the different locations where the application stores information.
    """

    SYSTEM = auto()
    """
    System wide configuration.

    Use this to refer to configuration options for the entire system. By default, the system configuration is
    looked for in the path pointed to by :any:`SYSTEM_CONFIG_DIR`.
    """

    USER = auto()
    """
    User configuration.

    Use this to refer to configuration specific to the current user. By default, the user configuration is
    looked for in the path pointed to by :any:`USER_CONFIG_DIR`.
    """

    WORKSPACE = auto()
    """
    Workspace configuration.

    Use this to refer to configuration specific to the current workspace (if any). By default, the workspace
    configuration is looked for in the :any:`ANYREPO_PATH` path within the workspace we are located in. The
    workspace location is retrieved by using :any:`Workspace.find_path`.
    """


class AppConfig:
    """
    Application wide configuration.

    This class holds the application wide configuration and also provides means to modify it.

    In the simplest case, default construct this class to gain access to the options defined
    in the various configuration files:

    .. code-block:: python

        config = AppConfig()
        options = config.options
        # Now we can use the values defined in the options

    Alternatively, the two methods :any:`load` and :any:`save`
    can be used to load and save configuration files explicitly.

    By default, three configuration files will be loaded and merged:

    - A system wide configuration file.
    - A configuration file specific for the current user.
    - And a configuration file for the current workspace.

    In case the current directory is not within a workspace (and no path to a
    workspace has been explicitly set in the constructor), the workspace configuration file
    is skipped. In any case, the configurations are merged in that order, i.e. options from the
    system configuration file have the lowest priority, while the ones from the workspace have
    highest priority.

    On top, the class will also evaluate environment variables named `ANYREPO_*` and allow overriding
    configuration values that way. For example, the `manifest_path` option can be explicitly overridden by
    setting the `ANYREPO_MANIFEST_PATH` environment variable.

    Keyword Args:

        system_config_dir: The path to where the system configuration file is stored. If not set, a platform specific
                           system configuration path will be used.
        user_config_dir: The path to where the user configuration file is stored. If not set, a platform specific
                           user configuration path will be used.
        workspace_config_dir: The path to where the workspace configuration file is stored. If not set, the path
                              will be looked up by searching from the current directory upwards for a workspace
                              configuration folder. If none is found, no workspace configuration will be
                              used.
        use_config_from_env: If set to False, reading of environment variables to override configuration values
                             will be skipped.
    """

    def __init__(
        self,
        system_config_dir: Optional[str] = None,
        user_config_dir: Optional[str] = None,
        workspace_config_dir: Optional[str] = None,
        use_config_from_env: bool = True,
    ) -> None:
        self._use_config_from_env = use_config_from_env
        if system_config_dir is None:
            system_config_dir = SYSTEM_CONFIG_DIR
        if user_config_dir is None:
            user_config_dir = USER_CONFIG_DIR
        if workspace_config_dir is None:
            workspace_dir = find_workspace()
            if workspace_dir:
                workspace_config_dir = str(workspace_dir / ANYREPO_PATH)
        self._system_config_dir = system_config_dir
        self._user_config_dir = user_config_dir
        self._workspace_config_dir = workspace_config_dir
        self._merged_config: Optional[AppConfigData] = None

    @property
    def options(self) -> AppConfigData:
        """
        Access the merged application configuration.

        This property holds the merged configuration options of the application.
        It is computed by loading the system, user and - if we are within a workspace -
        workspace configuration as well as - if enabled - the configurations read from
        environment variables and merging them together in that order.

        .. code-block:: python

            # Create a configuration object:
            config = AppConfig()

            # Now, we can use the options to access the configured values:
            if config.options.color_ui:
                # Print using color
            else:
                # Print without additional styling

        Note that the value is computed on first access, meaning that accessing this property
        can potentially raise an exception. See the documentation of the :any:`load` method
        to learn which exceptions are expected.

        Also note that the value is cached between accesses. Once the AppConfig object
        is created and this property is read once, its value will be reused between calls.
        An exception is when modifying configuration values using :any:`save`. In this
        case, the currently merged configurations are discarded and re-read on the next access of this
        property.
        """
        if self._merged_config is None:
            sys_config = self.load(AppConfigLocation.SYSTEM)
            user_config = self.load(AppConfigLocation.USER)
            workspace_config = self.load(AppConfigLocation.WORKSPACE)
            merged_config_data: dict = {}
            merged_config_data.update(sys_config.dict(exclude_none=True))
            merged_config_data.update(user_config.dict(exclude_none=True))
            merged_config_data.update(workspace_config.dict(exclude_none=True))
            if self._use_config_from_env:
                env_config = _EnvAppConfigData()
                merged_config_data.update(env_config.dict(exclude_none=True))
            self._merged_config = AppConfigData(**merged_config_data)
            self._fill_in_defaults(self._merged_config)
        return self._merged_config

    def load(self, location: AppConfigLocation) -> AppConfigData:
        """
        Load the configuration from the specified location.

        This method will load the configuration values from the given location. Note that
        only that location's config file values will be included. For *productive* use, rather
        read the merged values via the :any:`options` property.

        The main purpose of this method is to obtain a copy of the current configuration
        values from a specific configuration file, modify them and then write them back via
        :any:`save`.

        Args:
            location: The location to load the configuration from.

        Raises:
            InvalidConfigurationFileError: The configuration file is invalid and cannot be used.
            InvalidConfigurationLocationError: The location is invalid.
        """
        try:
            doc = self._load(location)
        except UninitializedError:
            return AppConfigData()
        try:
            return AppConfigData(**doc)
        except ValidationError as validation_error:
            raise InvalidConfigurationFileError(
                self._get_config_file_path(location), str(validation_error)
            ) from validation_error

    def save(self, config: AppConfigData, location: AppConfigLocation):
        """
        Save the configuration back to disk.

        This saves the given configuration back to a file on disk. Use it together
        with the :any:`load` method to modify configuration values on disk:

        .. code-block:: python

            config = AppConfig()

            # Load configuration values from disk:
            values = config.load(AppConfigLocation.WORKSPACE)

            # Request not to color output
            values.color_ui = False

            # Request using the default manifest by unsetting whatever is in workspace config
            values.manifest_path = None

            # Save values back:
            config.save(AppConfigLocation.WORKSPACE)

        Args:
            config: The configuration options to be saved to disk.
            location: The location where to store.

        Raises:
            InvalidConfigurationLocationError: The location is not valid.
            InvalidConfigurationFileError: The target configuration file exists but is broken and cannot be updated.
            UninitializedError: Tried to save to a workspace configuration but we are not within a valid workspace.
        """
        doc = self._load(location)
        values = config.dict()

        # Modify the document "in-place" to keep comments etc
        for key, value in values.items():
            if value is not None:
                doc[key] = value
            elif key in doc:
                del doc[key]

        doc_path = self._get_config_file_path(location)
        doc_path.write_text(doc.as_string(), encoding="utf-8")

        # Clear the cached merged config so we'll reload it on next access
        self._merged_config = None

    @contextmanager
    def edit(self, location: AppConfigLocation) -> Generator[AppConfigData, None, None]:
        """
        Edit a configuration file.

        This method can be used to conveniently edit a configuration file:

        .. code-block:: python

            with app_config.edit(AppConfigLocation.WORKSPACE) as cfg:
                cfg.color_ui = False

        It basically combines :any:`load` and :any:`save` into
        a single method call which can be used with a `with` statement.
        """
        config = self.load(location)
        yield config
        self.save(config, location)

    @staticmethod
    def _load_config_from_path(file_path: Path) -> tomlkit.TOMLDocument:
        """
        Load the configuration file at the given path.

        Args:
            file_path: The path to the config file to load.

        Raises:
            InvalidConfigurationFileError: The configuration file cannot be parsed.
        """
        try:
            config_data = file_path.read_text()
        except FileNotFoundError:
            # If the file does not exist, construct a default settings object
            return tomlkit.TOMLDocument()
        try:
            return tomlkit.loads(config_data)
        except tomlkit.exceptions.ParseError as parse_error:
            # If we cannot parse the config file, raise an error here as we cannot
            # safely continue
            raise InvalidConfigurationFileError(file_path, str(parse_error)) from parse_error

    def _load(self, location: AppConfigLocation) -> tomlkit.TOMLDocument:
        """
        Load a configuration file from a location.

        This loads a configuration file from a specific location.

        Args:

            location: The location to load the configuration from.

        Raises:
            UninitializedError: When trying to load the workspace configuration while being outside a workspace.
            InvalidConfigurationLocationError: When an invalid location has been specified.
        """
        config_file_path = self._get_config_file_path(location)
        return AppConfig._load_config_from_path(config_file_path)

    def _get_config_file_path(self, location: AppConfigLocation) -> Path:
        """
        Given a storage location, return the path to the config file.

        Raises:
            InvalidConfigurationLocationError: The location given is invalid.
        """
        if location == AppConfigLocation.SYSTEM:
            return Path(self._system_config_dir) / CONFIG_FILE_NAME
        if location == AppConfigLocation.USER:
            return Path(self._user_config_dir) / CONFIG_FILE_NAME
        if location == AppConfigLocation.WORKSPACE:
            workspace_config_dir = self._workspace_config_dir
            if workspace_config_dir is not None:
                return Path(workspace_config_dir) / CONFIG_FILE_NAME
            raise UninitializedError()
        raise InvalidConfigurationLocationError(str(location))

    @staticmethod
    def _fill_in_defaults(config: AppConfigData):
        """Fill in some sensible defaults in the given config object."""
        for name, default in AppConfigData.defaults().items():
            if getattr(config, name) is None:
                setattr(config, name, default)
