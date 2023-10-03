lint:
	flake8 aiosocketpool/
	black -l 100 --check aiosocketpool/ tests/
	pydocstyle --convention=numpy --add-ignore=D100,D101,D102,D103,D104,D105,D202 aiosocketpool/ tests/
	mypy .
format:
	black -l 100 aiosocketpool/ tests/
test:
	py.test -v -W ignore::DeprecationWarning tests/
