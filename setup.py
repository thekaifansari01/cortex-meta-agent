from setuptools import setup, find_packages

setup(
    name="cortex-cli",
    version="1.0.0",
    packages=find_packages(),
    py_modules=["main"],
    entry_points={
        "console_scripts": [
            "cortex=main:main",
        ],
    },
)
