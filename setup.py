from setuptools import setup, find_packages  # type: ignore

with open("aiosocketpool/version.py") as version_file:
    exec(version_file.read())

with open("README.md") as readme:
    README = readme.read()

setup(
    name="aiosocketpool",
    version=__version__,  # type: ignore
    packages=find_packages(exclude=["*test*"]),
    install_requires=["async_timeout>=3.0.1"],
    extras_require={
        "testing": [
            "black>=19.3b0",
            "flake8>=3.7.8",
            "pip-tools>=4.2.0",
            "pydocstyle>=4.0.0",
            "pytest>=5.1.0",
            "pytest-asyncio>=0.10.0",
        ]
    },
    author="Roo Sczesnak",
    author_email="andrewscz@gmail.com",
    description="An asyncio-compatible socket pool",
    long_description=README,
    long_description_content_type="text/markdown",
    license="MIT License",
    keyword="asyncio socketpool aiosocketpool",
    url="https://github.com/polyatail/aiosocketpool",
    test_suite="tests",
)
