from setuptools import setup, find_packages

setup(
    name="pullbox",
    version='0.1',
    description="A dead-simle Dropbox alternative using Git",
    keywords='dropbox,file synchronization,git',
    author='Prashanth Ellina',
    author_email="Use the github issues",
    url="https://github.com/prashanthellina/pullbox",
    license='MIT License',
    install_requires=[
        'filelock',
        'watchdog',
    ],
    package_dir={'pullbox': 'pullbox'},
    packages=find_packages('.'),
    include_package_data=True,

    entry_points = {
        'console_scripts': [
            'pullbox = pullbox:main',
        ],
    },
)
