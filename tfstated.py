#! /usr/bin/env python3
"""
tfstated.py
A HTTP backend for terraform.
This provides a API server which exposes endpoints for handling tfstate management, with locks
"""

__version__ = "0.1.0"

import configparser
import errno
import json
import os.path
import shutil
import tempfile

from functools import wraps

from flask import Flask, abort, jsonify, request, Response
from flask.views import MethodView

app = Flask(__name__)


# --- Configuration Management ---
def load_config():
    """Load configuration settings from config.ini file.

    @return dict: Configuration dictionary containing:
            - DATA_DIR: Base directory for data storage
            - STATE_DIR: Directory for storing Terraform state files
            - LOCK_DIR: Directory for storing lock files
            - AUTH_ENABLED: Boolean indicating if authentication is enabled
            - AUTH_CONFIG: Dictionary with username/password if auth enabled
    """
    config = configparser.ConfigParser()
    config.read("config.ini")

    data_dir = config.get("default", "DATA_DIR", fallback=".")
    state_dir = os.path.join(data_dir, "state")
    lock_dir = os.path.join(data_dir, "lock")

    auth_enabled = config.getboolean("auth", "ENABLED", fallback=False)
    auth_config = {}
    if auth_enabled:
        auth_config = {
            "username": config.get("auth", "USERNAME"),
            "password": config.get("auth", "PASSWORD"),
        }

    return {
        "DATA_DIR": data_dir,
        "STATE_DIR": state_dir,
        "LOCK_DIR": lock_dir,
        "AUTH_ENABLED": auth_enabled,
        "AUTH_CONFIG": auth_config,
    }


app.config.update(load_config())


# --- File Operations ---
def load_json(file_):
    """Load JSON data from the given file."""
    with open(file_, encoding="utf-8") as f:
        return json.load(f)


def save_json(file_, data):
    """Save data to a JSON file safely using a temporary file."""
    with tempfile.NamedTemporaryFile(
        dir=tempfile.gettempdir(), delete=False, mode="w+", encoding="utf-8"
    ) as tmp_file:
        json.dump(data, tmp_file, ensure_ascii=False)
        tmp_file.flush()
        shutil.move(tmp_file.name, file_)


def setup():
    """Initialize"""

    # create required directories if it does not exist
    dirs = (app.config["STATE_DIR"], app.config["LOCK_DIR"])
    for dir_ in dirs:
        try:
            os.makedirs(dir_, exist_ok=True)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise


# --- Authentication ---
def check_auth(username, password):
    """Check if a username/password combination is valid.
    @param username: Username to verify
    @param password: Password to verify

    @return bool: True if authentication is disabled or credentials match config
    """
    if not app.config["AUTH_ENABLED"]:
        return True
    auth_config = app.config["AUTH_CONFIG"]
    return (
        username == auth_config["username"]
        and password == auth_config["password"]
    )


