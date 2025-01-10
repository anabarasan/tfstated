#! /usr/bin/env python3
import configparser
import errno
import json
import os.path
import shutil
import tempfile
from flask import Flask, abort, jsonify, request, Response
from flask.views import MethodView
from functools import wraps

app = Flask(__name__)

# --- Configuration Management ---
def load_config():
    config = configparser.ConfigParser()
    config.read('config.ini')

    data_dir = config.get('default', 'DATA_DIR', fallback='.')
    state_dir = os.path.join(data_dir, 'state')
    lock_dir = os.path.join(data_dir, 'lock')

    auth_enabled = config.getboolean('auth', 'ENABLED', fallback=False)
    auth_config = {}
    if auth_enabled:
        auth_config = {
            'username': config.get('auth', 'USERNAME'),
            'password': config.get('auth', 'PASSWORD')
        }

    return {
        'DATA_DIR': data_dir,
        'STATE_DIR': state_dir,
        'LOCK_DIR': lock_dir,
        'AUTH_ENABLED': auth_enabled,
        'AUTH_CONFIG': auth_config
    }

app.config.update(load_config())

# --- File Operations ---
def load_json(file_):
    with open(file_, encoding='utf-8') as f:
        return json.load(f)

def save_json(file_, data):
    with tempfile.NamedTemporaryFile(
        dir=tempfile.gettempdir(), delete=False, mode="w+", encoding="utf-8"
    ) as tmp_file:
        json.dump(data, tmp_file, ensure_ascii=False)
        tmp_file.flush()
        shutil.move(tmp_file.name, file_)

def ensure_directories(*dirs):
    for dir_ in dirs:
        try:
            os.makedirs(dir_, exist_ok=True)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise

def setup():
    ensure_directories(app.config['STATE_DIR'], app.config['LOCK_DIR'])

# --- Authentication ---
def check_auth(username, password):
    """Check if a username/password combination is valid."""
    if not app.config['AUTH_ENABLED']:
        return True
    auth_config = app.config['AUTH_CONFIG']
    return username == auth_config['username'] and password == auth_config['password']

def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    """Decorator that verifies authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not app.config['AUTH_ENABLED']:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- State Management ---
class StateManager:
    def __init__(self, user_id, project_name):
        self.user_id = user_id
        self.project_name = project_name
        self.state_file = os.path.join(app.config['STATE_DIR'], f"{user_id}-{project_name}.tfstate")

    def get_state(self):
        return load_json(self.state_file)

    def save_state(self, data):
        if data.get('check_results') is None:
            data.pop('check_results', None)
        save_json(self.state_file, data)

    def delete_state(self):
        os.remove(self.state_file)

class LockManager:
    def create_lock(self, lock_data):
        lock_file = os.path.join(app.config['LOCK_DIR'], f"{lock_data['ID']}.lock")
        if os.path.exists(lock_file):
            return False
        save_json(lock_file, lock_data)
        return True

    def remove_lock(self, lock_id):
        lock_file = os.path.join(app.config['LOCK_DIR'], f"{lock_id}.lock")
        if not os.path.exists(lock_file):
            return False
        os.remove(lock_file)
        return True

    def verify_lock(self, lock_id):
        lock_file = os.path.join(app.config['LOCK_DIR'], f"{lock_id}.lock")
        return os.path.exists(lock_file)

# --- Views ---
class StateView(MethodView):
    @requires_auth
    def get(self, user_id, project_name):
        state_manager = StateManager(user_id, project_name)
        try:
            data = state_manager.get_state()
            return data
        except FileNotFoundError:
            return ('Not Found\n', 404)
        except OSError as exc:
            app.logger.error('Error retrieving state: %s', exc)
            abort(500)
        
    @requires_auth
    def post(self, user_id, project_name):
        lock_manager = LockManager()
        
        lock_id = request.args.get('ID')
        if lock_id and not lock_manager.verify_lock(lock_id):
            abort(409, "The requested lock does not exist")

        state_manager = StateManager(user_id, project_name)

        try:
            data = request.get_json()
            state_manager.save_state(data)
        except OSError as exc:
            app.logger.error('Error saving state: %s', exc)
            abort(500)

        return jsonify({'status': 'Created'})
        
    @requires_auth
    def delete(self, user_id, project_name):
        state_manager = StateManager(user_id, project_name)
        try:
            state_manager.delete_state()
        except OSError as exc:
            app.logger.error('Error deleting state: %s', exc)
            abort(500)

        return jsonify({'status': 'Deleted'})
        
    @requires_auth
    def lock(self):
        lock_manager = LockManager()
        data = request.get_json()

        try:
            if not lock_manager.create_lock(data):
                abort(423, "A Lock already exists")
        except OSError as exc:
            app.logger.error('Error creating lock: %s', exc)
            abort(500)

        return jsonify({'status': 'Locked'})
        
    @requires_auth
    def unlock(self):
        lock_manager = LockManager()
        data = request.get_json()

        try:
            if not lock_manager.remove_lock(data['ID']):
                abort(409, "Lock does not exist.")
        except OSError as exc:
            app.logger.error('Error removing lock: %s', exc)
            abort(500)

        return jsonify({'status': 'Unlocked'})

# --- Error Handlers ---
@app.errorhandler(400)
def bad_request(e):
    return jsonify({
        'error': 'Bad Request',
        'message': str(e)
    }), 400

@app.errorhandler(404)
def page_not_found(e):
    return 'Not Found', 404

@app.errorhandler(409)
def conflict(e):
    return jsonify({
        'error': 'Conflict',
        'message': str(e)
    }), 409

@app.errorhandler(423)
def locked(e):
    return jsonify({
        'error': 'Locked',
        'message': str(e)
    }), 423

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify({
        'error': 'Internal Server Error',
        'message': 'An unexpected error occurred.'
    }), 500

# --- URL Routes ---
state_view = StateView.as_view('state')
app.add_url_rule('/state/<user_id>/<project_name>', view_func=state_view, methods=['GET', 'POST', 'DELETE'])
app.add_url_rule('/lock', view_func=state_view, methods=['LOCK'])
app.add_url_rule('/unlock', view_func=state_view, methods=['UNLOCK'])

if __name__ == '__main__':
    setup()
    app.run()
