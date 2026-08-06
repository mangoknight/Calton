"""T32: task attachments.

Expected values come from a live Go reference server, and all 49 cases here also ran as a
differential against it on the same seed.

The download's **headers** are as much of the contract as its body — the parity harness
compares them byte for byte — so most of this file is about headers, including on the
error responses, where three of them disappear.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# starlette's TestClient is built on httpx2, not httpx (see pyproject); importing
# the response type from the wrong one type-checks locally and fails under mypy.
from httpx2 import Response
from sqlalchemy.orm import Session, sessionmaker
from tests.unit.conftest import ALICE, BOB, PROJECT

from calton.api.v1.attachments import content_disposition, parse_range
from calton.models import File, TaskAttachment

TASK = 920
EMPTY_TASK = 923
FORBIDDEN_TASK = 927
MISSING_TASK = 99999

FORBIDDEN_BODY = {"code": 1, "message": "You're not allowed to do this."}
NO_ATTACHMENT = {"code": 4011, "message": "This task attachment does not exist."}
NO_TASK = {"code": 4002, "message": "This task does not exist"}
NO_TASK_ID = {"message": "No task ID provided"}
NO_MULTIPART = {"message": "No multipart form provided"}

HELLO = b"hello attachment world"


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``files.basepath`` at a temp directory for the duration of a test.

    Without this the tests write into whatever the working directory is, which is both a
    surprise for whoever runs them and a way for one test's bytes to be read by another.
    """
    from calton.config import get_settings

    root = tmp_path / "files"
    monkeypatch.setenv("CALTON_FILES_BASEPATH", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI, storage_root: Path) -> TestClient:
    """Overrides conftest's ``client`` so every request in this file has its own store."""
    return TestClient(app, headers={"X-Test-User": str(ALICE)}, raise_server_exceptions=False)


def upload(
    client: TestClient, task: int = TASK, *, files: Any = None, user: int = ALICE
) -> Response:
    payload = files if files is not None else [("files", ("hello.txt", HELLO))]
    return client.put(
        f"/api/v1/tasks/{task}/attachments",
        files=payload,
        headers={"X-Test-User": str(user)},
    )


def upload_one(client: TestClient, name: str, content: bytes, task: int = TASK) -> dict[str, Any]:
    response = upload(client, task, files=[("files", (name, content))])
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["success"], body
    entry: dict[str, Any] = body["success"][0]
    return entry


class TestUpload:
    def test_answers_200_not_201(self, client: TestClient) -> None:
        """Every other v1 create answers 201. This handler is not CreateWeb."""
        assert upload(client).status_code == 200

    def test_the_body_shape(self, client: TestClient) -> None:
        body = upload(client).json()

        assert set(body) == {"errors", "success"}
        assert body["errors"] is None
        entry = body["success"][0]
        # Exactly these five keys. `file_id` and `created_by_id` are columns but are
        # json:"-" upstream, so emitting them would add two fields nobody sends.
        assert set(entry) == {"id", "task_id", "created_by", "file", "created"}
        assert set(entry["file"]) == {"id", "name", "mime", "size", "created"}
        assert entry["task_id"] == TASK
        assert entry["created_by"]["id"] == ALICE
        assert entry["file"]["name"] == "hello.txt"
        assert entry["file"]["size"] == len(HELLO)

    def test_several_files_in_one_request(self, client: TestClient) -> None:
        response = upload(
            client,
            files=[("files", ("a.txt", b"AAAA")), ("files", ("b.bin", b"\x00\x01\x02BBBB"))],
        )

        body = response.json()
        assert [entry["file"]["name"] for entry in body["success"]] == ["a.txt", "b.bin"]
        assert [entry["file"]["size"] for entry in body["success"]] == [4, 7]

    def test_the_wrong_form_field_name_uploads_nothing_and_still_answers_200(
        self, client: TestClient
    ) -> None:
        """Not an error — it simply found no files. Both keys are ``null``, not ``[]``."""
        response = client.put(
            f"/api/v1/tasks/{TASK}/attachments", files=[("attachment", ("y.txt", b"y"))]
        )

        assert response.status_code == 200
        assert response.json() == {"errors": None, "success": None}

    def test_an_empty_file_is_accepted(self, client: TestClient) -> None:
        entry = upload_one(client, "empty.txt", b"")

        assert entry["file"]["size"] == 0
        # The one mime with no charset parameter — measured.
        assert entry["file"]["mime"] == "text/plain"

    def test_the_bytes_are_stored_under_the_file_id(
        self, client: TestClient, storage_root: Path
    ) -> None:
        """The storage path is the id and nothing else. Storing under the client's
        filename would be both a divergence and a path traversal, since the name comes
        from the multipart header unmodified."""
        entry = upload_one(client, "hello.txt", HELLO)

        assert (storage_root / str(entry["file"]["id"])).read_bytes() == HELLO
        assert not (storage_root / "hello.txt").exists()

    def test_a_traversing_filename_cannot_escape_the_store(
        self, client: TestClient, storage_root: Path
    ) -> None:
        entry = upload_one(client, "../../escaped.txt", HELLO)

        assert (storage_root / str(entry["file"]["id"])).read_bytes() == HELLO
        assert not (storage_root.parent.parent / "escaped.txt").exists()


