#! /usr/bin/env python3
import errno
import json
import os.path
import shutil
from flask import Flask, abort, jsonify, request, Response
from functools import wraps
from flask.views import MethodView
import tempfile
import configparser

app = Flask(__name__)

config = configparser.ConfigParser()
config.read('config.ini')
data_dir = config.get('default', 'DATA_DIR', fallback='.')
state_dir = os.path.join(data_dir, 'state')
lock_dir = os.path.join(data_dir, 'lock')
auth_enabled = config.getboolean('auth', 'ENABLED', fallback=False)
if auth_enabled:
    auth_username = config.get('auth', 'USERNAME')
    auth_password = config.get('auth', 'PASSWORD')

app.config.from_mapping(
    DATA_DIR=data_dir,
    STATE_DIR=state_dir,
    LOCK_DIR=lock_dir,
)

def setup():
    for dir_ in (state_dir, lock_dir):
        try:
            os.makedirs(dir_, exist_ok=True)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise

def load(file_):
    with open(file_, encoding='utf-8') as f:
        return json.load(f)

def save(file_, data):
    with tempfile.NamedTemporaryFile(
        dir=tempfile.gettempdir(), delete=False, mode="w+", encoding="utf-8"
    ) as tmp_file:
        json.dump(data, tmp_file, ensure_ascii=False)
        tmp_file.flush()
        shutil.move(tmp_file.name, file_)

def check_auth(username, password):
    """Check if a username/password combination is valid."""
    if not auth_enabled:
        return True
    return username == auth_username and password == auth_password

def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not auth_enabled:
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

class StateView(MethodView):

    def configure_file_names(self, userid, setup_name):
        self.state_file = os.path.join(app.config['STATE_DIR'], f"{userid}-{setup_name}.tfstate")
        self.lock_file = os.path.join(app.config['LOCK_DIR'], f"{userid}-{setup_name}.lock")

    @requires_auth
    def get(self, user_id, setup_name):
        # print(f"GET request data: {request.args}")
        # print(f"GET request headers: {request.headers}")
        self.configure_file_names(user_id, setup_name)
        try:
            data = load(self.state_file)
            return data
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return ('Not Found\n', 404)
            app.logger.error('Error sending %s: %s', self.state_file, exc) # pylint: disable=E1101
            abort(500)
        
    @requires_auth
    def post(self, user_id, setup_name):
        self.configure_file_names(user_id, setup_name)
        # print(f"POST request args: {request.args}")
        
        lock_id = request.args.get('ID')
        if lock_id is not None:
            lock_file_name = os.path.join(app.config['LOCK_DIR'], f"{lock_id}.lock")
            if not os.path.exists(lock_file_name):
                abort(409, "The requested lock does not exist") # Conflict, The requested lock does not exist

        # print(f"POST request data: {request.get_json()}")

        try:
            data = request.get_json()
            if data['check_results'] is None:
                del data['check_results']
            save(self.state_file, data)
        except OSError as exc:
            app.logger.error('Error saving data to state file %s: %s', self.state_file, exc)
            abort(500)

        return jsonify({'status': 'Created'})
        
    @requires_auth
    def delete(self, user_id, setup_name):
        # print(f"DELETE request data: {request.get_json()}")
        self.configure_file_names(user_id, setup_name)
        try:
            os.remove(self.state_file)
        except OSError as exc:
            app.logger.error('Error deleting %s: %s', self.state_file, exc)
            abort(500)

        return jsonify({'status': 'Deleted'})
        
    @requires_auth
    def lock(self, user_id, setup_name):
        # print(f"LOCK request data: {request.get_json()}")
        self.configure_file_names(user_id, setup_name)
        data = request.get_json()
        lock_file_name = os.path.join(app.config['LOCK_DIR'], f"{data['ID']}.lock")

        if os.path.exists(lock_file_name):
            abort(423, "A Lock already exists") # A Lock already exists

        try:
            save(lock_file_name, data)
        except OSError as exc:
            app.logger.error('Error saving data to lock file %s: %s', lock_file_name, exc)
            abort(500)

        return jsonify({'status': 'Locked'})
        
    @requires_auth
    def unlock(self, user_id, setup_name):
        # print(f"UNLOCK request data: {request.get_json()}")
        self.configure_file_names(user_id, setup_name)
        data = request.get_json()
        lock_file_name = os.path.join(app.config['LOCK_DIR'], f"{data['ID']}.lock")

        if not os.path.exists(lock_file_name):
            abort(409, description="Lock does not exist.")  # Conflict

        try:
            os.remove(lock_file_name)
        except OSError as exc:
            app.logger.error('Error deleting %s: %s', lock_file_name, exc)
            abort(500)

        return jsonify({'status': 'Unlocked'})

@app.errorhandler(400)
def bad_request(e):
    return jsonify({'error': 'Bad Request', 'message': str(e)}), 400

@app.errorhandler(404)
def page_not_found(e):
    return 'Not Found', 404

@app.errorhandler(500)
def internal_server_error(e):
    return jsonify({'error': 'Internal Server Error', 'message': 'An unexpected error occurred.'}), 500

state_view = StateView.as_view('state')
app.add_url_rule('/state/<user_id>/<setup_name>', view_func=state_view, methods=['GET', 'POST', 'DELETE'])
app.add_url_rule('/state/<user_id>/<setup_name>', view_func=state_view, methods=['LOCK', 'UNLOCK'])

if __name__ == '__main__':
    setup()
    app.run(debug=True)