#!/usr/bin/env python3
#
# Connector for elFinder File Manager
# Original author Troex Nevelin <troex@fury.scancode.ru>
# Modified by Svintsov Dmitry (https://github.com/uralbash)
# Further adapted by Wei OUYANG (https://oeway.github.io/)
# License: 3-clauses BSD license
"""Provide the connector for elFinder File Manager."""
# pylint: disable=too-many-lines

import base64
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import shlex
import time
import traceback
import uuid
from datetime import datetime
from collections.abc import Callable
from types import ModuleType
from typing import Any, BinaryIO, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urljoin, quote

from typing_extensions import Literal, TypedDict

Archivers = TypedDict(  # pylint: disable=invalid-name
    "Archivers",
    {"create": Dict[str, Dict[str, str]], "extract": Dict[str, Dict[str, str]]},
)
Info = TypedDict(  # pylint: disable=invalid-name
    "Info",
    {
        "name": str,
        "hash": str,
        "mime": str,
        "read": int,
        "write": int,
        "locked": int,
        "ts": float,
        "volumeid": str,
        "dirs": int,
        "phash": str,
        "link": str,
        "alias": str,
        "size": int,
        "url": str,
        "dim": str,
        "tmb": Union[str, int],
        "path": str,
    },
    total=False,
)
Options = TypedDict(  # pylint: disable=invalid-name
    "Options",
    {
        "root": str,
        "files_url": str,
        "base_url": str,
        "expose_real_path": bool,
        "maxFolderDepth": int,
        "rootAlias": str,
        "dotFiles": bool,
        "dirSize": bool,
        "fileMode": Literal[420],
        "dirMode": Literal[493],
        "imgLib": Optional[str],
        "tmbDir": Optional[str],
        "tmbAtOnce": int,
        "tmbSize": int,
        "fileURL": bool,
        "uploadMaxSize": int,
        "uploadMaxConn": int,
        "uploadWriteChunk": int,
        "uploadAllow": List[str],
        "uploadDeny": List[str],
        "uploadOrder": List[Literal["deny", "allow"]],
        "defaults": Dict[str, bool],
        "perms": Dict[str, Dict[str, bool]],
        "archiveMimes": List[str],
        "archivers": Archivers,
        "disabled": List[str],
        "debug": bool,
    },
)


def exception_to_string(excp: Exception) -> str:
    """Convert exception to string."""
    stack = traceback.extract_stack()[:-3] + traceback.extract_tb(
        excp.__traceback__
    )  # add limit=??
    pretty = traceback.format_list(stack)
    return "".join(pretty) + "\n  {} {}".format(excp.__class__, excp)


