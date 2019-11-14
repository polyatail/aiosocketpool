lint:
	flake8 aio_socketpool/
	black -l 100 --check aio_socketpool/ tests/
	pydocstyle --convention=numpy --add-ignore=D100,D101,D102,D103,D104,D105,D202 aio_socketpool/ tests/
format:
	black -l 100 aio_socketpool/ tests/
test:
	py.test -v -W ignore::DeprecationWarning tests/
update_deps:
	pip-compile --generate-hashes
