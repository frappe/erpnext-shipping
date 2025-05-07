# TODO: Remove this file when bench >=v5.11.0 is adopted / v15.0.0 is released
from setuptools import setup, find_packages

setup(
    name="erpnext_shipping",
    version="0.0.1",
    description="ERPNext Shipping Integration with One World Express",
    author="Your Company",
    author_email="your.email@example.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=[
        "frappe>=14.0.0",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "urllib3>=2.0.0",
        "typing-extensions>=4.0.0"
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Framework :: Frappe",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Office/Business :: Financial :: Accounting",
    ],
)
