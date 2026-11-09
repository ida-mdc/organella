import functools
import http.client
import http.server
import threading

import pytest

from organella.viewer.server import _Handler


@pytest.fixture()
def served(tmp_path):
    (tmp_path / "meshes.bin").write_bytes(bytes(range(256)) * 4)       # 1024 bytes, each position telling its own value
    handler = functools.partial(_Handler, page=b"<html></html>", directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def get(port, path, range_header=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", path, headers={"Range": range_header} if range_header else {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def test_a_byte_range_of_a_file_comes_back_as_206_with_only_those_bytes(served):
    response, body = get(served, "/meshes.bin", "bytes=10-19")
    assert response.status == 206 and response.getheader("Content-Range") == "bytes 10-19/1024"
    assert body == bytes(range(10, 20)) and response.getheader("Content-Length") == "10"


def test_open_ended_and_suffix_ranges_and_a_range_past_the_end(served):
    response, body = get(served, "/meshes.bin", "bytes=1020-")
    assert response.status == 206 and body == bytes([252, 253, 254, 255])
    response, body = get(served, "/meshes.bin", "bytes=-3")
    assert response.status == 206 and body == bytes([253, 254, 255]) and response.getheader("Content-Range") == "bytes 1021-1023/1024"
    response, body = get(served, "/meshes.bin", "bytes=1000-5000")
    assert response.status == 206 and len(body) == 24
    response, _ = get(served, "/meshes.bin", "bytes=2000-2100")
    assert response.status == 416 and response.getheader("Content-Range") == "bytes */1024"


def test_without_a_range_the_whole_file_comes_back_and_says_ranges_are_welcome(served):
    response, body = get(served, "/meshes.bin")
    assert response.status == 200 and len(body) == 1024 and response.getheader("Accept-Ranges") == "bytes"
    response, body = get(served, "/")
    assert response.status == 200 and body == b"<html></html>"
    response, _ = get(served, "/missing.bin", "bytes=0-3")
    assert response.status == 404


def test_a_head_with_a_range_says_206_and_the_whole_length_as_s3_does_which_is_how_duckdb_finds_a_host_reads_ranges(served):
    connection = http.client.HTTPConnection("127.0.0.1", served, timeout=5)
    connection.request("HEAD", "/meshes.bin", headers={"Range": "bytes=0-"})
    response = connection.getresponse()
    assert response.status == 206 and response.getheader("Content-Length") == "1024" and response.read() == b""
    connection.request("HEAD", "/meshes.bin")
    plain = connection.getresponse()
    assert plain.status == 200 and plain.getheader("Accept-Ranges") == "bytes"
    connection.close()
