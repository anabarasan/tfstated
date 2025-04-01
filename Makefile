test:
	coverage run -m unittest test_stateview.py
	coverage report --omit test_stateview.py --show-missing