class TestUploadIsPerFileNotAtomic:
    """The opposite rule from the bulk task endpoint — measured separately for that
    reason. Getting these two the same way round is the likely mistake."""

    def test_an_oversize_file_lands_in_errors_without_failing_the_request(
        self, client: TestClient
    ) -> None:
        from calton.services import file_storage

        big = b"x" * (file_storage.MAX_SIZE_BYTES + 1)
        response = upload(client, files=[("files", ("big.bin", big))])

        assert response.status_code == 200
        assert response.json()["success"] is None
        assert response.json()["errors"][0]["code"] == 4012

    def test_a_good_file_alongside_an_oversize_one_is_still_persisted(
        self, client: TestClient
    ) -> None:
        """★ The read-back is the assertion: the response alone cannot show that the
        small file really landed."""
        from calton.services import file_storage

        big = b"x" * (file_storage.MAX_SIZE_BYTES + 1)
        response = upload(client, files=[("files", ("ok.txt", b"ok")), ("files", ("big.bin", big))])

        assert response.status_code == 200
        assert len(response.json()["success"]) == 1
        assert len(response.json()["errors"]) == 1
        listed = client.get(f"/api/v1/tasks/{TASK}/attachments").json()
        assert [entry["file"]["name"] for entry in listed] == ["ok.txt"]

    def test_too_large_message_reports_zero_not_the_real_limit(self, client: TestClient) -> None:
        """★ Upstream builds this message with an accessor that cannot parse "20MB" and
        yields 0, while enforcing 20 MiB from a different one. Copied verbatim. Do not
        "fix" the number: a client comparing it against the real server would then see us
        disagree, and we would be the only one that does."""
        from calton.services import file_storage

        big = b"x" * (file_storage.MAX_SIZE_BYTES + 1)
        response = upload(client, files=[("files", ("big.bin", big))])

        assert response.json()["errors"][0]["message"] == (
            "The task attachment exceeds the configured file size of 0 bytes, "
            f"filesize was {len(big)}"
        )


