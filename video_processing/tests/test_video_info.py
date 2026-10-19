"""
Parsing the V3C info JSONs. Both formats on the HDD, using the real 00001 and 17500 as samples.
"""

import json
from datetime import date

from pipeline.video_info import VideoInfo, clean_description, parse_info

SCRAPE_00001 = {
    "vimeoId": "96990666", "duration": 273, "height": 720, "width": 1280, "v3cId": "00001",
    "channel": "valentinxflad", "title": "PARISIANA JAM",
    "tags": ["fish eye", "edit", "vid?o", "BMX", "Flatland", "bmx"],
    "categories": ["/categories/sports", "/categories/sports/outdoorsports/videos"], "uploadDate": "2014-05-31 10:32:38",
    "license": "by-nc-sa",
    "description": '<p class="first">On Sunday, May 25th, many riders from Paris and from Argentina '
                   'came to the infamous &quot;Opera Spot&quot; in the middle of Paris to ride '
                   'together.</p>\n<p>It was a great time: we rode as hard as we could...</p>\n'
                   '<p>Valentin FLAD - May 2014</p>',
}

YTDLP_17500 = {
    "id": "233592617", "title": "ISPAヤングマリナープログラム 2017", "uploader": "ISPA Japan",
    "upload_date": "20170912", "duration": 430, "width": 1920, "height": 1080,
    "description": 'This is "ISPAヤングマリナープログラム 2017" by ISPA Japan on Vimeo, the home for '
                   'high quality videos and the people who love them.',
}


def test_vimeo_scrape():
    info = parse_info(json.dumps(SCRAPE_00001))
    assert info.title == "PARISIANA JAM"
    assert info.channel == "valentinxflad"
    assert info.tags == ["fish eye", "edit", "vid?o", "bmx", "flatland"]
    assert info.categories == ["sports", "sports/outdoorsports"]
    assert info.upload_date == date(2014, 5, 31)
    assert info.description.startswith('On Sunday, May 25th')
    assert '"Opera Spot"' in info.description and "<p" not in info.description
    assert info.description.endswith("as we could...\n\nValentin FLAD - May 2014")


def test_ytdlp_dump_without_a_description_has_none():
    """Vimeo's page filler is the same sentence for every undescribed video. Embedded, it would
    make all of them look alike."""
    info = parse_info(json.dumps(YTDLP_17500))
    assert info.description == ""
    assert info.channel == "ISPA Japan"
    assert info.tags == [] and info.categories == []
    assert info.upload_date == date(2017, 9, 12)


def test_line_breaks_survive_and_blank_runs_collapse():
    text = clean_description('A man&#039;s struggle<br>\n <br>\nGenre:</p>\n<p>Drama<br>\n <br>\n')
    assert text == "A man's struggle\n\nGenre:\n\nDrama"


def test_passage_puts_title_and_tags_before_the_description():
    passage = VideoInfo(title="T", tags=["a", "b"], description="long text").passage()
    assert passage == "T\na, b\nlong text"
    assert VideoInfo(description="only this").passage() == "only this"
    assert VideoInfo().passage() == ""


def test_windows_1252_files_keep_their_accents():
    """00116.json on the HDD: `Am\xe9rica` in a file that is otherwise JSON."""
    from pipeline.datasets import decode_text
    raw = '{"title": "El crecimiento de Am\u00e9rica \u201cLatina\u201d"}'.encode("cp1252")
    assert parse_info(decode_text(raw)).title == "El crecimiento de Am\u00e9rica \u201cLatina\u201d"
    assert decode_text("Am\u00e9rica".encode("utf-8")) == "Am\u00e9rica"