class Connector:
    """Connector for elFinder."""

    # pylint: disable=too-many-instance-attributes, too-many-arguments

    _options = {
        "root": "",
        "files_url": "",
        "base_url": "",
        "expose_real_path": False,
        "maxFolderDepth": 256,
        "rootAlias": "HOME",
        "dotFiles": False,
        "dirSize": False,
        "fileMode": 0o644,
        "dirMode": 0o755,
        "imgLib": "auto",
        "tmbDir": ".tmb",
        "tmbAtOnce": 5,
        "tmbSize": 48,
        "fileURL": True,
        "uploadMaxSize": 256 * 1024 * 1024,
        "uploadMaxConn": -1,
        "uploadWriteChunk": 8192,
        "uploadAllow": [],
        "uploadDeny": [],
        "uploadOrder": ["deny", "allow"],
        # 'aclObj': None, # TODO  # pylint: disable=fixme
        # 'aclRole': 'user', # TODO  # pylint: disable=fixme
        "defaults": {"read": True, "write": True, "rm": True},
        "perms": {},
        "archiveMimes": [],
        "archivers": {"create": {}, "extract": {}},
        "disabled": ["netmount", "zipdl"],
        "debug": False,
    }  # type: Options

    _commands = {
        "open": "__open",
        "reload": "__reload",
        "mkdir": "__mkdir",
        "mkfile": "__mkfile",
        "rename": "__rename",
        "upload": "__upload",
        "paste": "__paste",
        "rm": "__rm",
        "duplicate": "__duplicate",
        "get": "__get",
        "put": "__put",
        "dim": "__dim",
        "extract": "__extract",
        "archive": "__archive",
        "resize": "__resize",
        "tmb": "__thumbnails",
        "ping": "__ping",
        "search": "__search",
        "tree": "__tree",
        "size": "__size",
        "info": "__places",
        "ls": "__ls",
        "file": "__file",
        "parents": "__parents",
    }

    _mimeType = {
        # text
        "md": "text/markdown",
        "cfg": "text/plain",
        "txt": "text/plain",
        "conf": "text/plain",
        "ini": "text/plain",
        "php": "text/x-php",
        "html": "text/html",
        "htm": "text/html",
        "js": "text/javascript",
        "css": "text/css",
        "rtf": "text/rtf",
        "rtfd": "text/rtfd",
        "py": "text/x-python",
        "java": "text/x-java-source",
        "rb": "text/x-ruby",
        "sh": "text/x-shellscript",
        "pl": "text/x-perl",
        "sql": "text/x-sql",
        # apps
        "doc": "application/msword",
        "ogg": "application/ogg",
        "7z": "application/x-7z-compressed",
        # video
        "ogm": "application/ogm",
        "mkv": "video/x-matroska",
    }

    _time = 0.0
    _request = {}  # type: Dict[str, Any]
    _response = {}  # type: Dict[str, Any]
    _error_data = {}  # type: Dict[str, str]
    _img = None  # type: Optional[ModuleType]
    _today = 0.0
    _yesterday = 0.0

    _cached_path = {}  # type: Dict[str, str]

    # public variables
    http_allowed_parameters = (
        "cmd",
        "target",
        "targets[]",
        "current",
        "tree",
        "name",
        "content",
        "src",
        "dst",
        "cut",
        "init",
        "type",
        "width",
        "height",
        "upload[]",
        "q",
        "makedir",
    )
    # return variables
    http_status_code = 0
    http_header = {}  # type: Dict[str, str]
    http_response = None  # type: Optional[Union[str, Dict[str, str]]]

    def __init__(
        self,
        root: str,
        url: str,
        base_url: str,
        upload_max_size: int,
        tmb_dir: Optional[str],
        expose_real_path: bool = False,
        debug: bool = False,
    ) -> None:
        """Set up connector instance."""
        self._options["root"] = root
        self._options["files_url"] = url
        self._options["uploadMaxSize"] = upload_max_size
        self._options["debug"] = debug
        self._options["tmbDir"] = tmb_dir
        self._options["base_url"] = (
            base_url.lstrip("/") if base_url.startswith("//") else base_url
        )
        self._options["expose_real_path"] = expose_real_path

        self._response["debug"] = {}
        self._options["files_url"] = self.__check_utf8(self._options["files_url"])
        self._options["files_url"] = self._options["files_url"].rstrip("/")
        self._options["root"] = self.__check_utf8(self._options["root"])
        # only strip / if it's not root
        if os.path.dirname(self._options["root"]) != self._options["root"]:
            self._options["root"] = self._options["root"].rstrip(os.sep)
        self.__debug("files_url", self._options["files_url"])
        self.__debug("root", self._options["root"])
        self.volumeid = str(uuid.uuid4())

        for cmd in self._options["disabled"]:
            if cmd in self._commands:
                del self._commands[cmd]

        thumbs_dir = self._options["tmbDir"]

        if thumbs_dir:
            assert thumbs_dir  # typing
            thumbs_dir = os.path.join(self._options["root"], thumbs_dir)
            try:
                if not os.path.exists(thumbs_dir):
                    os.makedirs(thumbs_dir)  # self._options['tmbDir'] = False
                self._options["tmbDir"] = thumbs_dir
            except PermissionError:
                self._options["tmbDir"] = None
                self.__debug("thumbnail", " Permission denied: " + thumbs_dir)
                print(
                    "WARNING: failed to create thumbnail folder "
                    "due to permission denied, it will be disabled."
                )

    def __reset(self) -> None:
        """Flush per request variables."""
        self.http_status_code = 0
        self.http_header = {}
        self.http_response = None
        self._request = {}
        self._response = {}
        self._error_data = {}

        self._time = time.time()
        dt_time = datetime.fromtimestamp(self._time)
        self._today = time.mktime(
            datetime(dt_time.year, dt_time.month, dt_time.day).timetuple()
        )
        self._yesterday = self._today - 86400

        self._response["debug"] = {}

    def run(
        self, http_request: Optional[Dict[str, Any]] = None
    ) -> Tuple[int, Dict[str, str], Dict[str, Any]]:
        """Run main function."""
        if http_request is None:
            http_request = {}
        self.__reset()
        root_ok = True
        if not os.path.exists(self._options["root"]) or self._options["root"] == "":
            root_ok = False
            self._response["error"] = "Invalid backend configuration"
        elif not self.__is_allowed(self._options["root"], "read"):
            root_ok = False
            self._response["error"] = "Access denied"

        for field in self.http_allowed_parameters:
            if field in http_request:
                self._request[field] = http_request[field]

        if root_ok is True:
            if "cmd" in self._request:
                if self._request["cmd"] in self._commands:
                    cmd = self._commands[self._request["cmd"]]
                    func = getattr(self, "_" + self.__class__.__name__ + cmd, None)
                    # https://github.com/python/mypy/issues/6864
                    is_callable = isinstance(func, Callable)  # type: ignore

                    if is_callable:
                        try:
                            func()
                        except Exception as exc:  # pylint: disable=broad-except
                            self._response["error"] = (
                                "Command Failed: " + cmd + ", Error: \n" + str(exc)
                            )
                            traceback.print_exc()
                            self.__debug("exception", exception_to_string(exc))
                else:
                    self._response["error"] = "Unknown command: " + self._request["cmd"]

        if self._error_data:
            self.__debug("errorData", self._error_data)

        if self._options["debug"]:
            self.__debug("time", (time.time() - self._time))
        else:
            if "debug" in self._response:
                del self._response["debug"]

        if self.http_status_code < 100:
            self.http_status_code = 200

        if "Content-type" not in self.http_header:
            if (
                "cmd" in self._request and self._request["cmd"] == "upload"
            ) or self._options["debug"]:
                self.http_header["Content-type"] = "text/html"
            else:
                self.http_header["Content-type"] = "application/json"

        self.http_response = self._response

        return self.http_status_code, self.http_header, self.http_response

    def __places(self) -> None:
        if "targets[]" not in self._request:
            self._response["error"] = "Invalid parameters"
            return

        targets = self._request["targets[]"]
        files = []
        for target in targets:
            path = self.__find(target)
            if path is None:
                self.__set_error_data(target, "File not found")
            else:
                files.append(self.__info(path))
        self._response["files"] = files

    def __open(self) -> None:
        """Open file or directory."""
        if "target" not in self._request:
            self._response["error"] = "Invalid parameters"
            return

        if "init" in self._request and self._request["init"]:
            self._response["api"] = 2.1

        target = self._request.get("target")
        if target:
            target = self.__find_dir(target)
            if not target:
                self._response["error"] = (
                    "Invalid parameters: " + self._request["target"]
                )
            elif not self.__is_allowed(target, "read"):
                self._response["error"] = "Access denied"
                return
            else:
                path = target
        else:
            path = self._options["root"]

        self.__cwd(path)
        self.__files(path, False)
        if self._request.get("tree"):
            self._response["files"].append(self.__info(path))

        self.__check_archivers()
        if not self._options["fileURL"]:
            url = ""
        else:
            url = self._options["files_url"]

        self._response["netDrivers"] = []
        self._response["uplMaxFile"] = 1000
        self._response["uplMaxSize"] = (
            str(self._options["uploadMaxSize"] / (1024 * 1024)) + "M"
        )
        thumbs_dir = self._options["tmbDir"]
        if thumbs_dir:
            thumbs_url = self.__path2url(thumbs_dir)
        else:
            thumbs_url = ""
        self._response["options"] = {
            "path": path,
            "separator": os.path.sep,
            "url": url,
            "disabled": self._options["disabled"],
            "tmbURL": thumbs_url,
            "dotFiles": self._options["dotFiles"],
            "archivers": {
                "create": list(self._options["archivers"]["create"].keys()),
                "extract": list(self._options["archivers"]["extract"].keys()),
                "createext": {
                    k: self._options["archivers"]["create"][k]["ext"]
                    for k in self._options["archivers"]["create"]
                },
            },
            "copyOverwrite": True,
            "uploadMaxSize": self._options["uploadMaxSize"],
            "uploadOverwrite": True,
            "uploadMaxConn": 3,
            "uploadMime": {"allow": ["all"], "deny": [], "firstOrder": "deny"},
            "i18nFolderName": True,
            "dispInlineRegex": "^(?:(?:image|video|audio)|application/"
            + "(?:x-mpegURL|dash\\+xml)|(?:text/plain|application/pdf)$)",
            "jpgQuality": 100,
            "syncChkAsTs": 1,
            "syncMinMs": 30000,
            "uiCmdMap": {},
        }

    def __parents(self) -> None:
        # TODO: implement according to the spec
        # https://github.com/Studio-42/elFinder/wiki/Client-Server-API-2.1#parents
        self._response["tree"] = []

    def __file(self) -> None:
        target = self._request.get("target")
        if not target:
            self._response["error"] = "Invalid parameters"
            return

        download = self._request.get("download")
        cur_file = self.__find(target)

        if not cur_file or not os.path.exists(cur_file) or os.path.isdir(cur_file):
            self.http_status_code = 404
            self.http_header["Content-type"] = "text/html"
            self.http_response = "File not found"
            return

        if not self.__is_allowed(cur_file, "read"):
            self.http_status_code = 403
            self.http_header["Content-type"] = "text/html"
            self.http_response = "Access denied"
            return

        if os.path.islink(cur_file):
            cur_file = self.__read_link(cur_file)
            if not cur_file or os.path.isdir(cur_file):
                self.http_status_code = 404
                self.http_header["Content-type"] = "text/html"
                self.http_response = "File not found"
                return
            if not self.__is_allowed(
                os.path.dirname(cur_file), "read"
            ) or not self.__is_allowed(cur_file, "read"):
                self.http_status_code = 403
                self.http_header["Content-type"] = "text/html"
                self.http_response = "Access denied"
                return

        mime = self.__mimetype(cur_file)
        parts = mime.split("/", 2)

        if download:
            disp = "attachments"
        elif parts[0] == "image":
            disp = "image"
        else:
            disp = "inline"

        self.http_status_code = 200
        self.http_header["Content-type"] = mime
        self.http_header["Content-Disposition"] = (
            disp + "; filename=" + os.path.basename(cur_file)
        )
        self.http_header["Content-Location"] = cur_file.replace(
            self._options["root"], ""
        )
        self.http_header["Content-Transfer-Encoding"] = "binary"
        self.http_header["Content-Length"] = str(os.lstat(cur_file).st_size)
        self.http_header["Connection"] = "close"
        self._response["file"] = cur_file

    def __rename(self) -> None:
        """Rename file or dir."""
        name = self._request.get("name")
        target = self._request.get("target")

        if not (name and target):
            self._response["error"] = "Invalid parameters"
            return

        cur_name = self.__find(target)

        if not cur_name:
            self._response["error"] = "File not found"
            return

        cur_dir = os.path.dirname(cur_name)

        if not self.__is_allowed(cur_dir, "write") and self.__is_allowed(
            cur_name, "rm"
        ):
            self._response["error"] = "Access denied"
            return

        name = self.__check_utf8(name)

        if not name or not _check_name(name):
            self._response["error"] = "Invalid name"
            return

        new_name = os.path.join(cur_dir, name)

        if os.path.exists(new_name):
            self._response["error"] = (
                "File or folder with the same name" + "already exists"
            )
            return

        self.__rm_tmb(cur_name)
        try:
            os.rename(cur_name, new_name)
            self._response["added"] = [self.__info(new_name)]
            self._response["removed"] = [target]
        except OSError:
            self._response["error"] = "Unable to rename file"

    def __mkdir(self) -> None:
        """Create new directory."""
        path = None
        new_dir = None
        if "name" in self._request and "target" in self._request:
            name = self._request["name"]
            name = self.__check_utf8(name)
            target = self._request["target"]
            if not target:
                self._response["error"] = "Invalid parameters"
                return
            path = self.__find_dir(target)

        dirs = self._request.get("dirs[]") or []

        if not path:
            self._response["error"] = "Invalid parameters"
            return
        if not self.__is_allowed(path, "write"):
            self._response["error"] = "Access denied"
            return
        if not _check_name(name):
            self._response["error"] = "Invalid name"
            return

        new_dir = os.path.join(path, name)

        if os.path.exists(new_dir):
            self._response["error"] = (
                "File or folder with the same name" + " already exists"
            )
        else:
            try:
                os.mkdir(new_dir, int(self._options["dirMode"]))
                self._response["added"] = [self.__info(new_dir)]
                self._response["hashes"] = []
                for subdir in dirs:
                    new_subdir = os.path.join(new_dir, subdir)
                    os.mkdir(new_subdir, int(self._options["dirMode"]))
                    self._response["hashes"].append(self.__hash(new_subdir))
            except OSError:
                self._response["error"] = "Unable to create folder"

    def __mkfile(self) -> None:
        """Create new file."""
        name = None
        cur_dir = new_file = None
        if "name" in self._request and "target" in self._request:
            name = self._request["name"]
            target = self._request["target"]
            if not target:
                self._response["error"] = "Invalid parameters"
                return
            cur_dir = self.__find_dir(target)
            name = self.__check_utf8(name)

        if not cur_dir or not name:
            self._response["error"] = "Invalid parameters"
            return
        if not self.__is_allowed(cur_dir, "write"):
            self._response["error"] = "Access denied"
            return
        if not _check_name(name):
            self._response["error"] = "Invalid name"
            return

        new_file = os.path.join(cur_dir, name)

        if os.path.exists(new_file):
            self._response["error"] = "File or folder with the same name already exists"
        else:
            try:
                open(new_file, "w").close()
                self._response["added"] = [self.__info(new_file)]
            except OSError:
                self._response["error"] = "Unable to create file"

    def __rm(self) -> None:
        """Delete files and directories."""
        rm_file = rm_list = None
        if "targets[]" in self._request:
            rm_list = self._request["targets[]"]

        if not rm_list:
            self._response["error"] = "Invalid parameters"
            return

        if not isinstance(rm_list, list):
            rm_list = [rm_list]

        removed = []
        for rm_hash in rm_list:
            rm_file = self.__find(rm_hash)
            if not rm_file:
                continue
            if self.__remove(rm_file):
                removed.append(rm_hash)
            else:
                self._response["error"] = "Failed to remove: " + rm_file
                return

        self._response["removed"] = removed

    def __upload(self) -> None:
        """Upload files."""
        try:  # Windows needs stdio set for binary mode.
            import msvcrt  # pylint: disable=import-outside-toplevel

            # pylint: disable=no-member
            # stdin  = 0
            # stdout = 1
            msvcrt.setmode(0, os.O_BINARY)  # type: ignore
            msvcrt.setmode(1, os.O_BINARY)  # type: ignore
        except ImportError:
            pass

        if "target" in self._request:
            dir_hash = self._request["target"]
            cur_dir = self.__find_dir(dir_hash)
            if not cur_dir:
                self._response["error"] = "Invalid parameters"
                return
            if not self.__is_allowed(cur_dir, "write"):
                self._response["error"] = "Access denied"
                return
            if "upload[]" not in self._request:
                self._response["error"] = "No file to upload"
                return

            up_files = self._request["upload[]"]
            # invalid format
            # must be dict('filename1': 'filedescriptor1',
            #              'filename2': 'filedescriptor2', ...)
            if not isinstance(up_files, dict):
                self._response["error"] = "Invalid parameters"
                return

            self._response["added"] = []
            total = 0
            up_size = 0
            max_size = self._options["uploadMaxSize"]
            for name, data in up_files.items():
                if name:
                    name = self.__check_utf8(name)
                    total += 1
                    name = os.path.basename(name)
                    if not _check_name(name):
                        self.__set_error_data(name, "Invalid name: " + name)
                    else:
                        name = os.path.join(cur_dir, name)
                        try:
                            fil = open(name, "wb", self._options["uploadWriteChunk"])
                            for chunk in self.__fbuffer(data):
                                fil.write(chunk)
                            fil.close()
                            up_size += os.lstat(name).st_size
                            if self.__is_upload_allow(name):
                                os.chmod(name, self._options["fileMode"])
                                self._response["added"].append(self.__info(name))
                            else:
                                self.__set_error_data(name, "Not allowed file type")
                                try:
                                    os.unlink(name)
                                except OSError:
                                    pass
                        except OSError:
                            self.__set_error_data(name, "Unable to save uploaded file")
                        if up_size > max_size:
                            try:
                                os.unlink(name)
                                self.__set_error_data(
                                    name, "File exceeds the maximum allowed filesize"
                                )
                            except OSError:
                                # TODO ?  # pylint: disable=fixme
                                self.__set_error_data(
                                    name, "File was only partially uploaded"
                                )
                            break

            if self._error_data:
                if len(self._error_data) == total:
                    self._response["warning"] = "Unable to upload files"
                else:
                    self._response["warning"] = "Some files was not uploaded"
        else:
            self._response["added"] = []
            self._response["warning"] = "Invalid parameters"

    def __paste(self) -> None:
        """Copy or cut files/directories."""
        if "src" in self._request and "dst" in self._request:
            src = self.__find_dir(self._request["src"])
            dst = self.__find_dir(self._request["dst"])
            cur_dir = dst
            if not cur_dir or not src or not dst or "targets[]" not in self._request:
                self._response["error"] = "Invalid parameters"
                return
            files = self._request["targets[]"]
            if not isinstance(files, list):
                files = [files]

            cut = False
            if "cut" in self._request:
                if self._request["cut"] == "1":
                    cut = True

            if not self.__is_allowed(src, "read") or not self.__is_allowed(
                dst, "write"
            ):
                self._response["error"] = "Access denied"
                return

            added = []
            removed = []
            for fhash in files:
                fil = self.__find(fhash, src)
                if not fil:
                    self._response["error"] = "File not found"
                    return
                new_dst = os.path.join(dst, os.path.basename(fil))
                if dst.find(fil) == 0:
                    self._response["error"] = "Unable to copy into itself"
                    return

                if cut:
                    if not self.__is_allowed(fil, "rm"):
                        self._response["error"] = "Move failed"
                        self.__set_error_data(fil, "Access denied")
                        return
                    # TODO thumbs  # pylint: disable=fixme
                    if os.path.exists(new_dst):
                        self._response["error"] = "Unable to move files"
                        self.__set_error_data(
                            fil, "File or folder with the same name already exists"
                        )
                        return
                    try:
                        os.rename(fil, new_dst)
                        self.__rm_tmb(fil)
                        added.append(self.__info(new_dst))
                        removed.append(fhash)
                        continue
                    except OSError:
                        self._response["error"] = "Unable to move files"
                        self.__set_error_data(fil, "Unable to move")
                        return
                else:
                    if not self.__copy(fil, new_dst):
                        self._response["error"] = "Unable to copy files"
                        return
                    added.append(self.__info(new_dst))
                    continue
            self._response["added"] = added
            self._response["removed"] = removed
        else:
            self._response["error"] = "Invalid parameters"

    def __duplicate(self) -> None:
        """Create copy of files/directories."""
        targets = self._request.get("targets[]")
        if not targets:
            self._response["error"] = "Invalid parameters"
            return

        added = []
        for target in targets:
            target = self.__find(target)
            if not target:
                self._response["error"] = "File not found"
                return
            cur_dir = os.path.dirname(target)
            if not self.__is_allowed(target, "read") or not self.__is_allowed(
                cur_dir, "write"
            ):
                self._response["error"] = "Access denied"
                return
            new_name = _unique_name(target)
            if not self.__copy(target, new_name):
                self._response["error"] = "Unable to create file copy"
                return
            added.append(self.__info(new_name))
        self._response["added"] = added

    def __resize(self) -> None:
        """Scale image size."""
        target = self._request.get("target")
        width = self._request.get("width")
        height = self._request.get("height")
        if not (target and width is not None and height is not None):
            self._response["error"] = "Invalid parameters"
            return

        width = int(width)
        height = int(height)

        if width < 1 or height < 1:
            self._response["error"] = "Invalid parameters"
            return

        cur_file = self.__find(target)

        if not cur_file:
            self._response["error"] = "File not found"
            return

        if not self.__is_allowed(cur_file, "write"):
            self._response["error"] = "Access denied"
            return
        if self.__mimetype(cur_file).find("image") != 0:
            self._response["error"] = "File is not an image"
            return

        self.__debug("resize " + cur_file, str(width) + ":" + str(height))
        if not self.__init_img_lib():
            return

        try:
            img = self._img.open(cur_file)  # type: ignore
            img_resized = img.resize(
                (width, height), self._img.ANTIALIAS  # type: ignore
            )
            img_resized.save(cur_file)
        except OSError as exc:  # UnidentifiedImageError requires Pillow 7.0.0
            # self.__debug('resizeFailed_' + path, str(exc))
            self.__debug("resizeFailed_" + self._options["root"], str(exc))
            self._response["error"] = "Unable to resize image"
            return

        self._response["changed"] = [self.__info(cur_file)]

    def __thumbnails(self) -> None:
        """Create previews for images."""
        thumbs_dir = self._options["tmbDir"]
        targets = self._request.get("targets[]")
        if not targets:
            return

        if not self.__init_img_lib() or not self.__can_create_tmb():
            return
        assert thumbs_dir  # typing
        if self._options["tmbAtOnce"] > 0:
            tmb_max = self._options["tmbAtOnce"]
        else:
            tmb_max = 5
        self._response["images"] = {}
        i = 0
        for fhash in targets:
            path = self.__find(fhash)
            if path is None:
                continue
            if os.path.dirname(path) == thumbs_dir:
                continue
            if self.__can_create_tmb(path) and self.__is_allowed(path, "read"):
                tmb = os.path.join(thumbs_dir, fhash + ".png")
                if not os.path.exists(tmb):
                    if self.__tmb(path, tmb):
                        self._response["images"].update({fhash: self.__path2url(tmb)})
                        i += 1
            if i >= tmb_max:
                break

    def __files(self, path: str, tree: bool) -> None:
        """Get files and directories in current directory."""
        files = []
        dirs = []
        for fil in sorted(os.listdir(path)):
            if not self.__is_accepted(fil):
                continue
            file_path = os.path.join(path, fil)
            info = self.__info(file_path)
            if info["mime"] == "directory":
                dirs.append(info)
            else:
                files.append(info)

        dirs.extend(files)
        self._response["files"] = dirs

    def __cwd(self, path: str) -> None:
        """Get Current Working Directory."""
        name = os.path.basename(path)
        if path == self._options["root"]:
            name = self._options["rootAlias"]
            root = True
        else:
            root = False

        if self._options["rootAlias"]:
            basename = self._options["rootAlias"]
        else:
            basename = os.path.basename(self._options["root"])

        rel = os.path.join(basename, path[len(self._options["root"]) :])

        info = {
            "hash": self.__hash(path),
            "name": self.__check_utf8(name),
            "mime": "directory",
            "rel": self.__check_utf8(rel),
            "size": 0,
            "date": datetime.fromtimestamp(os.stat(path).st_mtime).strftime(
                "%d %b %Y %H:%M"
            ),
            "read": 1,
            "write": 1 if self.__is_allowed(path, "write") else 0,
            "locked": 0,
            "rm": not root and self.__is_allowed(path, "rm"),
            "volumeid": self.volumeid,
        }

        try:
            info["dirs"] = 1 if any(next(os.walk(path))[1]) else 0
        except StopIteration:
            info["dirs"] = 0

        self._response["cwd"] = info

    def __info(self, path: str) -> Info:
        # mime = ''
        filetype = "file"
        if os.path.isfile(path):
            filetype = "file"
        elif os.path.isdir(path):
            filetype = "dir"
        elif os.path.islink(path):
            filetype = "link"

        stat = os.lstat(path)
        readable = self.__is_allowed(path, "read")
        writable = self.__is_allowed(path, "write")
        deletable = self.__is_allowed(path, "rm")

        info = {
            "name": self.__check_utf8(os.path.basename(path)),
            "hash": self.__hash(path),
            "mime": "directory" if filetype == "dir" else self.__mimetype(path),
            "read": 1 if readable else 0,
            "write": 1 if writable else 0,
            "locked": 1 if not readable and not writable and not deletable else 0,
            "ts": stat.st_mtime,
        }  # type: Info

        if self._options["expose_real_path"]:
            info["path"] = os.path.abspath(path)

        if filetype == "dir":
            info["volumeid"] = self.volumeid
            try:
                info["dirs"] = 1 if any(next(os.walk(path))[1]) else 0
            except StopIteration:
                info["dirs"] = 0

        if path != self._options["root"]:
            info["phash"] = self.__hash(os.path.dirname(path))

        if filetype == "link":
            lpath = self.__read_link(path)
            if not lpath:
                info["mime"] = "symlink-broken"
                return info

            if os.path.isdir(lpath):
                info["mime"] = "directory"
            else:
                info["mime"] = self.__mimetype(lpath)

            if self._options["rootAlias"]:
                basename = self._options["rootAlias"]
            else:
                basename = os.path.basename(self._options["root"])

            info["link"] = self.__hash(lpath)
            info["alias"] = os.path.join(basename, lpath[len(self._options["root"]) :])
            info["read"] = 1 if info["read"] and self.__is_allowed(lpath, "read") else 0
            info["write"] = (
                1 if info["write"] and self.__is_allowed(lpath, "write") else 0
            )
            info["locked"] = (
                1
                if (
                    not info["write"]
                    and not info["read"]
                    and not self.__is_allowed(lpath, "rm")
                )
                else 0
            )
            info["size"] = 0
        else:
            lpath = None
            info["size"] = self.__dir_size(path) if filetype == "dir" else stat.st_size

        if not info["mime"] == "directory":
            if self._options["fileURL"] and info["read"] is True:
                if lpath:
                    info["url"] = self.__path2url(lpath)
                else:
                    info["url"] = self.__path2url(path)
            if info["mime"][0:5] == "image":
                thumbs_dir = self._options["tmbDir"]
                if self.__can_create_tmb():
                    assert thumbs_dir  # typing
                    dim = self.__get_img_size(path)
                    if dim:
                        info["dim"] = dim

                    # if we are in tmb dir, files are thumbs itself
                    if os.path.dirname(path) == thumbs_dir:
                        info["tmb"] = self.__path2url(path)
                        return info

                    tmb = os.path.join(thumbs_dir, info["hash"] + ".png")

                    if os.path.exists(tmb):
                        tmb_url = self.__path2url(tmb)
                        info["tmb"] = tmb_url
                    else:
                        self._response["tmb"] = True
                        if info["mime"].startswith("image/"):
                            info["tmb"] = 1

        if info["mime"] == "application/x-empty" or info["mime"] == "inode/x-empty":
            info["mime"] = "text/plain"

        return info

    def __size(self) -> None:
        if "targets[]" not in self._request:
            self._response["error"] = "Invalid parameters"
            return

        targets = self._request["targets[]"]

        all_total_size = 0
        all_file_count = 0
        all_dir_count = 0
        sizes = []  # type: List[Dict[str, int]]

        for target in targets:
            path = self.__find(target)
            if path is None:
                self.__set_error_data(target, "Target not found")
                continue
            total_size = 0
            file_count = 0
            dir_count = 0
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path, topdown=True):
                    for folder in dirs:
                        folder_path = os.path.join(root, folder)
                        size = self.__dir_size(folder_path)
                        sizes.append({})
                        dir_count += 1
                        total_size += size
                    for fil in files:
                        file_path = os.path.join(root, fil)
                        size = os.stat(file_path).st_size
                        total_size += size
                        file_count += 1
                    break
            else:
                size = os.stat(file_path).st_size
                total_size += size
                file_count += 1
            sizes.append(
                {"dirCnt": dir_count, "fileCnt": file_count, "size": total_size}
            )
            all_total_size += total_size
            all_file_count += file_count
            all_dir_count += dir_count

        self._response["size"] = all_total_size
        self._response["fileCnt"] = all_file_count
        self._response["dirCnt"] = all_dir_count
        self._response["sizes"] = sizes

    def __ls(self) -> None:
        if not self._request.get("target"):
            self._response["error"] = "Invalid parameters"
            return

        target = self._request["target"]
        intersect = self._request.get("intersect[]")
        if not target:
            self._response["error"] = "Invalid parameters"
            return
        path = self.__find(target)
        if path is None or not os.path.isdir(path):
            self._response["error"] = "Target directory not found."
            return

        items = {}
        for fname in os.listdir(path):
            fhash = self.__hash(os.path.join(path, fname))
            if intersect:
                if fhash in intersect:
                    items[fhash] = fname
            else:
                items[fhash] = fname
        self._response["list"] = items

    def __tree(self) -> None:
        """Return directory tree starting from path."""
        if "target" not in self._request:
            self._response["error"] = "Invalid parameters"
            return
        target = self._request["target"]
        if not target:
            self._response["error"] = "Invalid parameters"
            return
        path = self.__find_dir(target)

        if path is None or not os.path.isdir(path):
            self._response["error"] = "Directory not found"
            return

        if os.path.islink(path):
            path = self.__read_link(path)
            if path is None:
                self._response["error"] = "Directory (link) not found"
                return

        if self.__is_allowed(path, "read"):
            tree = []
            for directory in sorted(os.listdir(path)):
                dir_path = os.path.join(path, directory)
                if (
                    os.path.isdir(dir_path)
                    and not os.path.islink(dir_path)
                    and self.__is_accepted(directory)
                ):
                    tree.append(self.__info(dir_path))
            self._response["tree"] = tree
        else:
            self._response["error"] = "Access denied"

    def __remove(self, target: str) -> bool:
        """Provide internal remove procedure."""
        if not self.__is_allowed(target, "rm"):
            self.__set_error_data(target, "Access denied")

        if not os.path.isdir(target):
            try:
                os.unlink(target)
                return True
            except OSError:
                self.__set_error_data(target, "Remove failed")
                return False
        else:
            for i in os.listdir(target):
                if self.__is_accepted(i):
                    self.__remove(os.path.join(target, i))

            try:
                os.rmdir(target)
                return True
            except OSError:
                self.__set_error_data(target, "Remove failed")
                return False

    def __copy(self, src: str, dst: str) -> bool:
        """Provide internal copy procedure."""
        dst_dir = os.path.dirname(dst)
        if not self.__is_allowed(src, "read"):
            self.__set_error_data(src, "Access denied")
            return False
        if not self.__is_allowed(dst_dir, "write"):
            self.__set_error_data(dst_dir, "Access denied")
            return False
        if os.path.exists(dst):
            self.__set_error_data(
                dst, "File or folder with the same name already exists"
            )
            return False

        if not os.path.isdir(src):
            try:
                shutil.copyfile(src, dst)
                shutil.copymode(src, dst)
                return True
            except (shutil.SameFileError, OSError):
                self.__set_error_data(src, "Unable to copy files")
                return False
        else:
            try:
                os.mkdir(dst, int(self._options["dirMode"]))
                shutil.copymode(src, dst)
            except (shutil.SameFileError, OSError):
                self.__set_error_data(src, "Unable to copy files")
                return False

            for i in os.listdir(src):
                new_src = os.path.join(src, i)
                new_dst = os.path.join(dst, i)
                if not self.__copy(new_src, new_dst):
                    self.__set_error_data(new_src, "Unable to copy files")
                    return False

        return True

    def __find_dir(self, fhash: str, path: Optional[str] = None) -> Optional[str]:
        """Find directory by hash."""
        fhash = str(fhash)
        # try to get find it in the cache
        cached_path = self._cached_path.get(fhash)
        if cached_path:
            return cached_path

        if not path:
            path = self._options["root"]
            if fhash == self.__hash(path):
                return path

        if not os.path.isdir(path):
            return None

        for root, dirs, _ in os.walk(path, topdown=True):
            for folder in dirs:
                folder_path = os.path.join(root, folder)
                if not os.path.islink(folder_path) and fhash == self.__hash(
                    folder_path
                ):
                    return folder_path
        return None

    def __find(self, fhash: str, parent: Optional[str] = None) -> Optional[str]:
        """Find file/dir by hash."""
        fhash = str(fhash)
        cached_path = self._cached_path.get(fhash)
        if cached_path:
            return cached_path
        if not parent:
            parent = self._options["root"]
        if os.path.isdir(parent):
            for root, dirs, files in os.walk(parent, topdown=True):
                for folder in dirs:
                    folder_path = os.path.join(root, folder)
                    if fhash == self.__hash(folder_path):
                        return folder_path
                for fil in files:
                    file_path = os.path.join(root, fil)
                    if fhash == self.__hash(file_path):
                        return file_path

        return None

    def __get(self) -> None:
        target = self._request.get("target")
        if not target:
            self._response["error"] = "Invalid parameters"
            return

        cur_file = self.__find(target)

        if not cur_file:
            self._response["error"] = "File not found"
            return

        if not self.__is_allowed(cur_file, "read"):
            self._response["error"] = "Access denied"
            return

        try:
            with open(cur_file, "r") as text_fil:
                self._response["content"] = text_fil.read()
        except UnicodeDecodeError:
            with open(cur_file, "rb") as bin_fil:
                self._response["content"] = base64.b64encode(bin_fil.read()).decode(
                    "ascii"
                )

    def __dim(self) -> None:
        target = self._request.get("target")
        if not target:
            self._response["error"] = "Invalid parameters"
            return

        cur_file = self.__find(target)

        if not cur_file:
            self._response["error"] = "File not found"
            return

        if not self.__is_allowed(cur_file, "read"):
            self._response["error"] = "Access denied"
            return

        dim = self.__get_img_size(cur_file)
        if dim:
            self._response["dim"] = str(dim)
        else:
            self._response["dim"] = None

    def __put(self) -> None:
        """Save content in file."""
        target = self._request.get("target")
        content = self._request.get("content")
        if not target or not content:
            self._response["error"] = "Invalid parameters"
            return

        cur_file = self.__find(self._request["target"])

        if not cur_file:
            self._response["error"] = "File not found"
            return

        if not self.__is_allowed(cur_file, "write"):
            self._response["error"] = "Access denied"
            return

        try:
            if (
                self._request["content"].startswith("data:")
                and ";base64," in self._request["content"][:100]
            ):
                img_data = self._request["content"].split(";base64,")[1]
                img_data = base64.b64decode(img_data)
                with open(cur_file, "wb") as bin_fil:
                    bin_fil.write(img_data)
            else:
                with open(cur_file, "w+") as text_fil:
                    text_fil.write(self._request["content"])
            self._response["target"] = self.__info(cur_file)
        except OSError:
            self._response["error"] = "Unable to write to file"

    def __archive(self) -> None:
        """Compress files/directories to archive."""
        if (
            not self._options["archivers"]["create"]
            or "type" not in self._request
            or "target" not in self._request
            or "targets[]" not in self._request
        ):
            self._response["error"] = "Invalid parameters"
            return

        cur_dir = self.__find_dir(self._request["target"])
        archive_type = self._request["type"]
        if (
            archive_type not in self._options["archivers"]["create"]
            or archive_type not in self._options["archiveMimes"]
            or not cur_dir
            or not self.__is_allowed(cur_dir, "write")
        ):
            self._response["error"] = "Unable to create archive"
            return

        files = self._request["targets[]"]
        if not isinstance(files, list):
            files = [files]

        real_files = []
        for fhash in files:
            cur_file = self.__find(fhash, cur_dir)
            if not cur_file:
                self._response["error"] = "File not found"
                return
            real_files.append(os.path.basename(cur_file))

        arc = self._options["archivers"]["create"][archive_type]
        if len(real_files) > 1:
            archive_name = "Archive"
        else:
            archive_name = real_files[0]
        archive_name += "." + arc["ext"]
        archive_name = _unique_name(archive_name, "")
        archive_path = os.path.join(cur_dir, archive_name)

        cmd = [arc["cmd"]]
        for arg in arc["argc"].split():
            cmd.append(arg)
        cmd.append(archive_name)
        for fil in real_files:
            cmd.append(fil)

        cur_cwd = os.getcwd()
        os.chdir(cur_dir)
        _run_sub_process(cmd)
        os.chdir(cur_cwd)

        if os.path.exists(archive_path):
            self._response["added"] = [self.__info(archive_path)]
        else:
            self._response["error"] = "Unable to create archive"

    def __extract(self) -> None:
        """Uncompress archive."""
        if not self._options["archivers"]["extract"] or "target" not in self._request:
            self._response["error"] = "Invalid parameters"
            return
        target = self._request["target"]
        makedir = self._request.get("makedir")
        if not target:
            self._response["error"] = "Invalid parameters"
            return
        cur_file = self.__find(target)
        if cur_file is None or os.path.isdir(cur_file):
            self._response["error"] = "File not found"
            return

        cur_dir = os.path.dirname(cur_file)

        mime = self.__mimetype(cur_file)
        self.__check_archivers()
        if mime not in self._options["archivers"]["extract"] or not self.__is_allowed(
            cur_dir, "write"
        ):
            self._response["error"] = "Invalid parameters"
            return

        arc = self._options["archivers"]["extract"][mime]

        cmd = [arc["cmd"]]
        for arg in arc["argc"].split():
            cmd.append(arg)
        cmd.append(os.path.basename(cur_file))

        cur_cwd = os.getcwd()
        target_dir = cur_dir
        added = None
        if makedir and makedir != "0":
            try:
                base_name = os.path.basename(cur_file).split(".")[0] or "New Folder"
                target_dir = os.path.join(target_dir, base_name)
                target_dir = _unique_name(target_dir, copy="")

                os.mkdir(target_dir, int(self._options["dirMode"]))
                cmd += shlex.split(arc["argd"].format(shlex.quote(target_dir)))
                added = [self.__info(target_dir)]
            except OSError:
                self._response["error"] = "Unable to create folder: " + base_name
                return
        if added is None:
            existing_files = os.listdir(cur_dir)
        os.chdir(cur_dir)
        ret = _run_sub_process(cmd)
        os.chdir(cur_cwd)
        if ret:
            if added is None:
                added_names = [
                    dname
                    for dname in os.listdir(cur_dir)
                    if dname not in existing_files
                ]
                self._response["added"] = [
                    self.__info(os.path.join(cur_dir, item)) for item in added_names
                ]
            else:
                self._response["added"] = added
            return

        self._response["error"] = "Unable to extract files from archive"

    def __ping(self) -> None:
        """Workaround for Safari."""
        self.http_status_code = 200
        self.http_header["Connection"] = "close"

    def __search(self) -> None:
        if "q" not in self._request:
            self._response["error"] = "Invalid parameters"
            return

        if "target" in self._request:
            target = self._request["target"]
            if not target:
                self._response["error"] = "Invalid parameters"
                return
            search_path = self.__find_dir(target)
        else:
            search_path = self._options["root"]

        if not search_path:
            self._response["error"] = "File not found"
            return

        mimes = self._request.get("mimes")

        result = []
        query = self._request["q"]
        for root, dirs, files in os.walk(search_path):
            for fil in files:
                if query.lower() in fil.lower():
                    file_path = os.path.join(root, fil)
                    if mimes is None:
                        result.append(self.__info(file_path))
                    else:
                        if self.__mimetype(file_path) in mimes:
                            result.append(self.__info(file_path))
            if mimes is None:
                for folder in dirs:
                    file_path = os.path.join(root, folder)
                    if query.lower() in folder.lower():
                        result.append(self.__info(file_path))
        self._response["files"] = result

    def __mimetype(self, path: str) -> str:
        """Detect mimetype of file."""
        mime = mimetypes.guess_type(path)[0] or "unknown"
        ext = path[path.rfind(".") + 1 :]

        if mime == "unknown" and ("." + ext) in mimetypes.types_map:
            mime = mimetypes.types_map["." + ext]

        if mime == "text/plain" and ext == "pl":
            mime = self._mimeType[ext]

        if mime == "application/vnd.ms-office" and ext == "doc":
            mime = self._mimeType[ext]

        if mime == "unknown":
            if os.path.basename(path) in ["README", "ChangeLog", "LICENSE", "Makefile"]:
                mime = "text/plain"
            else:
                if ext in self._mimeType:
                    mime = self._mimeType[ext]

        # self.__debug('mime ' + os.path.basename(path), ext + ' ' + mime)
        return mime

    def __tmb(self, path: str, tmb: str) -> bool:
        """Provide internal thumbnail create procedure."""
        try:
            img = self._img.open(path).copy()  # type: ignore
            size = self._options["tmbSize"], self._options["tmbSize"]
            box = _crop_tuple(img.size)
            if box:
                img = img.crop(box)
            img.thumbnail(size, self._img.ANTIALIAS)  # type: ignore
            img.save(tmb, "PNG")
        # UnidentifiedImageError requires Pillow 7.0.0
        except (OSError, ValueError) as exc:
            self.__debug("tmbFailed_" + path, str(exc))
            return False
        return True

    def __rm_tmb(self, path: str) -> None:
        tmb = self.__tmb_path(path)
        if tmb and self._options["tmbDir"]:
            if os.path.exists(tmb):
                try:
                    os.unlink(tmb)
                except OSError:
                    pass

    def __read_link(self, path: str) -> Optional[str]:
        """Read link and return real path if not broken."""
        target = os.readlink(path)
        if not target[0] == "/":
            target = os.path.join(os.path.dirname(path), target)
        target = os.path.normpath(target)
        if os.path.exists(target):
            if not target.find(self._options["root"]) == -1:
                return target
        return None

    def __dir_size(self, path: str) -> int:
        total_size = 0
        if self._options["dirSize"]:
            for dirpath, _, filenames in os.walk(path):
                for fil in filenames:
                    file_path = os.path.join(dirpath, fil)
                    if os.path.exists(file_path):
                        total_size += os.stat(file_path).st_size
        else:
            total_size = os.lstat(path).st_size
        return total_size

    def __fbuffer(
        self, fil: BinaryIO, chunk_size: int = _options["uploadWriteChunk"]
    ) -> Generator[bytes, None, None]:
        # pylint: disable=no-self-use
        while True:
            chunk = fil.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def __can_create_tmb(self, path: Optional[str] = None) -> bool:
        if self._options["imgLib"] and self._options["tmbDir"]:
            if path is not None:
                mime = self.__mimetype(path)
                if mime[0:5] != "image":
                    return False
            return True
        return False

    def __tmb_path(self, path: str) -> Optional[str]:
        tmb = None
        thumbs_dir = self._options["tmbDir"]
        if thumbs_dir:
            if not os.path.dirname(path) == thumbs_dir:
                tmb = os.path.join(thumbs_dir, self.__hash(path) + ".png")
        return tmb

    def __is_upload_allow(self, name: str) -> bool:
        allow = False
        deny = False
        mime = self.__mimetype(name)

        if "all" in self._options["uploadAllow"]:
            allow = True
        else:
            for opt in self._options["uploadAllow"]:
                if mime.find(opt) == 0:
                    allow = True

        if "all" in self._options["uploadDeny"]:
            deny = True
        else:
            for opt in self._options["uploadDeny"]:
                if mime.find(opt) == 0:
                    deny = True

        if self._options["uploadOrder"][0] == "allow":  # ,deny
            if deny is True:
                return False
            return bool(allow)
        # deny,allow
        if allow is True:
            return True
        if deny is True:
            return False
        return True

    def __is_accepted(self, target: str) -> bool:
        if target in (".", ".."):
            return False
        if target[0:1] == "." and not self._options["dotFiles"]:
            return False
        return True

    def __is_allowed(self, path: str, access: str) -> bool:
        if not os.path.exists(path):
            return False

        if access == "read":
            if not os.access(path, os.R_OK):
                self.__set_error_data(path, access)
                return False
        elif access == "write":
            if not os.access(path, os.W_OK):
                self.__set_error_data(path, access)
                return False
        elif access == "rm":
            if not os.access(os.path.dirname(path), os.W_OK):
                self.__set_error_data(path, access)
                return False
        else:
            return False

        path = path[len(os.path.normpath(self._options["root"])) :]
        for ppath, permissions in self._options["perms"].items():
            regex = r"" + ppath
            if re.search(regex, path) and access in permissions:
                return permissions[access]

        return self._options["defaults"][access]

    def __hash(self, path: str) -> str:
        """Hash of the path."""
        hash_obj = hashlib.md5()
        hash_obj.update(path.encode("utf-8"))
        hash_code = str(hash_obj.hexdigest())

        # TODO: what if the cache getting to big?  # pylint: disable=fixme
        self._cached_path[hash_code] = path
        return hash_code

    def __path2url(self, path: str) -> str:
        cur_dir = path
        length = len(self._options["root"])
        url = multi_urljoin(
            self._options["base_url"], self._options["files_url"], cur_dir[length:],
        )
        url = self.__check_utf8(url).replace(os.sep, "/")
        url = quote(url, safe="/")
        return url

    def __set_error_data(self, path: str, msg: str) -> None:
        """Collect error/warning messages."""
        self._error_data[path] = msg

    def __init_img_lib(self) -> Optional[str]:
        if not self._options["imgLib"] or self._options["imgLib"] == "auto":
            self._options["imgLib"] = "PIL"

        if self._options["imgLib"] == "PIL":
            try:
                from PIL import Image  # pylint: disable=import-outside-toplevel

                self._img = Image
            except ImportError:
                self._img = None
                self._options["imgLib"] = None
        else:
            raise NotImplementedError

        self.__debug("imgLib", self._options["imgLib"])
        return self._options["imgLib"]

    def __get_img_size(self, path: str) -> Optional[str]:
        if not self.__init_img_lib():
            return None
        if self.__can_create_tmb():
            try:
                img = self._img.open(path)  # type: ignore
                return str(img.size[0]) + "x" + str(img.size[1])
            except OSError:  # UnidentifiedImageError requires Pillow 7.0.0
                print("WARNING: unidentified image or file not found: " + path)

        return None

    def __debug(self, key: str, val: Any) -> None:
        if self._options["debug"]:
            self._response["debug"].update({key: val})

    def __check_archivers(self) -> None:
        # import subprocess
        # proc = subprocess.Popen(['tar', '--version'], shell = False,
        # stdout = subprocess.PIPE, stderr=subprocess.PIPE)
        # out, err = proc.communicate()
        # print 'out:', out, '\nerr:', err, '\n'
        archive = {"create": {}, "extract": {}}  # type: Archivers

        if (
            "archive" in self._options["disabled"]
            and "extract" in self._options["disabled"]
        ):
            self._options["archiveMimes"] = []
            self._options["archivers"] = archive
            return

        tar = _run_sub_process(["tar", "--version"])
        gzip = _run_sub_process(["gzip", "--version"])
        bzip2 = _run_sub_process(["bzip2", "--version"])
        zipc = _run_sub_process(["zip", "--version"])
        unzip = _run_sub_process(["unzip", "--help"])
        rar = _run_sub_process(["rar", "--version"], valid_return=[0, 7])
        unrar = _run_sub_process(["unrar"], valid_return=[0, 7])
        p7z = _run_sub_process(["7z", "--help"])
        p7za = _run_sub_process(["7za", "--help"])
        p7zr = _run_sub_process(["7zr", "--help"])

        # tar = False
        # tar = gzip = bzip2 = zipc = unzip = rar = unrar = False
        # print tar, gzip, bzip2, zipc, unzip, rar, unrar, p7z, p7za, p7zr

        create = archive["create"]
        extract = archive["extract"]

        if tar:
            mime = "application/x-tar"
            create.update({mime: {"cmd": "tar", "argc": "-cf", "ext": "tar"}})
            extract.update(
                {mime: {"cmd": "tar", "argc": "-xf", "ext": "tar", "argd": "-C {}"}}
            )

        if tar and gzip:
            mime = "application/x-gzip"
            create.update({mime: {"cmd": "tar", "argc": "-czf", "ext": "tar.gz"}})
            extract.update(
                {mime: {"cmd": "tar", "argc": "-xzf", "ext": "tar.gz", "argd": "-C {}"}}
            )

        if tar and bzip2:
            mime = "application/x-bzip2"
            create.update({mime: {"cmd": "tar", "argc": "-cjf", "ext": "tar.bz2"}})
            extract.update(
                {
                    mime: {
                        "cmd": "tar",
                        "argc": "-xjf",
                        "ext": "tar.bz2",
                        "argd": "-C {}",
                    }
                }
            )

        mime = "application/zip"
        if zipc:
            create.update({mime: {"cmd": "zip", "argc": "-r9", "ext": "zip"}})
        if unzip:
            extract.update(
                {mime: {"cmd": "unzip", "argc": "", "ext": "zip", "argd": "-d {}"}}
            )

        mime = "application/x-rar"
        if rar:
            create.update({mime: {"cmd": "rar", "argc": "a -inul", "ext": "rar"}})
            extract.update(
                {mime: {"cmd": "rar", "argc": "x -y", "ext": "rar", "argd": "{}"}}
            )
        elif unrar:
            extract.update(
                {mime: {"cmd": "unrar", "argc": "x -y", "ext": "rar", "argd": "{}"}}
            )

        p7zip = None
        if p7z:
            p7zip = "7z"
        elif p7za:
            p7zip = "7za"
        elif p7zr:
            p7zip = "7zr"

        if p7zip:
            mime = "application/x-7z-compressed"
            create.update({mime: {"cmd": p7zip, "argc": "a -t7z", "ext": "7z"}})
            extract.update(
                {
                    mime: {
                        "cmd": p7zip,
                        "argc": "extract -y",
                        "ext": "7z",
                        "argd": "-o{}",
                    }
                }
            )

            mime = "application/x-tar"
            if mime not in create:
                create.update({mime: {"cmd": p7zip, "argc": "a -ttar", "ext": "tar"}})
            if mime not in extract:
                extract.update(
                    {
                        mime: {
                            "cmd": p7zip,
                            "argc": "extract -y",
                            "ext": "tar",
                            "argd": "-o{}",
                        }
                    }
                )

            mime = "application/x-gzip"
            if mime not in create:
                create.update({mime: {"cmd": p7zip, "argc": "a -tgzip", "ext": "gz"}})
            if mime not in extract:
                extract.update(
                    {
                        mime: {
                            "cmd": p7zip,
                            "argc": "extract -y",
                            "ext": "tar.gz",
                            "argd": "-o{}",
                        }
                    }
                )

            mime = "application/x-bzip2"
            if mime not in create:
                create.update({mime: {"cmd": p7zip, "argc": "a -tbzip2", "ext": "bz2"}})
            if mime not in extract:
                extract.update(
                    {
                        mime: {
                            "cmd": p7zip,
                            "argc": "extract -y",
                            "ext": "tar.bz2",
                            "argd": "-o{}",
                        }
                    }
                )

            mime = "application/zip"
            if mime not in create:
                create.update({mime: {"cmd": p7zip, "argc": "a -tzip", "ext": "zip"}})
            if mime not in extract:
                extract.update(
                    {
                        mime: {
                            "cmd": p7zip,
                            "argc": "extract -y",
                            "ext": "zip",
                            "argd": "-o{}",
                        }
                    }
                )

        if not self._options["archiveMimes"]:
            self._options["archiveMimes"] = list(create.keys())
        else:
            pass
        self._options["archivers"] = archive

    def __check_utf8(self, name: Union[str, bytes]) -> str:
        if isinstance(name, str):
            return name
        try:
            str_name = name.decode("utf-8")
        except UnicodeDecodeError:
            str_name = str(name, "utf-8", "replace")
            self.__debug("invalid encoding", str_name)
        return str_name


