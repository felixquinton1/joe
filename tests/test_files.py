import base64

import pytest

from joe.files import FileLibrary, MAX_FILE_BYTES


def test_files_are_scoped_by_project_and_resolved(tmp_path):
    library = FileLibrary(tmp_path)
    library.ensure()

    item = library.add(
        "project-a",
        "../notes.md",
        "text/markdown",
        base64.b64encode(b"# Notes").decode(),
    )

    assert item["name"] == "notes.md"
    assert "path" not in item
    assert library.list("project-b") == []
    assert library.list("project-a") == [item]
    resolved = library.resolve([item["id"]], "project-a")
    assert resolved[0]["path"]
    assert library.resolve([item["id"]], "project-b") == []


def test_delete_removes_metadata_and_content(tmp_path):
    library = FileLibrary(tmp_path)
    library.ensure()
    item = library.add(
        "free",
        "brief.txt",
        "text/plain",
        base64.b64encode(b"hello").decode(),
    )
    path = library.get(item["id"])["path"]

    assert library.delete(item["id"]) is True
    assert library.get(item["id"]) is None
    assert not tmp_path.joinpath("library").joinpath("free", path.split("/")[-1]).exists()
    assert library.delete(item["id"]) is False


def test_rejects_invalid_or_oversized_payload(tmp_path):
    library = FileLibrary(tmp_path)
    library.ensure()

    with pytest.raises(ValueError):
        library.add("free", "bad", "", "not-base64")
    with pytest.raises(ValueError, match="8 Mio"):
        library.add(
            "free",
            "large.bin",
            "application/octet-stream",
            base64.b64encode(b"x" * (MAX_FILE_BYTES + 1)).decode(),
        )


def test_download_and_delete_stay_scoped_to_their_project(tmp_path):
    """Un identifiant seul ne doit pas suffire à sortir un fichier d'un projet."""
    library = FileLibrary(tmp_path)
    library.ensure()
    item = library.add("projet-a", "notes.txt", "text/plain", base64.b64encode(b"x").decode())

    assert library.get(item["id"], "projet-a") is not None
    assert library.get(item["id"], "projet-b") is None
    assert library.delete(item["id"], "projet-b") is False
    assert library.get(item["id"], "projet-a") is not None
    assert library.delete(item["id"], "projet-a") is True
    assert library.get(item["id"], "projet-a") is None
