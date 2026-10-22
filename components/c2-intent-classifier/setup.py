"""Editable install fallback for older pip (the stock macOS 3.9 pip)."""

from setuptools import find_packages, setup

setup(
    name="c2-intent-classifier",
    version="0.1.0",
    description="MediGuard C2 semantic intent classifier",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.9",
    install_requires=[
        "fastapi>=0.115",
        "uvicorn[standard]>=0.30",
        "pydantic>=2",
        "pydantic-settings>=2",
        "httpx>=0.27",
        "pyyaml>=6",
        "torch>=2.2",
        "transformers>=4.44",
        "safetensors>=0.4",
    ],
    extras_require={
        "similarity": [
            "chromadb>=0.5",
            "sentence-transformers>=3",
        ],
        "dev": [
            "pytest>=8",
            "jsonschema>=4",
        ],
    },
)
