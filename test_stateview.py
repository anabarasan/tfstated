
import errno
import json
import os
import shutil
import unittest
from tfstated import app, setup, StateView

class TestStateView(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['STATE_DIR'] = './tmp'
        app.config['LOCK_DIR'] = './tmp'
        for dir_ in (app.config['STATE_DIR'], app.config['LOCK_DIR']):
            try:
                os.makedirs(dir_, exist_ok=True)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
        self.client = app.test_client()

    def tearDown(self):
        # Cleanup after tests
        for path in [app.config['STATE_DIR'], app.config['LOCK_DIR']]:
            try:
                shutil.rmtree(path)
            except Exception as e:
                pass

    def test_get_nonexistent_state(self):
        """Test GET request when state file doesn't exist"""
        response = self.client.get('/sate/anbarasan/a1b2c3')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, b'Not Found')

    def test_post_and_get_state(self):
        """Test POST request followed by GET request"""
        test_data = {
            'version': 4,
            'terraform_version': '1.0.0',
            'serial': 1,
            'lineage': 'test',
            'check_results': None
        }
        
        # Test POST
        response = self.client.post('/state/anbarasan/a1b2c3', json=test_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'Created'})
        
        # Test GET
        response = self.client.get('/state/anbarasan/a1b2c3')
        self.assertEqual(response.status_code, 200)
        received_data = json.loads(response.data)
        self.assertNotIn('check_results', received_data)  # Should be removed as per post method
        test_data.pop('check_results')
        self.assertEqual(received_data, test_data)

    def test_delete_state(self):
        """Test DELETE request"""
        # First create a state file
        test_data = {'version': 4, 'check_results': True}
        self.client.post('/state/anbarasan/a1b2c3', json=test_data)
        
        # Then delete it
        response = self.client.delete('/state/anbarasan/a1b2c3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'Deleted'})
        
        # Verify it's gone
        response = self.client.get('/state/anbarasan/a1b2c3')
        self.assertEqual(response.status_code, 404)

    def test_lock_and_unlock_state(self):
        """Test LOCK and UNLOCK operations"""
        test_lock_data = {
            'ID': 'test-lock',
            'Operation': 'plan',
            'Info': 'test lock info'
        }
        
        # Test LOCK
        response = self.client.open(
            '/lock',
            method='LOCK',
            json=test_lock_data,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'Locked'})
        
        # Verify lock file exists and contains correct data
        lock_file_name = os.path.join(app.config['LOCK_DIR'], f"{test_lock_data['ID']}.lock")
        with open(lock_file_name, 'r') as f:
            saved_lock_data = json.load(f)
        self.assertEqual(saved_lock_data, test_lock_data)
        
        # Test UNLOCK
        response = self.client.open(
            '/unlock',
            method='UNLOCK',
            json=test_lock_data,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {'status': 'Unlocked'})
        
        # Verify lock file is gone
        self.assertFalse(os.path.exists(lock_file_name))


if __name__ == '__main__':
    unittest.main(verbosity=2)