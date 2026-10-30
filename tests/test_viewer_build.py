from organella.viewer.build import build


def test_page_is_one_file_with_nothing_left_to_fill_in():
    page = build()
    assert "/*@" not in page and "base64,@" not in page
    assert page.count("<script>") == 1 and "data:font/woff2;base64," in page
