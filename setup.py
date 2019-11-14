from setuptools import setup, find_packages

with open("aio_socketpool/version.py") as version_file:
    exec(version_file.read())

setup(
    name="aio_socketpool",
    version=__version__,
    packages=find_packages(exclude=["*test*"]),
    install_requires=[
        "async_timeout>=3.0.1",
    ],
    extras_require={
        "testing": [
            "black>=19.3b0",
            "flake8>=3.7.8",
            "piptools>=4.2.0",
            "pydocstyle>=4.0.0",
            "pytest>=5.1.0",
            "pytest-asyncio>=0.10.0",
        ]
    },
)