def requires_auth(f):
    """Decorator that verifies authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not app.config["AUTH_ENABLED"]:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return Response(
                "Could not verify your access level for that URL.\n"
                "You have to login with proper credentials",
                401,
                {"WWW-Authenticate": 'Basic realm="Login Required"'},
            )
        return f(*args, **kwargs)

    return decorated


# --- State Management ---
class StateManager:
    """Manages Terraform state file operations for a specific user and project."""

    def __init__(self, user_id, project_name):
        """
        @param user_id: Identifier for the user
        @param project_name: Name of the Terraform project
        """
        self.user_id = user_id
        self.project_name = project_name
        self.state_file = os.path.join(
            app.config["STATE_DIR"], f"{user_id}-{project_name}.tfstate"
        )

    def get_state(self):
        """Retrieve the current Terraform state.

        @return dict: Current Terraform state data

        @raises FileNotFoundError: If state file doesn't exist
        @raises OSError: If state file cannot be read
        """
        return load_json(self.state_file)

    def save_state(self, data):
        """Save new Terraform state data.
        @param data: State data to save
        @raises OSError: If state file cannot be written
        """
        if data.get("check_results") is None:
            data.pop("check_results", None)
        save_json(self.state_file, data)

    def delete_state(self):
        """Delete the current Terraform state file.
        @raises OSError: If state file cannot be deleted
        """
        os.remove(self.state_file)


class LockManager:
    """Manages Terraform state locking operations."""

    def create_lock(self, lock_data):
        """Create a new lock file.
        @param lock_data: Dictionary containing lock information with an 'ID' key
        @return bool: True if lock was created, False if it already exists
        @raises OSError: If lock file cannot be created
        """
        lock_file = os.path.join(
            app.config["LOCK_DIR"], f"{lock_data['ID']}.lock"
        )
        if os.path.exists(lock_file):
            return False
        save_json(lock_file, lock_data)
        return True

    def remove_lock(self, lock_id):
        """Remove an existing lock file.
        @param lock_id: ID of the lock to remove
        @return bool: True if lock was removed, False if it didn't exist
        @raises OSError: If lock file cannot be deleted
        """
        lock_file = os.path.join(app.config["LOCK_DIR"], f"{lock_id}.lock")
        if not os.path.exists(lock_file):
            return False
        os.remove(lock_file)
        return True

    def verify_lock(self, lock_id):
        """Check if a lock exists.
        @param lock_id: ID of the lock to verify
        @return bool: True if lock exists, False otherwise
        """
        lock_file = os.path.join(app.config["LOCK_DIR"], f"{lock_id}.lock")
        return os.path.exists(lock_file)


# --- Views ---
class StateView(MethodView):
    """Flask view handling Terraform state operations via HTTP endpoints."""

    @requires_auth
    def get(self, user_id, project_name):
        """HTTP GET Method Handler"""
        state_manager = StateManager(user_id, project_name)
        try:
            data = state_manager.get_state()
            return data
        except FileNotFoundError:
            return ("Not Found", 404)
        except OSError as exc:
            app.logger.error("Error retrieving state: %s", exc)
            abort(500)

    @requires_auth
    def post(self, user_id, project_name):
        """HTTP POST Method HANDLER"""
        lock_manager = LockManager()

        lock_id = request.args.get("ID")
        if lock_id and not lock_manager.verify_lock(lock_id):
            abort(409, "The requested lock does not exist")

        state_manager = StateManager(user_id, project_name)

        try:
            data = request.get_json()
            state_manager.save_state(data)
        except OSError as exc:
            app.logger.error("Error saving state: %s", exc)
            abort(500)

        return jsonify({"status": "Created"})

    @requires_auth
    def delete(self, user_id, project_name):
        """HTTP DELETE Method Handler"""
        state_manager = StateManager(user_id, project_name)
        try:
            state_manager.delete_state()
        except OSError as exc:
            app.logger.error("Error deleting state: %s", exc)
            abort(500)

        return jsonify({"status": "Deleted"})

    @requires_auth
    def lock(self):
        """HTTP LOCK Method Handler"""
        lock_manager = LockManager()
        data = request.get_json()

        try:
            if not lock_manager.create_lock(data):
                abort(423, "A Lock already exists")
        except OSError as exc:
            app.logger.error("Error creating lock: %s", exc)
            abort(500)

        return jsonify({"status": "Locked"})

    @requires_auth
    def unlock(self):
        """HTTP UNLOCK Method Handler"""
        lock_manager = LockManager()
        data = request.get_json()

        try:
            if not lock_manager.remove_lock(data["ID"]):
                abort(409, "Lock does not exist.")
        except OSError as exc:
            app.logger.error("Error removing lock: %s", exc)
            abort(500)

        return jsonify({"status": "Unlocked"})


# --- Error Handlers ---
@app.errorhandler(404)
def page_not_found(e):  # pylint:disable=unused-argument
    """Page Not Found Handler"""
    return "Not Found", 404


@app.errorhandler(409)
def conflict(e):
    """409 Conflict Error Handler"""
    return jsonify({"error": "Conflict", "message": str(e)}), 409


@app.errorhandler(423)
def locked(e):
    """423 Locked Error Handler"""
    return jsonify({"error": "Locked", "message": str(e)}), 423


@app.errorhandler(500)
def internal_server_error(e):
    """Internal Server Error Handler"""
    return (
        jsonify(
            {
                "error": "Internal Server Error",
                "message": f"An unexpected error occurred:\n{e}\n",
            }
        ),
        500,
    )


# --- URL Routes ---
state_view = StateView.as_view("state")
app.add_url_rule(
    "/state/<user_id>/<project_name>",
    view_func=state_view,
    methods=["GET", "POST", "DELETE"],
)
app.add_url_rule("/lock", view_func=state_view, methods=["LOCK"])
app.add_url_rule("/unlock", view_func=state_view, methods=["UNLOCK"])

if __name__ == "__main__":
    setup()
    app.run()
