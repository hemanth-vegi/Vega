"""Disposable in-memory Linux practice simulator; it never invokes host commands."""

import posixpath
import shlex
import threading


class PracticeLab:
    PACKAGES = {
        "httpd": "Apache HTTP Server",
        "nginx": "Nginx web server",
        "git": "Distributed version control system",
        "python3": "Python 3 interpreter",
        "podman": "Daemonless container engine",
        "vim": "Vi IMproved text editor",
    }

    def __init__(self):
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self.cwd = "/home/student"
            self.dirs = {"/", "/home", "/home/student", "/tmp", "/etc", "/var", "/var/log"}
            self.files = {
                "/etc/os-release": "NAME=Rocky Linux (simulated)\nVERSION_ID=9\n",
                "/home/student/README.txt": "Welcome to the disposable Vega Practice Lab.\n",
                "/var/log/messages": "[simulated] system ready\n",
            }
            self.installed = {"bash", "coreutils", "dnf", "filesystem", "rpm"}
            return self.state()

    def state(self):
        with self._lock:
            return {"cwd": self.cwd, "installed": sorted(self.installed), "simulated": True}

    def _path(self, value):
        path = str(value)
        if path.startswith("/"):
            full = posixpath.normpath(path)
        else:
            full = posixpath.normpath(posixpath.join(self.cwd, path))
        if not full.startswith("/"):
            full = "/" + full
        return full

    def command(self, command):
        command = str(command or "").strip()
        if not command:
            raise ValueError("Enter a simulated command.")
        if len(command) > 500:
            raise ValueError("Commands are limited to 500 characters.")
        if any(char in command for char in ";&|<>$`(){}\\\n\r"):
            raise ValueError("Shell operators and scripting are disabled. Enter one simple simulated command.")
        try:
            parts = shlex.split(command, posix=True)
        except ValueError:
            raise ValueError("That command could not be parsed.") from None
        if not parts:
            raise ValueError("Enter a simulated command.")
        with self._lock:
            return self._execute(parts)

    def _execute(self, parts):
        cmd, args = parts[0], parts[1:]
        if cmd in ("help", "?"):
            return "Practice Lab commands: pwd, ls [path], cd [path], mkdir <name>, touch <file>, cat <file>, dnf search|info|list installed|install <package>, rpm -qi <package>, reset. All effects stay in this in-memory simulator."
        if cmd == "pwd" and not args:
            return self.cwd
        if cmd == "ls" and len(args) <= 1:
            target = self._path(args[0]) if args else self.cwd
            if target not in self.dirs:
                raise ValueError(f"No simulated directory: {target}")
            names = set()
            prefix = target.rstrip("/") + "/" if target != "/" else "/"
            for directory in self.dirs:
                if directory != target and directory.startswith(prefix):
                    remainder = directory[len(prefix):]
                    if remainder and "/" not in remainder:
                        names.add(remainder + "/")
            for filename in self.files:
                if filename.startswith(prefix):
                    remainder = filename[len(prefix):]
                    if remainder and "/" not in remainder:
                        names.add(remainder)
            return "\n".join(sorted(names)) or "(empty)"
        if cmd == "cd" and len(args) <= 1:
            target = self._path(args[0]) if args else "/home/student"
            if target not in self.dirs:
                raise ValueError(f"No simulated directory: {target}")
            self.cwd = target
            return self.cwd
        if cmd == "mkdir" and len(args) == 1:
            target = self._path(args[0])
            parent = posixpath.dirname(target) or "/"
            if parent not in self.dirs:
                raise ValueError(f"No simulated parent directory: {parent}")
            if target in self.dirs or target in self.files:
                raise ValueError(f"Already exists in the simulator: {target}")
            self.dirs.add(target)
            return f"Created simulated directory {target}"
        if cmd == "touch" and len(args) == 1:
            target = self._path(args[0])
            if posixpath.dirname(target) not in self.dirs:
                raise ValueError("No simulated parent directory.")
            self.files.setdefault(target, "")
            return f"Created simulated file {target}"
        if cmd == "cat" and len(args) == 1:
            target = self._path(args[0])
            if target not in self.files:
                raise ValueError(f"No simulated file: {target}")
            return self.files[target].rstrip("\n")
        if cmd == "dnf" and len(args) >= 1:
            action = args[0]
            if action == "search" and len(args) == 2:
                query = args[1].lower()
                matches = [f"{name} - {desc}" for name, desc in self.PACKAGES.items() if query in name or query in desc.lower()]
                return "\n".join(matches) or "No simulated packages found."
            if action == "info" and len(args) == 2:
                name = args[1]
                if name not in self.PACKAGES:
                    raise ValueError(f"No simulated package named {name}.")
                state = "installed" if name in self.installed else "available"
                return f"Name : {name}\nSummary : {self.PACKAGES[name]}\nStatus : {state} (simulation)"
            if action == "list" and args[1:] == ["installed"]:
                return "\n".join(sorted(f"{name} (simulated)" for name in self.installed))
            if action == "install" and len(args) == 2:
                name = args[1]
                if name not in self.PACKAGES:
                    raise ValueError(f"No simulated package named {name}.")
                self.installed.add(name)
                return f"Installed {name} in the disposable simulator. No host package manager was run."
        if cmd == "rpm" and args[:1] == ["-qi"] and len(args) == 2:
            name = args[1]
            if name not in self.installed:
                raise ValueError(f"{name} is not installed in the simulator.")
            description = self.PACKAGES.get(name, "Base simulated system package")
            return f"Name : {name}\nDescription : {description}\nInstall status : simulated"
        raise ValueError("Command is not in the simulator. Type help to see supported commands.")