def _check_name(name: str) -> bool:
    """Check for valid file/dir name."""
    pattern = r"[\/\\\:\<\>]"
    if re.search(pattern, name):
        return False
    return True


def _unique_name(path: str, copy: str = " copy") -> str:
    """Generate unique name for file copied file."""
    cur_dir = os.path.dirname(path)
    cur_name = os.path.basename(path)
    last_dot = cur_name.rfind(".")
    ext = new_name = ""

    if not os.path.isdir(path) and re.search(r"\..{3}\.(gz|bz|bz2)$", cur_name):
        pos = -7
        if cur_name[-1:] == "2":
            pos -= 1
        ext = cur_name[pos:]
        old_name = cur_name[0:pos]
        new_name = old_name + copy
    elif os.path.isdir(path) or last_dot <= 0:
        old_name = cur_name
        new_name = old_name + copy
    else:
        ext = cur_name[last_dot:]
        old_name = cur_name[0:last_dot]
        new_name = old_name + copy

    pos = 0

    if old_name[-len(copy) :] == copy:
        new_name = old_name
    elif re.search(r"" + copy + r"\s\d+$", old_name):
        pos = old_name.rfind(copy) + len(copy)
        new_name = old_name[0:pos]
    else:
        new_path = os.path.join(cur_dir, new_name + ext)
        if not os.path.exists(new_path):
            return new_path

    # if we are here then copy already exists or making copy of copy
    # we will make new indexed copy *black magic*
    idx = 1
    if pos > 0:
        idx = int(old_name[pos:])
    while True:
        idx += 1
        new_name_ext = new_name + " " + str(idx) + ext
        new_path = os.path.join(cur_dir, new_name_ext)
        if not os.path.exists(new_path):
            return new_path
        # if idx >= 1000: break # possible loop


def _run_sub_process(cmd: List[str], valid_return: Optional[List[int]] = None) -> bool:
    if valid_return is None:
        valid_return = [0]
    try:
        completed = subprocess.run(
            cmd, input=b"", check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except (subprocess.SubprocessError, OSError):
        return False

    if completed.returncode not in valid_return:
        print(str(completed.stderr))
        return False

    return True


def _crop_tuple(size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """Return the crop rectangle, as a (left, upper, right, lower)-tuple."""
    width, height = size
    if width > height:  # landscape
        left = int((width - height) / 2)
        upper = 0
        right = left + height
        lower = height
        return (left, upper, right, lower)
    if height > width:  # portrait
        left = 0
        upper = int((height - width) / 2)
        right = width
        lower = upper + width
        return (left, upper, right, lower)

    # cube
    return None


def multi_urljoin(*parts: str) -> str:
    """Join multiple url parts into a valid url."""
    if parts[0].startswith("http"):
        return str(urljoin(parts[0], "/".join(part.strip("/") for part in parts[1:]),))
    return "/" + "/".join(part.strip("/") for part in parts if part)
