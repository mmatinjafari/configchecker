from setuptools import setup, find_packages

setup(
    name="configchecker",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "aiohttp>=3.9.1",
        "python-dotenv>=1.0.0",
        "tqdm>=4.66.1",
        "rich>=13.7.0",
        "qrcode>=8.0",
    ],
    include_package_data=True,
    package_data={
        "configchecker": ["*.txt"],
    },
    entry_points={
        'console_scripts': [
            'configchecker=configchecker.cli:main',
        ],
    },
    author="Marczo",
    description="A high-performance V2Ray/Proxy stability checker.",
    python_requires='>=3.8',
)
