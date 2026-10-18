"""setup.py file for the adb_shell package."""

from setuptools import setup

with open("README.rst") as f:
    readme = f.read()

setup(
    name="adb-shell-wireless",
    version="0.4.4+wireless.1",
    description="A Python implementation of ADB with shell and FileSync functionality, plus Android 11+ wireless debugging (STLS) support.",
    long_description=readme,
    keywords=["adb", "android", "wireless debugging", "stls", "tls"],
    author="Jeff Irion",
    author_email="jefflirion@users.noreply.github.com",
    project_urls={
        "Upstream project": "https://github.com/JeffLIrion/adb_shell",
    },
    packages=["adb_shell", "adb_shell.auth", "adb_shell.transport"],
    install_requires=["cryptography", "pyasn1", "rsa"],
    tests_require=["pycryptodome", "libusb1>=1.0.16"],
    extras_require={"usb": ["libusb1>=1.0.16"], "async": ["aiofiles>=0.4.0", "async_timeout>=3.0.0"]},
    # The TLS support added by this fork is Python 3 only: `adb_shell.tls` uses
    # `datetime.timezone` and `ssl.PROTOCOL_TLS_CLIENT`, and the TCP transports import it.
    python_requires=">=3.7",
    classifiers=[
        "Operating System :: OS Independent",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
    ],
    test_suite="tests",
)
