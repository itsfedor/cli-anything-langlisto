import pathlib

from setuptools import setup, find_packages

_here = pathlib.Path(__file__).parent
_long = (_here / "README.md").read_text(encoding="utf-8")


setup(
    name="cli-langlisto",
    version="1.1.2",
    description="Unofficial CLI for the LangListo teacher workspace: students, classes, schedule, materials and books",
    long_description=_long,
    long_description_content_type="text/markdown",
    author="Fedor Molodtsov",
    url="https://github.com/itsfedor/cli-langlisto",
    license="MIT",
    packages=find_packages(include=["cli_langlisto*"]),
    python_requires=">=3.9",
    install_requires=["click>=8"],
    entry_points={
        "console_scripts": [
            "cli-langlisto = cli_langlisto.langlisto_cli:cli",
            # legacy name (pre-1.1), kept as an alias so existing scripts keep working
            "cli-anything-langlisto = cli_langlisto.langlisto_cli:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
