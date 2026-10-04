def test_package_files_exist():
    from pathlib import Path
    assert Path("app/main.py").exists()
    assert Path("requirements.txt").exists()