class TestList:
    def test_empty(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/attachments")

        assert response.status_code == 200
        assert response.json() == []

    def test_pagination_headers_are_present(self, client: TestClient) -> None:
        """MCP clients loop until they have seen ``x-pagination-total-pages`` pages; a
        missing header silently truncates every list to its first page."""
        upload(client)
        response = client.get(f"/api/v1/tasks/{TASK}/attachments")

        assert response.headers["x-pagination-total-pages"] == "1"
        assert response.headers["x-pagination-result-count"] == "1"
        assert (
            response.headers["access-control-expose-headers"]
            == "x-pagination-total-pages, x-pagination-result-count"
        )

    def test_pagination_slices(self, client: TestClient) -> None:
        for n in range(5):
            upload(client, files=[("files", (f"f{n}.txt", f"content-{n}".encode()))])

        first = client.get(f"/api/v1/tasks/{TASK}/attachments?page=1&per_page=2")
        second = client.get(f"/api/v1/tasks/{TASK}/attachments?page=2&per_page=2")

        assert first.headers["x-pagination-total-pages"] == "3"
        assert [e["file"]["name"] for e in first.json()] == ["f0.txt", "f1.txt"]
        assert [e["file"]["name"] for e in second.json()] == ["f2.txt", "f3.txt"]

    def test_a_missing_files_row_leaves_file_null_without_failing_the_list(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """The seed ships exactly this shape (fixture attachment 2 -> file 9999). One
        broken row must not take the whole list down."""
        upload(client)
        with sessions() as session:
            session.add(
                TaskAttachment(
                    id=4242, task_id=TASK, file_id=9999, created_by_id=ALICE, created=None
                )
            )
            session.commit()

        response = client.get(f"/api/v1/tasks/{TASK}/attachments")

        assert response.status_code == 200
        orphan = next(e for e in response.json() if e["id"] == 4242)
        assert orphan["file"] is None

    def test_a_null_mime_serialises_as_empty_string(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """★ Regression: ``files.mime`` is nullable and the seed ships a NULL one.

        Every upload writes a real mime, so no test built only from uploads can reach
        this — the whole list 500'd against the real seed while this file was green.
        Found by replaying the seed through both servers. Go answers ``"mime": ""``.
        """
        with sessions() as session:
            session.add(File(id=8888, name="test", mime=None, size=100, created_by_id=ALICE))
            session.add(TaskAttachment(id=4444, task_id=TASK, file_id=8888, created_by_id=ALICE))
            session.commit()

        response = client.get(f"/api/v1/tasks/{TASK}/attachments")

        assert response.status_code == 200
        entry = next(e for e in response.json() if e["id"] == 4444)
        assert entry["file"]["mime"] == ""

    def test_a_null_created_serialises_as_the_zero_time(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """Both ``created`` columns are nullable; nothing in Calton may emit a JSON null
        for a timestamp.

        Inserted with raw SQL because ``created_column`` carries ``default=utcnow``, so
        passing ``created=None`` through the ORM stores *now* and the NULL never reaches
        the serialiser — the first version of this test asserted the zero time against a
        row that had a real timestamp in it, and failed for that reason rather than
        finding a bug.

        Marked as defensive rather than measured: no row in the seed has a NULL here, so
        this pins our own behaviour, not a comparison with upstream.
        """
        from sqlalchemy import text

        with sessions() as session:
            session.execute(
                text(
                    "INSERT INTO files (id, name, mime, size, created, created_by_id) "
                    "VALUES (9001, 'nulltime', '', 1, NULL, :owner)"
                ),
                {"owner": ALICE},
            )
            session.execute(
                text(
                    "INSERT INTO task_attachments (id, task_id, file_id, created_by_id, created) "
                    "VALUES (4545, :task, 9001, :owner, NULL)"
                ),
                {"task": TASK, "owner": ALICE},
            )
            session.commit()

        entry = next(
            e for e in client.get(f"/api/v1/tasks/{TASK}/attachments").json() if e["id"] == 4545
        )

        assert entry["created"] == "0001-01-01T00:00:00Z"
        assert entry["file"]["created"] == "0001-01-01T00:00:00Z"

    def test_a_link_share_uploader_renders_as_a_pseudo_user(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """★ A negative ``created_by_id`` is a link share, not a missing user.

        Upstream's ``getUsersOrLinkSharesFromIDs`` (pkg/models/users.go:71) maps
        ``-share.id`` to a pseudo-user via ``LinkSharing.toUser``
        (pkg/models/link_sharing.go:130): ``id = -share.id``,
        ``name = share.name + " (Link Share)"`` (or just ``"Link Share"`` when the
        share has no name), ``username = "link-share-<id>"``, timestamps from the
        share row. Calton's old lookup did ``session.get(User, -2)`` → ``None``, so
        the attachment list rendered ``created_by: null`` where Go rendered a
        pseudo-user — exactly the deviation HANDOFF §1.2 records.

        The seed ships ``attachment 3`` with ``created_by_id = -2`` (link share
        id 2, ``name = ""``), so this is the row the parity corpus reaches.
        """
        from calton.models import LinkShare

        with sessions() as session:
            epoch = datetime(2026, 1, 1, tzinfo=UTC)
            session.add(
                LinkShare(
                    id=2,
                    hash="hash-two",
                    name="",
                    project_id=PROJECT,
                    permission=0,
                    sharing_type=1,
                    shared_by_id=ALICE,
                    created=epoch,
                    updated=epoch,
                )
            )
            session.add(File(id=7777, name="orphan", mime="", size=1, created_by_id=ALICE))
            session.add(
                TaskAttachment(id=4343, task_id=TASK, file_id=7777, created_by_id=-2, created=None)
            )
            session.commit()

        entry = next(
            e for e in client.get(f"/api/v1/tasks/{TASK}/attachments").json() if e["id"] == 4343
        )
        assert entry["created_by"] == {
            "id": -2,
            "name": "Link Share",
            "username": "link-share-2",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        assert entry["file"]["id"] == 7777

    def test_two_link_shares_render_as_two_distinct_pseudo_users(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """☠ Boundary pin from HANDOFF §1.2: ``-1`` and ``-2`` are different shares.
        An implementation that maps every negative id to one fixed pseudo-user is
        green on a corpus with a single sample. Both ids need a row to expose that.
        """
        from calton.models import LinkShare

        with sessions() as session:
            epoch = datetime(2026, 1, 1, tzinfo=UTC)
            session.add_all(
                [
                    LinkShare(
                        id=1,
                        hash="one",
                        name="alpha",
                        project_id=PROJECT,
                        permission=0,
                        sharing_type=1,
                        shared_by_id=ALICE,
                        created=epoch,
                        updated=epoch,
                    ),
                    LinkShare(
                        id=2,
                        hash="two",
                        name="",
                        project_id=PROJECT,
                        permission=0,
                        sharing_type=1,
                        shared_by_id=ALICE,
                        created=epoch,
                        updated=epoch,
                    ),
                ]
            )
            session.add_all(
                [
                    File(id=7001, name="f1", mime="", size=1, created_by_id=ALICE),
                    File(id=7002, name="f2", mime="", size=1, created_by_id=ALICE),
                    TaskAttachment(
                        id=5001, task_id=TASK, file_id=7001, created_by_id=-1, created=None
                    ),
                    TaskAttachment(
                        id=5002, task_id=TASK, file_id=7002, created_by_id=-2, created=None
                    ),
                ]
            )
            session.commit()

        rows = {
            e["id"]: e["created_by"]
            for e in client.get(f"/api/v1/tasks/{TASK}/attachments").json()
            if e["id"] in (5001, 5002)
        }
        # ``-1`` ≠ ``-2``: distinct ids, distinct usernames, distinct names (one
        # share has a name, the other does not — the suffix rule both branches).
        # Timestamps come from the share row, so pinning them fixes the
        # "timestamps are zero" mutation on this path too.
        assert rows[5001] == {
            "id": -1,
            "name": "alpha (Link Share)",
            "username": "link-share-1",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        assert rows[5002] == {
            "id": -2,
            "name": "Link Share",
            "username": "link-share-2",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }

    def test_a_gone_but_non_negative_user_id_still_renders_null(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """The deleted-user branch is the *other* arm: a non-negative id with no row
        returns ``None`` (upstream swallows it so the cascade can run). A "negative ids
        are missing" implementation would also pass this — the boundary test above is
        what separates the two — but this keeps the swallow-the-lookup rule visible.

        ``0`` is deliberately not negative: a zero ``created_by_id`` is the absence of
        an uploader, not a link share, and a generic "any falsy" check would conflate
        it with the share arm.
        """
        with sessions() as session:
            session.add(File(id=7778, name="gone", mime="", size=1, created_by_id=ALICE))
            session.add(
                TaskAttachment(
                    id=4444, task_id=TASK, file_id=7778, created_by_id=99999, created=None
                )
            )
            session.commit()

        entry = next(
            e for e in client.get(f"/api/v1/tasks/{TASK}/attachments").json() if e["id"] == 4444
        )
        assert entry["created_by"] is None
        assert entry["file"]["id"] == 7778


class TestDownload:
    def test_serves_the_bytes_with_upstreams_headers(self, client: TestClient) -> None:
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.get(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        assert response.status_code == 200
        assert response.content == HELLO
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["content-disposition"] == "attachment; filename=hello.txt"
        assert response.headers["content-length"] == str(len(HELLO))
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["accept-ranges"] == "bytes"
        # `no-cache`, not the API-wide `no-store`: downloads may be cached but must be
        # revalidated, which is what makes the 304 below work at all.
        assert response.headers["cache-control"] == "no-cache"
        assert "last-modified" in response.headers

    def test_the_wrong_task_for_a_real_attachment_is_404_not_403(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """★ The IDOR guard. Both tasks are readable by the caller, so a lookup that
        ignores ``task_id`` serves the bytes here with a 200."""
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.get(f"/api/v1/tasks/{EMPTY_TASK}/attachments/{entry['id']}")

        assert response.status_code == 404
        assert response.json() == NO_ATTACHMENT

    def test_a_missing_attachment_is_4011(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/tasks/{TASK}/attachments/99999")

        assert response.status_code == 404
        assert response.json() == NO_ATTACHMENT

    def test_missing_bytes_answer_404_rather_than_upstreams_500(
        self, client: TestClient, storage_root: Path
    ) -> None:
        """★ A registered deviation, not an oversight.

        Upstream answers 500 when the row exists but the bytes do not — measured on the
        seed's fixture attachment 1, so the parity corpus really reaches it. Shipping a
        known 500 is worse than a controlled divergence, so we answer the attachment's own
        404. Both sides are pinned in ``harness/corpus/_deviations.yaml``; do not "align"
        this back to a 500.
        """
        entry = upload_one(client, "hello.txt", HELLO)
        (storage_root / str(entry["file"]["id"])).unlink()

        response = client.get(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        assert response.status_code == 404
        assert response.json() == NO_ATTACHMENT


class TestDownloadRanges:
    @pytest.fixture
    def attachment_id(self, client: TestClient) -> int:
        return int(upload_one(client, "hello.txt", HELLO)["id"])

    def test_a_byte_range_is_206(self, client: TestClient, attachment_id: int) -> None:
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{attachment_id}", headers={"Range": "bytes=0-3"}
        )

        assert response.status_code == 206
        assert response.content == b"hell"
        assert response.headers["content-range"] == f"bytes 0-3/{len(HELLO)}"

    def test_a_suffix_range(self, client: TestClient, attachment_id: int) -> None:
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{attachment_id}", headers={"Range": "bytes=-5"}
        )

        assert response.status_code == 206
        assert response.content == b"world"
        assert response.headers["content-range"] == f"bytes 17-21/{len(HELLO)}"

    def test_an_open_ended_range(self, client: TestClient, attachment_id: int) -> None:
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{attachment_id}", headers={"Range": "bytes=3-"}
        )

        assert response.status_code == 206
        assert response.content == HELLO[3:]

    @pytest.mark.parametrize(
        ("header", "body", "has_content_range"),
        [
            # Parsed, but names nothing that exists -> carries Content-Range.
            ("bytes=999-1000", b"invalid range: failed to overlap\n", True),
            ("bytes=22-", b"invalid range: failed to overlap\n", True),
            # Could not be parsed at all -> no Content-Range.
            ("kilometres=0-3", b"invalid range\n", False),
            ("bytes=abc", b"invalid range\n", False),
        ],
    )
    def test_a_range_that_cannot_be_served_is_416(
        self,
        client: TestClient,
        attachment_id: int,
        header: str,
        body: bytes,
        has_content_range: bool,
    ) -> None:
        """★ 416, not a quiet fallback to the whole file.

        Ignoring a bad Range header feels lenient and is wrong. The two bodies are
        different errors, and only the overlap failure carries ``Content-Range``.
        """
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{attachment_id}", headers={"Range": header}
        )

        assert response.status_code == 416
        assert response.content == body
        assert ("content-range" in response.headers) is has_content_range

    def test_a_416_carries_no_cache_control_at_all(
        self, client: TestClient, attachment_id: int
    ) -> None:
        """★ Not ``no-cache``, and not the API-wide ``no-store`` either — absent.

        "Absent" is a third state from "unset", which is why the handler opts out of the
        cache-control middleware rather than just overriding it. The same endpoint's 403
        and 404 *do* carry ``no-store``, so this cannot be done with a path rule.
        """
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{attachment_id}",
            headers={"Range": "bytes=999-1000"},
        )

        assert response.status_code == 416
        assert "cache-control" not in response.headers
        assert "last-modified" not in response.headers
        assert "accept-ranges" not in response.headers

    def test_a_404_on_the_same_endpoint_does_carry_no_store(self, client: TestClient) -> None:
        """The control for the test above: without it, an implementation that dropped
        Cache-Control from every download response would pass."""
        response = client.get(f"/api/v1/tasks/{TASK}/attachments/99999")

        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"


class TestConditionalDownload:
    def test_if_modified_since_in_the_future_is_304_without_a_content_type(
        self, client: TestClient
    ) -> None:
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{entry['id']}",
            headers={"If-Modified-Since": "Wed, 01 Jan 2098 00:00:00 GMT"},
        )

        assert response.status_code == 304
        assert response.content == b""
        # Measured: a 304 carries neither Content-Type nor Accept-Ranges. Sending them is
        # harmless to a browser and still a byte-level parity failure.
        assert "content-type" not in response.headers
        assert "accept-ranges" not in response.headers
        assert response.headers["cache-control"] == "no-cache"

    def test_if_modified_since_in_the_past_serves_the_file(self, client: TestClient) -> None:
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{entry['id']}",
            headers={"If-Modified-Since": "Thu, 01 Jan 1990 00:00:00 GMT"},
        )

        assert response.status_code == 200
        assert response.content == HELLO


class TestDelete:
    def test_removes_the_row_and_the_bytes(self, client: TestClient, storage_root: Path) -> None:
        entry = upload_one(client, "hello.txt", HELLO)
        stored = storage_root / str(entry["file"]["id"])
        assert stored.exists()

        response = client.delete(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted."}
        assert client.get(f"/api/v1/tasks/{TASK}/attachments").json() == []
        assert not stored.exists()

    def test_deleting_twice_is_404(self, client: TestClient) -> None:
        entry = upload_one(client, "hello.txt", HELLO)
        client.delete(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        response = client.delete(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        assert response.status_code == 404
        assert response.json() == NO_ATTACHMENT

    def test_deleting_through_the_wrong_task_is_404_and_does_not_delete(
        self, client: TestClient
    ) -> None:
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.delete(f"/api/v1/tasks/{EMPTY_TASK}/attachments/{entry['id']}")

        assert response.status_code == 404
        assert len(client.get(f"/api/v1/tasks/{TASK}/attachments").json()) == 1


class TestPermissions:
    def test_bob_may_not_list(self, client: TestClient) -> None:
        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments", headers={"X-Test-User": str(BOB)}
        )

        assert response.status_code == 403
        # ★ code 1, not the code 0 the CRUD pipeline (and the bulk task endpoint) uses.
        assert response.json() == FORBIDDEN_BODY

    def test_bob_may_not_download(self, client: TestClient) -> None:
        entry = upload_one(client, "hello.txt", HELLO)

        response = client.get(
            f"/api/v1/tasks/{TASK}/attachments/{entry['id']}", headers={"X-Test-User": str(BOB)}
        )

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY

    def test_bob_may_not_upload(self, client: TestClient) -> None:
        response = upload(client, user=BOB)

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY

    def test_alice_may_not_upload_to_bobs_task(self, client: TestClient) -> None:
        response = upload(client, FORBIDDEN_TASK)

        assert response.status_code == 403
        assert response.json() == FORBIDDEN_BODY

    def test_the_task_is_resolved_before_permission(self, client: TestClient) -> None:
        """A missing task is 404 even for a caller who could not have written it — the
        two gates are ordered, and both are reachable."""
        assert upload(client, MISSING_TASK).status_code == 404
        assert upload(client, MISSING_TASK).json() == NO_TASK

    def test_a_collaborator_may_delete_someone_elses_attachment(
        self, client: TestClient, sessions: sessionmaker[Session]
    ) -> None:
        """Write on the task is the only check — the uploader is never consulted."""
        from calton.models import ProjectUser

        entry = upload_one(client, "hello.txt", HELLO)
        with sessions() as session:
            session.add(ProjectUser(user_id=BOB, project_id=PROJECT, permission=1))
            session.commit()

        response = client.delete(
            f"/api/v1/tasks/{TASK}/attachments/{entry['id']}", headers={"X-Test-User": str(BOB)}
        )

        assert response.status_code == 200


class TestMalformedRequests:
    def test_a_non_multipart_body_is_no_multipart(self, client: TestClient) -> None:
        response = client.put(f"/api/v1/tasks/{TASK}/attachments", json={"nope": 1})

        assert response.status_code == 400
        # No `code` field: these two handlers raise bare string errors.
        assert response.json() == NO_MULTIPART

    def test_a_body_that_cannot_bind_is_no_task_id(self, client: TestClient) -> None:
        """★ The ordering is the opposite of the intuitive one.

        Upstream binds the body first and asks for the multipart form second, so a
        multipart payload sent under a JSON content type fails at the bind — not at the
        "is it multipart?" check. Testing "is it multipart?" first answers NO_MULTIPART to
        both this and the case above.
        """
        response = client.put(
            f"/api/v1/tasks/{TASK}/attachments",
            content=b'--x\r\nContent-Disposition: form-data; name="files"\r\n\r\nz\r\n--x--\r\n',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json() == NO_TASK_ID

    @pytest.mark.parametrize(
        ("method", "path", "expected"),
        [
            # These two go through WebHandler upstream -> the coded error.
            ("GET", "/api/v1/tasks/abc/attachments", "coded"),
            ("DELETE", f"/api/v1/tasks/{TASK}/attachments/abc", "coded"),
            # These two are hand-written handlers -> the bare-string error.
            ("GET", "/api/v1/tasks/abc/attachments/1", "bare"),
            ("GET", f"/api/v1/tasks/{TASK}/attachments/abc", "bare"),
        ],
    )
    def test_a_bad_path_segment_has_two_different_shapes(
        self, client: TestClient, method: str, path: str, expected: str
    ) -> None:
        """★ The same malformed segment answers differently depending on the handler.

        Unifying them is the obvious tidy-up and breaks half of these. Neither shape is
        FastAPI's 422, which is why the path parameters are strings parsed by hand.
        """
        response = client.request(method, path)

        assert response.status_code == 400
        if expected == "coded":
            assert response.json() == {
                "code": 2004,
                "message": "Invalid model provided: Bad Request",
            }
        else:
            assert response.json() == NO_TASK_ID

    def test_a_truncated_multipart_is_400_not_a_silent_success(self, client: TestClient) -> None:
        """★ Starlette's parser is lenient where Go's is not.

        A body cut off mid-part yields an *empty form* rather than an error, so without an
        explicit completeness check this answers 200 ``{"errors": null, "success": null}``
        — a "succeeded, uploaded nothing" response, which is harder to notice than a
        failure. Upstream answers 400. This is the failure mode a large attachment upload
        over a flaky connection actually produces.
        """
        response = client.put(
            f"/api/v1/tasks/{TASK}/attachments",
            content=b"--zz\r\nContent-Dispo",
            headers={"Content-Type": "multipart/form-data; boundary=zz"},
        )

        assert response.status_code == 400
        assert response.json() == NO_TASK_ID

    def test_an_empty_multipart_body_is_400(self, client: TestClient) -> None:
        """Same exit as the truncated case. Upstream answers **500** here — a registered
        deviation (`_deviations.yaml`), not an oversight: a controlled 400 that matches
        the truncated case beats both reproducing a 5xx and answering 200."""
        response = client.put(
            f"/api/v1/tasks/{TASK}/attachments",
            content=b"",
            headers={"Content-Type": "multipart/form-data; boundary=zz"},
        )

        assert response.status_code == 400
        assert response.json() == NO_TASK_ID

    def test_no_upload_path_ever_answers_422(self, client: TestClient) -> None:
        """★ A blanket sweep, because 422 is FastAPI's *default* reaction to a multipart
        problem and this API never sends one.

        Declaring the files as ``UploadFile`` parameters — the idiomatic FastAPI spelling
        — makes several of these 422 at once, and every one of them would be a body no
        v1 client can parse.
        """
        bodies: list[tuple[dict[str, Any], dict[str, str]]] = [
            ({}, {}),
            ({"content": b""}, {"Content-Type": "multipart/form-data; boundary=zz"}),
            ({"content": b"--zz\r\nbroken"}, {"Content-Type": "multipart/form-data; boundary=zz"}),
            ({"content": b"not multipart at all"}, {"Content-Type": "multipart/form-data"}),
            ({"json": {"files": "nope"}}, {}),
            ({"json": []}, {}),
        ]
        for kwargs, headers in bodies:
            response = client.put(f"/api/v1/tasks/{TASK}/attachments", headers=headers, **kwargs)
            assert response.status_code != 422, (kwargs, response.text)
            assert "detail" not in response.json(), "FastAPI's own error shape leaked"

    def test_upload_with_a_bad_task_segment_is_bare(self, client: TestClient) -> None:
        response = client.put("/api/v1/tasks/abc/attachments", files=[("files", ("x.txt", b"x"))])

        assert response.status_code == 400
        assert response.json() == NO_TASK_ID

    def test_list_and_delete_on_a_missing_task_are_4002(self, client: TestClient) -> None:
        assert client.get(f"/api/v1/tasks/{MISSING_TASK}/attachments").json() == NO_TASK
        assert client.delete(f"/api/v1/tasks/{MISSING_TASK}/attachments/1").json() == NO_TASK


class TestContentDisposition:
    """``mime.FormatMediaType`` picks one of three encodings. The harness compares this
    header byte for byte, so quoting when upstream does not is a real failure."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("plain.txt", "attachment; filename=plain.txt"),
            ("with space.txt", 'attachment; filename="with space.txt"'),
            ("semi;colon.txt", 'attachment; filename="semi;colon.txt"'),
            ("unicode-中文.txt", "attachment; filename*=utf-8''unicode-%E4%B8%AD%E6%96%87.txt"),
        ],
    )
    def test_encodings(self, name: str, expected: str) -> None:
        assert content_disposition(name) == expected

    def test_end_to_end_through_a_real_download(self, client: TestClient) -> None:
        """The unit cases above would still pass if the handler never called this
        function."""
        entry = upload_one(client, "with space.txt", b"x")

        response = client.get(f"/api/v1/tasks/{TASK}/attachments/{entry['id']}")

        assert response.headers["content-disposition"] == 'attachment; filename="with space.txt"'


class TestMimeDetection:
    """Content-sniffed, never taken from the client's declared Content-Type — every
    sample below is uploaded as application/octet-stream and still comes back typed."""

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"hello attachment world", "text/plain; charset=utf-8"),
            (b"", "text/plain"),
            (b"\x00\x01\x02BBBB", "application/octet-stream"),
            (b"<html><body>hi</body></html>", "text/html; charset=utf-8"),
            (b'{"a":1}', "application/json"),
            (bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 40, "image/png"),
        ],
    )
    def test_measured_samples(self, client: TestClient, content: bytes, expected: str) -> None:
        entry = upload_one(client, "sample.bin", content)

        assert entry["file"]["mime"] == expected

    def test_the_declared_content_type_is_ignored(self, client: TestClient) -> None:
        """★ Sending a lie in the part header does not change what is stored."""
        response = client.put(
            f"/api/v1/tasks/{TASK}/attachments",
            files=[("files", ("x.bin", b"just words", "image/png"))],
        )

        assert response.json()["success"][0]["file"]["mime"] == "text/plain; charset=utf-8"


class TestParseRangeUnit:
    """The parser on its own, so the 416 cases above cannot pass by accident."""

    def test_no_header_means_no_ranges(self) -> None:
        assert parse_range("", 22) == []

    def test_spans(self) -> None:
        assert parse_range("bytes=0-3", 22) == [(0, 3)]
        assert parse_range("bytes=-5", 22) == [(17, 21)]
        assert parse_range("bytes=3-", 22) == [(3, 21)]
        # An end past the file is clamped rather than rejected.
        assert parse_range("bytes=3-999", 22) == [(3, 21)]

    @pytest.mark.parametrize("header", ["kilometres=0-3", "bytes=abc", "bytes=", "bytes=1"])
    def test_unparseable(self, header: str) -> None:
        from calton.api.v1.attachments import RANGE_UNPARSEABLE, RangeNotSatisfiableError

        with pytest.raises(RangeNotSatisfiableError) as raised:
            parse_range(header, 22)
        assert raised.value.body == RANGE_UNPARSEABLE
        assert raised.value.content_range is None

    @pytest.mark.parametrize("header", ["bytes=999-1000", "bytes=22-"])
    def test_no_overlap(self, header: str) -> None:
        from calton.api.v1.attachments import RANGE_NO_OVERLAP, RangeNotSatisfiableError

        with pytest.raises(RangeNotSatisfiableError) as raised:
            parse_range(header, 22)
        assert raised.value.body == RANGE_NO_OVERLAP
        assert raised.value.content_range == "bytes */22"


class TestWiring:
    def test_all_four_routes_are_in_the_contract(self, app: FastAPI) -> None:
        """Read from ``openapi()["paths"]``, not ``app.routes``: routes merged by
        ``include_router`` have no ``.path`` and are silently missed by that scan."""
        paths = app.openapi()["paths"]

        assert set(paths["/api/v1/tasks/{task}/attachments"]) == {"get", "put"}
        assert set(paths["/api/v1/tasks/{task}/attachments/{attachment}"]) == {"get", "delete"}

    def test_the_routes_are_registered_for_api_tokens(self) -> None:
        """Unregistered routes answer 403 to every API token while JWT callers see
        nothing wrong. Measured group: tasks_attachments."""
        from calton.api.v1 import attachments as attachments_api
        from calton.core.route_registry import registry

        for method, path in attachments_api.REGISTERED_ROUTES:
            assert registry.lookup(method, path) is not None, f"{method} {path} not registered"
