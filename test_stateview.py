import base64
import json
import os
import shutil
import unittest
from tfstated import app, setup


class TestOpenStateView(unittest.TestCase):
    def setUp(self):
        self.configure()
        setup()
        self.client = app.test_client()

    def tearDown(self):
        # Cleanup after tests
        for path in [app.config["STATE_DIR"], app.config["LOCK_DIR"]]:
            try:
                shutil.rmtree(path)
            except Exception as e:
                pass

    def configure(self):
        """Configure the app for testing"""
        app.config["TESTING"] = True
        app.config["STATE_DIR"] = "./tmp"
        app.config["LOCK_DIR"] = "./tmp"
        app.config["AUTH_ENABLED"] = False
        app.config["AUTH_CONFIG"] = {
            "username": "test-user",
            "password": "test-password",
        }

    def headers(self):
        """add standard headers to the requests"""
        headers = None
        if app.config["AUTH_ENABLED"]:
            auth = base64.b64encode(
                (
                    app.config["AUTH_CONFIG"]["username"]
                    + ":"
                    + app.config["AUTH_CONFIG"]["password"]
                ).encode("utf-8")
            ).decode("utf-8")
            headers = {"Authorization": "Basic " + auth}
        # print(headers)
        return headers

    def test_get_nonexistent_state(self):
        """Test GET request when state file doesn't exist"""
        response = self.client.get(
            "/state/anbarasan/a1b2c3", headers=self.headers()
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, b"Not Found")

    def test_post_and_get_state(self):
        """Test POST request followed by GET request"""
        test_data = {
            "version": 4,
            "terraform_version": "1.0.0",
            "serial": 1,
            "lineage": "test",
            "check_results": None,
        }

        # Test POST
        response = self.client.post(
            "/state/anbarasan/a1b2c3", json=test_data, headers=self.headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "Created"})

        # Test GET
        response = self.client.get(
            "/state/anbarasan/a1b2c3", headers=self.headers()
        )
        self.assertEqual(response.status_code, 200)
        received_data = json.loads(response.data)
        self.assertNotIn(
            "check_results", received_data
        )  # Should be removed as per post method
        test_data.pop("check_results")
        self.assertEqual(received_data, test_data)

    def test_delete_state(self):
        """Test DELETE request"""
        # First create a state file
        test_data = {"version": 4, "check_results": True}
        self.client.post(
            "/state/anbarasan/a1b2c3", json=test_data, headers=self.headers()
        )

        # Then delete it
        response = self.client.delete(
            "/state/anbarasan/a1b2c3", headers=self.headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "Deleted"})

        # Verify it's gone
        response = self.client.get(
            "/state/anbarasan/a1b2c3", headers=self.headers()
        )
        self.assertEqual(response.status_code, 404)

    def test_lock_and_unlock_state(self):
        """Test LOCK and UNLOCK operations"""
        test_lock_data = {
            "ID": "test-lock",
            "Operation": "plan",
            "Info": "test lock info",
        }

        # Test LOCK
        response = self.client.open(
            "/lock",
            method="LOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "Locked"})

        # Verify lock file exists and contains correct data
        lock_file_name = os.path.join(
            app.config["LOCK_DIR"], f"{test_lock_data['ID']}.lock"
        )
        with open(lock_file_name, "r") as f:
            saved_lock_data = json.load(f)
        self.assertEqual(saved_lock_data, test_lock_data)

        # Test UNLOCK
        response = self.client.open(
            "/unlock",
            method="UNLOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "Unlocked"})

        # Verify lock file is gone
        self.assertFalse(os.path.exists(lock_file_name))

    def test_post_with_lock(self):
        """Test POST request when state file is locked"""
        test_data = {"version": 4, "check_results": True}
        test_lock_data = {
            "ID": "test-lock",
            "Operation": "plan",
            "Info": "test lock info",
        }

        # Create a lock
        response = self.client.open(
            "/lock",
            method="LOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

        # Attempt to POST state
        response = self.client.post(
            "/state/anbarasan/a1b2c3?ID=test-lock",
            json=test_data,
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 200)

        # remove lock
        response = self.client.open(
            "/unlock",
            method="UNLOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

    def test_post_with_invalid_lock(self):
        """Test POST request when lock ID doesn't match"""
        test_data = {"version": 4, "check_results": True}
        test_lock_data = {
            "ID": "test-lock",
            "Operation": "plan",
            "Info": "test lock info",
        }

        # Create a lock
        response = self.client.open(
            "/lock",
            method="LOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

        # Attempt to POST state with different lock ID
        response = self.client.post(
            "/state/anbarasan/a1b2c3?ID=invalid-lock",
            json=test_data,
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json,
            {
                "error": "Conflict",
                "message": "409 Conflict: The requested lock does not exist",
            },
        )

        # remove lock
        response = self.client.open(
            "/unlock",
            method="UNLOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

    def test_duplicate_lock_fails_with_423(self):
        """Test that duplicate lock fails with 423 status code"""
        test_lock_data = {
            "ID": "test-lock",
            "Operation": "plan",
            "Info": "test lock info",
        }

        # Create a lock
        response = self.client.open(
            "/lock",
            method="LOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

        # Attempt to create duplicate lock
        response = self.client.open(
            "/lock",
            method="LOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 423)
        self.assertEqual(
            response.json,
            {
                "error": "Locked",
                "message": "423 Locked: A Lock already exists",
            },
        )

        # remove lock
        response = self.client.open(
            "/unlock",
            method="UNLOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )

    def test_unlock_invalid_id_fails_with_409(self):
        """Test that unlocking an invalid lock ID fails with 409 status code"""
        test_lock_data = {
            "ID": "test-lock",
            "Operation": "plan",
            "Info": "test lock info",
        }

        # Attempt to unlock an invalid lock ID
        response = self.client.open(
            "/unlock",
            method="UNLOCK",
            json=test_lock_data,
            content_type="application/json",
            headers=self.headers(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json,
            {
                "error": "Conflict",
                "message": "409 Conflict: Lock does not exist.",
            },
        )

    def test_get_without_auth_fails_with_401(self):
        """Test that GET request without authentication fails with 401 status code"""
        if not app.config["AUTH_ENABLED"]:
            self.skipTest("Skipping test as authentication is not enabled")

        response = self.client.get("/state/anbarasan/a1b2c3")
        self.assertEqual(response.status_code, 401)


class TestAuthenticatedStateView(TestOpenStateView):
    def configure(self):
        """Configure the app for testing"""
        super().configure()
        app.config["AUTH_ENABLED"] = True


if __name__ == "__main__":
    unittest.main(verbosity=2)
