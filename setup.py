from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-langlisto",
    version="1.0.1",
    description="Unofficial CLI for the LangListo teacher workspace: students, classes, schedule, materials and books",
    author="Fedor Molodtsov",
    url="https://github.com/itsfedor/cli-anything-langlisto",
    license="MIT",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    python_requires=">=3.9",
    install_requires=["click>=8"],
    entry_points={
        "console_scripts": [
            "cli-anything-langlisto = cli_anything.langlisto.langlisto_cli:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
